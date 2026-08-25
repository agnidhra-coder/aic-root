"""The HTTP layer, tested as what it claims to be: a view, not a second engine.

None of these need an API key. The model is a parameter all the way down, so the
same stub that drives the graph in `test_agent.py` drives it through the app --
which is the point of the boundary being real rather than described.

What is pinned here is mostly *refusal* and *equivalence*: that no live object
escapes onto the wire, that the streaming and non-streaming endpoints cannot
report different things, and that a JSON round trip does not quietly turn "the
caller said nothing" into "the caller said total level".
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from kpi_agent.models import Claim, Narrative

from kpi_api.app import create_app
from kpi_api.events import collapse
from kpi_api.models import AskRequest, build_config

from test_agent import StubLlm, _intent


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def _body(**kw) -> dict:
    """A request that runs the deterministic path over the small config."""
    base = {
        "question": "what needs attention?",
        "no_llm": True,
        "persona": "analyst",
        "time_grain": "week",
        "entity_keys": ["Region"],
        "top_events": 2,
    }
    base.update(kw)
    return base


def _timeless(markdown: str) -> str:
    """Wall-clock runtime is the one figure a report carries that two runs of the
    same question are allowed to disagree about."""
    return re.sub(r"runtime \| \d+ ms", "runtime | N ms", markdown)


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Read back the wire format, keepalive comments and all."""
    events = []
    for block in text.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name is not None:
            events.append((name, data))
    return events


# --------------------------------------------------------------------------- #
# The request surface
# --------------------------------------------------------------------------- #


def test_health_reports_whether_a_key_exists_never_what_it_is(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["api_key_present"] in (True, False)
    assert "GOOGLE_API_KEY" not in json.dumps(body)


def test_a_misspelled_field_is_refused_rather_than_ignored(client):
    """`extra="forbid"` is the whole reason the request model is `Strict`.

    A silently dropped `time_grian` is a report configured differently from what
    the caller asked for, delivered with no indication that it was.
    """
    response = client.post("/ask/sync", json=_body(time_grian="month"))
    assert response.status_code == 422


def test_entity_keys_of_empty_list_means_total_level_not_unset():
    """`None` and `[]` are different instructions and must stay different.

    `None` means the caller said nothing and the planner decides; `[]` means total
    level and is a real choice. A JSON round trip that conflated them would hand
    the graph an override it was never given.
    """
    assert build_config(AskRequest(question="q", entity_keys=[]))["entity_keys"] == []
    assert build_config(AskRequest(question="q"))["entity_keys"] is None
    assert build_config(AskRequest(question="q", time_grain=None))["time_grain"] is None


def test_the_request_mirrors_the_ask_cli():
    """The API is a second front-end on one command, so every flag that changes
    what is computed must be reachable from both."""
    fields = set(AskRequest.model_fields)
    assert {"question", "persona", "time_grain", "entity_keys", "model", "no_llm",
            "top_events", "run_id", "dataset", "scm", "source", "scm_source",
            "contract", "scm_contract", "graph", "detection", "personas"} <= fields
    # And no date field: a period must reach the pipeline as the planner's
    # report window, never as a load filter that starves the baseline.
    assert not any("date" in f for f in fields)


def test_an_unknown_run_is_a_404_not_a_traceback(client):
    assert client.get("/runs/no-such-run").status_code == 404


def test_a_run_id_cannot_climb_out_of_outputs(client):
    """`run_id` names a directory, so it is the one field that could read a file
    it was never meant to."""
    assert client.get("/runs/..%2F..%2F.env").status_code in (400, 404)


# --------------------------------------------------------------------------- #
# The stream
# --------------------------------------------------------------------------- #


@pytest.mark.slow
def test_the_stream_carries_every_stage_in_the_order_the_graph_runs_them(client):
    response = client.post("/ask", json=_body(run_id="pytest-api-stream"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    names = [n for n, _ in events]

    assert names[0] == "started"
    assert names[-1] == "done"
    for stage in ("ingest", "plan", "engine", "evidence", "narrative",
                  "verification", "telemetry", "report"):
        assert stage in names, f"{stage} never reached the wire"

    # The order is the graph's, so a client can render as it goes rather than
    # buffering to the end.
    assert names.index("ingest") < names.index("plan") < names.index("engine")
    assert names.index("engine") < names.index("evidence") < names.index("narrative")
    assert names.index("narrative") < names.index("verification") < names.index("report")

    payload = collapse([(n, d) for n, d in events])
    assert payload["done"]["outcome"] == "report"
    assert payload["report"]["report_markdown"].startswith("#")
    assert payload["evidence"]["facts"]


@pytest.mark.slow
def test_the_stage_log_reaches_the_wire_with_the_engine_model_split(client):
    """The terminal shows which half produced each line. So must the stream."""
    events = _parse_sse(client.post("/ask", json=_body(run_id="pytest-api-log")).text)
    logs = [d for n, d in events if n == "log"]
    assert logs, "no stage log reached the client"
    assert {d["source"] for d in logs} <= {"engine", "model"}
    assert any("ingested" in d["message"] for d in logs)
    # The prefix is lifted into a field, not left in the text for a client to
    # parse back out.
    assert not any(d["message"].startswith(("[engine]", "[model]")) for d in logs)


@pytest.mark.slow
def test_logs_can_be_turned_off_without_losing_a_stage(client):
    events = _parse_sse(
        client.post("/ask", json=_body(logs=False, run_id="pytest-api-nolog")).text
    )
    names = [n for n, _ in events]
    assert "log" not in names
    assert "report" in names and names[-1] == "done"


@pytest.mark.slow
def test_no_dataframe_or_model_object_reaches_the_wire(client):
    """Every event is built by naming its fields.

    `AgentState` carries a live chat model, a `Usage`, raw DataFrames and
    `PipelineResult` dataclasses. A payload assembled by dumping state would
    either fail to encode or, worse, succeed -- and become a shape nobody decided.
    """
    events = _parse_sse(client.post("/ask", json=_body(run_id="pytest-api-leak")).text)

    # Reaching here at all is most of the assertion: `_sse` encodes with no
    # `default=` fallback, so a DataFrame or a chat model on any payload would
    # have raised before the response was finished.
    assert events and events[-1][0] == "done"

    for name, payload in events:
        # `config` is where the live model and the run's Usage travel.
        assert "config" not in (payload or {}), name

    # The one event that legitimately has a `sources` key carries the projection,
    # not the (spec, contract, profile, DataFrame) tuples the state holds.
    ingest = collapse(events)["ingest"]
    assert ingest["sources"]
    assert all(set(s) == {"source_id", "rows", "columns"} for s in ingest["sources"])


@pytest.mark.slow
def test_the_sync_endpoint_and_the_stream_report_the_same_thing(client):
    """Two endpoints, one projection. Built from the same events rather than
    beside them, so there is nothing to keep in step."""
    streamed = collapse(
        _parse_sse(client.post("/ask", json=_body(run_id="pytest-api-parity")).text)
    )
    synced = client.post("/ask/sync", json=_body(run_id="pytest-api-parity")).json()

    assert _timeless(synced["report"]["report_markdown"]) == \
        _timeless(streamed["report"]["report_markdown"])
    assert synced["evidence"]["facts"] == streamed["evidence"]["facts"]
    assert synced["narrative"]["narrative"] == streamed["narrative"]["narrative"]
    assert synced["verification"]["passed"] == streamed["verification"]["passed"]


@pytest.mark.slow
def test_a_finished_run_is_readable_afterwards(client):
    """A client that disconnects mid-run has not lost the answer: the artefacts
    are written regardless."""
    run_id = "pytest-api-artefacts"
    client.post("/ask/sync", json=_body(run_id=run_id))

    assert client.get(f"/runs/{run_id}").json()["run_id"] == run_id
    assert client.get(f"/runs/{run_id}/report").text.startswith("#")


# --------------------------------------------------------------------------- #
# The branches that are not the happy path
# --------------------------------------------------------------------------- #


def _stubbed_client(monkeypatch, *responses) -> TestClient:
    """An app whose runs use a queued stub instead of reaching for a model."""
    import kpi_api.app as app_mod

    stub = StubLlm(*responses)
    monkeypatch.setattr(app_mod, "build_llm", lambda model=None: stub)
    return TestClient(create_app())


@pytest.mark.slow
def test_a_clarification_ends_the_stream_without_a_narrative(monkeypatch):
    """A question that cannot be resolved must not produce a confident answer --
    and the stream must say so rather than simply stopping."""
    client = _stubbed_client(monkeypatch, _intent(
        sources=["retail_daily"],
        clarification_needed="Which region did you mean by 'the usual one'?",
    ))
    events = _parse_sse(client.post("/ask", json=_body(
        question="how is the usual one doing?", no_llm=False,
        run_id="pytest-api-clarify",
    )).text)

    names = [n for n, _ in events]
    assert "clarification" in names
    assert "narrative" not in names
    assert "report" not in names
    payload = collapse(events)
    assert payload["done"]["outcome"] == "clarification"
    assert "usual one" in payload["clarification"]["message"]


@pytest.mark.slow
def test_a_narrative_that_fails_verification_twice_streams_both_attempts(monkeypatch):
    """The repair loop is visible on the wire, so a client can tell a first draft
    from the template that replaced it."""
    fabricated = Narrative(
        headline="Margin collapsed 87.3% company-wide.",
        what_happened=[Claim(text="Margin fell 87.3%.", evidence_ids=["F404"])],
    )
    client = _stubbed_client(
        monkeypatch,
        _intent(sources=["retail_daily"], entity_keys=["Region"]),
        fabricated.model_copy(deep=True), fabricated.model_copy(deep=True),
    )
    events = _parse_sse(client.post("/ask", json=_body(
        no_llm=False, run_id="pytest-api-repair",
    )).text)

    narratives = [d for n, d in events if n == "narrative"]
    assert len(narratives) > 1
    assert [d["attempt"] for d in narratives] == list(range(1, len(narratives) + 1))
    assert narratives[-1]["used_fallback"]
    assert narratives[-1]["fallback_reason"]

    payload = collapse(events)
    assert payload["verification"]["passed"]
    assert "87.3" not in payload["report"]["report_markdown"]
