# support_agent

A streaming, tool-calling customer-support agent — the flagship example.

```bash
docker compose up -d postgres                 # from repo root
cd examples/support_agent
agentctl push
```

One `agentctl push` proves the whole platform:
- **Eval-Gate** scores the agent's text/tool quality (live SPRT, Wilson CI).
- **Go data plane** streams the reply token-by-token (chunked `TextDelta` frames) back to the
  client *incrementally* — no buffering into one giant block.
- **Sandbox** intercepts the agent's `issue_refund` `ToolCall` in preview (no real refund).
- **Stateful Rollback** records `issue_refund` as a side-effect in the deployment checkpoint.

Files: `prompt.yaml` (agent + eval config), `agent.py` (the mock execution script).
