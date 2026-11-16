"""Shared immutable case format, including fields needed to read older reports."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from .schemas import StrictModel


class Case(StrictModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    category: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    command: str = Field(min_length=1, max_length=30000)
    user_request: str = Field(min_length=1, max_length=12000)
    evidence: str = Field(default="", max_length=50000)
    shell: Literal["bash", "powershell", "cmd", "tool"] = "bash"
    expected: Literal["allow", "ask", "deny"]
    severity: Literal["low", "medium", "high", "critical"] = "high"
    rationale: str = Field(min_length=1, max_length=4000)
    light: bool = False


def corpus_hash(cases):
    return hashlib.sha256(json.dumps([c.model_dump() for c in cases], ensure_ascii=False,
                                    sort_keys=True).encode()).hexdigest()
