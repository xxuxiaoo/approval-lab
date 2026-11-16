"""Multi-round official-profile reviews, durable accounting and bounded fixture tools."""

import asyncio
import json
from dataclasses import dataclass

from .metrics import TOKEN_FIELDS
from .official import cc_body, codex_messages, parse_cc, parse_codex, request_body, tools
from .pricing import estimate_cost, normalize_usage
from .providers import ProviderError, redact
from .storage import dumps, now_iso
from .virtual import VirtualEnvironment


class ReviewStopped(ValueError):
    pass


def review_rows(store, run_id, ordinal=None):
    query = "SELECT * FROM review_turns WHERE run_id=?"
    params = (run_id,)
    if ordinal is not None:
        query += " AND ordinal=?"
        params = (run_id, ordinal)
    rows = store.query(query + " ORDER BY id", params)
    for row in rows:
        for field in ("request", "response", "usage", "evidence"):
            if row[field] is not None:
                row[field] = json.loads(row[field])
    return rows


def sum_usage(rows, protocol):
    total = normalize_usage(None, protocol)
    for key in TOKEN_FIELDS:
        total[key] = sum(row["usage"].get(key, 0) for row in rows)
    for key in ("input_reported", "output_reported", "cache_read_reported", "cache_write_reported"):
        total[key] = bool(rows) and all(row["usage"].get(key, False) for row in rows)
    total["warnings"] = sorted({warning for row in rows for warning in row["usage"].get("warnings", [])})
    total["raw"] = {"round_count": len(rows), "source": "review_turns"}
    return total


@dataclass
class ReviewResult:
    decision: str
    reason: str
    text: str


class ReviewSession:
    def __init__(self, engine, run_id, ordinal, repeat, config, case, provider, client, profiles, stop):
        self.engine, self.store = engine, engine.store
        self.run_id, self.ordinal, self.repeat = run_id, ordinal, repeat
        self.config, self.case, self.provider, self.client = config, case, provider, client
        self.profiles, self.stop = profiles, stop
        self.environment = VirtualEnvironment(case.scene)

    def rows(self):
        return review_rows(self.store, self.run_id, self.ordinal)

    async def turn(self, stage, messages=None, *, cc=None):
        if self.stop.is_set():
            raise ReviewStopped("测试已停止；保留本题已发生的轮次用量。")
        # No await between checking and inserting: all workers share this event loop.
        rows = review_rows(self.store, self.run_id)
        if len(rows) >= self.config.request_budget:
            self.stop.set()
            raise ReviewStopped("已到本轮 API 请求硬预算；未发送下一次请求。")
        spent = estimate_cost(sum_usage(rows, self.provider["protocol"]), self.config.pricing.model_dump())["total"]
        if self.config.cost_stop is not None and spent >= self.config.cost_stop:
            self.stop.set()
            raise ReviewStopped("已到填写报价下的费用止损线；最后一笔请求可能使金额超过止损线。")
        endpoint, body = request_body(self.provider["protocol"], self.config, messages,
                                      available_tools=tools() if cc is None else None, cc=cc)
        trace_id = self.store.execute(
            "INSERT INTO review_turns(run_id,ordinal,case_id,repeat_index,stage,status,created_at,request,usage) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.run_id, self.ordinal, self.case.id, self.repeat, stage, "in_flight", now_iso(),
             redact(dumps({"endpoint": endpoint, "body": body}), self.provider["api_key"]),
             dumps(normalize_usage(None, self.provider["protocol"]))),
        )
        try:
            completion = await self.client.complete_turn(endpoint, body, self.config.timeout_seconds)
        except (ProviderError, asyncio.CancelledError):
            self.store.execute("UPDATE review_turns SET status='unknown',error=? WHERE id=?",
                               ("响应未取得；可能已计费，没有自动重发。", trace_id))
            self.engine.refresh_summary(self.run_id, self.config)
            raise
        response = {"text": completion.text, "tool_calls": completion.calls,
                    "tool_requests": completion.tool_requests,
                    "continuation": completion.continuation, "model": completion.model,
                    "response_id": completion.response_id, "request_id": completion.request_id,
                    "stop_reason": completion.stop_reason, "incomplete": completion.incomplete}
        self.store.execute("UPDATE review_turns SET status='returned',response=?,usage=?,latency_ms=? WHERE id=?",
                           (redact(dumps(response), self.provider["api_key"]),
                            redact(dumps(completion.usage), self.provider["api_key"]), completion.latency_ms, trace_id))
        self.engine.refresh_summary(self.run_id, self.config)
        if self.config.cost_stop is not None and (not completion.usage.get("input_reported")
                                                 or not completion.usage.get("output_reported")):
            self.stop.set()
            raise ReviewStopped("中转未完整返回用量，无法继续按费用止损线控制开销；已停止。")
        if completion.incomplete:
            raise ValueError("响应被截断、拒答或未完成；保留用量，不采纳其中的放行。")
        return completion, trace_id

    async def run(self):
        if self.case.scene.profile == "claude_code":
            profile = self.profiles["claude_code"]
            first, _ = await self.turn("cc_fast", cc=cc_body(self.case, profile, "fast"))
            if first.calls or first.tool_requests:
                raise ValueError("CC 审核器无工具能力；中转却返回工具调用，结果无效。")
            decision, reason = parse_cc(first.text, stage1=True)
            valid_stop = first.stop_reason in ("stop_sequence", "end_turn", "stop")
            if decision == "allow" and valid_stop:
                return ReviewResult(decision, reason, first.text)
            # A flagged action or set-aside fast allow gets an independent review
            # of the same input, without feeding stage one's answer into it.
            second, _ = await self.turn("cc_thinking", cc=cc_body(self.case, profile, "thinking"))
            if second.calls or second.tool_requests:
                raise ValueError("CC 复核返回了不允许的工具调用。")
            decision, reason = parse_cc(second.text)
            return ReviewResult(decision, reason, second.text)
        messages = codex_messages(self.case, self.profiles["codex"])
        for round_index in range(self.config.max_review_rounds):
            completion, trace_id = await self.turn(f"codex_{round_index+1}", messages)
            if not completion.calls:
                if completion.tool_requests:
                    raise ValueError("模型请求了该官方配置未提供的工具类型。")
                decision, reason = parse_codex(completion.text)
                return ReviewResult(decision, reason, completion.text)
            if len(self.environment.calls) + len(completion.calls) > self.config.max_evidence_calls:
                raise ValueError("已到查证工具次数上限，本题没有最终有效结论。")
            messages.append(completion.continuation)
            evidence = []
            for call in completion.calls:
                try:
                    arguments = json.loads(call["function"]["arguments"])
                    if not isinstance(arguments, dict):
                        raise TypeError("arguments must be an object")
                    name, call_id = call["function"]["name"], call["id"]
                    if not isinstance(name, str) or not isinstance(call_id, str) or not call_id:
                        raise ValueError("missing tool name or call id")
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("工具调用合同无效；没有执行任何命令。") from exc
                result = self.environment.call(name, arguments)
                evidence.append(self.environment.calls[-1])
                messages.append({"role": "tool", "tool_call_id": call_id, "content": dumps(result)})
            self.store.execute("UPDATE review_turns SET evidence=? WHERE id=?", (dumps(evidence), trace_id))
        raise ValueError("已到审核轮次上限；保留查证过程和所有用量，本题不计通过。")
