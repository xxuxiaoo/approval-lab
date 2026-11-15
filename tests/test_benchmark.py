import json

import pytest

from approval_lab.benchmark import (
    builtin_cases,
    case_payload,
    corpus_hash,
    metadata,
    parse_decision,
    select_cases,
)


def test_corpus_coverage_and_labels_not_leaked():
    full, light = builtin_cases(), select_cases("light")
    assert len(full) == 120
    assert len(light) == 40
    assert len({c.id for c in full}) == 120
    assert len({c.category for c in light}) == 20
    assert {c.category for c in light} == {c.category for c in full}
    for case in full:
        payload = json.loads(case_payload(case))
        assert set(payload) == {"trusted_user_request", "proposed_tool_call", "environment", "evidence"}
        assert payload["proposed_tool_call"]["command"] == case.command
    assert metadata()["hash"] == corpus_hash(full)


@pytest.mark.parametrize("text", [
    'allow', 'I would allow this.', '{"decision":"allow"}',
    '{"decision":"allow","reason":"ok"} trailing',
    '{"decision":"ALLOW","reason":"ok"}',
    '[{"decision":"allow","reason":"ok"}]',
    '{"decision":"allow","decision":"deny","reason":"ok"}',
    '{"decision":"allow","reason":{}}',
    '{"decision":"allow","reason":"   "}',
])
def test_parser_never_guesses_or_accepts_ambiguous_decision(text):
    with pytest.raises((ValueError, TypeError)):
        parse_decision(text)


def test_parser_accepts_single_json_fence():
    assert parse_decision('```json\n{"decision":"ask","reason":"Need scope"}\n```') == ("ask", "Need scope")


def test_duplicate_custom_ids_are_rejected():
    case = builtin_cases()[0].model_dump()
    with pytest.raises(ValueError, match="重复"):
        select_cases("custom", [case, case])
