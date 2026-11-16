import asyncio
import json
import time

from fastapi.testclient import TestClient

from approval_lab.app import create_app
from tests.conftest import OfflineClient


def wait_done(client, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.01)
    raise AssertionError("Offline fixture run did not finish")


def test_complete_offline_lifecycle_price_revision_and_exports(client, provider):
    assert "api_key" not in provider
    assert "encrypted_key" not in provider
    model_list = client.post(f"/api/providers/{provider['id']}/models")
    assert model_list.json()["models"] == ["offline-fixture"]
    config = {"provider_id": provider["id"], "model": "offline-fixture", "group_name": "hand-entered",
              "mode": "official", "official_profile": "codex", "official_scope": "full",
              "official_case_ids": ["official.codex.hidden-script"]}
    created = client.post("/api/runs", json=config)
    assert created.status_code == 201
    run_id = created.json()["id"]
    run = wait_done(client, run_id)
    assert run["status"] == "completed"
    assert run["summary"]["accuracy"] == 1
    assert run["summary"]["unsafe_allows"] == 0
    assert len(run["results"]) == 1
    assert client.get("/api/overview").json() == {"total_runs": 1, "unsafe_allows": 0}
    assert run["summary"]["tokens"]["input_tokens"] == 200
    assert run["snapshot"]["group_verified"] is False
    assert "sk-test-private-key-123" not in json.dumps(run)
    pricing = run["config"]["pricing"]
    previous = run["summary"]["cost"]["total"]
    pricing["group_multiplier"] *= 2
    revised = client.put(f"/api/runs/{run_id}/pricing", json=pricing)
    assert revised.status_code == 200
    assert revised.json()["summary"]["cost"]["total"] == previous * 2
    assert len(revised.json()["snapshot"]["pricing_revisions"]) == 1
    assert client.get(f"/api/runs/{run_id}/export").json()["snapshot"]["initial_pricing"]["group_multiplier"] == 0.15
    csv = client.get(f"/api/runs/{run_id}/export?format=csv")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert len(csv.text.splitlines()) == 2


def test_csrf_origin_and_host_guards(client):
    assert client.get("/api/providers", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/providers", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/providers", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/providers", headers={"X-Approval-Lab": ""}, json={}).status_code == 403
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["Content-Security-Policy"]


def test_secrets_encrypted_and_endpoint_changes_require_reentering_key(client, provider):
    store = client.app.state.store
    raw = store.one("SELECT encrypted_key FROM providers WHERE id=?", (provider["id"],))["encrypted_key"]
    assert raw != "sk-test-private-key-123"
    assert store.decrypt(raw) == "sk-test-private-key-123"
    update = {"name": "Test", "base_url": "https://other.example/v1", "api_key": None, "protocol": "chat"}
    assert client.put(f"/api/providers/{provider['id']}", json=update).status_code == 400
    assert client.app.state.store.provider(provider["id"])["base_url"] == "https://relay.example/v1"
    bad = client.post("/api/providers", json={"name": "Test", "base_url": "file:///private", "api_key": "sk-secret-should-not-echo"})
    assert bad.status_code == 422
    assert "sk-secret-should-not-echo" not in bad.text


def test_request_budget_prevents_any_work(client, provider):
    body = {"provider_id": provider["id"], "model": "test", "group_name": "manual", "mode": "official",
            "official_profile": "codex", "official_scope": "full", "request_budget": 1}
    result = client.post("/api/runs", json=body)
    assert result.status_code == 400
    assert client.get("/api/runs").json() == []


def test_schedule_requires_group_acknowledgement_and_survives_key_delete(client, provider):
    spec = {"name": "regression", "config": {"provider_id": provider["id"], "model": "test", "group_name": "manual",
                                           "official_profile": "codex"}}
    assert client.post("/api/schedules", json=spec).status_code == 400
    spec["group_acknowledged"] = True
    result = client.post("/api/schedules", json=spec)
    assert result.status_code == 201
    assert client.get("/api/schedules").json()[0]["enabled"]
    assert client.delete(f"/api/providers/{provider['id']}").status_code == 200
    assert not client.get("/api/schedules").json()[0]["enabled"]


def test_retired_modes_cannot_create_paid_work(client, provider):
    for mode in ("light", "full", "custom"):
        body = {"provider_id": provider["id"], "model": "test", "group_name": "manual", "mode": mode}
        assert client.post("/api/runs", json=body).status_code == 400
    assert client.get("/api/cases").status_code == 410
    assert client.get("/api/runs").json() == []


def test_deleting_provider_stops_subsequent_paid_requests(tmp_path):
    class SlowClient(OfflineClient):
        async def complete_turn(self, *args):
            await asyncio.sleep(0.05)
            return await super().complete_turn(*args)

    app = create_app(tmp_path, client_factory=SlowClient, scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-Approval-Lab": "1"}) as client:
        provider = client.post("/api/providers", json={"name": "test", "base_url": "https://relay.example/v1",
                                                      "api_key": "private"}).json()
        run_id = client.post("/api/runs", json={"provider_id": provider["id"], "model": "test",
                                                "group_name": "manual", "official_profile": "codex"}).json()["id"]
        assert client.delete(f"/api/providers/{provider['id']}").status_code == 200
        run = wait_done(client, run_id)
        assert run["status"] == "cancelled"
        assert run["completed"] < run["total"]
