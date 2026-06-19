"""Post the eval-gate verdict to a GitHub PR: a commit status (so it gates merge) + a comment
(so a human sees the Wilson CI / SPRT reasoning). stdlib-only (urllib), matching the repo's
no-extra-deps ethos - the ClickHouse exporter takes the same approach.

The split is deliberate: the *builders* (env parsing, status payload, comment markdown) are pure and
trivially unit-tested; the two POSTs go through an injectable opener so tests assert the exact request
without touching the network.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable

# The commit-status context that shows up as a check on the PR. Stable so re-runs update in place.
GATE_CONTEXT = "agentctl/eval-gate"
API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class GitHubTarget:
    repo: str            # "owner/repo"
    sha: str             # the PR head SHA the status attaches to
    token: str
    pr: int | None = None
    api_url: str = "https://api.github.com"


def from_github_env(env: dict | None = None) -> GitHubTarget | None:
    """Build a target from the environment a GitHub Action exposes. Returns None when the required
    pieces are absent (so `agentctl gate` runs fine off-CI). The workflow passes the PR head SHA and
    number explicitly (AGENTCTL_GATE_SHA/PR), since for a pull_request event GITHUB_SHA is the merge
    commit, not the head."""
    env = os.environ if env is None else env
    repo = env.get("GITHUB_REPOSITORY")
    token = env.get("AGENTCTL_GH_TOKEN") or env.get("GITHUB_TOKEN")
    sha = env.get("AGENTCTL_GATE_SHA") or env.get("GITHUB_SHA")
    if not (repo and token and sha):
        return None
    pr = env.get("AGENTCTL_GATE_PR")
    return GitHubTarget(
        repo=repo, sha=sha, token=token,
        pr=int(pr) if pr and str(pr).isdigit() else None,
        api_url=env.get("GITHUB_API_URL", "https://api.github.com"),
    )


def status_state(exit_code: int) -> str:
    """Map the gate's own exit code to a commit-status state, so the check and the CLI agree:
    0 (ALLOW, or non-strict INCONCLUSIVE) -> success; anything else (BLOCK / strict) -> failure."""
    return "success" if exit_code == 0 else "failure"


def status_payload(decision: str, reason: str, exit_code: int, target_url: str = "") -> dict:
    p = {
        "state": status_state(exit_code),
        "context": GATE_CONTEXT,
        "description": f"{decision}: {reason}"[:140],  # GitHub truncates at 140 chars
    }
    if target_url:
        p["target_url"] = target_url
    return p


def _row(suite: str, d) -> str:
    icon = {"ALLOW": "✅", "BLOCK": "⛔", "INCONCLUSIVE": "🟡", "INSUFFICIENT_DATA": "⏳"}.get(d.decision, "•")
    return (f"| `{suite}` | {icon} {d.decision} | {d.wins}/{d.losses}/{d.ties} | "
            f"{d.win_rate:.3f} | [{d.wilson_low:.3f}, {d.wilson_high:.3f}] | {d.n} |")


def comment_markdown(verdict, decisions: dict, *, sha: str = "", margin: float = 0.50) -> str:
    """A PR comment: the overall verdict + a per-suite table. `verdict` has .decision/.reason; each
    value in `decisions` is a GateDecision (wins/losses/ties/win_rate/wilson_low/high/n/decision)."""
    head = "✅ **ALLOW**" if verdict.decision == "ALLOW" else (
        "⛔ **BLOCK**" if verdict.decision == "BLOCK" else f"**{verdict.decision}**")
    lines = [
        "## agentctl eval-gate",
        "",
        f"{head} - {verdict.reason}",
        "",
        f"Non-inferiority margin `nim = {margin:.2f}` - the decision is made by the **95% Wilson "
        f"interval**, not a bare win-rate threshold.",
        "",
        "| suite | decision | W/L/T | win-rate | Wilson 95% CI | n |",
        "|---|---|---|---|---|---|",
    ]
    lines += [_row(s, d) for s, d in decisions.items()]
    if sha:
        lines += ["", f"<sub>commit `{sha[:12]}` - posted by `agentctl gate`</sub>"]
    return "\n".join(lines)


# --- POST plumbing (injectable opener for tests) ----------------------------------------------- #
Opener = Callable[[urllib.request.Request], "object"]


def _default_opener(req: urllib.request.Request):
    return urllib.request.urlopen(req, timeout=15)


def _post(url: str, token: str, payload: dict, opener: Opener) -> int:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "agentctl-gate",
        },
    )
    resp = opener(req)
    return getattr(resp, "status", getattr(resp, "code", 0))


def post_commit_status(t: GitHubTarget, payload: dict, opener: Opener = _default_opener) -> int:
    return _post(f"{t.api_url}/repos/{t.repo}/statuses/{t.sha}", t.token, payload, opener)


def post_pr_comment(t: GitHubTarget, body: str, opener: Opener = _default_opener) -> int:
    if t.pr is None:
        return 0
    return _post(f"{t.api_url}/repos/{t.repo}/issues/{t.pr}/comments", t.token, {"body": body}, opener)
