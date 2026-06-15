"""Preference judges (Vertical A).

A judge turns a (candidate_output, baseline_output) pair on the same eval item into a
paired preference WIN / LOSS / TIE (candidate's point of view). The gate consumes only
the aggregate counts, so the judge is a clean, swappable seam.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

Preference = Literal["WIN", "LOSS", "TIE"]


@runtime_checkable
class Judge(Protocol):
    def judge(self, item_id: str, candidate, baseline) -> Preference: ...


class ScoreJudge:
    """Deterministic prototype judge: compare numeric scores with a dead-band epsilon.

    Used for the fixtures (which carry per-arm scores). Deterministic => reproducible eval.
    """

    def __init__(self, epsilon: float = 1e-9):
        self.epsilon = epsilon

    def judge(self, item_id: str, candidate, baseline) -> Preference:
        c, b = float(candidate), float(baseline)
        if c > b + self.epsilon:
            return "WIN"
        if c < b - self.epsilon:
            return "LOSS"
        return "TIE"


class LLMJudge:
    """STUB seam for an LLM-as-judge. Intentionally not implemented in the prototype.

    Production checklist (documented here so the seam is honest):
      * pin the judge model + version -> store in eval_run.judge_version for reproducibility;
      * cancel position bias by double-judging with the two arms swapped and averaging;
      * treat low self-reported confidence as TIE (and persist judge_confidence);
      * keep temperature=0 for the judge to reduce run-to-run noise.
    """

    def __init__(self, model: str = "claude-opus-4-8", version: str | None = None):
        self.model = model
        self.version = version

    def judge(self, item_id: str, candidate, baseline) -> Preference:
        raise NotImplementedError(
            "LLMJudge is a stub. Wire a real LLM-as-judge here (see class docstring).")
