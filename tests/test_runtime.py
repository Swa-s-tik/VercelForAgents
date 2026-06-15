"""Runtime isolation lifecycle tests (Phase 1). Runs under pytest or as a plain script.
ProcessRuntime always runs; DockerRuntime runs only if a docker daemon is reachable."""
from __future__ import annotations

import time

from agentctl.runtime.isolated import (
    DockerRuntime,
    ProcessRuntime,
    RuntimeSpec,
    docker_available,
    free_port,
    provision_preview,
)


def test_process_runtime_lifecycle():
    rt = ProcessRuntime()
    handle = provision_preview(rt, RuntimeSpec(name="proc-test", port=free_port(), version_tag="vA"))
    assert handle.kind == "process"
    assert rt.health(handle)                       # gRPC Health says ready
    rt.teardown(handle)
    time.sleep(0.5)
    assert handle.ref.poll() is not None           # process actually gone
    print(f"  process runtime: provisioned {handle.endpoint}, healthy, torn down")


def test_docker_runtime_lifecycle():
    if not docker_available():
        print("  docker unavailable — skipping DockerRuntime test")
        return
    rt = DockerRuntime()
    handle = rt.provision(RuntimeSpec(name="smoke", image="busybox", cmd=["sleep", "60"]))
    try:
        assert handle.kind == "docker"
        assert rt.health(handle)                   # container is Running
        print(f"  docker runtime: container {handle.id} running")
    finally:
        rt.teardown(handle)
    # confirm gone
    import subprocess
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", handle.ref],
                       capture_output=True, text=True)
    assert r.returncode != 0 or r.stdout.strip() != "true"
    print("  docker runtime: torn down")


if __name__ == "__main__":
    test_process_runtime_lifecycle()
    test_docker_runtime_lifecycle()
    print("runtime isolation tests passed")
