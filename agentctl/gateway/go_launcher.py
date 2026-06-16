"""Launch & supervise the compiled Go data-plane gateway — the cutover from the Python
grpc.aio proxy (Milestone 1).

The Go binary reads its routing table from Postgres and LISTENs for ``routing_changed``, so the
Python control plane's flip transactions (Vertical C) update THIS live gateway. This module just
launches the process, health-checks it over gRPC, and tears it down — mirroring the runtime
pattern, so demos/push can run the Go engine programmatically.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import grpc

from agentctl.config import DEMO_PROJECT_ID, PG_DSN
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()

GATEWAY_BIN = Path(__file__).resolve().parents[1] / "gateway_core" / "bin" / "gateway"


def binary_available() -> bool:
    return GATEWAY_BIN.exists()


def _wait_health(endpoint: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with grpc.insecure_channel(endpoint) as ch:
                if dpg.AgentStreamStub(ch).Health(dp.HealthRequest(), timeout=1.0).ready:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def launch_go_gateway(port: int = 50050, pg_dsn: str = PG_DSN, project_id: str = DEMO_PROJECT_ID,
                      wait_healthy: bool = True, timeout: float = 10.0) -> subprocess.Popen:
    if not binary_available():
        raise FileNotFoundError(
            f"Go gateway not built: {GATEWAY_BIN}\n  build it: cd agentctl/gateway_core && make build")
    env = dict(os.environ)
    env.update({"AGENTCTL_PG_DSN": pg_dsn or "", "AGENTCTL_PROJECT_ID": project_id,
                "AGENTCTL_GW_PORT": str(port)})
    proc = subprocess.Popen([str(GATEWAY_BIN)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # proc.poll() guards against a stale process already holding the port (the binary would
    # fail to bind and exit, yet a straggler could still answer the health check).
    if wait_healthy and (not _wait_health(f"localhost:{port}", timeout) or proc.poll() is not None):
        stop(proc)
        raise RuntimeError(
            f"Go gateway failed to come up healthy on :{port} (exit={proc.poll()}; "
            f"is the port already in use?)")
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
