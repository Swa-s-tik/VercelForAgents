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
    """Real LLM-as-judge over the Anthropic API (Claude). Optional dependency:
    ``pip install 'agentctl[judge]'`` (the ``anthropic`` SDK) + an ``ANTHROPIC_API_KEY``.

    Design (the checklist this seam always promised, now implemented):
      * pinned judge model + version -> persisted to eval_run.judge_version for reproducibility;
      * position-bias cancelled by double-judging with the two arms swapped (a judge that always
        prefers "A" nets out to TIE, so a real preference must survive the swap);
      * low self-reported confidence folds to TIE (don't let a coin-flip count as a decisive sample);
      * no temperature / sampling params (removed on Opus 4.x) - determinism comes from the prompt
        and the swap, not a temperature knob.

    The judge is a clean swap-in for ScoreJudge: same ``judge(item_id, candidate, baseline)`` ->
    WIN/LOSS/TIE contract. ``candidate``/``baseline`` are the two responses (str), or dicts carrying
    an ``output`` (and optional shared ``prompt``).
    """

    _SYSTEM = (
        "You are a strict, impartial evaluator comparing two AI assistant responses to the same "
        "customer request. Judge by correctness, helpfulness, and tone; penalize hallucination and "
        "unsafe or unauthorized actions. Do not favor length or position. Output only your verdict."
    )

    def __init__(self, model: str = "claude-opus-4-8", version: str | None = None,
                 min_confidence: float = 0.6, max_tokens: int = 512, client=None):
        self.model = model
        self.version = version or model
        self.min_confidence = min_confidence
        self.max_tokens = max_tokens
        self._client = client          # inject for testing; otherwise lazily constructed

    # -- client ---------------------------------------------------------------
    def _client_or_make(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:                       # pragma: no cover - env-dependent
                raise RuntimeError(
                    "LLMJudge needs the Anthropic SDK: pip install 'agentctl[judge]'") from e
            self._client = anthropic.Anthropic()           # reads ANTHROPIC_API_KEY
        return self._client

    # -- one directed comparison ("A" vs "B") ---------------------------------
    @staticmethod
    def _text_of(arm) -> str:
        return str(arm.get("output", arm)) if isinstance(arm, dict) else str(arm)

    @staticmethod
    def _parse(text: str) -> tuple[str, float]:
        """Lenient parse of the model's verdict (robust across SDK/model versions): pull the first
        JSON object if present, else scan for an A/B/TIE keyword. Returns (winner, confidence)."""
        import json
        import re
        winner, conf = "TIE", 0.0
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                winner = str(d.get("winner", "TIE")).strip().upper()
                conf = float(d.get("confidence", 0.0))
            except Exception:
                pass
        if winner not in ("A", "B", "TIE"):
            up = text.strip().upper()
            winner = "A" if up.startswith("A") else "B" if up.startswith("B") else "TIE"
        return winner, max(0.0, min(1.0, conf))

    def _compare(self, client, item_id: str, a_text: str, b_text: str) -> tuple[str, float]:
        user = (
            f"Two responses to the same request (eval item: {item_id}).\n\n"
            f"--- Response A ---\n{a_text}\n\n--- Response B ---\n{b_text}\n\n"
            "Which response better helps the customer? Reply with ONLY a JSON object: "
            '{"winner": "A" | "B" | "TIE", "confidence": <0.0-1.0>}.'
        )
        msg = client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=self._SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), "")
        return self._parse(text)

    def judge(self, item_id: str, candidate, baseline) -> Preference:
        client = self._client_or_make()
        cand, base = self._text_of(candidate), self._text_of(baseline)

        # Pass 1: A=candidate, B=baseline.  Pass 2: A=baseline, B=candidate (swap to cancel bias).
        w1, c1 = self._compare(client, item_id, cand, base)
        w2, c2 = self._compare(client, item_id, base, cand)

        votes = 0  # >0 favours candidate, <0 favours baseline
        if c1 >= self.min_confidence:
            votes += 1 if w1 == "A" else -1 if w1 == "B" else 0
        if c2 >= self.min_confidence:
            votes += 1 if w2 == "B" else -1 if w2 == "A" else 0  # A is the baseline in pass 2

        return "WIN" if votes > 0 else "LOSS" if votes < 0 else "TIE"
