"""Normalize vendor token semantics before applying user-entered relay prices."""

from decimal import Decimal

PRICING_PRESETS = [
    {"id": "conservative", "name": "未知 / 保守按原价", "read": 1, "write": 1, "write_1h": 1,
     "note": "不知道中转站缓存规则时使用；没有缓存字段仍标记为未知，不假定命中。",
     "source": None},
    {"id": "gpt56", "name": "GPT-5.6+ · 默认 30 分钟", "read": 0.1, "write": 1.25, "write_1h": 2.5,
     "note": "官方默认 30 分钟写入 1.25×，12 小时 2.5×；如使用 12h，请把默认写入倍率改成 2.5。中转可能另计。",
     "source": "https://developers.openai.com/api/docs/guides/prompt-caching"},
    {"id": "gpt61", "name": "GPT-6.1 Sol / Terra", "read": 0.05, "write": 1.25, "write_1h": 2.5,
     "note": "官方缓存读取 0.05×；默认 30 分钟写入 1.25×。不根据中转模型别名自动套用。",
     "source": "https://developers.openai.com/api/docs/guides/prompt-caching"},
    {"id": "claude", "name": "Claude 标准 / Haiku 5.5", "read": 0.1, "write": 1.25, "write_1h": 2,
     "note": "官方标准读取 0.1×，5 分钟写入 1.25×，1 小时写入 2×。",
     "source": "https://platform.claude.com/docs/en/build-with-claude/prompt-caching"},
    {"id": "claude55", "name": "Claude Opus / Sonnet 5.5", "read": 0.05, "write": 1.25, "write_1h": 2,
     "note": "官方这两个系列读取 0.05×；其他系列不能直接套用。",
     "source": "https://platform.claude.com/docs/en/build-with-claude/prompt-caching"},
    {"id": "claude_fable", "name": "Claude Fable / Mythos 5.1–5.2", "read": 0.025, "write": 1.25, "write_1h": 2,
     "note": "官方上述系列读取 0.025×；中转计价请以账单为准。",
     "source": "https://platform.claude.com/docs/en/build-with-claude/prompt-caching"},
    {"id": "deepseek_flash", "name": "DeepSeek Flash · 当前价格表", "read": 0.02, "write": 1, "write_1h": 1,
     "note": "当前表 Flash 命中/未命中为 0.006/0.3（高峰），即 0.02×。旧版和其他型号请手填。",
     "source": "https://api-docs.deepseek.com/quick_start/pricing"},
    {"id": "deepseek_pro", "name": "DeepSeek Pro · 当前价格表", "read": 1 / 30, "write": 1, "write_1h": 1,
     "note": "当前表 Pro 命中/未命中为 0.044/1.32（高峰），约 0.03333×。型号/时段定价另行核实。",
     "source": "https://api-docs.deepseek.com/quick_start/pricing"},
]


def _number(mapping, key):
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
        return number if number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_usage(raw, protocol):
    raw = raw if isinstance(raw, dict) else {}
    warnings = []
    if protocol == "anthropic":
        normal = _number(raw, "input_tokens")
        cached = _number(raw, "cache_read_input_tokens")
        written = _number(raw, "cache_creation_input_tokens")
        creation = raw.get("cache_creation") or {}
        one_hour = _number(creation, "ephemeral_1h_input_tokens") or 0
        five_minute = _number(creation, "ephemeral_5m_input_tokens") or 0
        if written is None and (one_hour or five_minute):
            written = one_hour + five_minute
        total_input = None if normal is None else normal + (cached or 0) + (written or 0)
        output = _number(raw, "output_tokens")
        reasoning = None
    else:
        total_input = _number(raw, "prompt_tokens")
        if total_input is None:
            total_input = _number(raw, "input_tokens")
        output = _number(raw, "completion_tokens")
        if output is None:
            output = _number(raw, "output_tokens")
        details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
        cached = _number(details, "cached_tokens")
        if cached is None:
            cached = _number(raw, "prompt_cache_hit_tokens")
        written = _number(details, "cache_write_tokens")
        if written is None:
            written = _number(details, "cache_creation_tokens")
        if written is None:
            written = _number(raw, "cache_creation_input_tokens")
        one_hour = _number(details, "cache_write_1h_tokens") or 0
        output_details = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
        reasoning = _number(output_details, "reasoning_tokens")
    if total_input is not None and (cached or 0) + (written or 0) > total_input:
        warnings.append("缓存 token 超过总输入，服务端 usage 语义异常；金额仅为估算")
    if one_hour > (written or 0):
        warnings.append("长时缓存写入大于总缓存写入，服务端 usage 异常")
    if total_input is None or output is None:
        warnings.append("服务端未完整返回输入/输出用量，费用不完整")
    if cached is None:
        warnings.append("服务端未报告缓存读取量；未报告不等于未命中")
    if written is None:
        warnings.append("服务端未报告缓存写入量；无法确认写入附加费")
    return {
        "input_tokens": total_input or 0, "output_tokens": output or 0,
        "cache_read_tokens": cached or 0, "cache_write_tokens": written or 0,
        "cache_write_1h_tokens": min(one_hour, written or 0), "reasoning_tokens": reasoning or 0,
        "input_reported": total_input is not None, "output_reported": output is not None,
        "cache_read_reported": cached is not None, "cache_write_reported": written is not None,
        "warnings": warnings, "raw": raw,
    }


def estimate_cost(usage, pricing):
    def d(value):
        return Decimal(str(value))

    normal = max(0, usage.get("input_tokens", 0) - usage.get("cache_read_tokens", 0)
                 - usage.get("cache_write_tokens", 0))
    one_hour = min(usage.get("cache_write_1h_tokens", 0), usage.get("cache_write_tokens", 0))
    regular_write = usage.get("cache_write_tokens", 0) - one_hour
    input_price = d(pricing["input_per_million"])
    components = {
        "uncached_input": d(normal) * input_price,
        "cache_read": d(usage.get("cache_read_tokens", 0)) * input_price * d(pricing["cache_read_multiplier"]),
        "cache_write": d(regular_write) * input_price * d(pricing["cache_write_multiplier"])
        + d(one_hour) * input_price * d(pricing["cache_write_1h_multiplier"]),
        "output": d(usage.get("output_tokens", 0)) * d(pricing["output_per_million"]),
    }
    factor = d(pricing["group_multiplier"]) / d(1_000_000)
    values = {key: float(value * factor) for key, value in components.items()}
    total = sum(components.values()) * factor
    return {**values, "total": float(total), "converted_total": float(total * d(pricing["exchange_rate"])),
            "currency": pricing["currency"], "converted_currency": "CNY" if pricing["exchange_rate"] != 1
            else pricing["currency"]}
