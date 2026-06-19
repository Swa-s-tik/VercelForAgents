#!/usr/bin/env bash
# Dynamic eval-gate demo: a synthetic judge generates statistically realistic eval data,
# inserts it into the DuckDB OLAP store, and the real Wilson-CI + McNemar gate BLOCKs an
# inferior preview agent and ALLOWs a superior one. No hardcoded fixtures.
set -uo pipefail
cd "$(dirname "$0")/.."
DB=.agentctl/eval_gate.duckdb
rm -f "$DB"

echo "############################################################"
echo "#   Dynamic eval-gate: synthetic judge -> DuckDB -> gate    #"
echo "############################################################"

echo; echo ">>> Scenario 1: mathematically INFERIOR preview agent (true win-rate ~40%)"
python -m agentctl.eval.synthetic_judge --label "INFERIOR PR #200" \
  --p-win 0.36 --p-tie 0.08 --n 240 --suite correctness --commit infer200 --pr 200 --db "$DB"
rc_inferior=$?

echo; echo ">>> Scenario 2: mathematically SUPERIOR preview agent (true win-rate ~64%)"
python -m agentctl.eval.synthetic_judge --label "SUPERIOR PR #201" \
  --p-win 0.60 --p-tie 0.08 --n 240 --suite correctness --commit super201 --pr 201 --db "$DB"
rc_superior=$?

echo
echo "------------------------------------------------------------"
echo "inferior PR exit=$rc_inferior (1 = BLOCKED)   superior PR exit=$rc_superior (0 = ALLOWED)"
if [ "$rc_inferior" -eq 1 ] && [ "$rc_superior" -eq 0 ]; then
  echo "EVAL GATE WORKS ✔  - inferior PR BLOCKED, superior PR ALLOWED on dynamic data"
  exit 0
else
  echo "UNEXPECTED gate outcome"
  exit 1
fi
