"""Create a company, or check one that already exists.

    python -m kpi_engine.cli.init_company --company orbit-grocers \\
        --display-name "Orbit Grocers" --domain other --template minimal \\
        --from-csv extracts/orbit.csv

A thin wrapper over `kpi_engine.provisioning`, which `POST /companies` also calls.
Never `mkdir` a company by hand: `company.yaml` is rendered from the model that
reads it, and a hand-made folder skips the column check that catches a CSV missing
a KPI's measure.

`--check` validates an existing company and writes nothing -- what CI runs over
every tenant, and what a health check calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from kpi_engine.contracts.tenancy import AgentDefaults
from kpi_engine.provisioning import (
    DataRejected,
    ProvisioningError,
    attach_source_data,
    create_company,
    list_templates,
    required_columns,
)
from kpi_engine.tenancy import CompanyPaths, UnknownCompany, open_company


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--company", required=True, help="Slug: ^[a-z0-9][a-z0-9_-]{1,62}$")
    p.add_argument("--display-name", default=None, help="Human-readable name.")
    p.add_argument(
        "--domain",
        action="append",
        dest="domains",
        default=None,
        choices=["retail", "supply-chain", "marketing", "finance", "other"],
        help="Repeatable. Defaults to the template's own domains.",
    )
    p.add_argument(
        "--template",
        default=None,
        help=f"Which templates/company/<name>/ to seed from. Known: {list_templates()}",
    )
    p.add_argument("--from-csv", default=None, help="CSV to attach after creating.")
    p.add_argument(
        "--source-id",
        default=None,
        help="Which declared source --from-csv belongs to. Defaults to the primary.",
    )
    p.add_argument("--time-grain", default=None, choices=["day", "week", "month"])
    p.add_argument("--entity-keys", nargs="*", default=None)
    p.add_argument("--top-events", type=int, default=None)
    p.add_argument(
        "--supabase-user-id",
        action="append",
        dest="supabase_user_ids",
        default=[],
        help="Repeatable. Recorded in the registry so an upload resolves to this folder.",
    )
    p.add_argument("--notes", default="")
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing folder, and accept a CSV missing contract columns.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Validate an existing company and exit. Writes nothing.",
    )
    p.add_argument("--json", action="store_true", help="Machine-readable result.")
    return p


def _agent_overrides(args: argparse.Namespace) -> AgentDefaults | None:
    fields = {
        k: v
        for k, v in (
            ("time_grain", args.time_grain),
            ("entity_keys", args.entity_keys),
            ("top_events", args.top_events),
        )
        if v is not None
    }
    return AgentDefaults(**fields) if fields else None


def _describe(paths: CompanyPaths, warnings: list[str] | None = None) -> dict[str, Any]:
    missing = paths.missing_datasets()
    return {
        "company_id": paths.slug,
        "display_name": paths.spec.display_name,
        "domains": list(paths.spec.domains),
        "root": str(paths.root),
        "status": "ready" if not missing else "awaiting_data",
        "problems": paths.validate(),
        "warnings": warnings or [],
        "sources": [
            {
                "source_id": b.source_id,
                "role": b.role,
                "domain": b.domain,
                "label": b.label,
                "expects": str(missing[b.source_id]) if b.source_id in missing else None,
                "required_columns": required_columns(paths, b.source_id),
            }
            for b in paths.bindings
        ],
        "agent": paths.spec.agent.model_dump(mode="json"),
    }


def _report(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    print(f"{result['company_id']}  ({result['display_name']})")
    print(f"  root      {result['root']}")
    print(f"  status    {result['status']}")
    for s in result["sources"]:
        line = f"  source    {s['source_id']} [{s['role']}, {s['domain']}]"
        if s["expects"]:
            line += f"  -> awaiting {s['expects']}"
        print(line)
    for w in result["warnings"]:
        print(f"  warning   {w}")
    for problem in result["problems"]:
        print(f"  PROBLEM   {problem}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.check:
        try:
            paths = open_company(args.company)
        except (UnknownCompany, FileNotFoundError) as exc:
            print(exc, file=sys.stderr)
            return 2
        result = _describe(paths)
        _report(result, args.json)
        return 2 if result["problems"] else 0

    try:
        paths = create_company(
            args.company,
            args.display_name or args.company,
            args.domains or ["other"],
            template=args.template,
            agent=_agent_overrides(args),
            supabase_user_ids=args.supabase_user_ids,
            notes=args.notes,
            force=args.force,
        )
    except ProvisioningError as exc:
        print(exc, file=sys.stderr)
        return 2

    warnings: list[str] = []
    if args.from_csv:
        source_id = args.source_id or paths.primary_source_id
        try:
            paths, warnings = attach_source_data(
                paths, source_id, csv_path=args.from_csv, force=args.force
            )
        except DataRejected as exc:
            print(exc, file=sys.stderr)
            print(
                f"\nThe company was created at {paths.root} and is awaiting data. "
                f"Attach a file with a matching header, or edit its contract.",
                file=sys.stderr,
            )
            return 2

    result = _describe(paths, warnings)
    _report(result, args.json)
    if not args.json:
        nxt = (
            f"python -m kpi_engine.cli.ask --company {paths.slug} \"what needs attention?\""
            if result["status"] == "ready"
            else f"python -m kpi_engine.cli.init_company --company {paths.slug} "
            f"--from-csv <path>   # attach data"
        )
        print(f"\nnext: {nxt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
