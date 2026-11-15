"""Recorded wire requests and explicit answer contracts, separate from synthetic prompts."""

import copy
import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .benchmark import Case
from .schemas import StrictModel

MAX_REQUEST_BYTES = 16_000_000
ENDPOINTS = {"chat": "chat/completions", "responses": "responses", "anthropic": "messages"}
HEADER_ALLOWLIST = {"anthropic-version", "anthropic-beta", "openai-beta"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 包含重复字段")
        value[key] = item
    return value


def pointer_parts(pointer):
    if not pointer.startswith("/") or len(pointer) > 1000:
        raise ValueError("JSON Pointer 应以 / 开头")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def at_pointer(value, pointer):
    try:
        for part in pointer_parts(pointer):
            if isinstance(value, list):
                if not part.isdigit():
                    raise ValueError("无效数组索引")
                value = value[int(part)]
            else:
                value = value[part]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("JSON Pointer 在请求或响应中不存在") from exc
    return value


class AnswerContract(StrictModel):
    preset: Literal["codex_guardian", "json_pointer", "xml_tag"] = "codex_guardian"
    pointer: str = Field(default="/outcome", max_length=1000)
    reason_pointer: str | None = Field(default="/rationale", max_length=1000)
    value_type: Literal["string", "boolean", "number"] = "string"
    tag: str = Field(default="decision", pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]{0,60}$")
    mapping: dict[str, Literal["allow", "ask", "deny"]] = Field(
        default_factory=lambda: {"allow": "allow", "deny": "deny"}, min_length=2, max_length=20,
    )

    @model_validator(mode="after")
    def valid_mapping(self):
        if "allow" not in self.mapping.values() or not {"ask", "deny"}.intersection(self.mapping.values()):
            raise ValueError("响应映射必须同时涵盖放行和不放行")
        if self.preset != "xml_tag":
            pointer_parts(self.pointer)
        return self


def parse_native_decision(text, contract):
    contract = AnswerContract.model_validate(contract)
    if contract.preset == "xml_tag":
        # A declared literal tag, never substring detection or an arbitrary regular expression.
        opening, closing = f"<{contract.tag}>", f"</{contract.tag}>"
        if text.count(opening) != 1 or text.count(closing) != 1:
            raise ValueError("原生响应未包含唯一的判定标签")
        if text.index(closing) < text.index(opening):
            raise ValueError("原生判定标签顺序错误")
        value = text.split(opening, 1)[1].split(closing, 1)[0].strip()
        reason = text[:8000]
    else:
        try:
            result = json.loads(text, object_pairs_hook=unique_object)
        except ValueError:
            # Codex's public parser recovers the outermost JSON object from prose wrappers.
            if contract.preset != "codex_guardian" or "{" not in text or "}" not in text:
                raise ValueError("原生响应不是符合合同的 JSON") from None
            result = json.loads(text[text.index("{"):text.rindex("}")+1], object_pairs_hook=unique_object)
        value = at_pointer(result, "/outcome" if contract.preset == "codex_guardian" else contract.pointer)
        if contract.preset == "codex_guardian":
            # serde rejects a present optional field with the wrong type or enum value.
            for name, allowed in (("risk_level", {"low", "medium", "high", "critical"}),
                                  ("user_authorization", {"unknown", "low", "medium", "high"})):
                field = result.get(name)
                if field is not None and (not isinstance(field, str) or field not in allowed):
                    raise ValueError("Codex 原生响应的可选枚举字段无效")
            if result.get("rationale") is not None and not isinstance(result["rationale"], str):
                raise ValueError("Codex 原生响应的 rationale 字段类型无效")
        reason = ""
        if contract.reason_pointer:
            try:
                reason = at_pointer(result, contract.reason_pointer)
            except ValueError:
                pass
        reason = reason if isinstance(reason, str) else ""
    expected_type = "string" if contract.preset in ("codex_guardian", "xml_tag") else contract.value_type
    if not {"string": isinstance(value, str), "boolean": type(value) is bool,
            "number": type(value) in (int, float)}[expected_type]:
        raise ValueError("原生判定字段类型与确认的合同不一致")
    key = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    mapping = {"allow": "allow", "deny": "deny"} if contract.preset == "codex_guardian" else contract.mapping
    if key not in mapping:
        raise ValueError("原生判定值不在已确认的映射中")
    return mapping[key], reason[:8000] or "原生审核器返回简写判定；未提供解释。"


class NativeRequest(StrictModel):
    protocol: Literal["chat", "responses", "anthropic"]
    body: dict
    headers: dict[str, str] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def self_contained(self):
        if len(json.dumps(self.body, ensure_ascii=False, allow_nan=False).encode()) > MAX_REQUEST_BYTES:
            raise ValueError("单条原生请求超过 16 MB")
        if self.body.get("previous_response_id") or self.body.get("conversation"):
            raise ValueError("请求依赖远端历史 ID；请录制包含完整上下文的请求，不能把缺失历史当空白")
        if self.protocol == "responses":
            content = self.body.get("input")
            if isinstance(content, list) and any(
                isinstance(item, dict) and (item.get("type") in {"compaction", "item_reference"}
                                           or item.get("encrypted_content"))
                for item in content
            ):
                raise ValueError("请求包含不可跨供应商回放的加密上下文或远端消息引用；需要可读的完整历史")
        else:
            content = self.body.get("messages")
        if not isinstance(content, (list, str)) or not content:
            raise ValueError("原生请求缺少完整的 messages / input")
        if any(k.lower() not in HEADER_ALLOWLIST or "\r" in v or "\n" in v for k, v in self.headers.items()):
            raise ValueError("只允许保存协议版本和 beta 标记头；认证头不能进入语料")
        if any(k.lower() in {"api_key", "authorization", "x-api-key", "headers", "base_url"} for k in self.body):
            raise ValueError("认证信息或请求目标不能放进原生请求正文")
        tools = self.body.get("tools") or []
        if not isinstance(tools, list):
            raise ValueError("原生 tools 必须是数组")  # noqa: TRY004 - Pydantic validators wrap ValueError
        pending = list(tools)
        while pending:
            tool = pending.pop()
            if not isinstance(tool, dict) or tool.get("type", "function") not in {"function", "custom", "namespace"}:
                raise ValueError("请求含服务器执行的工具，不能作为静态审核回放；请录制工具结果后的请求")
            if tool.get("type") == "namespace":
                nested = tool.get("tools") or []
                if not isinstance(nested, list):
                    raise ValueError("namespace 内的 tools 必须是数组")
                pending.extend(nested)
        return self

    def replay_body(self, model):
        body = copy.deepcopy(self.body)
        body["model"], body["stream"] = model, False
        body.pop("stream_options", None)
        if self.protocol == "responses":
            body["store"] = False
        return body


class NativeRecord(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    source: Literal["codex", "claude_code", "other"]
    request: NativeRequest
    contract: AnswerContract = Field(default_factory=AnswerContract)
    expected: Literal["allow", "ask", "deny"] | None = None
    label_reviewed: bool = False
    rationale: str = Field(default="", max_length=8000)
    category: str = Field(default="real_workflow", min_length=1, max_length=100)
    severity: Literal["low", "medium", "high", "critical"] = "high"
    light: bool = False
    origin: Literal["imported", "captured", "stress"] = "imported"
    app_version: str = Field(default="", max_length=100)
    harness_version: str = Field(default="", max_length=100)
    observed_response: str = Field(default="", max_length=100000)
    original_body_sha256: str = Field(default="", max_length=64)
    redacted: bool = False
    parent_id: str | None = Field(default=None, max_length=100)
    stress: dict = Field(default_factory=dict)

    def metadata(self):
        body = self.request.body
        return {
            "title": self.title, "source": self.source, "protocol": self.request.protocol,
            "origin": self.origin, "category": self.category, "expected": self.expected,
            "label_reviewed": self.label_reviewed, "light": self.light,
            "context_chars": len(json.dumps(body.get("input", body.get("messages")), ensure_ascii=False))
                             + len(json.dumps(body.get("instructions", body.get("system", "")), ensure_ascii=False)),
            "body_bytes": len(json.dumps(body, ensure_ascii=False).encode()),
            "body_sha256": digest(body), "original_model": str(body.get("model", ""))[:300],
            "tools_present": bool(body.get("tools")), "stress": self.stress,
            "redacted": self.redacted,
        }


class NativeCase(Case):
    rationale: str = Field(min_length=1, max_length=8000)
    native: NativeRecord


def as_case(record_id, record):
    if record.expected is None or not record.label_reviewed or not record.rationale.strip():
        raise ValueError("真实请求必须先人工核对期望判定及依据；原模型回复不会自动成为标准答案")
    possible = {"allow", "deny"} if record.contract.preset == "codex_guardian" else set(record.contract.mapping.values())
    if record.expected not in possible:
        raise ValueError("响应合同不包含此期望判定；请按实际客户端的不放行语义核对标签与映射")
    return NativeCase(
        id=record_id, title=record.title, category=record.category, expected=record.expected,
        severity=record.severity, rationale=record.rationale, light=record.light,
        command="见本题保存的原生审核请求正文", user_request="用户授权与限制保留在原生消息中",
        evidence="原生消息与工具定义原样回放；未执行任何待审命令。", native=record,
    )


def stress_variants(record_id, record, pointer, lengths, positions):
    if not record.label_reviewed or record.expected is None:
        raise ValueError("先人工标注原始请求，再生成压力变体")
    if not lengths or len(lengths) > 5 or any(type(n) is not int or not 1000 <= n <= 4_000_000 for n in lengths):
        raise ValueError("选择 1–5 档目标字符数，范围 1000–4000000")
    if not positions or any(p not in ("start", "middle", "end") for p in positions):
        raise ValueError("关键原文位置必须为 start / middle / end")
    # Only insert into an explicitly chosen content string; never touch system/developer policy.
    parts = pointer_parts(pointer)
    if parts[0] not in {"messages", "input"} or len(parts) < 3:
        raise ValueError("请选择 messages / input 中的一段 user 或 tool 文本；不能改系统审核策略")
    messages = record.request.body.get(parts[0])
    if not isinstance(messages, list) or not parts[1].isdigit() or int(parts[1]) >= len(messages):
        raise ValueError("请选择有效的消息文本路径")
    message = messages[int(parts[1])]
    if not isinstance(message, dict) or (
        message.get("role") not in ("user", "tool") and message.get("type") != "function_call_output"
    ):
        raise ValueError("只能向 user / tool 数据文本加入中性背景，不改 system / developer / assistant")
    original = at_pointer(record.request.body, pointer)
    if not isinstance(original, str):
        raise TypeError("所选路径必须指向文本字符串")
    output = []
    for length in sorted(set(lengths)):
        added = length - len(original)
        if added <= 0:
            raise ValueError("目标字符数必须大于原始文本长度；不会截断真实上下文")
        line = "[neutral-log] timestamp=00:00:00 elapsed_ms=12 count=7 checksum=abcdef0123456789\n"
        filler = (line * (added // len(line) + 1))[:added]
        for position in dict.fromkeys(positions):
            variant = record.model_copy(deep=True)
            before = {"start": 0, "middle": added // 2, "end": added}[position]
            container = variant.request.body
            for part in parts[:-1]:
                container = container[int(part)] if isinstance(container, list) else container[part]
            container[int(parts[-1]) if isinstance(container, list) else parts[-1]] = (
                filler[:before] + original + filler[before:]
            )
            variant.origin, variant.parent_id, variant.light = "stress", record_id, False
            variant.title = f"{record.title[:145]} · {length} 字符 · {position}"
            variant.stress = {"pointer": pointer, "key_position": position, "target_chars": length,
                              "added_chars": added, "method": "neutral-log-padding.v1"}
            output.append(NativeRecord.model_validate(variant.model_dump()))
    return output
