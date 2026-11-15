import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .benchmark import corpus_hash, parse_decision, select_cases, system_prompt
from .metrics import summarize
from .pricing import normalize_usage
from .providers import ProviderClient, ProviderError, redact
from .schemas import RunInput
from .storage import dumps, now_iso

logger = logging.getLogger(__name__)


def next_due(spec, after=None):
    after = after or datetime.now(UTC)
    if spec["kind"] == "interval":
        return (after + timedelta(hours=spec["interval_hours"])).isoformat()
    zone = ZoneInfo(spec["timezone"])
    local = after.astimezone(zone)
    hour, minute = map(int, spec["local_time"].split(":"))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.astimezone(UTC).isoformat()


def result_rows(store, run_id):
    rows = store.query("SELECT * FROM results WHERE run_id=? ORDER BY ordinal", (run_id,))
    for row in rows:
        row["case"] = json.loads(row.pop("case_data"))
        row["usage"] = json.loads(row["usage"])
    return rows


class RunEngine:
    def __init__(self, store, client_factory=ProviderClient):
        self.store = store
        self.client_factory = client_factory
        self.tasks = {}
        self.stop_events = {}
        self.queue_lock = asyncio.Lock()
        self.scheduler_task = None
        self.scheduler_error = None

    async def start(self):
        # Never resume paid requests automatically after a crash; their billing outcome is unknown.
        self.store.execute(
            "UPDATE runs SET status='interrupted',finished_at=?,error=? WHERE status IN ('queued','running')",
            (now_iso(), "进程中断；没有自动重发。未记录的在途请求可能已计费。"),
        )
        self.scheduler_task = asyncio.create_task(self.scheduler_loop())

    async def shutdown(self):
        if self.scheduler_task:
            self.scheduler_task.cancel()
            await asyncio.gather(self.scheduler_task, return_exceptions=True)
        for event in self.stop_events.values():
            event.set()
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def validate(self, config):
        cases = select_cases(config.mode, config.custom_cases)
        total = len(cases) * config.repeats
        if total > config.request_budget:
            raise ValueError(f"需要 {total} 次请求，超过本次请求预算 {config.request_budget}")
        provider = self.store.provider(config.provider_id, secret=True)
        if provider["protocol"] == "anthropic" and config.reasoning_effort:
            raise ValueError("Anthropic 协议暂不映射 reasoning_effort，请选择默认；不会悄悄更改推理设置。")
        if provider["protocol"] != "anthropic" and config.anthropic_cache:
            raise ValueError("显式 Anthropic 缓存仅适用于 Anthropic Messages 协议")
        if config.mode != "custom" and config.custom_cases:
            raise ValueError("导入用例时请选择自定义模式")
        return cases, provider, total

    def create_run(self, config: RunInput, schedule_id=None):
        if len(self.tasks) >= 20:
            raise ValueError("待测任务已达 20 个，请先完成或停止现有任务")
        cases, provider, total = self.validate(config)
        prompt = system_prompt(config)
        run_id = uuid.uuid4().hex
        config_data = config.model_dump()
        snapshot = {
            "provider": {key: value for key, value in provider.items() if key != "api_key"},
            "group_verified": False,
            "group_note": "分组为用户手填，程序无法核验中转后台当前分组。",
            "corpus_version": "2026-11-15.v1" if config.mode != "custom" else "custom",
            "corpus_hash": corpus_hash(cases), "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
            "system_prompt": prompt, "case_count": len(cases),
        }
        self.store.execute(
            "INSERT INTO runs(id,status,created_at,config,snapshot,total,schedule_id) VALUES(?,?,?,?,?,?,?)",
            (run_id, "queued", now_iso(), dumps(config_data), dumps(snapshot), total, schedule_id),
        )
        stop = asyncio.Event()
        self.stop_events[run_id] = stop
        task = asyncio.create_task(self.execute(run_id, config, cases, provider, prompt, stop))
        self.tasks[run_id] = task
        task.add_done_callback(lambda done: self.cleanup(run_id, done))
        return run_id

    def cleanup(self, run_id, task):
        self.tasks.pop(run_id, None)
        self.stop_events.pop(run_id, None)
        # Retrieve exceptions so background task failures don't leak to logs.
        if not task.cancelled():
            task.exception()

    def stop(self, run_id):
        event = self.stop_events.get(run_id)
        if event:
            event.set()
            return True
        return False

    async def execute(self, run_id, config, cases, provider, prompt, stop):
        try:
            async with self.queue_lock:
                if stop.is_set():
                    self.store.execute("UPDATE runs SET status='cancelled',finished_at=? WHERE id=?",
                                       (now_iso(), run_id))
                    self.refresh_summary(run_id, config)
                    return
                self.store.execute("UPDATE runs SET status='running',started_at=? WHERE id=?", (now_iso(), run_id))
                work = [(case, repeat) for repeat in range(config.repeats) for case in cases]
                random.Random(config.seed).shuffle(work)
                queue = asyncio.Queue()
                for ordinal, (case, repeat) in enumerate(work):
                    queue.put_nowait((ordinal, case, repeat))
                fatal_errors = 0
                async with self.client_factory(provider) as client:
                    async def worker():
                        nonlocal fatal_errors
                        while not stop.is_set():
                            try:
                                ordinal, case, repeat = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                return
                            decision, reason, raw, error = None, None, None, None
                            usage = normalize_usage(None, provider["protocol"])
                            returned_model = response_id = request_id = None
                            started = time.perf_counter()
                            latency = None
                            try:
                                completion = await client.complete(config, prompt, case)
                                fatal_errors = 0
                                usage, raw, latency = completion.usage, completion.text, completion.latency_ms
                                returned_model = completion.model
                                response_id, request_id = completion.response_id, completion.request_id
                                if completion.incomplete:
                                    raise ValueError("响应被截断、拒答或未完成；不采纳其中的 allow")
                                decision, reason = parse_decision(raw)
                            except ProviderError as exc:
                                error = str(exc)
                                fatal_errors += 1
                                # Avoid paying for an entire run with an incompatible/dead endpoint.
                                if fatal_errors >= 3 or error.startswith(("HTTP 401", "HTTP 403", "HTTP 404")):
                                    stop.set()
                            except (ValueError, TypeError) as exc:
                                error = "响应格式错误：" + str(exc)[:500]
                            latency = latency if latency is not None else (time.perf_counter() - started) * 1000
                            # Sanitize every persisted provider-derived string, including raw usage.
                            usage = json.loads(redact(dumps(usage), provider["api_key"]))
                            self.store.execute(
                                "INSERT INTO results(run_id,ordinal,case_id,repeat_index,case_data,decision,reason,"
                                "raw_response,error,latency_ms,usage,returned_model,response_id,request_id) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (run_id, ordinal, case.id, repeat, dumps(case.model_dump()), decision,
                                 redact(reason or "", provider["api_key"]), raw,
                                 redact(error or "", provider["api_key"]) or None, latency, dumps(usage),
                                 returned_model, response_id, request_id),
                            )
                            self.refresh_summary(run_id, config)
                            if config.stop_on_unsafe and decision == "allow" and case.expected != "allow":
                                stop.set()
                            queue.task_done()

                    async with asyncio.TaskGroup() as workers:
                        for _ in range(config.concurrency):
                            workers.create_task(worker())
                count = self.store.one("SELECT count(*) AS n FROM results WHERE run_id=?", (run_id,))["n"]
                status = "completed" if count == len(work) else "cancelled"
                self.store.execute("UPDATE runs SET status=?,finished_at=?,error=? WHERE id=?",
                                   (status, now_iso(), "连接连续失败，已停止后续请求。" if fatal_errors >= 3
                                    else None, run_id))
                self.refresh_summary(run_id, config)
        except asyncio.CancelledError:
            self.store.execute("UPDATE runs SET status='interrupted',finished_at=?,error=? WHERE id=?",
                               (now_iso(), "进程停止；在途请求可能已计费，没有自动重试。", run_id))
            self.refresh_summary(run_id, config)
            raise
        except Exception as exc:  # noqa: BLE001 - task boundary; never log a possibly secret exception body
            # Exception details may contain credentials: only expose the exception class here.
            self.store.execute("UPDATE runs SET status='failed',finished_at=?,error=? WHERE id=?",
                               (now_iso(), f"内部错误 {type(exc).__name__}；已停止请求。", run_id))
            self.refresh_summary(run_id, config)

    def refresh_summary(self, run_id, config=None):
        run = self.store.one("SELECT total,config FROM runs WHERE id=?", (run_id,))
        config = config or RunInput.model_validate_json(run["config"])
        rows = result_rows(self.store, run_id)
        summary = summarize(rows, run["total"], config.pricing.model_dump(), config.repeats)
        self.store.execute("UPDATE runs SET completed=?,summary=? WHERE id=?", (len(rows), dumps(summary), run_id))
        return summary

    async def scheduler_loop(self):
        while True:
            try:
                self.tick()
                self.scheduler_error = None
            except Exception as exc:  # noqa: BLE001 - keep scheduler alive; log only the exception class
                self.scheduler_error = type(exc).__name__
                logger.error("Scheduler tick failed (%s); no exception body logged", self.scheduler_error)
            await asyncio.sleep(10)

    def tick(self, current=None):
        current = current or datetime.now(UTC)
        for schedule in self.store.query("SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=?",
                                         (current.isoformat(),)):
            spec = json.loads(schedule["spec"])
            next_time = next_due(spec, current)
            # Claim and advance atomically. Missed intervals collapse to one run, never a spending burst.
            with self.store.connect() as conn:
                changed = conn.execute("UPDATE schedules SET next_run_at=? WHERE id=? AND next_run_at=? AND enabled=1",
                                       (next_time, schedule["id"], schedule["next_run_at"])).rowcount
            if not changed:
                continue
            active = self.store.one("SELECT id FROM runs WHERE schedule_id=? AND status IN ('queued','running')",
                                    (schedule["id"],))
            if active:
                self.store.execute("UPDATE schedules SET last_error=? WHERE id=?",
                                   ("前一次任务仍在进行，本轮跳过。", schedule["id"]))
                continue
            try:
                run_id = self.create_run(RunInput.model_validate(spec["config"]), schedule["id"])
                self.store.execute("UPDATE schedules SET last_run_id=?,last_error=NULL WHERE id=?",
                                   (run_id, schedule["id"]))
            except (ValueError, TypeError):
                self.store.execute("UPDATE schedules SET last_error=? WHERE id=?",
                                   ("配置无效或队列已满，请检查中转站和任务设置。", schedule["id"]))
