"""Propose a KPI configuration for a company, from its own extract.

    python -m kpi_engine.cli.plan_kpis --company acme --from-csv extract.csv
    python -m kpi_engine.cli.plan_kpis --company acme --from-csv extract.csv --no-llm
    python -m kpi_engine.cli.plan_kpis --company acme --from-csv extract.csv --json

The terminal half of `POST /kpi-plan?company=...`. Both drain the same
generator, so a plan proposed here and one proposed over HTTP cannot differ --
the guarantee `stream_agent` gives `/ask`, extended to onboarding.

The upload is *staged*, not accepted: it lands outside every declared source
path, so the company keeps reporting `awaiting_data` and `ask` keeps refusing it
until `confirm_kpis` writes a contract the file actually satisfies.

Writes `configs/_draft/kpi_plan.json`. Confirm it with
`python -m kpi_engine.cli.confirm_kpis --company <slug> --plan-id <id>`.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from kpi_engine.cli._common import console, open_from_args
from kpi_engine.tenancy import UnknownCompany, add_company_argument


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__ or "")
    add_company_argument(p)
    p.add_argument("--from-csv", required=True, help="The extract to plan against.")
    p.add_argument(
        "--source-id",
        default=None,
        help="Which declared source this file is. Defaults to the primary.",
    )
    p.add_argument("--plan-id", default=None, help="Name the draft. Defaults to a timestamp.")
    p.add_argument("--model", default=None, help="Override the model id.")
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="Bind by exact column-name matching only. Needs no API key; proposes "
        "fewer KPIs, never wrong ones.",
    )
    p.add_argument("--json", action="store_true", help="Emit the plan as JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = open_from_args(args)
    except (UnknownCompany, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 2

    llm = None
    if not args.no_llm:
        from kpi_agent.llm import LlmUnavailable, build_llm

        try:
            llm = build_llm(args.model)
        except LlmUnavailable as exc:
            print(f"note: {exc}\n      falling back to exact name matching.", file=sys.stderr)

    from kpi_agent.onboard import propose

    source_id = args.source_id or paths.primary_source_id
    plan: dict[str, Any] | None = None
    try:
        for name, payload in propose(
            paths, source_id, csv_path=args.from_csv, llm=llm, plan_id=args.plan_id
        ):
            if name == "plan" and payload.get("stage") == "resolved":
                plan = payload["plan"]
            elif name == "staged" and not args.json:
                print(f"staged {payload['rows']} rows, {payload['columns']} columns")
            elif name == "profile" and not args.json:
                print("profiled (this is the slow step)")
    except Exception as exc:  # noqa: BLE001 - reported, not a traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if plan is None:
        print("No plan was produced.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(plan, indent=2))
        return 0

    _report(plan, paths.slug)
    return 0


def _report(plan: dict[str, Any], slug: str) -> None:
    """The plan as a table, or as plain text when rich is absent."""
    proposed = plan["proposed"]
    recommended = [p for p in proposed if p["recommended"]]
    con = console()

    if con is not None:
        from rich.table import Table

        table = Table(title=f"KPI plan {plan['plan_id']} for {slug}", show_lines=False)
        table.add_column("KPI")
        table.add_column("bound to")
        table.add_column("from")
        table.add_column("", justify="center")
        for p in proposed:
            bound = ", ".join(
                f"{m['alias']}={m['column']}" for m in p["measures"] if m["column"]
            )
            sources = {m["bound_by"] for m in p["measures"]}
            mark = "[green]yes[/green]" if p["recommended"] else "[yellow]opt-in[/yellow]"
            table.add_row(p["name"], bound, "/".join(sorted(sources)), mark)
        con.print(table)
    else:
        print(f"\nKPI plan {plan['plan_id']} for {slug}")
        for p in proposed:
            bound = ", ".join(
                f"{m['alias']}={m['column']}" for m in p["measures"] if m["column"]
            )
            print(f"  {'*' if p['recommended'] else '-'} {p['name']}: {bound}")

    print(f"\ndate column   {plan['date_column']}")
    print(f"dimensions    {', '.join(plan['entity_columns']) or '(none)'}")
    print(f"grain         {plan['time_grain']}")
    print(f"proposed      {len(proposed)} ({len(recommended)} recommended)")

    unavailable = plan["unavailable"]
    if unavailable:
        print(f"\nnot computable from this file ({len(unavailable)}):")
        for u in unavailable[:8]:
            print(f"  - {u['name']}: {u['reason']}")
        if len(unavailable) > 8:
            print(f"  ... and {len(unavailable) - 8} more")

    unused = [c["name"] for c in plan["columns"] if c["role"] == "unused"]
    if unused:
        print(f"\ncolumns nothing used ({len(unused)}): {', '.join(unused[:12])}"
              + (" ..." if len(unused) > 12 else ""))

    for problem in plan["problems"]:
        print(f"\nnote: {problem}")

    print(
        f"\nnext: python -m kpi_engine.cli.confirm_kpis --company {slug} "
        f"--plan-id {plan['plan_id']} --accept-all"
    )


if __name__ == "__main__":
    raise SystemExit(main())
