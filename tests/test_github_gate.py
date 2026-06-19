"""Unit tests for the GitHub eval-gate poster: pure builders + the two POSTs through an injected
opener (no network). Proves the request agentctl would send GitHub is exactly right."""
from __future__ import annotations

import json
from dataclasses import dataclass

from agentctl.gitops import github_gate as gh


@dataclass
class FakeDecision:
    decision: str
    wins: int
    losses: int
    ties: int
    win_rate: float
    wilson_low: float
    wilson_high: float
    n: int
    reason: str = "ci"


class FakeVerdict:
    def __init__(self, decision, reason):
        self.decision, self.reason = decision, reason


def test_status_state_tracks_exit_code():
    assert gh.status_state(0) == "success"
    assert gh.status_state(1) == "failure"
    assert gh.status_state(2) == "failure"


def test_status_payload_allow_and_block():
    allow = gh.status_payload("ALLOW", "CI above margin", 0, target_url="http://ci/run/1")
    assert allow["state"] == "success"
    assert allow["context"] == gh.GATE_CONTEXT
    assert allow["target_url"] == "http://ci/run/1"
    assert "ALLOW" in allow["description"]

    block = gh.status_payload("BLOCK", "regression", 1)
    assert block["state"] == "failure"
    assert "target_url" not in block  # omitted when empty


def test_status_description_truncated_to_140():
    p = gh.status_payload("BLOCK", "x" * 500, 1)
    assert len(p["description"]) <= 140


def test_from_github_env_present_absent_and_pr():
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_SHA": "deadbeef",
           "AGENTCTL_GATE_PR": "42"}
    tgt = gh.from_github_env(env)
    assert tgt and tgt.repo == "o/r" and tgt.sha == "deadbeef" and tgt.pr == 42

    # head SHA + dedicated token take precedence
    env2 = dict(env, AGENTCTL_GATE_SHA="headsha", AGENTCTL_GH_TOKEN="t2")
    tgt2 = gh.from_github_env(env2)
    assert tgt2.sha == "headsha" and tgt2.token == "t2"

    assert gh.from_github_env({"GITHUB_REPOSITORY": "o/r"}) is None  # missing token/sha


def test_comment_markdown_has_verdict_and_rows():
    d = FakeDecision("ALLOW", 26, 11, 4, 0.683, 0.530, 0.804, 41)
    md = gh.comment_markdown(FakeVerdict("ALLOW", "CI at/above margin"), {"support-quality": d},
                             sha="abcdef1234567890", margin=0.50)
    assert "agentctl eval-gate" in md
    assert "ALLOW" in md and "CI at/above margin" in md
    assert "support-quality" in md
    assert "[0.530, 0.804]" in md          # the Wilson CI is shown
    assert "26/11/4" in md                  # W/L/T
    assert "abcdef123456" in md             # short sha footer


def _capture_opener(sink):
    def opener(req):
        sink["url"] = req.full_url
        sink["method"] = req.get_method()
        sink["headers"] = {k.lower(): v for k, v in req.header_items()}
        sink["body"] = json.loads(req.data.decode())

        class R:
            status = 201
        return R()
    return opener


def test_post_commit_status_builds_correct_request():
    sink: dict = {}
    tgt = gh.GitHubTarget(repo="Swa-s-tik/agentctl", sha="headsha", token="secret", pr=7)
    code = gh.post_commit_status(tgt, gh.status_payload("BLOCK", "regression", 1),
                                 opener=_capture_opener(sink))
    assert code == 201
    assert sink["url"] == "https://api.github.com/repos/Swa-s-tik/agentctl/statuses/headsha"
    assert sink["method"] == "POST"
    assert sink["headers"]["authorization"] == "Bearer secret"
    assert sink["headers"]["x-github-api-version"] == gh.API_VERSION
    assert sink["body"]["state"] == "failure"
    assert sink["body"]["context"] == gh.GATE_CONTEXT


def test_post_pr_comment_request_and_noop_without_pr():
    sink: dict = {}
    tgt = gh.GitHubTarget(repo="o/r", sha="s", token="tok", pr=9)
    gh.post_pr_comment(tgt, "hello", opener=_capture_opener(sink))
    assert sink["url"] == "https://api.github.com/repos/o/r/issues/9/comments"
    assert sink["body"] == {"body": "hello"}

    # no PR number -> no request, returns 0
    no_pr = gh.GitHubTarget(repo="o/r", sha="s", token="tok", pr=None)
    called = {"n": 0}
    gh.post_pr_comment(no_pr, "x", opener=lambda r: called.__setitem__("n", called["n"] + 1))
    assert called["n"] == 0


def test_api_url_override_for_ghe():
    sink: dict = {}
    tgt = gh.GitHubTarget(repo="o/r", sha="s", token="t", api_url="https://ghe.corp/api/v3")
    gh.post_commit_status(tgt, gh.status_payload("ALLOW", "ok", 0), opener=_capture_opener(sink))
    assert sink["url"].startswith("https://ghe.corp/api/v3/repos/o/r/statuses/")
