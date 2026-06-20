# Design - The hosted GitHub App (webhook receiver shipped; hosting is ops)

**Status:** the App's brain (signed webhook receiver + gate dispatch) shipped; hosting it (a public
URL, the App registration, per-install tokens) is the operational step.
**Commit:** `feat(gitops): GitHub App webhook receiver (signed PR-gate dispatch)`

## Why

The roadmap's last item was "a hosted GitHub App." The honest decomposition (same as the CRD
operator): the App's *code* - verify GitHub's webhook signature, route the `pull_request` event, run
the gate, post the verdict - is real and unit-testable now; *hosting* it is ops. The reusable Action
(`.github/actions/agentctl-gate`) already covers the **CI-triggered** path; this is the
**webhook-triggered** path that an installed App uses.

## What ships now

`agentctl/gitops/webhook_app.py` (run: `agentctl gitops-app`):

- **`verify_signature(body, sig, secret)`** - GitHub's `X-Hub-Signature-256` (HMAC-SHA256 of the raw
  body), constant-time compare. **Fails closed** when no secret is configured - an unauthenticated
  webhook endpoint must never act.
- **`pr_coords(payload)`** - extracts `{repo, pr, sha}` for an actionable `pull_request` event
  (`opened`/`synchronize`/`reopened`/`ready_for_review`); ignores the rest.
- **`handle_pull_request(coords)`** - gates the PR against its ingested eval runs (`gate_pr`) and
  posts the commit status + comment back via the existing `github_gate` (best-effort, only when a
  token is set). Skips cleanly when no eval runs exist yet (the CI ingests them first).
- `POST /webhook` (verify -> route -> dispatch) + `GET /healthz`.

## What hosting adds (the remaining ops)

- A reachable HTTPS endpoint for `POST /webhook` (deploy the app; it is a stateless FastAPI service).
- A registered **GitHub App** (name, permissions: `checks`/`statuses`/`pull_requests` write, a
  `pull_request` webhook) and the per-installation token exchange (the App's private key -> an
  installation access token), versus the single `AGENTCTL_GH_TOKEN` the receiver uses today.
- Multi-repo eval-data plumbing (where each repo's eval runs are ingested from).

None of that is control-plane code; it is deployment + a GitHub App manifest.

## Verified

`tests/test_github_app.py`: signature verification (valid / tampered / missing / no-secret-fails-
closed), event routing (`pr_coords` actionable vs ignored), and the receiver over HTTP (TestClient):
a tampered signature -> 401, a non-`pull_request` event -> ignored, and a correctly-signed `opened`
event for a PR with ingested eval data -> `gated` with the right decision; plus the no-eval-data skip.
Full suite 208 passed.
