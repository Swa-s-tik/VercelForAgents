"""Statistical eval-gating - the corrected core of Vertical A.

WHY THIS EXISTS (and why the spec's literal rule is wrong)
----------------------------------------------------------
The product spec proposed: "block if win-rate vs main < 52% with a p-value > 0.05."
That rule is statistically incoherent:

  * ``p > 0.05`` means "NOT statistically significant" - i.e. *no evidence of a
    difference*. Conditioning a BLOCK on non-significance means you block precisely
    when you have the least evidence, and you let a regression through the moment it
    becomes noisy enough to clear p>0.05. Backwards.
  * A bare 52% threshold ignores sample size: 13/25 and 2600/5000 are both "52%" but
    represent completely different evidentiary states.
  * It conflates the effect threshold (a statement about the *true* rate) with the
    inference (a statement about a *test*).

THE CORRECTED GATE - a non-inferiority test driven by a confidence interval.
We compare a candidate (PR commit) against ``main`` on a *paired* binary preference
signal (same eval item, both arms -> WIN / LOSS / TIE for the candidate). We then ask:
"is the candidate's true win-rate credibly at/above a margin ``nim``?"

  * BLOCK   when the *entire* 95% CI lies below ``nim``  -> confident regression.
  * ALLOW   when the *entire* 95% CI lies at/above ``nim`` -> confident non-inferiority.
  * INCONCLUSIVE when the CI straddles ``nim`` (honest uncertainty).
  * INSUFFICIENT_DATA when n < n_min (don't act on a thin suite).

The decision is made by the *interval*. The McNemar p-value and the Bayesian posterior
are computed and REPORTED for context, but they do not independently gate (that would
re-introduce the dual-criterion incoherence above).

``nim`` (non-inferiority margin) defaults to 0.50 ("must not be worse than a coin-flip
vs main"). Setting ``nim = 0.52`` turns this into a *superiority* gate - the only
statistically sound reading of the spec's "52%".

Pure & dependency-light: scipy.stats only (norm, binomtest, beta). No statsmodels.
This module does NO I/O and is fully deterministic, so it is trivially unit-testable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from scipy.stats import beta, binomtest, norm

Decision = Literal["BLOCK", "ALLOW", "INCONCLUSIVE", "INSUFFICIENT_DATA"]
TieMode = Literal["exclude", "halve", "loss"]


@dataclass(frozen=True)
class GateConfig:
    """Per-suite gate configuration. Frozen for reproducibility (persisted in run config)."""

    alpha: float = 0.05            # 1 - alpha = CI level (0.05 -> 95% CI)
    nim: float = 0.50             # non-inferiority margin; 0.52 => superiority mode
    n_min: int = 100              # minimum effective sample size before the gate acts
    tie_mode: TieMode = "halve"   # how ties fold into the win-rate
    inconclusive_action: Literal["warn", "block"] = "warn"
    power: float = 0.80           # used only for the reported MDE


@dataclass(frozen=True)
class GateDecision:
    decision: Decision
    n: int                  # effective sample size driving the interval
    wins: int
    losses: int
    ties: int
    win_rate: float
    wilson_low: float
    wilson_high: float
    p_value: float          # exact McNemar (paired) - REPORTED, not gating
    bayes_p_better: float   # P(theta > nim | Beta posterior)
    bayes_cred_low: float
    bayes_cred_high: float
    mde: float              # minimum detectable effect (delta above 0.5) at this n
    margin: float           # the nim used
    reason: str

    @property
    def blocks_merge(self) -> bool:
        return self.decision == "BLOCK"


# --------------------------------------------------------------------------- #
# Primitive statistics (scipy-only, hand-rolled where scipy lacks a one-liner)
# --------------------------------------------------------------------------- #
def _effective_counts(wins: int, losses: int, ties: int, tie_mode: TieMode) -> tuple[float, int]:
    """Return (k_eff, n_eff): the effective success count and denominator.

    * exclude: drop ties entirely (analyse only decisive pairs).
    * halve  : a tie is half a win and half a loss (default; unbiased, keeps n stable).
    * loss   : a tie counts against the candidate (strict "must clearly improve").
    """
    if tie_mode == "exclude":
        return float(wins), wins + losses
    if tie_mode == "loss":
        return float(wins), wins + losses + ties
    # halve (default)
    return wins + 0.5 * ties, wins + losses + ties


def wilson_interval(k: float, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Correct near 0/1 and at small n (unlike the Wald/normal-approx interval).
    Accepts fractional ``k`` (from the 'halve' tie mode).
    """
    if n <= 0:
        return 0.0, 1.0
    p_hat = k / n
    z = norm.ppf(1.0 - alpha / 2.0)
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    return max(0.0, center - half), min(1.0, center + half)


def mcnemar_exact(wins: int, losses: int) -> float:
    """Exact McNemar two-sided p-value via the binomial on discordant pairs.

    In a paired preference test the discordant pairs are exactly the decisive ones:
    ``wins`` = candidate-preferred, ``losses`` = main-preferred (ties are concordant
    and carry no signal for McNemar). H0: P(win) = P(loss).
    """
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    return float(binomtest(wins, discordant, 0.5, alternative="two-sided").pvalue)


def beta_posterior(
    k_eff: float, n_eff: int, nim: float, alpha: float = 0.05
) -> tuple[float, float, float]:
    """Beta-Binomial posterior with a Jeffreys prior: theta ~ Beta(k+.5, n-k+.5).

    Returns (P(theta > nim), credible_low, credible_high).
    Degrades gracefully at small n, which is why we surface it for thin preview runs.
    """
    a = k_eff + 0.5
    b = (n_eff - k_eff) + 0.5
    p_better = float(1.0 - beta.cdf(nim, a, b))
    lo = float(beta.ppf(alpha / 2.0, a, b))
    hi = float(beta.ppf(1.0 - alpha / 2.0, a, b))
    return p_better, lo, hi


def detectable_effect(n: int, alpha: float = 0.05, power: float = 0.80) -> float:
    """Approx. minimum detectable effect (delta above 0.5) for a one-sided proportion
    test at this n. Lets reviewers see the gate's resolving power for the data collected.
    """
    if n <= 0:
        return 1.0
    za = norm.ppf(1.0 - alpha)   # one-sided (non-inferiority is one-sided)
    zb = norm.ppf(power)
    return (za + zb) * 0.5 / math.sqrt(n)   # sd ~ 0.5 near p=0.5


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def evaluate_gate(wins: int, losses: int, ties: int, cfg: GateConfig = GateConfig()) -> GateDecision:
    """Turn raw paired counts into a statistically sound BLOCK/ALLOW decision.

    Decision order (matches the approved design):
      1. INSUFFICIENT_DATA  if n_eff < n_min          (never act on a thin suite)
      2. BLOCK              if wilson_high < nim       (whole CI below margin)
      3. ALLOW              if wilson_low  >= nim       (whole CI at/above margin)
      4. INCONCLUSIVE       otherwise                  (CI straddles the margin)

    Note: INSUFFICIENT_DATA is checked first by design - we want at least ``n_min``
    samples before the gate produces an *actionable* verdict, even a damning one,
    because tiny eval suites are often unrepresentative. (An alternative ordering would
    let a catastrophic small-n result BLOCK early; we deliberately prefer "collect more".)
    """
    k_eff, n_eff = _effective_counts(wins, losses, ties, cfg.tie_mode)

    if n_eff <= 0:
        return GateDecision(
            decision="INSUFFICIENT_DATA", n=0, wins=wins, losses=losses, ties=ties,
            win_rate=float("nan"), wilson_low=0.0, wilson_high=1.0, p_value=1.0,
            bayes_p_better=float("nan"), bayes_cred_low=0.0, bayes_cred_high=1.0,
            mde=1.0, margin=cfg.nim, reason="no decisive samples",
        )

    win_rate = k_eff / n_eff
    lo, hi = wilson_interval(k_eff, n_eff, cfg.alpha)
    p_value = mcnemar_exact(wins, losses)
    p_better, cred_lo, cred_hi = beta_posterior(k_eff, n_eff, cfg.nim, cfg.alpha)
    mde = detectable_effect(n_eff, cfg.alpha, cfg.power)

    if n_eff < cfg.n_min:
        decision: Decision = "INSUFFICIENT_DATA"
        reason = (f"n={n_eff} < n_min={cfg.n_min}: collect more eval items "
                  f"(MDE at this n ≈ {mde:.3f}).")
    elif hi < cfg.nim:
        decision = "BLOCK"
        reason = (f"95% CI [{lo:.3f}, {hi:.3f}] entirely below margin {cfg.nim:.2f} "
                  f"-> confident regression.")
    elif lo >= cfg.nim:
        decision = "ALLOW"
        reason = (f"95% CI [{lo:.3f}, {hi:.3f}] entirely at/above margin {cfg.nim:.2f} "
                  f"-> confident non-inferiority.")
    else:
        if cfg.inconclusive_action == "block":
            decision = "BLOCK"
            reason = (f"95% CI [{lo:.3f}, {hi:.3f}] straddles margin {cfg.nim:.2f}; "
                      f"suite policy blocks on unproven non-inferiority.")
        else:
            decision = "INCONCLUSIVE"
            reason = (f"95% CI [{lo:.3f}, {hi:.3f}] straddles margin {cfg.nim:.2f} "
                      f"-> uncertain; not blocking (warn).")

    return GateDecision(
        decision=decision, n=n_eff, wins=wins, losses=losses, ties=ties,
        win_rate=win_rate, wilson_low=lo, wilson_high=hi, p_value=p_value,
        bayes_p_better=p_better, bayes_cred_low=cred_lo, bayes_cred_high=cred_hi,
        mde=mde, margin=cfg.nim, reason=reason,
    )


# --------------------------------------------------------------------------- #
# Multi-suite aggregation with Benjamini-Hochberg FDR control
# --------------------------------------------------------------------------- #
def bh_reject(pvalues: list[float], q: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg step-up. Returns a boolean per input p-value (in input order):
    True means "reject H0 at FDR <= q". Used to control false regressions across the
    many eval suites a single PR runs (correctness, safety, tone, latency, ...).
    """
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    rejected = [False] * m
    max_k = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= (rank / m) * q:
            max_k = rank
    if max_k >= 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= max_k:
                rejected[idx] = True
    return rejected


@dataclass(frozen=True)
class PRVerdict:
    decision: Literal["BLOCK", "ALLOW", "INCONCLUSIVE", "INSUFFICIENT_DATA"]
    blocking_suites: list[str]
    inconclusive_suites: list[str]
    insufficient_suites: list[str]
    bh_significant: dict[str, bool]   # suite -> is its difference discoverable under FDR
    reason: str

    @property
    def exit_code(self) -> int:
        return 1 if self.decision == "BLOCK" else 0


def aggregate_pr(
    suite_decisions: dict[str, GateDecision],
    gating_suites: set[str] | None = None,
    q: float = 0.05,
) -> PRVerdict:
    """Combine per-suite gate decisions into one PR-level verdict.

    * PR BLOCKs if any suite BLOCKs (CI-based, as designed).
    * BH-FDR across all suite McNemar p-values is computed and attached as context, so a
      reviewer sees which suite differences are real once multiplicity is accounted for
      (a single suite at alpha=0.05 spuriously "differs" ~1-in-20 times).
    * ALLOW only if every *gating* suite is ALLOW and nothing BLOCKs.
    """
    names = list(suite_decisions)
    bh = bh_reject([suite_decisions[n].p_value for n in names], q=q)
    bh_significant = {n: bh[i] for i, n in enumerate(names)}

    blocking = [n for n, d in suite_decisions.items() if d.decision == "BLOCK"]
    inconclusive = [n for n, d in suite_decisions.items() if d.decision == "INCONCLUSIVE"]
    insufficient = [n for n, d in suite_decisions.items() if d.decision == "INSUFFICIENT_DATA"]
    gating = gating_suites if gating_suites is not None else set(names)

    if blocking:
        decision = "BLOCK"
        reason = f"{len(blocking)} suite(s) regressed: {', '.join(blocking)}."
    elif any(suite_decisions[n].decision != "ALLOW" for n in gating if n in suite_decisions):
        not_allowed = [n for n in gating if n in suite_decisions and suite_decisions[n].decision != "ALLOW"]
        decision = "INCONCLUSIVE"
        reason = f"no regression, but gating suite(s) not proven non-inferior: {', '.join(not_allowed)}."
    else:
        decision = "ALLOW"
        reason = "all gating suites proven non-inferior; no regressions."

    return PRVerdict(
        decision=decision, blocking_suites=blocking, inconclusive_suites=inconclusive,
        insufficient_suites=insufficient, bh_significant=bh_significant, reason=reason,
    )
