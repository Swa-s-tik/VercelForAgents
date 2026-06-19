"""Sequential evaluation engine (Phase 4) - stop early, save compute.

Fixed-horizon gating (gate.py) waits for all N eval iterations. For expensive agent evals
(1000+ prompts, each an LLM call), that is wasteful when the verdict is obvious after 60.
This module adds two sequential methods that emit BLOCK/ALLOW the moment significance is
crossed, with controlled error rates valid under continuous monitoring:

  * SPRT (Wald's Sequential Probability Ratio Test): tests H0: p=p0 vs H1: p=p1 on the paired
    win/loss stream. Minimizes expected sample size for given (alpha, beta).
  * Anytime-valid confidence sequence: a Hoeffding time-uniform CI (valid at every n
    simultaneously, via a telescoping union bound) on the decisive win-rate; stop when the CI
    clears the non-inferiority margin. No peeking penalty.

Ties are non-informative for the win/loss comparison and are skipped (decisive-pair analysis),
consistent with the paired McNemar treatment in gate.py. gate.py is unchanged - this is purely
additive.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal

SeqDecision = Literal["BLOCK", "ALLOW", "CONTINUE"]


@dataclass(frozen=True)
class SequentialResult:
    decision: Literal["BLOCK", "ALLOW", "INCONCLUSIVE"]
    method: str
    n_used: int           # samples consumed before stopping (or the horizon)
    n_total: int          # samples available
    decisive: int         # non-tie samples seen
    wins: int
    losses: int
    ties: int
    statistic: float      # final LLR (sprt) or CI half-width (anytime)
    low: float            # CI bounds (anytime) / NaN for sprt
    high: float
    reason: str

    @property
    def compute_saved_pct(self) -> float:
        return 100.0 * (1.0 - self.n_used / self.n_total) if self.n_total else 0.0

    @property
    def blocks_merge(self) -> bool:
        return self.decision == "BLOCK"


# --------------------------------------------------------------------------- #
# SPRT (Wald)
# --------------------------------------------------------------------------- #
def sprt_evaluate(prefs: Iterable[str], *, p0: float = 0.45, p1: float = 0.55,
                  alpha: float = 0.05, beta: float = 0.05) -> SequentialResult:
    """Wald SPRT over a stream of 'WIN'/'LOSS'/'TIE'. p0 = inferior bound, p1 = superior bound
    (indifference region brackets the 0.50 margin). Accept H1 -> ALLOW, accept H0 -> BLOCK."""
    upper = math.log((1 - beta) / alpha)     # cross -> accept H1 (ALLOW)
    lower = math.log(beta / (1 - alpha))     # cross -> accept H0 (BLOCK)
    lr_win = math.log(p1 / p0)
    lr_loss = math.log((1 - p1) / (1 - p0))

    llr = 0.0
    wins = losses = ties = n = decisive = 0
    decision: SeqDecision = "CONTINUE"
    for pref in prefs:
        n += 1
        if pref == "TIE":
            ties += 1
            continue
        decisive += 1
        if pref == "WIN":
            wins += 1
            llr += lr_win
        else:
            losses += 1
            llr += lr_loss
        if llr >= upper:
            decision = "ALLOW"
            break
        if llr <= lower:
            decision = "BLOCK"
            break

    final = "INCONCLUSIVE" if decision == "CONTINUE" else decision
    reason = ({
        "ALLOW": f"LLR {llr:.2f} >= {upper:.2f} after {decisive} decisive pairs -> superior.",
        "BLOCK": f"LLR {llr:.2f} <= {lower:.2f} after {decisive} decisive pairs -> inferior.",
        "INCONCLUSIVE": f"LLR {llr:.2f} stayed within ({lower:.2f}, {upper:.2f}); no decision by horizon.",
    })[final]
    return SequentialResult(final, "sprt", n, n, decisive, wins, losses, ties,
                            llr, float("nan"), float("nan"), reason)


def sprt_stream(prefs: Iterable[str], *, p0: float = 0.45, p1: float = 0.55,
                alpha: float = 0.05, beta: float = 0.05, nim: float = 0.50):
    """Generator for LIVE display: yield an incremental SPRT + running Wilson CI state after
    EVERY sample. The last yielded dict carries the terminal ``decision``
    (ALLOW / BLOCK / INCONCLUSIVE). Used by the CLI to animate the gate in real time."""
    from agentctl.eval.gate import wilson_interval

    upper = math.log((1 - beta) / alpha)
    lower = math.log(beta / (1 - alpha))
    lr_win = math.log(p1 / p0)
    lr_loss = math.log((1 - p1) / (1 - p0))

    llr = 0.0
    wins = losses = ties = n = decisive = 0
    lo, hi, k = 0.0, 1.0, 0.0
    for pref in prefs:
        n += 1
        if pref == "WIN":
            wins += 1; decisive += 1; llr += lr_win
        elif pref == "LOSS":
            losses += 1; decisive += 1; llr += lr_loss
        else:
            ties += 1
        k = wins + 0.5 * ties
        lo, hi = wilson_interval(k, n, alpha)
        decision = "CONTINUE"
        if decisive:
            if llr >= upper:
                decision = "ALLOW"
            elif llr <= lower:
                decision = "BLOCK"
        yield {"i": n, "n": n, "wins": wins, "losses": losses, "ties": ties,
               "decisive": decisive, "win_rate": (k / n if n else 0.0),
               "wilson_low": lo, "wilson_high": hi, "llr": llr,
               "upper": upper, "lower": lower, "decision": decision, "margin": nim}
        if decision != "CONTINUE":
            return
    # horizon exhausted without an SPRT decision -> fall back to Wilson vs the margin
    final = "ALLOW" if lo >= nim else ("BLOCK" if hi < nim else "INCONCLUSIVE")
    yield {"i": n, "n": n, "wins": wins, "losses": losses, "ties": ties,
           "decisive": decisive, "win_rate": (k / n if n else 0.0),
           "wilson_low": lo, "wilson_high": hi, "llr": llr,
           "upper": upper, "lower": lower, "decision": final, "margin": nim}


# --------------------------------------------------------------------------- #
# Anytime-valid confidence sequence (Hoeffding, time-uniform)
# --------------------------------------------------------------------------- #
def _anytime_radius(n: int, alpha: float) -> float:
    # time-uniform Hoeffding bound: sum_n alpha/(n(n+1)) = alpha, so each n spends alpha/(n(n+1)).
    return math.sqrt(math.log(n * (n + 1) / alpha) / (2 * n))


def anytime_evaluate(prefs: Iterable[str], *, nim: float = 0.50,
                     alpha: float = 0.05) -> SequentialResult:
    """Stop as soon as a time-uniform CI on the decisive win-rate clears the margin `nim`."""
    wins = losses = ties = n = decisive = 0
    low, high, half = 0.0, 1.0, 1.0
    decision = "CONTINUE"
    for pref in prefs:
        n += 1
        if pref == "TIE":
            ties += 1
            continue
        decisive += 1
        wins += 1 if pref == "WIN" else 0
        losses += 1 if pref == "LOSS" else 0
        mean = wins / decisive
        half = _anytime_radius(decisive, alpha)
        low, high = max(0.0, mean - half), min(1.0, mean + half)
        if low >= nim:
            decision = "ALLOW"
            break
        if high < nim:
            decision = "BLOCK"
            break

    final = "INCONCLUSIVE" if decision == "CONTINUE" else decision
    reason = ({
        "ALLOW": f"anytime CI [{low:.3f},{high:.3f}] cleared margin {nim:.2f} at n={decisive}.",
        "BLOCK": f"anytime CI [{low:.3f},{high:.3f}] below margin {nim:.2f} at n={decisive}.",
        "INCONCLUSIVE": f"anytime CI [{low:.3f},{high:.3f}] never cleared {nim:.2f} by horizon.",
    })[final]
    return SequentialResult(final, "anytime", n, n, decisive, wins, losses, ties,
                            half, low, high, reason)


def sequential_evaluate(prefs, method: str = "sprt", **kw) -> SequentialResult:
    prefs = list(prefs)
    res = sprt_evaluate(prefs, **{k: v for k, v in kw.items() if k in ("p0", "p1", "alpha", "beta")}) \
        if method == "sprt" else \
        anytime_evaluate(prefs, **{k: v for k, v in kw.items() if k in ("nim", "alpha")})
    # n_total reflects the full horizon, n_used reflects where we stopped.
    return SequentialResult(res.decision, res.method, res.n_used, len(prefs), res.decisive,
                            res.wins, res.losses, res.ties, res.statistic, res.low, res.high,
                            res.reason)


def main(argv=None) -> int:
    import argparse

    from agentctl.eval.synthetic_judge import SyntheticJudge

    ap = argparse.ArgumentParser(description="sequential (SPRT / anytime) early-stop eval")
    ap.add_argument("--p-win", type=float, required=True, dest="p_win")
    ap.add_argument("--p-tie", type=float, default=0.08, dest="p_tie")
    ap.add_argument("--n", type=int, default=1000, help="horizon (full suite size)")
    ap.add_argument("--method", choices=["sprt", "anytime", "both"], default="both")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)

    prefs = SyntheticJudge(args.p_win, args.p_tie, args.seed).judge_suite(args.n)
    if args.label:
        print(f"### {args.label}  (true p_win={args.p_win}, horizon={args.n})")
    methods = ["sprt", "anytime"] if args.method == "both" else [args.method]
    rc = 0
    for m in methods:
        r = sequential_evaluate(prefs, method=m, nim=0.50)
        print(f"  [{m:7s}] {r.decision:12s} at n_used={r.n_used:<4d} "
              f"(/{r.n_total})  saved {r.compute_saved_pct:.0f}% compute - {r.reason}")
        if r.blocks_merge:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
