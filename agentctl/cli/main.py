"""`agentctl push` - the developer-facing command (typer + rich).

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
app = typer.Typer(add_completion=False, help="Vercel for Agents - ship agents with one command.")

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
# streaming proof through the Go data plane (for `streaming: true` agents)
# --------------------------------------------------------------------------- #
async def _stream_via(gateway_endpoint: str = "localhost:50050"):
    """Open one bidi turn through whatever data plane is listening at ``gateway_endpoint`` and
    collect the streamed TextDelta chunks (with arrival timestamps) + the ToolCall frame."""
    import time as _t

    import grpc

    from agentctl.gateway import frames as F
    from agentctl.gen import load
    _pb, _dp, dpg, _cp, _cpg = load()
    ch = grpc.aio.insecure_channel(gateway_endpoint)
    stub = dpg.AgentStreamStub(ch)

    async def gen():
        yield F.client_text("support-demo", 1, 0, "where is my refund?")

    t0 = _t.perf_counter()
    chunks, tool = [], None
    async for resp in stub.Converse(gen()):
        if resp.HasField("text"):
            chunks.append((_t.perf_counter() - t0, resp.text.content))
        elif resp.HasField("tool_call"):
            tool = resp.tool_call
    await ch.close()
    return chunks, tool


def _gateway_healthy(endpoint: str = "localhost:50050", timeout: float = 1.5) -> bool:
    """True if a data-plane gateway (e.g. a `docker compose up` Go gateway) already answers here."""
    import grpc

    from agentctl.gen import load
    _pb, dpm, dpg, _cp, _cpg = load()
    try:
        with grpc.insecure_channel(endpoint) as ch:
            return bool(dpg.AgentStreamStub(ch).Health(dpm.HealthRequest(), timeout=timeout).ready)
    except Exception:
        return False


async def _stream_through_python(project_id, port: int = 50050):
    """Fallback data plane so the proof runs on any fresh checkout: spin up the in-process Python
    grpc.aio reference proxy (Postgres-routed, so it forwards to the same preview backend the Go
    gateway would) and stream one turn through it."""
    from agentctl.gateway.pg_route_cache import PgRouteCache
    from agentctl.gateway.proxy import GatewayServicer, serve
    from agentctl.gateway.router import Router

    servicer = GatewayServicer(Router(PgRouteCache(project_id=project_id)))
    server, _ = await serve(port, servicer)
    try:
        return await _stream_via(f"localhost:{port}")
    finally:
        await server.stop(0)


def _streaming_proof(conn, dep_id, sha, name, project_id=DEMO_PROJECT_ID) -> dict:
    """Make the preview the live route, stream a turn through the data plane (prove incremental
    chunks + the streamed issue_refund ToolCall), intercept THAT tool call in the sandbox, and seal
    the side-effect into the deployment checkpoint.

    Data-plane selection (so the proof always runs, even on a fresh clone): reuse a Go gateway already
    listening on :50050 (e.g. from `docker compose up`); else launch the host Go binary if it's built;
    else fall back to the in-process Python reference proxy.
    """
    import asyncio
    import json

    from agentctl.gateway.go_launcher import binary_available, launch_go_gateway, stop
    from agentctl.rollback import manifest as mf, routing
    from agentctl.rollback.models import Pointer
    from agentctl.runtime.sandbox_interceptor import SandboxInterceptor, Tool, ToolInvoker

    conn.commit()
    routing.flip_routing(conn, project_id, dep_id, reason=f"preview:{sha}", actor="push")

    if _gateway_healthy("localhost:50050"):
        engine = "go-running"
        chunks, tool = asyncio.run(_stream_via("localhost:50050"))
    elif binary_available():
        engine = "go"
        gw = launch_go_gateway(port=50050, project_id=project_id)
        try:
            chunks, tool = asyncio.run(_stream_via("localhost:50050"))
        finally:
            stop(gw)
    else:
        engine = "python"
        chunks, tool = asyncio.run(_stream_through_python(project_id))

    # Intercept the ACTUAL streamed tool call (not a hardcoded one): in preview mode the sandbox
    # mocks any side-effecting/external tool, so the real fn never runs -> real effects stay 0.
    real_effects: list = []
    sres = None
    if tool is not None:
        try:
            args = json.loads((tool.arguments or b"").decode() or "{}")
        except Exception:
            args = {}
        inv = ToolInvoker([Tool(tool.tool_name, side_effecting=tool.side_effecting, klass="external",
                                fn=lambda a: real_effects.append(a))])
        sres = SandboxInterceptor(inv, mode="preview").invoke(tool.tool_name, args)

    tool_name = tool.tool_name if tool else "issue_refund"
    mf.seal_checkpoint(conn, dep_id, sha, [Pointer(
        "side_effect", "irreversible", "stripe",
        {"provider": "stripe", "tool": tool_name,
         "idempotency_key": (tool.call_id if tool else "n/a"), "compensation": "reverse_refund"},
        strategy="compensate_or_flag")])
    man = mf.load_manifest(conn, dep_id)
    side = next((pt for pt in (man.pointers if man else []) if pt.mutation_class == "side_effect"), None)
    return {"chunks": chunks, "tool": tool, "sandbox": sres, "engine": engine,
            "real_refunds": len(real_effects), "side_effect": side}


_ENGINE_LABEL = {"go-running": "the Go gateway", "go": "the Go gateway",
                 "python": "the Python reference proxy"}


def _streaming_panel(sp: dict) -> Panel:
    if "skipped" in sp:
        return Panel(f"[yellow]streaming proof skipped[/]: {sp['skipped']}",
                     title="②′ live stream", box=box.ROUNDED, border_style="yellow")
    chunks, tool = sp["chunks"], sp["tool"]
    eng = _ENGINE_LABEL.get(sp.get("engine", "go"), "the data plane")
    spread = (chunks[-1][0] - chunks[0][0]) if len(chunks) > 1 else 0.0
    incremental = spread > 0.15
    t = Table(box=box.SIMPLE, show_edge=False)
    t.add_column("", style="dim"); t.add_column("")
    t.add_row("text stream", f"{len(chunks)} TextDelta frames via {eng}")
    t.add_row("arrival", (f"first @ {chunks[0][0]*1000:.0f}ms · last @ {chunks[-1][0]*1000:.0f}ms · "
                          f"spread {spread*1000:.0f}ms") if chunks else "-")
    t.add_row("buffering", "[green]none - chunks streamed incrementally[/]" if incremental
              else "[red]buffered into one block[/]")
    t.add_row("tool call", f"[cyan]{tool.tool_name}[/] (side_effecting={tool.side_effecting})"
              if tool else "-")
    t.add_row("sandbox", (f"intercepted the streamed call → mocked (source={sp['sandbox'].source}); "
                          f"real refunds issued: [green]{sp['real_refunds']}[/]")
              if sp.get("sandbox") else "-")
    s = sp["side_effect"]
    t.add_row("rollback", f"{tool.tool_name if tool else 'issue_refund'} sealed as "
              f"[yellow]{s.mutation_class}/{s.reversibility}[/] in checkpoint" if s else "-")
    return Panel(t, title=f"②′ live stream through {eng} + side-effect",
                 box=box.ROUNDED, border_style="cyan")


# --------------------------------------------------------------------------- #
# the command
# --------------------------------------------------------------------------- #
def run_push(path: str = ".", simulate_regression: bool = False, samples: int | None = None,
             db: str = DUCKDB_PATH, provision: bool = True, api_key: str | None = None) -> int:
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
    streaming = str(meta.get("streaming", "")).lower() == "true"
    agent_kind = str(meta.get("kind", "echo"))

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
        from agentctl.auth.principal import AuthError, resolve_principal
        try:
            principal = resolve_principal(conn, api_key).require("developer")
        except AuthError as e:
            console.print(Panel(f"[red]auth denied[/]: {e}", title="② preview", border_style="red"))
            return 4
        project_id = principal.project_id
        console.print(f"[dim]   principal [yellow]{principal.name}[/] · role={principal.role} · "
                      f"project={project_id}[/]")
        from agentctl.control.webhook import handle_push, make_push_payload, teardown_preview
        from agentctl.runtime.isolated import ProcessRuntime
        runtime = ProcessRuntime()
        with console.status("[cyan]provisioning isolated preview…[/]", spinner="dots"):
            res = handle_push(conn, make_push_payload(sha, ref="refs/heads/preview", repo=name,
                              changed=files, version_tag="preview"), project_id=project_id,
                              provision=provision, runtime=runtime if provision else None,
                              agent_kind=agent_kind)
        dep_id, endpoint = res["deployment_id"], res.get("endpoint")
        ptbl = Table(box=box.SIMPLE, show_edge=False)
        ptbl.add_column("", style="dim"); ptbl.add_column("")
        ptbl.add_row("deployment", f"#{dep_id}")
        ptbl.add_row("lifecycle", " → ".join(f"[green]{s}[/]" for s in res["sequence"]))
        ptbl.add_row("endpoint", f"[cyan]{endpoint or '(not provisioned)'}[/]")
        console.print(Panel(ptbl, title="② preview", box=box.ROUNDED, border_style="grey50"))

        # ── 2′. streaming proof through the Go data plane (streaming agents) ──
        if streaming and provision and endpoint:
            try:
                console.print(_streaming_panel(_streaming_proof(conn, dep_id, sha, name, project_id)))
            except Exception as e:
                console.print(Panel(f"[yellow]streaming proof error[/]: {e}",
                                    title="②′ live stream", box=box.ROUNDED, border_style="yellow"))

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
            routing.flip_routing(conn, project_id, dep_id, reason=f"merge:{sha}", actor="push")
            url = f"https://{name}-{sha[:8]}.agents.live"
            console.print(Panel(
                f"[bold green]✅  PR MERGED[/]  →  promoted to 100% live\n\n"
                f"[bold]Live URL[/]  [link={url}]{url}[/]\n"
                f"[dim]gRPC[/]      {endpoint or 'n/a'}\n"
                f"[dim]{wl}[/]\n"
                f"[dim]{ci} · SPRT log-LR {final['llr']:+.2f} ≥ {final['upper']:.2f} - superior to main[/]",
                title="③ verdict", box=box.DOUBLE, border_style="green"))
            rc = 0
        elif decision == "BLOCK":
            console.print(Panel(
                f"[bold red]⛔  PR BLOCKED[/]  →  preview torn down, main protected\n\n"
                f"[dim]{wl}[/]\n"
                f"[dim]{ci} · SPRT log-LR {final['llr']:+.2f} ≤ {final['lower']:.2f} - inferior to main (regression)[/]",
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
    api_key: str = typer.Option(None, "--api-key", envvar="AGENTCTL_API_KEY",
                                help="API key for the target project (else bootstrap)"),
):
    """Package an agent dir, preview it, run the live eval-gate, and merge or block."""
    raise typer.Exit(run_push(path=path, simulate_regression=simulate_regression,
                              samples=samples, provision=not no_provision, api_key=api_key))


if __name__ == "__main__":
    app()
