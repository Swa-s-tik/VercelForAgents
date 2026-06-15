"""Deterministically generate paired eval fixtures for the Vertical A demo.

Each fixture line: {"item_id", "suite", "score"}. Candidate & baseline are paired on
(suite, item_id); the ScoreJudge derives WIN/LOSS/TIE from the scores. Seeded with
numpy for full reproducibility (no Date/random nondeterminism).

Outputs (in demo/fixtures/):
  main.jsonl              baseline (3 suites)            shared by good & regression
  candidate.jsonl         all suites non-inferior   -> per-suite ALLOW, PR ALLOW
  candidate_regression.jsonl  correctness regressed -> correctness BLOCK, PR BLOCK
  main_small.jsonl / candidate_small.jsonl   n=30    -> INSUFFICIENT_DATA
  main_borderline.jsonl / candidate_borderline.jsonl -> INCONCLUSIVE
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)

SUITES = {"correctness": 240, "safety": 200, "tone": 160}

# (p_win, p_tie) per suite. Remaining mass -> LOSS.
GOOD = {"correctness": (0.60, 0.05), "safety": (0.60, 0.05), "tone": (0.62, 0.04)}
REGRESSION = {"correctness": (0.38, 0.06), "safety": (0.60, 0.05), "tone": (0.62, 0.04)}


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def base_scores(seed: int, suites: dict[str, int]) -> dict[tuple[str, str], float]:
    rng = np.random.default_rng(seed)
    items: dict[tuple[str, str], float] = {}
    for suite, n in suites.items():
        for i in range(n):
            items[(suite, f"{suite}-{i:04d}")] = round(float(rng.uniform(0.40, 0.90)), 4)
    return items


def candidate_from(base: dict[tuple[str, str], float], targets: dict, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for (suite, item), b in base.items():
        p_win, p_tie = targets[suite]
        gap = float(rng.uniform(0.02, 0.15))
        u = float(rng.uniform(0.0, 1.0))
        if u < p_win:
            c = min(1.0, b + gap)
        elif u < p_win + p_tie:
            c = b                       # exact tie -> ScoreJudge returns TIE
        else:
            c = max(0.0, b - gap)
        rows.append({"item_id": item, "suite": suite, "score": round(c, 4)})
    return rows


def main_rows(base: dict[tuple[str, str], float]) -> list[dict]:
    return [{"item_id": item, "suite": suite, "score": b} for (suite, item), b in base.items()]


def exact_candidate(base: dict[tuple[str, str], float],
                    counts: dict[str, tuple[int, int, int]], gap: float = 0.05) -> list[dict]:
    """Assign WIN/LOSS/TIE by position to hit EXACT counts (no RNG variance).
    Used for the borderline fixture so the demo reliably lands an INCONCLUSIVE verdict.
    """
    per_suite: dict[str, list[tuple[str, float]]] = {}
    for (suite, item), b in base.items():
        per_suite.setdefault(suite, []).append((item, b))
    rows = []
    for suite, items in per_suite.items():
        w, l, _t = counts[suite]
        for idx, (item, b) in enumerate(items):
            if idx < w:
                c = min(1.0, b + gap)            # WIN
            elif idx < w + l:
                c = max(0.0, b - gap)            # LOSS
            else:
                c = b                            # TIE
            rows.append({"item_id": item, "suite": suite, "score": round(c, 4)})
    return rows


def build() -> None:
    base = base_scores(seed=42, suites=SUITES)
    _write(OUT / "main.jsonl", main_rows(base))
    _write(OUT / "candidate.jsonl", candidate_from(base, GOOD, seed=7))
    _write(OUT / "candidate_regression.jsonl", candidate_from(base, REGRESSION, seed=7))

    small_suites = {"correctness": 30}
    sbase = base_scores(seed=99, suites=small_suites)
    _write(OUT / "main_small.jsonl", main_rows(sbase))
    _write(OUT / "candidate_small.jsonl", candidate_from(sbase, {"correctness": (0.55, 0.05)}, seed=3))

    bbase = base_scores(seed=123, suites={"correctness": 240})
    _write(OUT / "main_borderline.jsonl", main_rows(bbase))
    # exact 120/116/4 -> win_rate=(120+2)/240=0.508, 95% CI straddles 0.50 -> INCONCLUSIVE
    _write(OUT / "candidate_borderline.jsonl", exact_candidate(bbase, {"correctness": (120, 116, 4)}))

    print(f"fixtures written to {OUT}")
    for p in sorted(OUT.glob("*.jsonl")):
        print(f"  {p.name:30s} {sum(1 for _ in p.open())} lines")


if __name__ == "__main__":
    build()
