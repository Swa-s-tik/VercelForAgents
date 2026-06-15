#!/usr/bin/env bash
# End-to-end demo of all three agentctl verticals. Assumes `pip install -e .`, a running
# Postgres (deploy/docker-compose.yml), and compiled protos in agentctl/gen/.
set -euo pipefail
cd "$(dirname "$0")/.."
export GRPC_VERBOSITY=NONE
DB=.agentctl/run_all.duckdb

echo "############################################################"
echo "#                  agentctl — full demo                    #"
echo "############################################################"

echo; echo "===== 0. generate eval fixtures ====="
python demo/make_fixtures.py

echo; echo "===== 1. Vertical A — eval-gating ====="
rm -f "$DB"
echo "--- good candidate, PR 100 (expect ALLOW) ---"
agentctl eval ingest --run demo/fixtures/candidate.jsonl --baseline demo/fixtures/main.jsonl \
  --commit good1234 --pr 100 --db "$DB" >/dev/null
if agentctl gate --pr 100 --db "$DB"; then echo ">> PR 100 ALLOWED (exit 0)"; fi
echo "--- regression candidate, PR 101 (expect BLOCK) ---"
agentctl eval ingest --run demo/fixtures/candidate_regression.jsonl --baseline demo/fixtures/main.jsonl \
  --commit regr5678 --pr 101 --db "$DB" >/dev/null
if agentctl gate --pr 101 --db "$DB"; then echo ">> PR 101 ALLOWED"; else echo ">> PR 101 BLOCKED the merge (exit 1)"; fi

echo; echo "===== 2. Vertical C — stateful rollback ====="
agentctl rollback schema
agentctl rollback seed
agentctl rollback run aaaa1111aaaa
agentctl rollback audit

echo; echo "===== 3. Vertical B — gRPC gateway (canary / shadow / interrupt) ====="
python demo/gateway_demo.py

echo; echo "===== 4. Vertical B — WebSocket edge interrupt ====="
python demo/ws_demo.py

echo; echo "############################################################"
echo "#                 all demos complete ✔                      #"
echo "############################################################"
