"""Mock customer-support agent — streams a reply token-by-token and issues a refund.

The platform hosts the streaming version (agentctl/agents/support_agent.py) in the isolated
preview during `agentctl push`; this is the packaged source artifact. Run it to see a turn:
`python agent.py`.
"""
from __future__ import annotations

import time

REPLY = ("I'm sorry about the trouble with your order. I've checked the details and "
         "I'll process a refund for you right away.")


def respond(message: str):
    """Yield streamed events: chunked text deltas, then an issue_refund tool call."""
    for token in REPLY.split(" "):
        time.sleep(0.02)
        yield {"type": "text", "delta": token + " "}
    yield {"type": "tool_call", "tool": "issue_refund",
           "args": {"order_id": "A-2291", "amount": 29.99}}


if __name__ == "__main__":
    for event in respond("where is my refund?"):
        print(event)
