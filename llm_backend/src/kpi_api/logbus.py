"""Forward the run's stage log to whoever is watching that run.

The terminal's progressive output is not a data structure -- it is 26 `log.info`
calls on the `kpi_agent` logger, which `cli/ask.py` routes to stderr through a
`RichHandler`. An HTTP client watching a run wants the same commentary, so the
handler here is the wire's equivalent of that one.

The problem a single global handler creates is cross-talk: two concurrent runs
share one logger. The sink is therefore held in a `ContextVar` set inside each
run's worker thread, so a record is delivered to the run that produced it and to
no other. Context is set explicitly at the top of the worker rather than relying
on propagation, because that is one fewer thing to be wrong about.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator
from contextvars import ContextVar

from kpi_agent.llm import ENGINE, MODEL

Sink = Callable[[dict], None]

_sink: ContextVar[Sink | None] = ContextVar("kpi_api_log_sink", default=None)

_LOGGER = "kpi_agent"
_installed = False


class _RunLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        sink = _sink.get()
        if sink is None:
            return
        try:
            sink(_as_event(record))
        except Exception:  # noqa: BLE001 - a broken client must not break a run
            pass


def _as_event(record: logging.LogRecord) -> dict:
    """One log line, with the engine/model split made machine-readable.

    Every message already carries one of the two prefixes, because a reader
    watching a run scroll past has to be able to see which half produced a line
    without reading the words. The same distinction is what a UI wants, so it is
    lifted into a field rather than left for the client to parse back out.
    """
    message = record.getMessage()
    engine, model = ENGINE.strip(), MODEL.strip()
    if message.startswith(engine):
        source, text = "engine", message[len(engine):].strip()
    elif message.startswith(model):
        source, text = "model", message[len(model):].strip()
    else:
        source, text = "engine", message
    return {
        "level": record.levelname.lower(),
        "source": source,
        "message": text,
        "logger": record.name,
    }


def install(level: int = logging.INFO) -> None:
    """Attach the handler once, and quiet what would otherwise drown it.

    The root logger stays at WARNING and only ours is raised -- turning the root
    up instead pulls in httpx's request lines, which bury the dozen lines that
    describe the actual run. The SDK's standing advice about automatic function
    calling arrives at WARNING and is not about anything we do, so it is muted by
    name rather than by level. Same reasoning, same two lines, as `cli/ask.py`.
    """
    global _installed
    if _installed:
        return
    logger = logging.getLogger(_LOGGER)
    logger.setLevel(level)
    logger.addHandler(_RunLogHandler())
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)
    _installed = True


@contextlib.contextmanager
def capturing(sink: Sink | None) -> Iterator[None]:
    """Route this thread's `kpi_agent` log records to `sink` for the duration."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
