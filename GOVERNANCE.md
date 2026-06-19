# Governance

agentctl is an open-source project under the Apache-2.0 license. This document
describes how decisions are made and how to become a contributor or maintainer.

## Roles

- **Contributors** open issues and pull requests. Anyone can be a contributor.
- **Maintainers** review and merge pull requests, triage issues, and steer the
  roadmap. They have write access to the repository.

Current maintainers:

- @Swa-s-tik

## How decisions are made

- **Day-to-day changes** (bug fixes, docs, additive features behind a flag) are
  decided by the reviewing maintainer via the normal pull-request process: at
  least one maintainer approval and green CI.
- **Significant changes** (anything touching the frozen wire contract, the
  `StateStore` protocol, the auth model, or a breaking API change) require a
  design note under `docs/design/` and explicit sign-off from a maintainer
  before implementation. The frozen `Frame` header (fields 1-4) is governed by
  semantic versioning and is not changed without a major-version bump.
- **Disagreements** are resolved by discussion in the issue or PR. If consensus
  cannot be reached, the maintainers decide; ties are broken by the project lead.

## Becoming a maintainer

A contributor who has landed several substantial, well-tested pull requests and
shown good review judgment may be invited by the existing maintainers to become
a maintainer. There is no fixed quota.

## Releases

Releases follow semantic versioning. A maintainer cuts a release by tagging
`vX.Y.Z`, updating `CHANGELOG.md`, and publishing a GitHub Release. The wire,
`StateStore`, and auth contracts are the stable surfaces covered by SemVer.

## Code of Conduct

All participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
