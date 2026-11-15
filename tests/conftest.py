import json

import pytest
from fastapi.testclient import TestClient

from approval_lab.app import create_app
from approval_lab.pricing import normalize_usage
from approval_lab.providers import Completion


class OfflineClient:
    """Deterministic software fixture, NOT a real model capability evaluation."""
    def __init__(self, provider):
        self.provider = provider

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def list_models(self):
        return {"models": ["offline-fixture"], "truncated": False}

    async def complete(self, config, prompt, case):
        return Completion(
            text=json.dumps({"decision": case.expected, "reason": "offline software fixture"}),
            usage=normalize_usage({"prompt_tokens": 100, "completion_tokens": 20,
                                   "prompt_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 10}}, "chat"),
            model="offline-fixture", response_id="fixture-id", request_id="fixture-request", latency_ms=10,
        )


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, client_factory=OfflineClient, scheduler=False)
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-Approval-Lab": "1"}) as test_client:
        yield test_client


@pytest.fixture
def provider(client):
    result = client.post("/api/providers", json={"name": "Test relay", "base_url": "https://relay.example/v1",
                                               "api_key": "sk-test-private-key-123", "protocol": "chat"})
    assert result.status_code == 201
    return result.json()
