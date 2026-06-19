"""`agentctl status` - the terminal counterpart of the web dashboard. Reads the same
system-of-record (deployments + live routing + rollback honesty + traffic) plus the DuckDB eval
verdicts, and prints a rich summary. Read-only; reuses agentctl.dashboard.queries so the two surfaces
never drift."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from agentctl.common.db import connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.dashboard import queries as q
from agentctl.dashboard.render import match_verdict

_DPILL = {"ALLOW": "[green]ALLOW[/]", "BLOCK": "[red]BLOCK[/]",
          "INCONCLUSIVE": "[yellow]INCONCLUSIVE[/]", "INSUFFICIENT_DATA": "[dim]INSUFF[/]"}
_SPILL = {"active": "[green]active[/]", "ready": "[cyan]ready[/]", "building": "[yellow]building[/]",
          "rolled_back": "[red]rolled_back[/]"}


def _verdict_str(v: dict | None) -> str:
    if not v:
        return "[dim]-[/]"
    base = _DPILL.get(v["decision"], v["decision"])
    extra = f" [dim]x{v['suites']}[/]" if v.get("suites", 1) > 1 else ""
    ci = (f" [dim][{v['wilson_low']:.2f},{v['wilson_high']:.2f}][/]"
          if v.get("wilson_low") is not None else "")
    return base + extra + ci


def _arm_str(d: dict) -> str:
    if not d["in_live_table"]:
        return "[dim]-[/]"
    tags = []
    if d["is_canary"]:
        tags.append("[yellow]canary[/]")
    if d["shadow_target"]:
        tags.append("[blue]shadow[/]")
    return f"{d['weight'] / 100:.0f}%" + ("  " + " ".join(tags) if tags else "")


def build_snapshot(conn, project_id: str) -> dict:
    return {
        "deployments": q.list_deployments(conn, project_id),
        "honesty": q.deployment_honesty(conn, project_id),
        "verdicts": q.verdicts_by_commit(),
        "traffic": q.stream_telemetry(conn, project_id),
        "history": q.rollback_history(conn, project_id, limit=5),
        "routing": q.routing_history(conn, project_id, limit=8),
        "routing_version": q.live_routing_version(conn, project_id),
    }


def render(snap: dict, project_id: str, console: Console) -> None:
    v = snap["routing_version"]
    console.print(f"\n[bold]agentctl[/]  project [dim]{project_id[:12]}[/]  "
                  f"live routing [cyan]{'-' if v is None else 'v' + str(v)}[/]\n")

    dt = Table(title="Deployments", title_justify="left", expand=False)
    for c in ("#", "commit", "status", "eval verdict", "live traffic", "honesty", "by"):
        dt.add_column(c)
    for d in snap["deployments"]:
        h = snap["honesty"].get(d["id"])
        honesty = ("[red]" + str(h["irreversible"]) + " irrev[/]" if h and h["irreversible"]
                   else (f"{h['pointers']} rev" if h else "[dim]-[/]"))
        dt.add_row(str(d["id"]), d["git_commit_sha"][:12], _SPILL.get(d["status"], d["status"]),
                   _verdict_str(match_verdict(d["git_commit_sha"], snap["verdicts"])),
                   _arm_str(d), honesty, f"[dim]{d['created_by']}[/]")
    console.print(dt)

    if snap["traffic"]:
        tt = Table(title="Live traffic (recent streams by arm)", title_justify="left")
        for c in ("arm", "streams", "frames", "avg latency", "shadow dropped"):
            tt.add_column(c)
        for r in snap["traffic"]:
            lat = r.get("avg_latency_ms")
            tt.add_row(str(r["arm"]), str(int(r["streams"])), str(int(r.get("frames") or 0)),
                       f"{lat:.0f} ms" if lat is not None else "-",
                       str(int(r.get("shadow_dropped") or 0)))
        console.print(tt)

    if snap.get("routing"):
        gt = Table(title="Delivery timeline (every routing change)", title_justify="left")
        for c in ("version", "reason", "arms", "by"):
            gt.add_column(c)
        for r in snap["routing"]:
            ver = f"v{r['version']}" + (" [green]live[/]" if r["is_live"] else "")
            gt.add_row(ver, r["reason"] or "-", f"[dim]{r['arms']}[/]", f"[dim]{r['created_by']}[/]")
        console.print(gt)

    if snap["history"]:
        rt = Table(title="Recent rollbacks", title_justify="left")
        for c in ("to commit", "status", "by", "when"):
            rt.add_column(c)
        for r in snap["history"]:
            rt.add_row(r["to_commit_sha"][:12], r["status"], f"[dim]{r['initiated_by']}[/]",
                       f"[dim]{r['initiated_at']}[/]")
        console.print(rt)
    console.print()


def run_status(project_id: str | None = None, console: Console | None = None) -> int:
    project_id = project_id or DEMO_PROJECT_ID
    console = console or Console()
    conn = connect()
    try:
        snap = build_snapshot(conn, project_id)
    finally:
        conn.close()
    render(snap, project_id, console)
    return 0
