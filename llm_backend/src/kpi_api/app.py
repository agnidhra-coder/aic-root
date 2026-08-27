"""The HTTP surface: provision a company, give it data, ask it questions.

A third view of one run. `run_agent`, `POST /ask` and `POST /ask/sync` all drain
`stream_agent`, so the terminal and the wire cannot report different numbers for
the same question -- the same guarantee `render_console` has against
`render_markdown`, extended one layer out.

Every route below `/companies/{company}` is scoped to one tenant by its path
segment rather than by a body field. That keeps `AskRequest` about the question,
gives `GET .../runs/{run_id}` a company to resolve against, and lets FastAPI
reject an unknown tenant with a 404 before the body is even parsed. The
traversal guard is stronger for it: `_artefact` now roots three levels deeper,
inside the company's own outputs.

There is still no authentication here, and `GET /companies` lists every tenant
while `POST /companies` writes to disk. This service is meant to sit behind the
NestJS tier on a private network; `__main__` binds 127.0.0.1 for that reason.

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

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from kpi_agent.llm import DEFAULT_MODEL, LlmUnavailable, build_llm
from kpi_engine import SCHEMA_VERSION
from kpi_engine.cli.list_companies import describe as describe_company
from kpi_engine.config_io import project_root
from kpi_engine.provisioning import (
    CompanyExists,
    DataRejected,
    ProvisioningError,
    attach_source_data,
    create_company,
    list_templates,
)
from kpi_engine.tenancy import (
    CompanyConfigMissing,
    CompanyPaths,
    InvalidRunId,
    UnknownCompany,
    list_companies,
    open_company,
    user_root,
)

from kpi_api import logbus
from kpi_api.events import Event, ask_events, collapse
from kpi_api.models import AskRequest, CreateCompanyRequest

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
            "user_root": str(user_root()),
            "companies": len(_safe_company_list()),
        }

    @app.get("/companies")
    def get_companies(include_inactive: bool = False) -> list[dict[str, Any]]:
        """Every tenant, as the CLI's `list_companies --json` reports them.

        This is what the NestJS tier calls to resolve a Supabase user to a folder
        and to confirm a company exists before enqueuing an analysis.
        """
        try:
            entries = list_companies(active_only=not include_inactive)
        except CompanyConfigMissing:
            return []
        return [describe_company(e.company_id) for e in entries]

    @app.post("/companies", status_code=201)
    def post_company(req: CreateCompanyRequest) -> dict[str, Any]:
        """Provision a tenant. It is created before it has data -- see the model."""
        try:
            paths = create_company(
                req.company_id,
                req.display_name,
                req.domains,
                template=req.template,
                agent=req.agent,
                supabase_user_ids=req.supabase_user_ids,
                notes=req.notes,
            )
        except CompanyExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProvisioningError as exc:
            # Includes an unknown template and an already-claimed supabase id:
            # both are the caller's input, not a server fault.
            raise HTTPException(
                status_code=422,
                detail=f"{exc}  (templates: {list_templates()})",
            ) from exc
        return describe_company(paths.slug)

    @app.get("/companies/{company}")
    def get_company(paths: CompanyPaths = Depends(_company)) -> dict[str, Any]:
        return describe_company(paths.slug)

    @app.post("/companies/{company}/sources/{source_id}/data")
    async def post_source_data(
        source_id: str,
        file: UploadFile = File(...),
        paths: CompanyPaths = Depends(_company),
    ) -> dict[str, Any]:
        """Attach a CSV to a declared source.

        The NestJS tier already holds the uploaded buffer, so it forwards rather
        than re-downloading from storage. The file is staged and validated before
        it replaces anything, so a rejected upload leaves the previous data intact.
        """
        if source_id not in paths.spec.source_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Company '{paths.slug}' declares no source '{source_id}'. "
                f"Known: {paths.spec.source_ids}",
            )
        content = await file.read()
        try:
            updated, warnings = await asyncio.to_thread(
                attach_source_data, paths, source_id, content=content
            )
        except DataRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProvisioningError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**describe_company(updated.slug), "warnings": warnings}

    @app.post("/companies/{company}/ask")
    async def ask(
        req: AskRequest, request: Request, paths: CompanyPaths = Depends(_company)
    ) -> StreamingResponse:
        _require_data(paths)

        async def body() -> AsyncIterator[bytes]:
            async with app.state.runs:
                async for name, payload in _run(req, paths):
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

    @app.post("/companies/{company}/ask/sync")
    async def ask_sync(
        req: AskRequest, paths: CompanyPaths = Depends(_company)
    ) -> dict[str, Any]:
        """The same events, folded into one object.

        Built from the stream rather than beside it, so there is no second
        projection to keep in step.
        """
        _require_data(paths)
        async with app.state.runs:
            events = [(n, p) async for n, p in _run(req, paths) if n]
        return collapse(events)

    @app.get("/companies/{company}/runs/{run_id}")
    def get_run(run_id: str, paths: CompanyPaths = Depends(_company)) -> Any:
        return json.loads(_artefact(paths, run_id, "agent_report.json").read_text())

    @app.get("/companies/{company}/runs/{run_id}/report", response_class=PlainTextResponse)
    def get_report(run_id: str, paths: CompanyPaths = Depends(_company)) -> str:
        return _artefact(paths, run_id, "agent_report.md").read_text()

    return app


def _safe_company_list() -> list[Any]:
    """The registry may legitimately not exist yet; /health must still answer."""
    try:
        return list_companies()
    except CompanyConfigMissing:
        return []


def _company(company: str) -> CompanyPaths:
    """Resolve the path segment to a workspace, or 404.

    `company` cannot reach the filesystem as `../..`: `CompanySlug`'s pattern
    rejects it when the registry is parsed, so an unregistered value never gets
    as far as being joined to a path.
    """
    try:
        return open_company(company)
    except (UnknownCompany, CompanyConfigMissing) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_data(paths: CompanyPaths) -> None:
    """Refuse to analyse a company whose files are not there yet.

    Running anyway would detect nothing and report a quiet period -- a wrong
    answer that looks like a right one, which is exactly what this engine's
    abstention rules exist to prevent.
    """
    missing = paths.missing_datasets()
    if missing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Company '{paths.slug}' is awaiting data for "
                f"{sorted(missing)}. POST a CSV to "
                f"/companies/{paths.slug}/sources/<source_id>/data first."
            ),
        )


def _artefact(paths: CompanyPaths, run_id: str, name: str) -> Path:
    """Locate one artefact of a finished run, refusing to leave the company."""
    try:
        path = paths.artefact(run_id, name)
    except InvalidRunId as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No {name} for run '{run_id}' of '{paths.slug}'. "
            "It may still be running.",
        )
    return path


async def _run(
    req: AskRequest, paths: CompanyPaths
) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
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
                for name, payload in ask_events(req, llm, paths=paths):
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
