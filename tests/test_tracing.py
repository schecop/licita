"""The Langfuse exporter: OTLP body and auth, and a failed export never raises. No network."""

from __future__ import annotations

import asyncio

import httpx

from licita.tracing import LangfuseExporter, Trace


def test_a_trace_is_posted_once_as_otlp_with_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    client = httpx.AsyncClient(base_url="http://lf", transport=httpx.MockTransport(handler))
    trace = Trace("tender", tender_id="T-1")
    with trace.span("screening", cpv="72000000"):
        pass
    trace.finish(decision="go")
    assert asyncio.run(LangfuseExporter("http://lf", "pk", "sk", client=client).export(trace))
    [req] = seen
    assert req.url.path == LangfuseExporter.PATH
    assert req.headers["Authorization"].startswith("Basic ")
    assert b"resourceSpans" in req.content and b"licita" in req.content


def test_a_failed_export_returns_false() -> None:
    client = httpx.AsyncClient(
        base_url="http://lf", transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    assert not asyncio.run(
        LangfuseExporter("http://lf", "pk", "sk", client=client).export(Trace("t"))
    )
