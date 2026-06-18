"""gRPC server interceptor enforcing API keys on the Python reference proxy (Workstream 2).

Reads ``x-api-key`` from call metadata. Permissive by default (no key -> pass through) so the demo
and existing gateway tests need no key; set ``AGENTCTL_REQUIRE_KEY=1`` to make a key mandatory. On
an invalid/missing-but-required key it returns a same-cardinality handler that aborts
UNAUTHENTICATED. The compiled Go gateway is the default data plane and carries its own
(wired-but-permissive) interceptor; this protects the Python proxy path.
"""
from __future__ import annotations

import os

import grpc

from agentctl.auth.principal import AuthError, resolve_principal


def _deny(handler, message: str):
    """Return a handler matching ``handler``'s cardinality that aborts UNAUTHENTICATED."""
    async def abort_unary(request, context):
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, message)

    async def abort_stream(request_iter, context):
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, message)

    if handler.request_streaming and handler.response_streaming:
        return grpc.aio.stream_stream_rpc_method_handler(abort_stream)
    if handler.request_streaming:
        return grpc.aio.stream_unary_rpc_method_handler(abort_stream)
    if handler.response_streaming:
        return grpc.aio.unary_stream_rpc_method_handler(abort_unary)
    return grpc.aio.unary_unary_rpc_method_handler(abort_unary)


class ApiKeyServerInterceptor(grpc.aio.ServerInterceptor):
    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None:
            return None
        md = dict(handler_call_details.invocation_metadata or ())
        key = md.get("x-api-key")
        if not key:
            if os.environ.get("AGENTCTL_REQUIRE_KEY") == "1":
                return _deny(handler, "API key required (AGENTCTL_REQUIRE_KEY=1)")
            return handler  # permissive default
        try:
            from agentctl.common.db import connect
            conn = connect()
            try:
                resolve_principal(conn, key)
            finally:
                conn.close()
        except AuthError as e:
            return _deny(handler, str(e))
        except Exception:
            return handler  # fail-open on auth-backend errors (availability over strictness)
        return handler
