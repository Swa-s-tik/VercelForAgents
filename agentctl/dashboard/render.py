"""Server-rendered HTML for the dashboard. Pure functions (data in, HTML string out) so they are
unit-tested with no server and no DB. Dark theme matching the landing page; htmx (from a CDN) drives
the 1-click rollback - no build step, no SPA framework."""
from __future__ import annotations

import html


def _esc(v) -> str:
    return html.escape(str(v))


def _short(sha: str) -> str:
    return _esc(sha[:12])


def _status_pill(status: str) -> str:
    color = {"active": "#34d399", "ready": "#60a5fa", "building": "#fbbf24",
             "queued": "#94a3b8", "rolled_back": "#f87171", "failed": "#f87171"}.get(status, "#94a3b8")
    return f'<span class="pill" style="color:{color};border-color:{color}33">{_esc(status)}</span>'


def _arm_cell(d: dict) -> str:
    if not d["in_live_table"]:
        return '<span class="dim">-</span>'
    pct = d["weight"] / 100.0
    tags = []
    if d["is_canary"]:
        tags.append('<span class="tag canary">canary</span>')
    if d["shadow_target"]:
        tags.append('<span class="tag shadow">shadow</span>')
    bar = (f'<div class="bar"><div class="fill" style="width:{min(100, pct):.0f}%"></div></div>'
           f'<span class="wt">{pct:.0f}%</span>')
    return bar + " " + " ".join(tags)


def _honesty_cell(h: dict | None) -> str:
    if not h or h["pointers"] == 0:
        return '<span class="dim">-</span>'
    if h["irreversible"]:
        return (f'<span class="tag irrev" title="side effects that a rollback cannot undo">'
                f'⚠ {h["irreversible"]} irreversible</span>')
    return f'<span class="tag ok">{h["pointers"]} reversible</span>'


def _rollback_btn(d: dict) -> str:
    # Offer rollback only to a sealed, non-live target (a ready/active deploy not currently at 100%).
    eligible = d["status"] in ("ready", "active") and not (d["in_live_table"] and d["weight"] >= 10000)
    if not eligible:
        return ""
    sha = _esc(d["git_commit_sha"])
    return (f'<button class="rb" hx-post="/api/rollback" hx-vals=\'{{"to_commit_sha":"{sha}"}}\' '
            f'hx-target="#dash" hx-swap="innerHTML" hx-confirm="Roll back to {_short(d["git_commit_sha"])}?">'
            f'rollback to this</button>')


def deployments_table(deployments: list[dict], honesty: dict[int, dict]) -> str:
    if not deployments:
        return '<p class="dim">No deployments yet. Run <code>agentctl push</code>.</p>'
    rows = []
    for d in deployments:
        rows.append(
            "<tr>"
            f'<td class="mono">#{d["id"]}</td>'
            f'<td class="mono">{_short(d["git_commit_sha"])}</td>'
            f"<td>{_status_pill(d['status'])}</td>"
            f"<td>{_arm_cell(d)}</td>"
            f"<td>{_honesty_cell(honesty.get(d['id']))}</td>"
            f'<td class="dim">{_esc(d["created_by"])}</td>'
            f"<td>{_rollback_btn(d)}</td>"
            "</tr>")
    return (
        '<table><thead><tr>'
        "<th>#</th><th>commit</th><th>status</th><th>live traffic</th>"
        "<th>rollback honesty</th><th>by</th><th></th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def history_table(history: list[dict]) -> str:
    if not history:
        return '<p class="dim">No rollbacks yet.</p>'
    rows = []
    for r in history:
        unrb = r.get("unrollbackable_count") or 0
        note = f'<span class="tag irrev">{unrb} not undone</span>' if unrb else ""
        rows.append(
            "<tr>"
            f'<td class="mono">{_short(r["to_commit_sha"])}</td>'
            f"<td>{_status_pill(r['status'])}</td>"
            f'<td class="dim">{_esc(r["initiated_by"])}</td>'
            f'<td class="dim">{_esc(r["initiated_at"])}</td>'
            f"<td>{note}</td>"
            "</tr>")
    return ('<table><thead><tr><th>to commit</th><th>status</th><th>by</th><th>when</th><th></th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def dashboard_inner(deployments, honesty, history, routing_version, flash: str = "") -> str:
    """The swappable inner region (#dash) - re-rendered after a rollback POST."""
    v = "-" if routing_version is None else f"v{routing_version}"
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    return (
        f"{flash_html}"
        f'<section><h2>Deployments <span class="dim">- live routing {v}</span></h2>'
        f"{deployments_table(deployments, honesty)}</section>"
        f"<section><h2>Rollback history</h2>{history_table(history)}</section>")


_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0b0e14;color:#e6edf3;font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto}
header{padding:22px 32px;border-bottom:1px solid #1c2330;display:flex;align-items:baseline;gap:14px}
header h1{font-size:20px;margin:0}
header .sub{color:#7d8590;font-size:13px}
main{padding:24px 32px;max-width:1100px}
section{margin:0 0 30px}
h2{font-size:15px;font-weight:600;color:#c9d1d9;margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:#7d8590;font-weight:500;padding:8px 10px;border-bottom:1px solid #1c2330}
td{padding:10px;border-bottom:1px solid #141a24;vertical-align:middle}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.dim{color:#6e7681}
.pill{font-size:12px;padding:2px 9px;border:1px solid;border-radius:999px}
.bar{display:inline-block;width:90px;height:7px;background:#1c2330;border-radius:6px;overflow:hidden;vertical-align:middle}
.fill{height:100%;background:linear-gradient(90deg,#22d3ee,#34d399)}
.wt{font-size:12px;color:#8b949e;margin-left:6px}
.tag{font-size:11px;padding:2px 7px;border-radius:5px;background:#1c2330;color:#adbac7}
.tag.canary{background:#3b2f10;color:#fbbf24}
.tag.shadow{background:#102a3b;color:#60a5fa}
.tag.ok{background:#10261c;color:#34d399}
.tag.irrev{background:#3b1414;color:#f87171}
button.rb{background:#1f6feb;color:#fff;border:0;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}
button.rb:hover{background:#388bfd}
.flash{background:#10261c;border:1px solid #34d39944;color:#9be7c4;padding:10px 14px;border-radius:8px;margin-bottom:18px}
.flash.err{background:#3b1414;border-color:#f8717144;color:#f3a3a3}
code{background:#161b22;padding:1px 6px;border-radius:4px}
"""


def page(deployments, honesty, history, routing_version, project_id: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>agentctl</title>"
        '<script src="https://unpkg.com/htmx.org@1.9.12"></script>'
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>agentctl</h1>"
        f'<span class="sub">deploy control plane - project {_short(project_id)}</span></header>'
        f'<main id="dash">{dashboard_inner(deployments, honesty, history, routing_version)}</main>'
        "</body></html>")
