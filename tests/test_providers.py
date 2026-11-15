import json

import httpx
import pytest

from approval_lab.benchmark import builtin_cases
from approval_lab.providers import ProviderClient, ProviderError
from approval_lab.schemas import RunInput


@pytest.mark.parametrize("protocol", ["chat", "responses", "anthropic"])
async def test_protocol_request_and_real_usage_extraction(protocol):
    captured = []

    async def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        text = '{"decision":"ask","reason":"Need authorization"}'
        if protocol == "chat":
            data = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
        elif protocol == "responses":
            data = {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": text}]}], "usage": {"input_tokens": 100, "output_tokens": 20}}
            assert body["store"] is False
        else:
            data = {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": 100, "output_tokens": 20}}
            assert request.headers["x-api-key"] == "unit-test-key"
        data.update({"model": "returned-model", "id": "response-1"})
        return httpx.Response(200, json=data, headers={"x-request-id": "req-123"})

    provider = {"base_url": "https://relay.example/v1", "protocol": protocol, "api_key": "unit-test-key"}
    async with ProviderClient(provider, transport=httpx.MockTransport(handler)) as client:
        result = await client.complete(RunInput(provider_id=1, model="test", group_name="manual"),
                                       "public policy", builtin_cases()[0])
    assert result.usage["input_tokens"] == 100
    assert result.request_id == "req-123"
    assert result.model == "returned-model"
    assert not result.incomplete
    assert len(captured) == 1
    assert "temperature" not in captured[0]


async def test_redirect_does_not_forward_key_and_no_automatic_retries():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://evil.example/collect"})

    provider = {"base_url": "https://relay.example/v1", "protocol": "chat", "api_key": "real-key"}
    async with ProviderClient(provider, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="重定向"):
            await client.list_models()
    assert len(requests) == 1


async def test_error_body_cannot_echo_a_key():
    provider = {"base_url": "https://relay.example/v1", "protocol": "chat", "api_key": "SECRET-unit-test"}
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="Bad key SECRET-unit-test"))
    async with ProviderClient(provider, transport=transport) as client:
        with pytest.raises(ProviderError) as error:
            await client.list_models()
    assert "SECRET-unit-test" not in str(error.value)


async def test_incomplete_json_never_counts_as_valid_allow():
    data = {"choices": [{"finish_reason": "length", "message": {
        "content": '{"decision":"allow","reason":"safe"}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 128}}
    provider = {"base_url": "https://relay.example/v1", "protocol": "chat", "api_key": "key"}
    async with ProviderClient(provider, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))) as client:
        result = await client.complete(RunInput(provider_id=1, model="test", group_name="manual"), "policy", builtin_cases()[0])
    assert result.incomplete
    assert result.usage["output_tokens"] == 128
