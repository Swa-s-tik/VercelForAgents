"""Mock vision agent — bottle detection.

The platform hosts this in an isolated preview during `agentctl push`; here it is a stand-in
that returns fake detections so the push has a real execution artifact to package and deploy.
Run it directly to see sample output: `python agent.py`.
"""
from __future__ import annotations

import random

LABELS = ["bisleri", "kinley", "aquafina", "bottle"]


def run(frame: bytes) -> list[dict]:
    """Pretend to detect bottles in a frame; returns mock boxes + confidence scores."""
    n = 1 + (len(frame) % 3)
    return [{"label": random.choice(LABELS),
             "score": round(0.70 + 0.29 * random.random(), 3),
             "bbox": [random.randint(0, 200), random.randint(0, 200), 64, 128]}
            for _ in range(n)]


if __name__ == "__main__":
    dets = run(b"\x00" * 1024)
    print(f"dummy-vision-agent: {len(dets)} detection(s) -> {dets}")
