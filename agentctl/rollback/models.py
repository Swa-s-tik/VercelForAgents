"""Dataclasses for the checkpoint manifest (Vertical C)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Pointer:
    """One tracked external-state mutation, with its restore coordinate."""

    mutation_class: str       # vector_store | relational_schema | memory_graph | side_effect
    reversibility: str        # reversible | forward_fix | irreversible
    store_id: str
    coordinate: dict
    state_digest: str | None = None
    strategy: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Pointer":
        return cls(
            mutation_class=d["mutation_class"], reversibility=d["reversibility"],
            store_id=d["store_id"], coordinate=d.get("coordinate") or {},
            state_digest=d.get("state_digest"), strategy=d.get("strategy"),
        )


@dataclass
class Manifest:
    """The contract binding a git commit to the external-state coordinates it produced."""

    git_commit_sha: str
    deployment_id: int
    pointers: list[Pointer] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "schema_version": 1,
            "git_commit_sha": self.git_commit_sha,
            "deployment_id": self.deployment_id,
            "pointers": [p.to_dict() for p in self.pointers],
        }
