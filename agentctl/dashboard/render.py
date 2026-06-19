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


def match_verdict(sha: str, verdicts: dict[str, dict]) -> dict | None:
    """Find a commit's eval verdict, tolerating full-vs-short SHAs (the deployment may store the full
    git sha while the eval run stored a prefix, or vice versa). Exact match first, then prefix."""
    if not verdicts:
        return None
    if sha in verdicts:
        return verdicts[sha]
    for csha, v in verdicts.items():
        n = min(len(sha), len(csha))
        if n >= 8 and (sha[:n] == csha[:n]):
            return v
    return None


def _verdict_cell(v: dict | None) -> str:
    if not v:
        return '<span class="dim">-</span>'
    icon = {"ALLOW": "✅", "BLOCK": "⛔", "INCONCLUSIVE": "🟡", "INSUFFICIENT_DATA": "⏳"}.get(v["decision"], "•")
    cls = {"ALLOW": "ok", "BLOCK": "irrev"}.get(v["decision"], "")
    suites = f' <span class="dim">x{v["suites"]}</span>' if v.get("suites", 1) > 1 else ""
    ci = (f' <span class="dim">[{v["wilson_low"]:.2f}, {v["wilson_high"]:.2f}]</span>'
          if v.get("wilson_low") is not None else "")
    return f'<span class="tag {cls}">{icon} {_esc(v["decision"])}</span>{suites}{ci}'


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
    # commit sha in the path (git shas are URL-safe hex) -> no form body, no python-multipart dep.
    return (f'<button class="rb" hx-post="/api/rollback/{sha}" '
            f'hx-target="#dash" hx-swap="innerHTML" hx-confirm="Roll back to {_short(d["git_commit_sha"])}?">'
            f'rollback to this</button>')


def _rollout_btns(d: dict) -> str:
    """Forward actions: canary a slice of traffic, or promote to 100%. Offered on a sealed deploy that
    is not already serving 100% - the mirror of the rollback button (a full forward+back control)."""
    eligible = d["status"] in ("ready", "active") and not (d["in_live_table"] and d["weight"] >= 10000)
    if not eligible:
        return ""
    sha = _esc(d["git_commit_sha"])
    short = _short(d["git_commit_sha"])
    canary = (f'<button class="rb fwd" hx-post="/api/rollout/{sha}/10" hx-target="#dash" '
              f'hx-swap="innerHTML" hx-confirm="Send 10% canary to {short}?">canary 10%</button>')
    promote = (f'<button class="rb fwd" hx-post="/api/rollout/{sha}/100" hx-target="#dash" '
               f'hx-swap="innerHTML" hx-confirm="Promote {short} to 100%?">promote</button>')
    return canary + promote


def deployments_table(deployments: list[dict], honesty: dict[int, dict],
                      verdicts: dict[str, dict] | None = None) -> str:
    if not deployments:
        return '<p class="dim">No deployments yet. Run <code>agentctl push</code>.</p>'
    verdicts = verdicts or {}
    rows = []
    for d in deployments:
        rows.append(
            "<tr>"
            f'<td class="mono">#{d["id"]}</td>'
            f'<td class="mono">{_short(d["git_commit_sha"])}</td>'
            f"<td>{_status_pill(d['status'])}</td>"
            f"<td>{_verdict_cell(match_verdict(d['git_commit_sha'], verdicts))}</td>"
            f"<td>{_arm_cell(d)}</td>"
            f"<td>{_honesty_cell(honesty.get(d['id']))}</td>"
            f'<td class="dim">{_esc(d["created_by"])}</td>'
            f"<td>{_rollback_btn(d)}{_rollout_btns(d)}</td>"
            "</tr>")
    return (
        '<table><thead><tr>'
        "<th>#</th><th>commit</th><th>status</th><th>eval verdict</th><th>live traffic</th>"
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


def traffic_table(rows: list[dict]) -> str:
    if not rows:
        return ('<p class="dim">No gateway traffic recorded yet. Run <code>agentctl push</code> '
                "(spans land in <code>otel_spans</code> when telemetry is on).</p>")
    out = []
    for r in rows:
        lat = r.get("avg_latency_ms")
        lat_s = f"{lat:.0f} ms" if lat is not None else "-"
        dropped = int(r.get("shadow_dropped") or 0)
        drop_s = f'<span class="tag irrev">{dropped} dropped</span> ' if dropped else ""
        out.append(
            "<tr>"
            f'<td><span class="tag shadow">{_esc(r["arm"])}</span></td>'
            f'<td class="mono">{int(r["streams"])}</td>'
            f'<td class="mono">{int(r.get("frames") or 0)}</td>'
            f'<td class="mono">{lat_s}</td>'
            f"<td>{drop_s}{_shadow_divergence(r)}</td>"
            "</tr>")
    return ('<table><thead><tr><th>canary arm</th><th>streams</th><th>frames</th>'
            "<th>avg latency</th><th>shadow output</th></tr></thead><tbody>"
            + "".join(out) + "</tbody></table>")


def _shadow_divergence(r: dict) -> str:
    """Show what the shadow produced vs the primary - the point of shadowing. A large gap means the
    candidate would have responded very differently."""
    recv = int(r.get("shadow_received") or 0)
    frames = int(r.get("frames") or 0)
    if recv == 0:
        return '<span class="dim">-</span>'
    div = abs(frames - recv) / max(frames, 1)
    cls = "irrev" if div >= 0.20 else "ok"        # >=20% output divergence is worth a look
    return f'<span class="tag {cls}">{recv} frames ({div * 100:.0f}% diverge)</span>'


def routing_history_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="dim">No routing changes yet.</p>'
    out = []
    for r in rows:
        live = '<span class="tag ok">live</span>' if r["is_live"] else ""
        out.append(
            "<tr>"
            f'<td class="mono">v{r["version"]} {live}</td>'
            f'<td>{_esc(r["reason"] or "-")}</td>'
            f'<td class="mono dim">{_esc(r["arms"])}</td>'
            f'<td class="dim">{_esc(r["created_by"])}</td>'
            f'<td class="dim">{_esc(r["created_at"])}</td>'
            "</tr>")
    return ('<table><thead><tr><th>version</th><th>reason</th><th>arms</th><th>by</th><th>when</th>'
            "</tr></thead><tbody>" + "".join(out) + "</tbody></table>")


def dashboard_inner(deployments, honesty, history, routing_version, verdicts=None, traffic=None,
                    routing=None, flash: str = "") -> str:
    """The swappable inner region (#dash) - re-rendered after a rollback POST."""
    v = "-" if routing_version is None else f"v{routing_version}"
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    return (
        f"{flash_html}"
        f'<section><h2>Deployments <span class="dim">- live routing {v}</span></h2>'
        f"{deployments_table(deployments, honesty, verdicts)}</section>"
        f"<section><h2>Live traffic <span class=\"dim\">- recent gateway streams by arm</span></h2>"
        f"{traffic_table(traffic or [])}</section>"
        f'<section><h2>Delivery timeline <span class="dim">- every routing change</span></h2>'
        f"{routing_history_table(routing or [])}</section>"
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
button.rb.fwd{background:#1c2330;color:#9be7c4;border:1px solid #34d39944;margin-left:6px}
button.rb.fwd:hover{background:#10261c}
.flash{background:#10261c;border:1px solid #34d39944;color:#9be7c4;padding:10px 14px;border-radius:8px;margin-bottom:18px}
.flash.err{background:#3b1414;border-color:#f8717144;color:#f3a3a3}
code{background:#161b22;padding:1px 6px;border-radius:4px}
"""


def page(deployments, honesty, history, routing_version, project_id: str, verdicts=None,
         traffic=None, routing=None) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>agentctl</title>"
        '<script src="https://unpkg.com/htmx.org@1.9.12"></script>'
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>agentctl</h1>"
        f'<span class="sub">deploy control plane - project {_short(project_id)}</span></header>'
        f'<main id="dash" hx-get="/api/dashboard" hx-trigger="every 6s" hx-swap="innerHTML">'
        f'{dashboard_inner(deployments, honesty, history, routing_version, verdicts, traffic, routing)}</main>'
        "</body></html>")
