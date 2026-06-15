#!/usr/bin/env bash
# Phase 10: the complete operational sequence across all verticals, end to end.
set -uo pipefail
cd "$(dirname "$0")/.."
export GRPC_VERBOSITY=NONE

echo "############################################################"
echo "#        agentctl — complete end-to-end pipeline           #"
echo "############################################################"
python demo/complete_pipeline.py
rc=$?
echo
[ "$rc" -eq 0 ] && echo "PIPELINE GREEN ✔" || echo "PIPELINE FAILED (rc=$rc)"
exit $rc
