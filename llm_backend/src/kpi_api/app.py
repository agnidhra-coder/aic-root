"""The HTTP surface: `ask`, streamed.

A third view of one run. `run_agent`, `POST /ask` and `POST /ask/sync` all drain
`stream_agent`, so the terminal and the wire cannot report different numbers for
the same question -- the same guarantee `render_console` has against
`render_markdown`, extended one layer out.

The awkward part is that the agent is entirely synchronous and mostly CPU-bound
-- pandas, statsmodels, ruptures -- with two blocking model calls of up to 150s
each. So a run happens on a worker thread and reaches the event loop through a
queue, and the response is kept alive across the long silences in between.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from kpi_agent.llm import DEFAULT_MODEL, LlmUnavailable, build_llm
from kpi_engine import SCHEMA_VERSION
from kpi_engine.config_io import project_root

from kpi_api import logbus
from kpi_api.events import Event, ask_events, collapse
from kpi_api.models import AskRequest

log = logging.getLogger("kpi_api")

# Each run is CPU-heavy and writes its own outputs/<run_id>/, so they do not
# contend for artefacts -- but they do contend for cores, and four concurrent
# pipelines are four times as slow rather than four at once.
MAX_CONCURRENT_RUNS = int(os.environ.get("KPI_API_MAX_CONCURRENT_RUNS", "2"))

# Between "computing KPIs at week grain" and the counts there can be minutes of
# silence, and a proxy will drop an idle connection well before that.
KEEPALIVE_SECONDS = 15.0

_DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:3001"


def _sse(name: str, payload: dict[str, Any]) -> bytes:
    """Encode one event.

    Deliberately no `default=` fallback. Every payload out of `events.py` is
    already plain JSON, so anything that needs coercing is a projection bug --
    and a bug that stringifies a `PipelineResult` into `"<object at 0x...>"` and
    ships it is worse than one that fails.
    """
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


def create_app() -> FastAPI:
    app = FastAPI(
        title="KPI engine",
        version=SCHEMA_VERSION,
        description=(
            "Ask a question in plain language. Two model calls sit at the ends of "
            "the run -- one turns the question into a validated pipeline "
            "configuration, one writes the prose -- and everything that produces "
            "a number between them is deterministic."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            o.strip()
            for o in os.environ.get("CORS_ORIGIN", _DEFAULT_ORIGINS).split(",")
            if o.strip()
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logbus.install()
    app.state.runs = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": SCHEMA_VERSION,
            "default_model": DEFAULT_MODEL,
            # Whether a key is present, never the key. `--no-llm` works without one.
            "api_key_present": bool(os.environ.get("GOOGLE_API_KEY"))
            or (project_root() / ".env").exists(),
            "max_concurrent_runs": MAX_CONCURRENT_RUNS,
        }

    @app.post("/ask")
    async def ask(req: AskRequest, request: Request) -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            async with app.state.runs:
                async for name, payload in _run(req):
                    if await request.is_disconnected():
                        # The worker runs to completion regardless and still
                        # writes outputs/<run_id>/, so the answer stays
                        # recoverable from GET /runs/{run_id}. Pretending this
                        # cancels the run would be the lie.
                        log.info("client disconnected; run continues to disk")
                        return
                    yield _sse(name, payload) if name else b": keepalive\n\n"

        return StreamingResponse(
            body(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/ask/sync")
    async def ask_sync(req: AskRequest) -> dict[str, Any]:
        """The same events, folded into one object.

        Built from the stream rather than beside it, so there is no second
        projection to keep in step.
        """
        async with app.state.runs:
            events = [(n, p) async for n, p in _run(req) if n]
        return collapse(events)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> Any:
        return json.loads(_artefact(run_id, "agent_report.json").read_text())

    @app.get("/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str) -> str:
        return _artefact(run_id, "agent_report.md").read_text()

    return app


def _artefact(run_id: str, name: str) -> Path:
    """Locate one artefact of a finished run, refusing to leave `outputs/`."""
    root = (project_root() / "outputs").resolve()
    path = (root / run_id / name).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Invalid run id.")
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No {name} for run '{run_id}'. It may still be running.",
        )
    return path


async def _run(req: AskRequest) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
    """Drive one run on a worker thread, yielding its events as they arrive.

    A `None` name is a keepalive rather than an event.
    """
    llm = None
    if not req.no_llm:
        try:
            llm = build_llm(req.model)
        except LlmUnavailable as exc:
            # A missing key must not cost the analysis. The deterministic path
            # still runs and still reports; it just reports in plainer prose.
            log.info("no model available (%s); running the deterministic path", exc)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    def emit(name: str, payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (name, payload))

    def worker() -> None:
        # Set inside the thread: this is what keeps two concurrent runs' log
        # lines from arriving in each other's streams.
        sink = (lambda record: emit("log", record)) if req.logs else None
        try:
            with logbus.capturing(sink):
                for name, payload in ask_events(req, llm):
                    emit(name, payload)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = loop.run_in_executor(None, worker)
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield None, {}
                continue
            if item is None:
                return
            yield item
    finally:
        # Never abandon the thread silently: a half-written outputs/ directory
        # with nothing waiting on it is how a run becomes untraceable.
        await task


app = create_app()
