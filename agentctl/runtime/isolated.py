"""Isolated preview-agent runtime abstraction (Phase 1).

A clean programmatic layer replacing ad-hoc script launches. One protocol, two backends:
  * ProcessRuntime — spins the agent up in a child PROCESS (no Docker needed; the default).
  * DockerRuntime  — spins it up in a container via the docker CLI (no docker SDK needed).
Both provision -> health-check -> teardown. Health uses the AgentStream.Health gRPC for agents,
or container/port liveness for opaque images. When a (mock) deployment is registered, the
control plane provisions an isolated environment through this layer.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Protocol

import grpc

from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()


@dataclass
class RuntimeSpec:
    name: str
    port: int = 0
    version_tag: str = "vA"
    kind: str = "echo"                    # agent kind for ProcessRuntime: echo | support
    image: str | None = None              # DockerRuntime only
    cmd: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)


@dataclass
class RuntimeHandle:
    id: str
    kind: str
    port: int
    endpoint: str
    ref: object = None                    # Popen (process) or container name (docker)
    meta: dict = field(default_factory=dict)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def grpc_agent_health(endpoint: str, timeout: float = 8.0) -> bool:
    """Poll AgentStream.Health until ready or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with grpc.insecure_channel(endpoint) as ch:
                reply = dpg.AgentStreamStub(ch).Health(
                    dp.HealthRequest(deployment_id=endpoint), timeout=1.0)
                if reply.ready:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


class IsolatedRuntime(Protocol):
    kind: str
    def provision(self, spec: RuntimeSpec) -> RuntimeHandle: ...
    def health(self, handle: RuntimeHandle) -> bool: ...
    def teardown(self, handle: RuntimeHandle) -> None: ...


class ProcessRuntime:
    """Isolated agent in a child process. Default — works with zero container setup."""
    kind = "process"

    def provision(self, spec: RuntimeSpec) -> RuntimeHandle:
        port = spec.port or free_port()
        cmd = [sys.executable, "-m", "agentctl.cli", "agent",
               "--kind", spec.kind, "--tag", spec.version_tag, "--port", str(port)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return RuntimeHandle(id=f"proc-{proc.pid}", kind="process", port=port,
                             endpoint=f"localhost:{port}", ref=proc,
                             meta={"version_tag": spec.version_tag})

    def health(self, handle: RuntimeHandle) -> bool:
        return grpc_agent_health(handle.endpoint, timeout=10.0)

    def teardown(self, handle: RuntimeHandle) -> None:
        proc = handle.ref
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


class DockerRuntime:
    """Isolated environment in a Docker/Podman container via the CLI (no SDK dependency)."""
    kind = "docker"

    def __init__(self, runner: str = "docker"):
        self.runner = runner

    def provision(self, spec: RuntimeSpec) -> RuntimeHandle:
        name = f"agentctl-rt-{spec.name}"
        subprocess.run([self.runner, "rm", "-f", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cmd = [self.runner, "run", "-d", "--rm", "--name", name]
        if spec.port:
            cmd += ["-p", f"{spec.port}:{spec.port}"]
        for k, v in spec.env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [spec.image or "busybox"] + list(spec.cmd)
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        cid = out.stdout.strip()
        return RuntimeHandle(id=cid[:12], kind="docker", port=spec.port,
                             endpoint=f"localhost:{spec.port}" if spec.port else "",
                             ref=name, meta={"image": spec.image})

    def health(self, handle: RuntimeHandle) -> bool:
        r = subprocess.run([self.runner, "inspect", "-f", "{{.State.Running}}", handle.ref],
                           capture_output=True, text=True)
        running = r.stdout.strip() == "true"
        if running and handle.port:
            return _tcp_open("localhost", handle.port, 2.0) or running
        return running

    def teardown(self, handle: RuntimeHandle) -> None:
        subprocess.run([self.runner, "rm", "-f", handle.ref],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def provision_preview(runtime: IsolatedRuntime, spec: RuntimeSpec) -> RuntimeHandle:
    """Provision and block until healthy; raise if it never comes up."""
    handle = runtime.provision(spec)
    if not runtime.health(handle):
        runtime.teardown(handle)
        raise RuntimeError(f"preview {spec.name} failed health check on {handle.endpoint or handle.id}")
    return handle


def default_runtime() -> IsolatedRuntime:
    return ProcessRuntime()
