"""Unit tests for the eval-gating statistics. Runs under pytest OR as a plain script
(`python tests/test_gate.py`) so it needs no extra dependency.

Assertions are pinned against hand-computed scipy values so a regression in the math
is caught immediately.
"""
from __future__ import annotations

import math

from agentctl.eval.gate import (
    GateConfig,
    aggregate_pr,
    beta_posterior,
    bh_reject,
    detectable_effect,
    evaluate_gate,
    mcnemar_exact,
    wilson_interval,
)


def approx(a: float, b: float, tol: float = 2e-3) -> bool:
    return abs(a - b) <= tol


def test_wilson_known_value():
    lo, hi = wilson_interval(136, 240, 0.05)   # 128 wins + 8 (halved ties)
    assert approx(lo, 0.5034), lo
    assert approx(hi, 0.6278), hi


def test_wilson_edges():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(0, 50)
    assert lo == 0.0 and hi < 0.1


def test_mcnemar_known_value():
    assert approx(mcnemar_exact(30, 18), 0.1115, tol=5e-3)
    assert mcnemar_exact(0, 0) == 1.0
    assert approx(mcnemar_exact(50, 50), 1.0, tol=1e-9)  # perfectly balanced


def test_beta_posterior_known_value():
    p_better, lo, hi = beta_posterior(60, 100, 0.5, 0.05)
    assert p_better > 0.97, p_better          # 60/40 strongly favours candidate
    assert lo < 0.6 < hi


def test_gate_allow():
    d = evaluate_gate(128, 96, 16)            # halve -> k=136 n=240
    assert d.decision == "ALLOW", d
    assert approx(d.win_rate, 0.56667, tol=1e-3)
    assert d.wilson_low >= 0.50


def test_gate_block():
    d = evaluate_gate(96, 128, 16)            # mirror -> confident regression
    assert d.decision == "BLOCK", d
    assert d.wilson_high < 0.50


def test_gate_insufficient_data():
    d = evaluate_gate(13, 12, 5)              # n=30 < n_min=100
    assert d.decision == "INSUFFICIENT_DATA", d


def test_gate_inconclusive():
    d = evaluate_gate(124, 116, 0)            # ~0.517, CI straddles 0.50
    assert d.decision == "INCONCLUSIVE", d
    assert d.wilson_low < 0.50 < d.wilson_high


def test_superiority_mode():
    # nim=0.52 => must prove > 52%. A strong 2:1 candidate clears it.
    d = evaluate_gate(300, 150, 0, GateConfig(nim=0.52))
    assert d.decision == "ALLOW", d
    assert d.wilson_low >= 0.52


def test_tie_mode_flips_decision():
    # Same raw counts, opposite verdict under different tie policy.
    allow = evaluate_gate(60, 40, 100, GateConfig(tie_mode="exclude"))
    block = evaluate_gate(60, 40, 100, GateConfig(tie_mode="loss"))
    assert allow.decision == "ALLOW", allow
    assert block.decision == "BLOCK", block


def test_inconclusive_action_block():
    d = evaluate_gate(124, 116, 0, GateConfig(inconclusive_action="block"))
    assert d.decision == "BLOCK", d


def test_bh_reject():
    rejected = bh_reject([0.001, 0.2, 0.03, 0.04], q=0.05)
    assert rejected == [True, False, False, False], rejected
    assert bh_reject([]) == []


def test_detectable_effect_shrinks_with_n():
    assert detectable_effect(100) > detectable_effect(1000)
    assert detectable_effect(0) == 1.0


def test_aggregate_pr():
    allow = evaluate_gate(128, 96, 16)
    block = evaluate_gate(96, 128, 16)
    v_block, _ = aggregate_pr({"correctness": block, "safety": allow}), None
    v = aggregate_pr({"correctness": block, "safety": allow})
    assert v.decision == "BLOCK"
    assert "correctness" in v.blocking_suites
    assert v.exit_code == 1
    v_ok = aggregate_pr({"correctness": allow, "safety": allow})
    assert v_ok.decision == "ALLOW"
    assert v_ok.exit_code == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} gate tests passed")
