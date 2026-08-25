"""Serve the API.

    uv run python -m kpi_api
    uv run python -m kpi_api --port 8080 --host 0.0.0.0

Port 8000 by default: 3000 is the Next.js frontend and 3001 is the NestJS
server, so nothing else in this repo claims it.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        print(f"The api extra is not installed ({exc}).\n"
              "  uv sync --extra api", file=sys.stderr)
        return 2

    # Imported here rather than at module scope so a missing agent layer arrives
    # as advice instead of a traceback out of uvicorn's app loader. `uv sync` is
    # exact -- it uninstalls every extra you did not name -- so an environment
    # with fastapi and no langgraph is a normal way to end up here.
    try:
        import kpi_api.app  # noqa: F401
    except ImportError as exc:
        print(f"The API serves the agent, and the agent layer is missing ({exc}).\n"
              "  uv sync --extra api", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--host", default=os.environ.get("KPI_API_HOST", "127.0.0.1"),
                        help="Bind address. Localhost by default: these routes "
                             "carry no authentication.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("KPI_API_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    uvicorn.run("kpi_api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
