"""The HTTP surface: provision a company, give it data, ask it questions.

A third view of one run. `run_agent`, `POST /ask` and `POST /ask/sync` all drain
`stream_agent`, so the terminal and the wire cannot report different numbers for
the same question -- the same guarantee `render_console` has against
`render_markdown`, extended one layer out.

**Nothing is addressed by a path segment.** Every path here is a fixed literal,
and every selector -- `company`, `source_id`, `run_id` -- is a query parameter:
`GET /run?company=acme-retail&run_id=nightly`. A client builds one constant
string and varies a parameter dict, rather than assembling URLs by
interpolation. `company` is resolved once by the `_company` dependency; the
other two are declared as `SourceId` and `RunId` below, so their descriptions
and failure modes are written once for every route that takes them.

They are query parameters rather than body fields because three of these routes
carry a *file* in the body and could not be scoped by one. `company` stays out
of the body for the same reason it always did: it keeps `AskRequest` about the
question, and it gives `GET /run` a company to resolve against.

FastAPI solves sub-dependencies before it validates params or body, so an
unknown tenant is still a 404 raised before the body is parsed. A missing
selector is a 422 naming it, where the old path shapes made it a route miss --
refused by name rather than by the router failing to find a match.

`run_id` is the one selector that reaches the filesystem, and moving it out of
the path strengthens its guard rather than weakening it: it now arrives already
decoded, so `_artefact` sees the `../..` a client actually sent instead of
whatever survived path normalisation, and refuses it with a 400.

There is still no authentication of individual callers -- no user, no scoping
-- and `GET /companies` lists every tenant while `POST /companies` writes to
disk. This service is meant to sit behind the NestJS tier on a private
network; `__main__` binds 127.0.0.1 for that reason. Where it must be
reachable over the public internet instead (a deployed demo, `server` and this
service on different hosts), `KPI_API_SHARED_SECRET` gates every route but
`/health` behind one shared header -- coarse, "is this NestJS or not", not
per-user auth.

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
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

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
    reregister_company,
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

from pydantic import ValidationError

from kpi_api import logbus
from kpi_api.events import Event, ask_events, collapse, confirm_events, plan_events
from kpi_api.models import (
    AskRequest,
    ConfirmPlanRequest,
    CreateCompanyRequest,
    PlanKpisRequest,
)

log = logging.getLogger("kpi_api")

# Each run is CPU-heavy and writes its own outputs/<run_id>/, so they do not
# contend for artefacts -- but they do contend for cores, and four concurrent
# pipelines are four times as slow rather than four at once.
MAX_CONCURRENT_RUNS = int(os.environ.get("KPI_API_MAX_CONCURRENT_RUNS", "2"))

# Between "computing KPIs at week grain" and the counts there can be minutes of
# silence, and a proxy will drop an idle connection well before that.
KEEPALIVE_SECONDS = 15.0

_DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:3001"

# `X-Accel-Buffering` is what stops nginx holding a stage's event until the
# response is large enough to be worth flushing, which defeats the whole point.
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# The two selectors that are not the company. Declared once so every route that
# takes one carries the same documentation, and so `/docs` explains them rather
# than showing a bare string.
SourceId = Annotated[
    str,
    Query(
        description="Which of the company's declared sources. Never a filename: "
        "an id the company's `company.yaml` names, and a 404 if it does not.",
    ),
]
RunId = Annotated[
    str,
    Query(
        description="Names a directory under the company's outputs/. Refused "
        "with a 400 if it resolves outside them.",
    ),
]


class _RequireSharedSecret(BaseHTTPMiddleware):
    """Reject every request missing `X-KPI-Api-Key`, when a secret is configured.

    This service has no authentication by design when reached over a private
    network (see the module docstring), but a deployed instance is reachable
    from the public internet, and `POST /companies` writes to disk while
    `GET /companies`/`GET /company` read tenant data -- a public, unauthenticated
    instance would let anyone provision or read another judge's company. Unset
    `KPI_API_SHARED_SECRET` (the local, behind-NestJS deployment) is a no-op, so
    nothing changes for a judge running this on localhost per RUNNING.md.
    """

    def __init__(self, app: Any, secret: str) -> None:
        super().__init__(app)
        self._secret = secret

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if request.url.path == "/health":
            return await call_next(request)
        if request.headers.get("x-kpi-api-key") != self._secret:
            return JSONResponse({"detail": "Missing or invalid X-KPI-Api-Key"}, status_code=401)
        return await call_next(request)


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
    secret = os.environ.get("KPI_API_SHARED_SECRET", "").strip()
    if secret:
        app.add_middleware(_RequireSharedSecret, secret=secret)

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
            # Two different things raise this: the slug is already registered
            # (a real conflict), or its folder is on disk but the registry
            # entry was lost independently -- `metadata.yaml` reset without
            # `user/` being touched. Only the second is self-healing: closing
            # a registry gap for a folder that already validates, never
            # touching its data. `reregister_company` re-raises `CompanyExists`
            # itself when the slug turns out to already be registered after
            # all, so that path still surfaces the original 409 untouched.
            try:
                paths = reregister_company(req.company_id)
            except ProvisioningError:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProvisioningError as exc:
            # Includes an unknown template and an already-claimed supabase id:
            # both are the caller's input, not a server fault.
            raise HTTPException(
                status_code=422,
                detail=f"{exc}  (templates: {list_templates()})",
            ) from exc
        return describe_company(paths.slug)

    @app.get("/company")
    def get_company(paths: CompanyPaths = Depends(_company)) -> dict[str, Any]:
        """One tenant. Singular, because `GET /companies` is already the list."""
        return describe_company(paths.slug)

    @app.post("/sources/data")
    async def post_source_data(
        source_id: SourceId,
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

    @app.post("/ask")
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
                        # recoverable from GET /run?run_id=... Pretending this
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

    @app.post("/kpi-plan")
    async def plan_kpis(
        request: Request,
        file: UploadFile = File(...),
        source_id: str | None = None,
        model: str | None = None,
        no_llm: bool = False,
        plan_id: str | None = None,
        logs: bool = True,
        paths: CompanyPaths = Depends(_company),
    ) -> StreamingResponse:
        """Propose a KPI configuration from an uploaded CSV.

        The upload is *staged*, not accepted: it lands outside every declared
        source path, so the company stays `awaiting_data` and `/ask` keeps
        refusing it until a plan is confirmed. A tenant is never briefly
        answerable against a contract that does not match its data.

        Streams because profiling a wide extract takes minutes, and a silent
        socket is indistinguishable from a hung one.
        """
        req = _plan_request(source_id, model, no_llm, plan_id, logs)
        if req.source_id and req.source_id not in paths.spec.source_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Company '{paths.slug}' declares no source '{req.source_id}'. "
                f"Known: {paths.spec.source_ids}",
            )
        content = await file.read()

        async def body() -> AsyncIterator[bytes]:
            async with app.state.runs:
                llm = _maybe_llm(req.no_llm, req.model)
                async for name, payload in _pump(
                    lambda: plan_events(req, llm, paths=paths, content=content),
                    logs=req.logs,
                ):
                    if await request.is_disconnected():
                        log.info("client disconnected; the draft is still written")
                        return
                    yield _sse(name, payload) if name else b": keepalive\n\n"

        return StreamingResponse(body(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/kpi-plan/sync")
    async def plan_kpis_sync(
        file: UploadFile = File(...),
        source_id: str | None = None,
        model: str | None = None,
        no_llm: bool = False,
        plan_id: str | None = None,
        paths: CompanyPaths = Depends(_company),
    ) -> dict[str, Any]:
        """The same events, folded into one object. What the NestJS tier calls."""
        req = _plan_request(source_id, model, no_llm, plan_id, logs=False)
        content = await file.read()
        async with app.state.runs:
            llm = _maybe_llm(req.no_llm, req.model)
            events = [
                (n, p)
                async for n, p in _pump(
                    lambda: plan_events(req, llm, paths=paths, content=content),
                    logs=False,
                )
                if n
            ]
        return collapse(events)

    @app.get("/kpi-plan")
    def get_kpi_plan(paths: CompanyPaths = Depends(_company)) -> dict[str, Any]:
        """The current draft, so a caller can resume a handshake it did not start."""
        from kpi_engine.onboarding import UnknownPlan, load_draft

        try:
            return load_draft(paths).model_dump(mode="json")
        except UnknownPlan as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/kpi-plan/confirm")
    async def confirm_kpi_plan(
        req: ConfirmPlanRequest,
        request: Request,
        paths: CompanyPaths = Depends(_company),
    ) -> StreamingResponse:
        """Commit a decided plan: write the configs, accept the data, warm up.

        The merge and the synthesis run *here*, before the response starts, so a
        decision that does not validate is a 422 with nothing written rather than
        an error event arriving inside a 200. Everything after that touches disk
        and takes minutes, which is what the stream is for.
        """
        prepared = _prepare(paths, req)

        async def body() -> AsyncIterator[bytes]:
            async with app.state.runs:
                llm = _maybe_llm(req.no_llm, req.model)
                async for name, payload in _pump(
                    lambda: confirm_events(req, llm, paths=paths, prepared=prepared),
                    logs=req.logs,
                ):
                    if await request.is_disconnected():
                        # The worker runs to completion regardless: the configs
                        # land and the warm-up still writes outputs/<run_id>/.
                        log.info("client disconnected; the run continues to disk")
                        return
                    yield _sse(name, payload) if name else b": keepalive\n\n"

        return StreamingResponse(body(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.post("/kpi-plan/confirm/sync")
    async def confirm_kpi_plan_sync(
        req: ConfirmPlanRequest, paths: CompanyPaths = Depends(_company)
    ) -> dict[str, Any]:
        prepared = _prepare(paths, req)
        async with app.state.runs:
            llm = _maybe_llm(req.no_llm, req.model)
            events = [
                (n, p)
                async for n, p in _pump(
                    lambda: confirm_events(req, llm, paths=paths, prepared=prepared),
                    logs=False,
                )
                if n
            ]
        return collapse(events)

    @app.get("/run")
    def get_run(run_id: RunId, paths: CompanyPaths = Depends(_company)) -> Any:
        return json.loads(_artefact(paths, run_id, "agent_report.json").read_text())

    @app.get("/run/report", response_class=PlainTextResponse)
    def get_report(run_id: RunId, paths: CompanyPaths = Depends(_company)) -> str:
        return _artefact(paths, run_id, "agent_report.md").read_text()

    return app


def _plan_request(
    source_id: str | None,
    model: str | None,
    no_llm: bool,
    plan_id: str | None,
    logs: bool,
) -> PlanKpisRequest:
    """Build and validate the plan request from query parameters.

    Assembled rather than declared as a body model because the CSV occupies the
    body. Going through the model keeps `plan_id`'s pattern -- it becomes a
    directory name under `configs/_superseded/` -- enforced in one place.
    """
    try:
        return PlanKpisRequest(
            source_id=source_id, model=model, no_llm=no_llm,
            plan_id=plan_id, logs=logs,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _prepare(paths: CompanyPaths, req: ConfirmPlanRequest) -> Any:
    """Merge and synthesise inside the request, so a bad decision writes nothing.

    Pure and fast -- no file is touched until `commit`. A 422 here is worth far
    more than an `error` event inside a 200 the client has already started
    rendering.
    """
    from kpi_agent.onboard import prepare
    from kpi_engine.onboarding import OnboardingError, StalePlan, UnknownPlan

    try:
        return prepare(paths, req.as_confirmation())
    except UnknownPlan as exc:
        # Nothing to confirm: a missing resource.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StalePlan as exc:
        # A draft exists but has moved on. A conflict, not a missing thing --
        # the fix is to re-read the draft, not to create one.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OnboardingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _safe_company_list() -> list[Any]:
    """The registry may legitimately not exist yet; /health must still answer."""
    try:
        return list_companies()
    except CompanyConfigMissing:
        return []


def _company(
    company: str = Query(
        ...,
        description="Which tenant this call is about -- the `company_id` that "
        "`GET /companies` reports. Required on every scoped route; there is no "
        "default, because a run configured from whichever tenant happened to be "
        "wired in reports one company's numbers under another's name.",
    ),
) -> CompanyPaths:
    """Resolve the `company` query parameter to a workspace, or 404.

    Declared once here rather than per route, so all eleven scoped routes take
    the same parameter with the same documentation and the same failure modes.

    `company` cannot reach the filesystem as `../..`: `CompanySlug`'s pattern
    rejects it when the registry is parsed, so an unregistered value never gets
    as far as being joined to a path. That guard never depended on the selector
    being a path segment, which is why moving it costs nothing here. No
    `pattern=` on the parameter for the same reason -- a second gate would only
    turn a 404 for an unknown tenant into a 422 for a malformed one.
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
                f"/sources/data?company={paths.slug}&source_id=<source_id> first."
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
    llm = _maybe_llm(req.no_llm, req.model)
    async for item in _pump(lambda: ask_events(req, llm, paths=paths), logs=req.logs):
        yield item


def _maybe_llm(no_llm: bool, model: str | None) -> Any:
    """The model for a run, or None.

    A missing key must never cost the work. Every flow here has a deterministic
    path, so an unavailable model degrades what the answer contains, never
    whether there is one.
    """
    if no_llm:
        return None
    try:
        return build_llm(model)
    except LlmUnavailable as exc:
        log.info("no model available (%s); running the deterministic path", exc)
        return None


async def _pump(
    make_events: Any, *, logs: bool
) -> AsyncIterator[tuple[str | None, dict[str, Any]]]:
    """Run a synchronous event generator on a worker thread, off the event loop.

    Everything the engine does is CPU-bound -- pandas, statsmodels, ruptures --
    with blocking model calls of up to 150s in between, so it cannot run on the
    loop. `make_events` is a thunk rather than an iterator because it must be
    *called* inside the thread: the generator body would otherwise start on
    whichever thread first advanced it, and the `logbus` ContextVar that keeps
    two concurrent runs' log lines apart is set in here.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    def emit(name: str, payload: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (name, payload))

    def worker() -> None:
        sink = (lambda record: emit("log", record)) if logs else None
        try:
            with logbus.capturing(sink):
                for name, payload in make_events():
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
