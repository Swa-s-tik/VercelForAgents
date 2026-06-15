"""EvalStore — the local DuckDB OLAP store for eval traces (Vertical A).

This is the storage seam. ``record_samples`` / ``fetch_aggregate`` are the only two
methods the gate path depends on, so swapping DuckDB for ClickHouse later touches
nothing in ``eval/gate.py`` or ``eval/runner.py``.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import duckdb

from agentctl.eval.gate import GateDecision

DEFAULT_DB = os.environ.get("AGENTCTL_DUCKDB", ".agentctl/eval.duckdb")
_SCHEMA = Path(__file__).with_name("schema_duckdb.sql")


@dataclass
class Sample:
    item_id: str
    preference: str                      # WIN | LOSS | TIE
    cand_score: float | None = None
    base_score: float | None = None
    judge_confidence: float | None = None
    judge_raw: dict | None = None


def connect(db_path: str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path)
    con.execute(_SCHEMA.read_text())
    return con


class EvalStore:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    @classmethod
    def open(cls, db_path: str = DEFAULT_DB) -> "EvalStore":
        return cls(connect(db_path))

    # ---- runs -------------------------------------------------------------
    def create_run(self, *, run_id: str, commit_sha: str, baseline_sha: str,
                   suite_name: str, deployment_id: str | None = None,
                   pr_number: int | None = None, judge_name: str | None = None,
                   judge_version: str | None = None, config: dict | None = None) -> str:
        self.con.execute("DELETE FROM eval_run WHERE run_id = ?", [run_id])
        self.con.execute(
            """INSERT INTO eval_run
               (run_id, deployment_id, commit_sha, baseline_sha, pr_number, suite_name,
                judge_name, judge_version, started_at, finished_at, config_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [run_id, deployment_id, commit_sha, baseline_sha, pr_number, suite_name,
             judge_name, judge_version, dt.datetime.now(), None, json.dumps(config or {})],
        )
        return run_id

    def finish_run(self, run_id: str) -> None:
        self.con.execute("UPDATE eval_run SET finished_at = ? WHERE run_id = ?",
                         [dt.datetime.now(), run_id])

    # ---- samples ----------------------------------------------------------
    def record_samples(self, run_id: str, samples: Iterable[Sample]) -> int:
        self.con.execute("DELETE FROM eval_sample WHERE run_id = ?", [run_id])
        rows = []
        for i, s in enumerate(samples):
            rows.append([f"{run_id}:{s.item_id}:{i}", run_id, s.item_id, s.preference,
                         s.cand_score, s.base_score, s.judge_confidence,
                         json.dumps(s.judge_raw or {}), dt.datetime.now()])
        if rows:
            self.con.executemany(
                "INSERT INTO eval_sample VALUES (?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def fetch_aggregate(self, run_id: str) -> tuple[int, int, int]:
        """The OLAP rollup the gate consumes: -> (wins, losses, ties)."""
        res = dict(self.con.execute(
            "SELECT preference, COUNT(*) FROM eval_sample WHERE run_id = ? GROUP BY preference",
            [run_id]).fetchall())
        return int(res.get("WIN", 0)), int(res.get("LOSS", 0)), int(res.get("TIE", 0))

    # ---- gate verdict cache ----------------------------------------------
    def save_gate_result(self, run_id: str, d: GateDecision) -> None:
        self.con.execute("DELETE FROM gate_result WHERE run_id = ?", [run_id])
        self.con.execute(
            "INSERT INTO gate_result VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, d.decision, d.n, d.wins, d.losses, d.ties, d.win_rate,
             d.wilson_low, d.wilson_high, d.p_value, d.bayes_p_better, d.margin,
             dt.datetime.now()])

    # ---- queries ----------------------------------------------------------
    def runs_for_pr(self, pr_number: int) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.con.execute(
            "SELECT run_id, suite_name FROM eval_run WHERE pr_number = ? ORDER BY suite_name",
            [pr_number]).fetchall()]

    def run_meta(self, run_id: str) -> dict | None:
        row = self.con.execute(
            "SELECT run_id, commit_sha, baseline_sha, suite_name, pr_number "
            "FROM eval_run WHERE run_id = ?", [run_id]).fetchone()
        if not row:
            return None
        return dict(zip(["run_id", "commit_sha", "baseline_sha", "suite_name", "pr_number"], row))

    def close(self) -> None:
        self.con.close()
