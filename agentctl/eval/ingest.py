"""Ingest paired candidate-vs-baseline eval records into the DuckDB store.

Fixture format (JSONL, one object per line):
    {"item_id": "q001", "suite": "correctness", "score": 0.81}
optionally with an explicit "preference" (WIN/LOSS/TIE) to bypass the judge, and an
optional "confidence". Candidate and baseline files are matched on (suite, item_id);
one eval_run is created PER suite.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentctl.eval.judge import Judge, ScoreJudge
from agentctl.storage.duckdb_store import EvalStore, Sample


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def ingest_paired(
    store: EvalStore,
    *,
    candidate_path: str,
    baseline_path: str,
    commit_sha: str,
    baseline_sha: str,
    pr_number: int | None = None,
    deployment_id: str | None = None,
    judge: Judge | None = None,
) -> dict[str, str]:
    """Ingest one candidate/baseline pair, creating one eval_run per suite.

    Returns ``{suite_name: run_id}``.
    """
    judge = judge or ScoreJudge()
    cand_rows = _read_jsonl(candidate_path)
    base_rows = _read_jsonl(baseline_path)
    base_by_key = {(r.get("suite", "default"), r["item_id"]): r for r in base_rows}
    suites = sorted({r.get("suite", "default") for r in cand_rows})

    out: dict[str, str] = {}
    for suite in suites:
        prefix = f"pr{pr_number}-" if pr_number is not None else ""
        run_id = f"{prefix}{commit_sha[:8]}-{suite}"
        store.create_run(
            run_id=run_id, commit_sha=commit_sha, baseline_sha=baseline_sha,
            suite_name=suite, pr_number=pr_number, deployment_id=deployment_id,
            judge_name=type(judge).__name__,
        )
        samples: list[Sample] = []
        for cr in cand_rows:
            if cr.get("suite", "default") != suite:
                continue
            br = base_by_key.get((suite, cr["item_id"]))
            if br is None:
                continue  # unpaired candidate item -> skip (pairing is enforced here)
            pref = cr.get("preference") or judge.judge(cr["item_id"], cr["score"], br["score"])
            samples.append(Sample(
                item_id=cr["item_id"], preference=pref,
                cand_score=cr.get("score"), base_score=br.get("score"),
                judge_confidence=cr.get("confidence"),
            ))
        n = store.record_samples(run_id, samples)
        store.finish_run(run_id)
        out[suite] = run_id
        if not samples:
            # surface an empty/unpaired suite instead of silently producing 0 rows
            print(f"  [warn] suite '{suite}': 0 paired samples ingested")
        else:
            print(f"  ingested suite '{suite}': {n} paired samples -> run {run_id}")
    return out
