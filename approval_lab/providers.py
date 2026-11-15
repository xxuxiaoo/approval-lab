import asyncio
import json
import os
import re
import time
from dataclasses import dataclass

import httpx

from .benchmark import case_payload
from .pricing import normalize_usage


def redact(text, secret=""):
    text = str(text)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    return text


class ProviderError(Exception):
    pass


@dataclass
class Completion:
    text: str
    usage: dict
    model: str | None
    response_id: str | None
    request_id: str | None
    latency_ms: float
    incomplete: bool = False


class ProviderClient:
    def __init__(self, provider, *, transport=None):
        self.provider = provider
        self.protocol = provider["protocol"]
        self.secret = provider["api_key"]
        self.base_url = provider["base_url"].rstrip("/")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.protocol == "anthropic":
            headers.update({"x-api-key": self.secret, "anthropic-version": "2023-06-01"})
        else:
            headers["Authorization"] = "Bearer " + self.secret
        self.client = httpx.AsyncClient(
            headers=headers, follow_redirects=False, trust_env=False, transport=transport,
            proxy=os.environ.get("APPROVAL_LAB_PROXY") or None,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.client.aclose()

    async def request(self, method, endpoint, *, timeout=30, body=None, params=None):
        try:
            async with asyncio.timeout(timeout):
                async with self.client.stream(
                    method, f"{self.base_url}/{endpoint}", json=body, params=params,
                    timeout=httpx.Timeout(timeout),
                ) as response:
                    chunks, length = [], 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > 4_000_000:
                            raise ProviderError("服务端响应超过 4 MB 限制；该请求可能已计费")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if response.is_redirect:
                        raise ProviderError("中转返回重定向，已阻止向其他地址转发 Key；请填写最终 API 地址")
                    if response.status_code >= 400:
                        # Do not persist arbitrary error bodies: a relay may echo request credentials.
                        hint = {
                            400: "请求参数或协议不兼容；检查协议、推理参数和输出上限",
                            401: "Key 无效或未获授权", 403: "分组或模型无权限", 404: "API 路径或模型不存在",
                            429: "限流或余额不足；未自动重试", 502: "中转上游异常", 503: "上游暂不可用",
                        }.get(response.status_code, "中转请求失败；请在中转后台核查")
                        raise ProviderError(f"HTTP {response.status_code}：{hint}（不保存响应错误正文）")
                    try:
                        data = json.loads(content)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise ProviderError("返回的不是 JSON；确认使用非流式兼容 API") from exc
                    if not isinstance(data, dict):
                        raise ProviderError("返回 JSON 格式不正确，预期为对象")
                    return data, response.headers.get("x-request-id") or response.headers.get("request-id")
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ProviderError(f"{type(exc).__name__}：连接失败或超时；请求可能已计费，未自动重试") from exc

    async def list_models(self):
        models, cursor, has_more = [], None, False
        for _ in range(10):
            params = {"after_id": cursor, "limit": 1000} if cursor else None
            data, _ = await self.request("GET", "models", params=params)
            entries = data.get("data")
            if not isinstance(entries, list):
                raise ProviderError("模型列表没有 data 数组；可在界面手动输入模型 ID")
            for item in entries:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append(item["id"][:300])
            has_more = data.get("has_more") is True
            next_cursor = data.get("last_id")
            if not has_more:
                break
            if not next_cursor or next_cursor == cursor:
                raise ProviderError("模型分页缺少有效 last_id；请手动输入模型 ID")
            cursor = next_cursor
        return {"models": sorted(set(models)), "truncated": has_more,
                "note": "列表仅代表该 Key 当前权限；切换分组后请重新拉取。返回模型 ID 不证明底层模型身份。"}

    async def complete(self, config, prompt, case):
        content = case_payload(case)
        if self.protocol == "chat":
            endpoint = "chat/completions"
            body = {"model": config.model, "messages": [{"role": "system", "content": prompt},
                    {"role": "user", "content": content}], config.chat_token_parameter: config.max_output_tokens,
                    "stream": False}
            if config.reasoning_effort:
                body["reasoning_effort"] = config.reasoning_effort
        elif self.protocol == "responses":
            endpoint = "responses"
            body = {"model": config.model, "instructions": prompt,
                    "input": [{"role": "user", "content": content}],
                    "max_output_tokens": config.max_output_tokens, "stream": False, "store": False}
            if config.reasoning_effort:
                body["reasoning"] = {"effort": config.reasoning_effort}
        else:
            endpoint = "messages"
            system = prompt
            if config.anthropic_cache:
                system = [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]
            body = {"model": config.model, "system": system, "max_tokens": config.max_output_tokens,
                    "messages": [{"role": "user", "content": content}], "stream": False}
        if config.temperature is not None:
            body["temperature"] = config.temperature
        started = time.perf_counter()
        data, request_id = await self.request("POST", endpoint, timeout=config.timeout_seconds, body=body)
        incomplete = False
        try:
            if self.protocol == "chat":
                choice = data["choices"][0]
                text = choice["message"].get("content") or ""
                if isinstance(text, list):
                    text = "".join(item.get("text", "") for item in text if isinstance(item, dict))
                incomplete = choice.get("finish_reason") not in (None, "stop")
            elif self.protocol == "responses":
                text = "".join(
                    part.get("text", "") for item in data.get("output", [])
                    if item.get("type") == "message" for part in item.get("content", [])
                    if part.get("type") == "output_text"
                )
                incomplete = data.get("status") not in (None, "completed")
            else:
                text = "".join(part.get("text", "") for part in data.get("content", [])
                               if part.get("type") == "text")
                incomplete = data.get("stop_reason") not in (None, "end_turn", "stop_sequence")
        except (KeyError, IndexError, TypeError, AttributeError):
            text, incomplete = "", True
        if not isinstance(text, str):
            text, incomplete = "", True
        return Completion(
            text=redact(text, self.secret), usage=normalize_usage(data.get("usage"), self.protocol),
            model=redact(str(data.get("model", "")), self.secret)[:300],
            response_id=redact(str(data.get("id", "")), self.secret)[:300],
            request_id=redact(request_id or "", self.secret)[:300],
            latency_ms=(time.perf_counter() - started) * 1000, incomplete=incomplete,
        )
