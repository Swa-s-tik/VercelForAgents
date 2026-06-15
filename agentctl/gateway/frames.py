"""Frame construction helpers shared by the agent and gateway (Vertical B)."""
from __future__ import annotations

from agentctl.gen import load

pb, _dp, _dpg, _cp, _cpg = load()


def new_text(src, seq: int, content: str, version: str, partial: bool = False):
    f = pb.Frame(session_id=src.session_id, stream_id=src.stream_id, seq=seq,
                 direction=pb.AGENT_TO_CLIENT,
                 text=pb.TextDelta(content=content, partial=partial))
    f.attributes["served_by"] = version
    return f


def new_turn_end(src, seq: int, reason, version: str):
    f = pb.Frame(session_id=src.session_id, stream_id=src.stream_id, seq=seq,
                 direction=pb.AGENT_TO_CLIENT,
                 turn_end=pb.TurnEnd(turn_id=f"{src.session_id}:{src.stream_id}", reason=reason))
    f.attributes["served_by"] = version
    return f


def client_text(session_id: str, stream_id: int, seq: int, content: str, attrs: dict | None = None):
    f = pb.Frame(session_id=session_id, stream_id=stream_id, seq=seq,
                 direction=pb.CLIENT_TO_AGENT, text=pb.TextDelta(content=content))
    for k, v in (attrs or {}).items():
        f.attributes[k] = str(v)
    return f


def client_control(session_id: str, stream_id: int, seq: int, kind, reason: str = ""):
    return pb.Frame(session_id=session_id, stream_id=stream_id, seq=seq,
                    direction=pb.CLIENT_TO_AGENT, control=pb.Control(kind=kind, reason=reason))


def shadow_copy(frame):
    """A copy of a client frame marked shadow=true (so a shadow agent mocks side-effects)."""
    g = pb.Frame()
    g.CopyFrom(frame)
    g.attributes["shadow"] = "true"
    return g
