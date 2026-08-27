"""List the registered companies.

    python -m kpi_engine.cli.list_companies [--all] [--json]

The terminal mirror of `GET /companies`. `--json` emits the same shape the route
returns, so a client can be developed against either.
"""

from __future__ import annotations

import argparse
import json

from kpi_engine.tenancy import CompanyConfigMissing, list_companies, open_company


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--all", action="store_true", help="Include inactive companies.")
    p.add_argument("--json", action="store_true")
    return p


def describe(company_id: str) -> dict:
    """One company as the API reports it. Never raises: an unopenable company is
    listed with its problem rather than omitted, because a tenant that has silently
    vanished from a listing is harder to debug than one that says why it is broken."""
    try:
        paths = open_company(company_id)
    except Exception as exc:  # noqa: BLE001
        return {"company_id": company_id, "status": "broken", "problems": [str(exc)]}

    missing = paths.missing_datasets()
    return {
        "company_id": paths.slug,
        "display_name": paths.spec.display_name,
        "domains": list(paths.spec.domains),
        "created_at": paths.spec.created_at.isoformat(),
        "status": "ready" if not missing else "awaiting_data",
        "awaiting": {sid: str(p) for sid, p in missing.items()},
        "problems": paths.validate(),
        "sources": [
            {
                "source_id": b.source_id,
                "role": b.role,
                "domain": b.domain,
                "label": b.label,
            }
            for b in paths.bindings
        ],
        "agent": paths.spec.agent.model_dump(mode="json"),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        entries = list_companies(active_only=not args.all)
    except CompanyConfigMissing as exc:
        print(exc)
        return 2

    rows = [describe(e.company_id) for e in entries]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("No companies registered. Create one with cli.init_company.")
        return 0
    width = max(len(r["company_id"]) for r in rows)
    for r in rows:
        name = r.get("display_name", "")
        detail = ", ".join(r.get("domains", [])) or "-"
        print(f"{r['company_id']:<{width}}  {r['status']:<13}  {detail:<24}  {name}")
        for problem in r.get("problems", []):
            print(f"{'':<{width}}  PROBLEM  {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
