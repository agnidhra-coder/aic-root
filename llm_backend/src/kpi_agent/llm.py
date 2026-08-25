"""The model: one object, built once, called directly by the nodes that need it.

There is exactly one provider and one place its name is written -- `DEFAULT_MODEL`
below. `build_llm` returns a configured `ChatGoogleGenerativeAI`; `graph.run_agent`
puts it in the run config, and `intent.plan_intent` / `narrate.narrate` call
`.with_structured_output(...).invoke(...)` on it themselves. Nothing wraps it, so
there is no second API to keep in step with LangChain's.

What stays here is what is genuinely shared: how the model is configured, how a
failure is phrased, and how tokens are counted.

Structured output is the load-bearing part. `method="json_schema"` sends the
Pydantic model's own `model_json_schema()` as the API's `response_json_schema`, so
`$ref`, `anyOf` and the `additionalProperties: false` that `extra="forbid"` emits
all reach the API intact -- the constraint the model is held to is the strict
schema we wrote, and the reply is validated against that same model on the way
back. A narrator that cannot emit free text cannot emit an unattributed sentence.

Message ordering is the other one: frozen system prompt, then the stable context
block, then the varying question. Gemini's context caching is an implicit prefix
match with no handle to manage, so that ordering is the whole of the optimisation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# The only place a model name is written. `--model` on the CLI overrides it.
# Two prefixes, one stream. Not decoration: a reader watching the run scroll past
# must be able to see, without reading the words, which lines were a model deciding
# something and which were the engine computing it. They live here because this is
# the module both model call sites already import from.
ENGINE = "[engine]"
MODEL = "[model] "

DEFAULT_MODEL = "gemini-3.5-flash-lite"

# Per-attempt deadline and attempt count. The worst case is their product, so these
# are chosen together: 150s x 3 caps one call at ~7.5 minutes, which is the most a
# CLI should silently spend before giving up and reporting deterministically.
# Measured on this API: a healthy structured call returns in ~3-25s, so 150s is far
# above a normal slow response. Three attempts absorb the transient 503 "high
# demand" these models return under load; a saturated one still reaches the
# fallback rather than hanging.
TIMEOUT_SECONDS = 150
ATTEMPTS = 3

# One cap for both calls. The narrator's `Narrative` is the larger of the two
# payloads and fits well inside this; the planner's `AnalysisIntent` is tiny.
MAX_OUTPUT_TOKENS = 16000

# Both calls think hard. The planner's choice of grain and slice decides whether
# the whole run has anything to say, which is not the place to economise.
REASONING_EFFORT = "medium"


class LlmUnavailable(RuntimeError):
    """Raised when the model cannot be reached. Callers degrade; they do not abort."""


@dataclass
class Usage:
    """Token and cost accounting for a run, shared by both call sites.

    `RunManifest` carries `llm_calls` / `llm_tokens_in` / `llm_tokens_out` /
    `llm_cost_usd`, and the deterministic-versus-LLM split is only an interesting
    claim if both halves are actually measured.
    """

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    per_call: list[dict[str, Any]] = field(default_factory=list)
    # Rates are per model and change, so they are supplied rather than assumed. When
    # they are unknown, `cost_usd` is None and the report says the run is unpriced --
    # a confidently wrong dollar figure is the last thing that belongs in a document
    # whose whole claim is that its numbers can be trusted.
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None

    @property
    def cost_usd(self) -> float | None:
        if self.price_in_per_mtok is None or self.price_out_per_mtok is None:
            return None
        return (
            self.tokens_in / 1e6 * self.price_in_per_mtok
            + self.tokens_out / 1e6 * self.price_out_per_mtok
        )

    def record(self, label: str, metadata: dict[str, Any] | None) -> None:
        """Add one call, from LangChain's normalised `usage_metadata`.

        `input_tokens` already includes cached tokens and `output_tokens` already
        includes thinking tokens, so both are taken as they come -- the cache read is
        reported alongside for interest, never added on top.
        """
        metadata = metadata or {}
        cached = int((metadata.get("input_token_details") or {}).get("cache_read", 0) or 0)
        tin = int(metadata.get("input_tokens", 0) or 0)
        tout = int(metadata.get("output_tokens", 0) or 0)
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        self.cache_read += cached
        self.per_call.append(
            {"label": label, "tokens_in": tin, "tokens_out": tout, "cache_read": cached}
        )


def load_env() -> None:
    """Load a project-root `.env` if one exists.

    `GOOGLE_API_KEY` lives there rather than in the shell so a run is reproducible
    without a setup ritual. `.env` is gitignored; nothing here ever writes a key
    anywhere, and none of it reaches a report.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional convenience
        return
    from kpi_engine.config_io import project_root

    load_dotenv(project_root() / ".env")


def build_llm(model: str | None = None) -> Any:
    """The one model object for a run.

    Everything that varies per deployment is a constant above, and everything that
    used to vary per call no longer does: both calls take the same effort and the
    same output cap, so the object is built once and shared. That is not only
    simpler -- varying settings per call by copying the model shares its underlying
    httpx client and closes it when the copy is collected, which killed the second
    call of every run until it was found.

    `temperature` is deliberately not set. The Gemini 3 models sample at fixed
    settings and warn that they are ignoring it, and determinism here comes from the
    fact table rather than from decoding: no quantity in a report is the model's to
    choose.
    """
    load_env()
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise LlmUnavailable(
            "The `langchain-google-genai` package is not installed. "
            "Install the agent extra, or run with --no-llm."
        ) from exc

    try:
        return ChatGoogleGenerativeAI(
            model=model or DEFAULT_MODEL,
            timeout=TIMEOUT_SECONDS,
            max_retries=ATTEMPTS,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            reasoning_effort=REASONING_EFFORT,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as LlmUnavailable
        raise LlmUnavailable(
            f"Could not construct the model client: {exc}. "
            "Is GOOGLE_API_KEY set (a .env file is loaded automatically)?"
        ) from exc


def unavailable(exc: Exception, started: float) -> LlmUnavailable:
    """Phrase a failed call so the reader knows whose problem it is.

    Both call sites raise through this, because the failure that actually happens
    -- 503 "high demand" -- is not a defect in the request, and a message that does
    not say so sends people looking for a bug they do not have.
    """
    elapsed = time.monotonic() - started
    text = str(exc)
    if "UNAVAILABLE" in text or "503" in text:
        return LlmUnavailable(
            f"Model saturated after {elapsed:.0f}s ({ATTEMPTS} attempts): {text}. "
            "This is load, not misconfiguration -- retry, or try another "
            "--model."
        )
    return LlmUnavailable(
        f"Could not complete the call after {elapsed:.0f}s "
        f"({ATTEMPTS} attempts x {TIMEOUT_SECONDS}s): {text}"
    )
