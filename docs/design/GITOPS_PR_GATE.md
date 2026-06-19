# Design - GitHub-native eval-gate (the GitOps loop, made real)

**Status:** done · **Commit:** `feat(gitops): post the eval-gate verdict to GitHub PRs (status + comment)`

## Why

The headline promise is "open a PR, the agent is quality-gated automatically." Until now that loop
was *emulated*: `agentctl/control/webhook.py` is a webhook emulator, and the eval-gate verdict only
ever printed to a terminal. Nothing connected a real GitHub PR to the gate. This closes that gap with
the minimum honest surface: the existing `agentctl gate` now posts its verdict to GitHub as a **commit
status** (so it gates merge) and a **PR comment** (so a human sees the Wilson-interval reasoning).

It builds on what already existed - `gate_pr` / `evaluate_gate` produce the verdict - so this is a
thin, well-tested I/O layer, not new gate logic.

## What it does

- `agentctl/gitops/github_gate.py` - stdlib-only (urllib, like the ClickHouse exporter):
  - `from_github_env()` reads the Actions environment (`GITHUB_REPOSITORY`, a token, and the **PR head
    SHA** - passed explicitly as `AGENTCTL_GATE_SHA`, because for a `pull_request` event `GITHUB_SHA`
    is the merge commit, not the head the check must attach to).
  - `status_payload()` maps the gate's own **exit code** to a commit-status state (`0 -> success`,
    else `failure`), so the check and the CLI never disagree. `comment_markdown()` renders the overall
    verdict + a per-suite table (decision, W/L/T, win-rate, Wilson 95% CI, n).
  - `post_commit_status()` / `post_pr_comment()` POST through an **injectable opener**, so tests assert
    the exact request (URL, method, headers, body) with no network.
- `agentctl gate --github` (and `--dry-run`) in `agentctl/cli/__init__.py`: after computing the
  verdict it posts the status (+ comment when a PR number is present). `--dry-run` prints both
  artifacts and calls nothing - the safe way to see what would be posted. Off-CI, `--github` is a
  no-op with a clear note, so it is safe to leave in a pipeline.

## Surfaces

- `.github/actions/agentctl-gate/action.yml` - a **reusable composite action** for *other* repos:
  `setup-python` -> `pip install agentctl` -> `eval ingest` -> `gate --github`. Inputs:
  candidate/baseline JSONL, `pr`, `nim` (0.52 = superiority), `n-min`, `strict`, `token`.
- `.github/workflows/eval-gate.yml` - **dogfoods** the loop on this repo: on every `pull_request` it
  installs from source, gates the bundled demo suites, and posts the ALLOW/BLOCK status + comment to
  the PR. The PR that introduced this is its own first demonstration.

## Decision mapping

| gate result | exit code | commit status | merge |
|---|---|---|---|
| ALLOW (CI entirely at/above `nim`) | 0 | success | unblocked |
| INCONCLUSIVE, non-strict | 0 | success (description flags the uncertainty) | unblocked |
| INCONCLUSIVE, `--strict` | 1 | failure | blocked |
| BLOCK (CI entirely below `nim`) | 1 | failure | blocked |

## Boundaries (honest)

- **Fork PRs**: GitHub makes `GITHUB_TOKEN` read-only for PRs from forks, so the post is skipped
  there (the gate still runs and the job's exit status reflects the verdict). Same-repo branches get
  the full status + comment. A GitHub App token would lift this; out of scope for v1.
- The action installs `agentctl` from **PyPI**, so it is fully usable by third-party repos once the
  package is published; the in-repo dogfood workflow installs from source so it works today.
- This is the **commit-status** API (success/failure), not the richer **Check Runs** API (which adds
  a neutral conclusion + annotations). Commit status is sufficient to gate merge and needs no App.

## Verified

- `tests/test_github_gate.py` - the pure builders (env parsing, payload, markdown) + both POSTs
  through an injected opener (exact URL/method/headers/body for ALLOW and BLOCK, the no-PR comment
  no-op, and a GHE `api_url` override).
- End-to-end locally: ingested the demo `candidate.jsonl` vs `main.jsonl` and ran `agentctl gate --pr
  --dry-run` -> ALLOW -> `state: success`; the regression fixture -> BLOCK -> `state: failure`,
  exit 1.

## Update: Check Runs (`--check-run`)

`agentctl gate --check-run` additionally posts a GitHub **Check Run** - richer than a commit status:
a markdown **summary** (the per-suite Wilson-CI table) shows in the Checks tab, and the conclusion can
be **`neutral`** (the honest outcome for an INCONCLUSIVE/INSUFFICIENT gate that isn't a hard block) -
something a commit status (success/failure only) can't express. `check_conclusion` maps exit-code +
decision: non-zero -> failure, ALLOW -> success, otherwise -> neutral. It composes with `--github`
(status still gates merge); requires `checks: write` on the token. The dogfood workflow and the
reusable action both pass `--check-run`, so PRs get the rich check live.
