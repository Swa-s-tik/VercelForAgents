# dummy_vision_agent

An example agent you can ship with one command:

```bash
cd examples/dummy_vision_agent
agentctl push                      # -> ✅ PR MERGED (live URL)
agentctl push --simulate-regression  # -> ⛔ PR BLOCKED (inferior agent)
```

`agentctl push` packages this directory, provisions an isolated preview via the webhook
emulator, streams the SPRT sequential eval live (win/loss/tie + Wilson CI narrowing), persists
to DuckDB, and merges or blocks based on the statistical gate. Requires the Postgres container:
`docker compose -f ../../deploy/docker-compose.yml up -d postgres`.

- `prompt.yaml` — agent metadata + eval config (`win_rate` drives the simulated quality).
- `agent.py` — the mock execution script (bottle detection).
