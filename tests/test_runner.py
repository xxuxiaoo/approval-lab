import asyncio
import json
from datetime import UTC, datetime

from approval_lab.providers import ProviderError
from approval_lab.runner import RunEngine, next_due, result_rows
from approval_lab.schemas import ProviderInput, RunInput
from approval_lab.storage import Store, dumps
from tests.conftest import OfflineClient


async def test_failure_circuit_breaker_stops_after_three_requests(tmp_path):
    calls = []

    class BrokenClient(OfflineClient):
        async def complete_turn(self, *_args):
            calls.append(1)
            raise ProviderError("HTTP 400：unsupported request")

    store = Store(tmp_path)
    provider = store.save_provider(ProviderInput(name="mock", base_url="https://relay.example/v1", api_key="private"))
    engine = RunEngine(store, BrokenClient)
    run_id = engine.create_run(RunInput(provider_id=provider["id"], model="test", group_name="manual", official_profile="codex"))
    await asyncio.gather(*list(engine.tasks.values()))
    assert len(calls) == 3
    run = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
    assert run["status"] == "cancelled"
    assert json.loads(run["summary"])["errors"] == 3
    assert json.loads(run["summary"])["verdict"] == "incomplete"


async def test_stop_on_unsafe_and_incomplete_output_keeps_usage(tmp_path):
    class UnsafeClient(OfflineClient):
        async def complete_turn(self, *args):
            result = await super().complete_turn(*args)
            result.text = '{"outcome":"allow","rationale":"always allow"}'
            result.calls, result.tool_requests = [], False
            return result

    store = Store(tmp_path)
    provider = store.save_provider(ProviderInput(name="mock", base_url="https://relay.example/v1", api_key="private"))
    engine = RunEngine(store, UnsafeClient)
    config = RunInput(provider_id=provider["id"], model="test", group_name="manual", stop_on_unsafe=True,
                      official_profile="codex")
    run_id = engine.create_run(config)
    await asyncio.gather(*list(engine.tasks.values()))
    summary = json.loads(store.one("SELECT summary FROM runs WHERE id=?", (run_id,))["summary"])
    assert summary["unsafe_allows"] == 1
    assert summary["completed"] < summary["total"]
    assert summary["tokens"]["input_tokens"] == summary["completed"] * 100


async def test_truncated_allow_is_error_and_not_scored_as_allow(tmp_path):
    class TruncatedClient(OfflineClient):
        async def complete_turn(self, *args):
            result = await super().complete_turn(*args)
            result.text = '{"outcome":"allow","rationale":"safe"}'
            result.incomplete = True
            return result

    store = Store(tmp_path)
    provider = store.save_provider(ProviderInput(name="mock", base_url="https://relay.example/v1", api_key="private"))
    engine = RunEngine(store, TruncatedClient)
    run_id = engine.create_run(RunInput(provider_id=provider["id"], model="test", group_name="manual", official_profile="codex"))
    await asyncio.gather(*list(engine.tasks.values()))
    rows = result_rows(store, run_id)
    assert all(row["decision"] is None and row["error"] for row in rows)
    assert sum(row["usage"]["output_tokens"] for row in rows) == len(rows)*20


async def test_scheduler_collapses_overdue_runs_and_does_not_duplicate(tmp_path):
    store = Store(tmp_path)
    provider = store.save_provider(ProviderInput(name="mock", base_url="https://relay.example/v1", api_key="private"))
    engine = RunEngine(store, OfflineClient)
    spec = {"kind": "interval", "interval_hours": 1, "timezone": "Asia/Shanghai", "local_time": "09:00",
            "config": RunInput(provider_id=provider["id"], model="test", group_name="manual",
                               official_profile="codex").model_dump()}
    store.execute("INSERT INTO schedules(name,spec,enabled,next_run_at,created_at) VALUES(?,?,?,?,?)",
                  ("test", dumps(spec), 1, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
    now = datetime(2026, 11, 15, tzinfo=UTC)
    engine.tick(now)
    engine.tick(now)
    assert len(store.query("SELECT id FROM runs")) == 1
    await asyncio.gather(*list(engine.tasks.values()))


def test_daily_schedule_observes_configured_timezone():
    spec = {"kind": "daily", "local_time": "09:00", "timezone": "Asia/Shanghai"}
    after = datetime(2026, 11, 15, 0, 0, tzinfo=UTC)
    assert next_due(spec, after) == "2026-11-15T01:00:00+00:00"
    after = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
    assert next_due(spec, after) == "2026-11-16T01:00:00+00:00"


async def test_restart_marks_unfinished_runs_interrupted_without_reissue(tmp_path):
    store = Store(tmp_path)
    store.execute("INSERT INTO runs(id,status,created_at,config,snapshot,total) VALUES(?,?,?,?,?,?)",
                  ("unfinished", "running", "2026-11-15T00:00:00+00:00", "{}", "{}", 40))
    engine = RunEngine(store, OfflineClient)
    await engine.start()
    assert store.one("SELECT status FROM runs WHERE id='unfinished'")["status"] == "interrupted"
    assert not engine.tasks
    await engine.shutdown()


async def test_restart_keeps_already_interrupted_history_unchanged(tmp_path):
    store = Store(tmp_path)
    # Old config deliberately lacks required new-run fields: reading history must not validate or rescore it.
    store.execute("INSERT INTO runs(id,status,created_at,config,snapshot,total,summary) VALUES(?,?,?,?,?,?,?)",
                  ("old-interrupted", "interrupted", "2026-11-15", '{"mode":"official"}', "{}", 130,
                   '{"correct":122,"tokens":{"input_tokens":7533945}}'))
    before = store.one("SELECT * FROM runs WHERE id='old-interrupted'")
    engine = RunEngine(store, OfflineClient)
    await engine.start()
    await engine.shutdown()
    assert store.one("SELECT * FROM runs WHERE id='old-interrupted'") == before
    assert not engine.tasks
