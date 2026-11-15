from approval_lab.benchmark import builtin_cases
from approval_lab.metrics import summarize
from approval_lab.pricing import normalize_usage
from approval_lab.schemas import Pricing


def row(case, decision, error=None):
    return {"case": case.model_dump(), "decision": decision, "error": error, "latency_ms": 100,
            "usage": normalize_usage({"prompt_tokens": 100, "completion_tokens": 20}, "chat")}


def test_always_allow_cannot_score_as_safe_and_errors_arent_passes():
    safe, dangerous = builtin_cases()[:2]
    rows = [row(safe, "allow"), row(dangerous, "allow"), row(dangerous, None, "malformed JSON")]
    summary = summarize(rows, 4, Pricing().model_dump())
    assert summary["unsafe_allows"] == 1
    assert summary["errors"] == 1
    assert summary["correct"] == 1
    assert summary["accuracy"] == 0.25
    assert summary["verdict"] == "observed_unsafe"
    assert summary["cost_is_partial"]


def test_repetitions_do_not_inflate_distinct_risky_sample_count():
    dangerous = builtin_cases()[1]
    summary = summarize([row(dangerous, "deny") for _ in range(5)], 5, Pricing().model_dump(), repeats=5)
    assert summary["unique_risky_complete"] == 1
    assert abs(summary["zero_miss_upper_95"] - 0.95) < 1e-10
    assert summary["verdict"] == "no_unsafe_observed"


def test_always_deny_shows_false_blocks():
    rows = [row(c, "deny") for c in builtin_cases()[:2]]
    summary = summarize(rows, 2, Pricing().model_dump())
    assert summary["unsafe_allows"] == 0
    assert summary["false_blocks"] == 1
    assert summary["false_block_rate"] == 1
