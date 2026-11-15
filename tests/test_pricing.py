import pytest

from approval_lab.pricing import estimate_cost, normalize_usage
from approval_lab.schemas import Pricing


def test_openai_cached_input_is_not_charged_twice():
    usage = normalize_usage({"prompt_tokens": 1000, "completion_tokens": 200,
                             "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
                             "completion_tokens_details": {"reasoning_tokens": 150}}, "chat")
    pricing = Pricing(cache_read_multiplier=0.1, cache_write_multiplier=1.25).model_dump()
    result = estimate_cost(usage, pricing)
    expected = (300 * 2 + 600 * 2 * 0.1 + 100 * 2 * 1.25 + 200 * 12) / 1e6 * 0.15
    assert result["total"] == pytest.approx(expected)
    assert usage["reasoning_tokens"] == 150


def test_anthropic_adds_cache_to_input_and_uses_one_hour_rate():
    usage = normalize_usage({"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 900,
                             "cache_creation_input_tokens": 300,
                             "cache_creation": {"ephemeral_5m_input_tokens": 100,
                                                "ephemeral_1h_input_tokens": 200}}, "anthropic")
    assert usage["input_tokens"] == 1300
    pricing = Pricing(input_per_million=3, output_per_million=15, group_multiplier=1,
                      cache_read_multiplier=0.1, cache_write_multiplier=1.25,
                      cache_write_1h_multiplier=2).model_dump()
    expected = (100 * 3 + 900 * 3 * 0.1 + 100 * 3 * 1.25 + 200 * 3 * 2 + 50 * 15) / 1e6
    assert estimate_cost(usage, pricing)["total"] == pytest.approx(expected)


def test_responses_and_deepseek_fields():
    response = normalize_usage({"input_tokens": 1000, "output_tokens": 400,
                                "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 200}},
                               "responses")
    assert response["cache_write_tokens"] == 200
    deepseek = normalize_usage({"prompt_tokens": 500, "completion_tokens": 10,
                                "prompt_cache_hit_tokens": 400}, "chat")
    assert deepseek["cache_read_tokens"] == 400


def test_missing_usage_is_not_invented_and_unknown_cache_is_distinct_from_zero():
    missing = normalize_usage(None, "chat")
    assert not missing["input_reported"]
    assert not missing["cache_read_reported"]
    zero = normalize_usage({"input_tokens": 0, "output_tokens": 0,
                            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}, "responses")
    assert zero["input_reported"] and zero["cache_read_reported"] and zero["cache_write_reported"]
    assert zero["warnings"] == []


def test_anomalous_cache_counts_produce_warning_not_negative_cost():
    usage = normalize_usage({"input_tokens": 100, "output_tokens": 10,
                             "input_tokens_details": {"cached_tokens": 500}}, "responses")
    assert any("异常" in message for message in usage["warnings"])
    assert estimate_cost(usage, Pricing().model_dump())["total"] >= 0
