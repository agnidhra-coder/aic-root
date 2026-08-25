"""HTTP access to the agent, streamed one stage at a time.

A third view of a run, beside the CLI's terminal rendering and the written
markdown artefact. All three drain `kpi_agent.stream_agent`; none of them
computes anything of its own.

The ASGI target is `kpi_api.app:app`. The application object is deliberately not
re-exported here -- binding the name `app` on the package would shadow the
`kpi_api.app` module for anyone who imports both, which is a confusing way to
save one line.

Nothing is imported eagerly. `kpi_api.app` reaches `kpi_agent`, which imports
langgraph at module scope, so an eager re-export would make `import kpi_api`
fail with a bare `ModuleNotFoundError` on an install missing the agent extra --
before `__main__` gets the chance to say which command fixes it. The names below
still resolve; they are just resolved on use.
"""

_LAZY = {
    "AskRequest": "kpi_api.models",
    "build_config": "kpi_api.models",
    "ask_events": "kpi_api.events",
    "collapse": "kpi_api.events",
    "create_app": "kpi_api.app",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(_LAZY[name]), name)
