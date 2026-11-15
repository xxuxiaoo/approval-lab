from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ProviderInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=2048)
    protocol: Literal["chat", "responses", "anthropic"] = "chat"
    notes: str = Field(default="", max_length=2000)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError("请输入 http(s) API 根地址，例如 https://relay.example/v1")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("地址中不能包含账号、密码、查询参数或片段")
        try:
            _port = parsed.port
        except ValueError as exc:
            raise ValueError("端口无效") from exc
        if any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("地址不能包含空白字符")
        if parsed.path.rstrip("/").endswith(("/chat/completions", "/responses", "/messages", "/models")):
            raise ValueError("请填写 API 根地址（通常以 /v1 结尾），不要附带具体接口路径")
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value and any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError("Key 必须为可打印的 ASCII 字符")
        return value


class Pricing(StrictModel):
    input_per_million: float = Field(default=2, ge=0, le=1_000_000)
    output_per_million: float = Field(default=12, ge=0, le=1_000_000)
    group_multiplier: float = Field(default=0.15, ge=0, le=1000)
    cache_read_multiplier: float = Field(default=1, ge=0, le=100)
    cache_write_multiplier: float = Field(default=1, ge=0, le=100)
    cache_write_1h_multiplier: float = Field(default=1, ge=0, le=100)
    currency: Literal["USD", "CNY", "CREDIT"] = "USD"
    exchange_rate: float = Field(default=1, gt=0, le=10000)
    cache_preset: str = Field(default="conservative", max_length=100)
    notes: str = Field(default="", max_length=2000)


class RunInput(StrictModel):
    provider_id: int = Field(gt=0)
    model: str = Field(min_length=1, max_length=200)
    group_name: str = Field(min_length=1, max_length=100)
    mode: Literal["light", "full", "custom", "native", "official"] = "light"
    official_profile: Literal["both", "codex", "claude_code"] = "both"
    official_scope: Literal["light", "full"] = "light"
    official_case_ids: list[str] | None = Field(default=None, max_length=500)
    max_review_rounds: int = Field(default=6, ge=1, le=12)
    max_evidence_calls: int = Field(default=12, ge=0, le=30)
    cost_stop: float | None = Field(default=None, gt=0, le=10000)
    native_case_ids: list[str] | None = Field(default=None, max_length=500)
    native_scope: Literal["light", "full"] = "light"
    native_origin: Literal["all", "recorded", "stress"] = "all"
    repeats: int = Field(default=1, ge=1, le=10)
    concurrency: int = Field(default=1, ge=1, le=5)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    chat_token_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    timeout_seconds: int = Field(default=90, ge=10, le=600)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    harness: Literal["generic", "codex", "claude_code"] = "generic"
    system_prompt: str | None = Field(default=None, max_length=60000)
    pricing: Pricing = Field(default_factory=Pricing)
    notes: str = Field(default="", max_length=4000)
    custom_cases: list[dict] | None = Field(default=None, max_length=500)
    request_budget: int = Field(default=500, ge=1, le=5000)
    stop_on_unsafe: bool = False
    anthropic_cache: bool = False


class ScheduleInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    config: RunInput
    kind: Literal["interval", "daily"] = "daily"
    interval_hours: float = Field(default=24, ge=0.25, le=8760)
    local_time: str = "09:00"
    timezone: str = "Asia/Shanghai"
    enabled: bool = True
    group_acknowledged: bool = False

    @field_validator("local_time")
    @classmethod
    def valid_time(cls, value):
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("时间格式为 HH:MM") from exc
        return parsed.strftime("%H:%M")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("未知时区") from exc
        return value
