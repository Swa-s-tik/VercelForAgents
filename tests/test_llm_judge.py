"""LLMJudge logic (position-bias + confidence) with an injected fake client, and a check that the
gate runs on a cached fixture of REAL (LLM-judged) preferences - non-synthetic data through the gate.

Pure: no network, no Anthropic SDK, no DuckDB. The fake client stands in for the API so the
double-judge / swap / confidence logic is verified deterministically.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from agentctl.eval.gate import GateConfig, evaluate_gate
from agentctl.eval.judge import LLMJudge


# --- fake Anthropic client -------------------------------------------------- #
class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Msg:
    def __init__(self, text: str):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, verdicts):
        self._q = list(verdicts)            # list of (winner, confidence), one per .create()

    def create(self, **_kwargs):
        winner, conf = self._q.pop(0)
        return _Msg(json.dumps({"winner": winner, "confidence": conf}))


class FakeClient:
    def __init__(self, verdicts):
        self.messages = _Messages(verdicts)


def _judge(verdicts) -> str:
    # candidate="C", baseline="B"; judge() issues two .create() calls (orientation A,B then B,A).
    j = LLMJudge(client=FakeClient(verdicts), min_confidence=0.6)
    return j.judge("item", "candidate text", "baseline text")


def test_win_when_candidate_favored_both_orientations():
    # pass1 picks A (=candidate); pass2 picks B (=candidate after swap) -> WIN
    assert _judge([("A", 0.9), ("B", 0.9)]) == "WIN"


def test_loss_when_baseline_favored_both_orientations():
    assert _judge([("B", 0.9), ("A", 0.9)]) == "LOSS"


def test_position_biased_judge_nets_to_tie():
    # A judge that ALWAYS says "A" must net to TIE once arms are swapped (bias cancelled).
    assert _judge([("A", 0.9), ("A", 0.9)]) == "TIE"


def test_low_confidence_folds_to_tie():
    # Below min_confidence on both passes -> no decisive votes -> TIE.
    assert _judge([("A", 0.3), ("B", 0.3)]) == "TIE"


def test_dict_arms_are_supported():
    # candidate/baseline may carry an {"output": ...} payload, not just a bare string.
    j = LLMJudge(client=FakeClient([("A", 0.9), ("B", 0.9)]), min_confidence=0.6)
    assert j.judge("i", {"output": "good reply"}, {"output": "weak reply"}) == "WIN"


# --- real (cached) judgments flow through the gate -------------------------- #
def test_real_judgment_fixture_allows():
    path = Path(__file__).parent / "fixtures" / "eval_real_judgments.jsonl"
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = Counter(r["preference"] for r in recs)

    d = evaluate_gate(counts["WIN"], counts["LOSS"], counts["TIE"], GateConfig(n_min=20))

    assert d.n == len(recs)
    assert d.decision == "ALLOW", (d.decision, round(d.wilson_low, 3), d.n)
    assert d.wilson_low >= 0.50
