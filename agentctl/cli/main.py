"""`agentctl push` — the developer-facing command (typer + rich).

Abstracts the whole backend into one command: package the agent at PATH, provision an
isolated preview through the webhook emulator (Phase 2), stream the SPRT sequential eval live
(Phase 4), persist to DuckDB, and report PR MERGED (live URL) or PR BLOCKED.

The typer ``app`` here is the production-grade entry point; the argparse `agentctl push`
(agentctl/cli/__init__.py) delegates to ``run_push`` so both surfaces share one implementation.
"""
from __future__ import annotations

import hashlib
import io
import tarfile
import time
from pathlib import Path

import typer
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from agentctl.config import DEMO_PROJECT_ID, DUCKDB_PATH

console = Console()
app = typer.Typer(add_completion=False, help="Vercel for Agents — ship agents with one command.")

DEFAULT_GOOD_WIN = 0.62
REGRESSION_WIN = 0.40
_STATUS_COLOR = {"ALLOW": "green", "BLOCK": "red", "CONTINUE": "cyan", "INCONCLUSIVE": "yellow"}


# --------------------------------------------------------------------------- #
# prompt.yaml (tiny, dependency-free parser for the shapes we control)
# --------------------------------------------------------------------------- #
def _coerce(v: str):
    v = v.strip().strip("'\"")
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def _parse_yaml(text: str) -> dict:
    data: dict = {}
    cur = data
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, val = line.strip().partition(":")
        if indent == 0:
            if val.strip() == "":
                cur = data.setdefault(key, {})
            else:
                data[key] = _coerce(val)
                cur = data
        else:
            cur[key] = _coerce(val)
    return data


def _load_agent(path: Path) -> dict:
    meta = {"name": path.resolve().name, "model": "claude-opus-4-8",
            "eval": {"suite": "eval", "samples": 300, "win_rate": DEFAULT_GOOD_WIN, "tie_rate": 0.08}}
    pj = path / "prompt.yaml"
    if pj.exists():
        parsed = _parse_yaml(pj.read_text())
        meta.update({k: v for k, v in parsed.items() if k != "eval"})
        meta["eval"].update(parsed.get("eval", {}))
    return meta


# --------------------------------------------------------------------------- #
# 1. pack
# --------------------------------------------------------------------------- #
def _pack(path: Path):
    files = sorted(p for p in path.rglob("*") if p.is_file() and ".agentctl" not in p.parts
                   and "__pycache__" not in p.parts)
    h = hashlib.sha256()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in files:
            data = f.read_bytes()
            h.update(f.name.encode()); h.update(data)
            info = tarfile.TarInfo(name=str(f.relative_to(path)))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    sha = h.hexdigest()[:12]
    rel = [str(f.relative_to(path)) for f in files]
    return rel, sha, buf.getbuffer().nbytes


# --------------------------------------------------------------------------- #
# 2. preview (webhook + isolated runtime)
# --------------------------------------------------------------------------- #
def _ensure_pg():
    from agentctl.common.db import apply_schema, connect
    import agentctl.rollback as rb
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('controlplane.deployments') AS t")
        if cur.fetchone()["t"] is None:
            apply_schema(conn, str(Path(rb.__file__).with_name("schema_postgres.sql")))
    return conn


# --------------------------------------------------------------------------- #
# 3. live eval rendering
# --------------------------------------------------------------------------- #
def _ci_bar(lo: float, hi: float, margin: float = 0.50, width: int = 46) -> Text:
    cells = []
    for i in range(width):
        x = i / (width - 1)
        if abs(x - margin) <= 0.5 / (width - 1):
            cells.append("[bold white]┃[/]")
        elif lo <= x <= hi:
            cells.append("[green]█[/]" if x >= margin else "[red]█[/]")
        else:
            cells.append("[grey37]─[/]")
    return Text.from_markup(f"   0.0 {''.join(cells)} 1.0   ┃ = margin {margin:.2f}")


def _eval_panel(name: str, s: dict, progress: Progress) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, expand=False, show_edge=False)
    t.add_column("metric", style="dim"); t.add_column("value", justify="right")
    t.add_row("samples", f"{s['n']}")
    t.add_row("win / loss / tie",
              f"[green]{s['wins']}[/] / [red]{s['losses']}[/] / [yellow]{s['ties']}[/]")
    t.add_row("win-rate", f"{s['win_rate']:.3f}")
    t.add_row("Wilson 95% CI", f"[{s['wilson_low']:.3f}, {s['wilson_high']:.3f}]")
    t.add_row("SPRT log-LR", f"{s['llr']:+.2f}  (block ≤ {s['lower']:.2f} · allow ≥ {s['upper']:.2f})")
    dec = s["decision"]
    t.add_row("status", f"[{_STATUS_COLOR[dec]}]●  {dec}[/]")
    body = Group(t, Text(""), _ci_bar(s["wilson_low"], s["wilson_high"], s["margin"]), Text(""), progress)
    return Panel(body, title=f"[bold]Sequential eval-gate[/] · {name}",
                 subtitle="SPRT + Wilson CI · live", box=box.ROUNDED, border_style="cyan")


def _run_live_eval(name: str, win_rate: float, tie_rate: float, n_samples: int):
    from agentctl.eval.engine import sprt_stream
    from agentctl.eval.synthetic_judge import SyntheticJudge

    prefs = SyntheticJudge(win_rate, tie_rate, seed=7).judge_suite(n_samples)
    progress = Progress(TextColumn("[dim]eval[/]"), BarColumn(bar_width=40),
                        TextColumn("{task.completed}/{task.total} samples"), console=console)
    task = progress.add_task("eval", total=n_samples)
    final = {"n": 0, "wins": 0, "losses": 0, "ties": 0, "win_rate": 0.0,
             "wilson_low": 0.0, "wilson_high": 1.0, "decision": "INSUFFICIENT_DATA"}
    with Live(console=console, refresh_per_second=24, transient=False) as live:
        for state in sprt_stream(prefs, nim=0.50):
            progress.update(task, completed=state["n"])
            live.update(_eval_panel(name, state, progress))
            final = state
            time.sleep(0.012)   # pacing so the CI is visibly narrowing
    return final, prefs[: final["n"]]


def _persist_duckdb(db: str, sha: str, name: str, prefs: list, suite: str):
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.runner import gate_run
    from agentctl.storage.duckdb_store import EvalStore, Sample
    store = EvalStore.open(db)
    run_id = f"{sha}-{suite}"
    store.create_run(run_id=run_id, commit_sha=sha, baseline_sha="main", suite_name=suite,
                     judge_name="SyntheticJudge")
    store.record_samples(run_id, [Sample(item_id=f"i{i}", preference=p) for i, p in enumerate(prefs)])
    store.finish_run(run_id)
    d = gate_run(store, run_id, GateConfig(n_min=min(50, max(1, len(prefs)))))
    return run_id, d


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def run_push(path: str = ".", simulate_regression: bool = False, samples: int | None = None,
             db: str = DUCKDB_PATH, provision: bool = True) -> int:
    p = Path(path).resolve()
    if not p.is_dir():
        console.print(f"[red]✗[/] not a directory: {p}")
        return 2
    meta = _load_agent(p)
    ev = meta["eval"]
    name = str(meta.get("name", p.name))
    win_rate = REGRESSION_WIN if simulate_regression else float(ev.get("win_rate", DEFAULT_GOOD_WIN))
    tie_rate = float(ev.get("tie_rate", 0.08))
    n_samples = samples or int(ev.get("samples", 300))
    suite = str(ev.get("suite", "eval"))

    console.print()
    console.print(Panel.fit(f"[bold cyan]agentctl push[/]  •  [bold]{name}[/]  ·  model={meta.get('model')}",
                            subtitle="Vercel for Agents", box=box.HEAVY, border_style="cyan"))

    # ── 1. pack ──────────────────────────────────────────────────────────
    files, sha, size = _pack(p)
    tbl = Table(box=box.SIMPLE, show_edge=False)
    tbl.add_column("", style="dim"); tbl.add_column("")
    tbl.add_row("assets", ", ".join(files) or "(none)")
    tbl.add_row("commit", f"[yellow]{sha}[/]")
    tbl.add_row("bundle", f"{size/1024:.1f} KiB (tar.gz)")
    console.print(Panel(tbl, title="① pack", box=box.ROUNDED, border_style="grey50"))

    # ── 2. provision isolated preview via the webhook emulator ───────────
    conn = None
    runtime = dep_id = endpoint = None
    try:
        try:
            conn = _ensure_pg()
        except Exception as e:
            console.print(Panel(f"[red]Postgres unreachable[/]: {e}\n"
                                f"Start it with: [dim]docker compose -f deploy/docker-compose.yml up -d postgres[/]",
                                title="② preview", border_style="red"))
            return 3
        from agentctl.control.webhook import handle_push, make_push_payload, teardown_preview
        from agentctl.runtime.isolated import ProcessRuntime
        runtime = ProcessRuntime()
        with console.status("[cyan]provisioning isolated preview…[/]", spinner="dots"):
            res = handle_push(conn, make_push_payload(sha, ref="refs/heads/preview", repo=name,
                              changed=files, version_tag="preview"),
                              provision=provision, runtime=runtime if provision else None)
        dep_id, endpoint = res["deployment_id"], res.get("endpoint")
        ptbl = Table(box=box.SIMPLE, show_edge=False)
        ptbl.add_column("", style="dim"); ptbl.add_column("")
        ptbl.add_row("deployment", f"#{dep_id}")
        ptbl.add_row("lifecycle", " → ".join(f"[green]{s}[/]" for s in res["sequence"]))
        ptbl.add_row("endpoint", f"[cyan]{endpoint or '(not provisioned)'}[/]")
        console.print(Panel(ptbl, title="② preview", box=box.ROUNDED, border_style="grey50"))

        # ── 3. live eval-gating ──────────────────────────────────────────
        final, used = _run_live_eval(name, win_rate, tie_rate, n_samples)
        run_id, gate = _persist_duckdb(db, sha, name, used, suite)
        decision = final["decision"]
        console.print(f"[dim]   DuckDB run [yellow]{run_id}[/] persisted · gate={gate.decision}[/]")

        # ── 4. verdict ───────────────────────────────────────────────────
        ci = f"Wilson95 [{final['wilson_low']:.3f}, {final['wilson_high']:.3f}]"
        wl = f"win-rate {final['win_rate']:.3f}  ({final['wins']}W/{final['losses']}L/{final['ties']}T over {final['n']})"
        if decision == "ALLOW":
            conn.commit()
            from agentctl.rollback import routing
            routing.flip_routing(conn, DEMO_PROJECT_ID, dep_id, reason=f"merge:{sha}", actor="push")
            url = f"https://{name}-{sha[:8]}.agents.live"
            console.print(Panel(
                f"[bold green]✅  PR MERGED[/]  →  promoted to 100% live\n\n"
                f"[bold]Live URL[/]  [link={url}]{url}[/]\n"
                f"[dim]gRPC[/]      {endpoint or 'n/a'}\n"
                f"[dim]{wl}[/]\n"
                f"[dim]{ci} · SPRT log-LR {final['llr']:+.2f} ≥ {final['upper']:.2f} — superior to main[/]",
                title="③ verdict", box=box.DOUBLE, border_style="green"))
            rc = 0
        elif decision == "BLOCK":
            console.print(Panel(
                f"[bold red]⛔  PR BLOCKED[/]  →  preview torn down, main protected\n\n"
                f"[dim]{wl}[/]\n"
                f"[dim]{ci} · SPRT log-LR {final['llr']:+.2f} ≤ {final['lower']:.2f} — inferior to main (regression)[/]",
                title="③ verdict", box=box.DOUBLE, border_style="red"))
            rc = 1
        else:
            console.print(Panel(
                f"[bold yellow]⚠️  NEEDS REVIEW[/]  ({decision})\n\n[dim]{wl}\n{ci}[/]",
                title="③ verdict", box=box.DOUBLE, border_style="yellow"))
            rc = 0
        return rc
    finally:
        if runtime is not None and dep_id is not None:
            from agentctl.control.webhook import teardown_preview
            teardown_preview(runtime, dep_id)
        if conn is not None:
            conn.close()


@app.command()
def push(
    path: str = typer.Argument(".", help="agent directory containing prompt.yaml"),
    simulate_regression: bool = typer.Option(False, "--simulate-regression",
                                              help="simulate an inferior agent (-> PR BLOCKED)"),
    samples: int = typer.Option(None, "--samples", help="override eval sample count"),
    no_provision: bool = typer.Option(False, "--no-provision", help="skip the isolated preview"),
):
    """Package an agent dir, preview it, run the live eval-gate, and merge or block."""
    raise typer.Exit(run_push(path=path, simulate_regression=simulate_regression,
                              samples=samples, provision=not no_provision))


if __name__ == "__main__":
    app()
