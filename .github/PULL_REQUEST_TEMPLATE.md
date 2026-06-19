## What

A short description of the change and why.

## How it was verified

- [ ] `python -m pytest -q` is green
- [ ] `cd agentctl/gateway_core && make conformance` is green (if the data plane / proto changed)
- [ ] New behavior has tests
- [ ] Docs updated (`README.md` / `docs/design/*` / `CHANGELOG.md` as relevant)

## Contract impact

- [ ] Does not change the frozen `Frame` header (fields 1-4), the `StateStore` protocol, or the auth model
- [ ] If it does: a design note under `docs/design/` is included and a maintainer has signed off (see GOVERNANCE.md)

## Notes for reviewers
