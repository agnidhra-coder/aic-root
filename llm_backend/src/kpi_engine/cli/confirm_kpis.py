"""Accept a drafted KPI plan, write the company's configuration, and warm it up.

    python -m kpi_engine.cli.confirm_kpis --company acme --plan-id kpiplan-... --accept-all
    python -m kpi_engine.cli.confirm_kpis --company acme --plan-id kpiplan-... \\
        --accept "Gross Profit Margin" --accept ROAS
    python -m kpi_engine.cli.confirm_kpis --company acme --plan-id kpiplan-... \\
        --accept-all --reject "Churn Rate" --bind "ROAS.cost_of_ads=Ad Spend"

The terminal half of `POST /kpi-plan/confirm?company=...`. This is the writing
step: it replaces the company's `configs/semantics/kpis.yaml`,
`configs/causal/dag.yaml`, its source spec and its `company.yaml`, archiving each
under `configs/_superseded/<plan_id>/` first. All four are validated in memory
before any is written, and a failure restores the archive.

It then accepts the staged CSV through the ordinary `attach_source_data` -- header
gate and all -- so a contract this tool synthesised but the file cannot satisfy
fails loudly here rather than becoming a panel full of NaN.
"""

from __future__ import annotations

import argparse
import json
import sys

from kpi_engine.cli._common import open_from_args
from kpi_engine.contracts.onboarding import PlanConfirmation, PlanDecision
from kpi_engine.tenancy import UnknownCompany, add_company_argument


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__ or "")
    add_company_argument(p)
    p.add_argument("--plan-id", default=None, help="Defaults to the current draft's id.")
    p.add_argument(
        "--accept-all",
        action="store_true",
        help="Take every recommended KPI. The default when no --accept is given.",
    )
    p.add_argument(
        "--accept", action="append", default=[], metavar="KPI",
        help="Repeatable. Accept this KPI, including ones only offered as opt-in.",
    )
    p.add_argument(
        "--reject", action="append", default=[], metavar="KPI",
        help="Repeatable. Drop this KPI even though it was proposed.",
    )
    p.add_argument(
        "--variant", action="append", default=[], metavar="KPI=VARIANT",
        help="Repeatable. Compute a KPI a different way (see the plan's alternatives).",
    )
    p.add_argument(
        "--bind", action="append", default=[], metavar="KPI.ALIAS=COLUMN",
        help="Repeatable. Override one measure's column.",
    )
    p.add_argument("--time-grain", default=None, choices=["day", "week", "month"])
    p.add_argument("--entity-keys", nargs="*", default=None,
                   help="Dimensions to slice by. Pass with no values for total level.")
    p.add_argument("--date-column", default=None)
    p.add_argument("--contract-id", default=None)
    p.add_argument("--no-warm-up", action="store_true",
                   help="Write the configuration but do not run the pipeline.")
    p.add_argument("--model", default=None)
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the causal-structure call. Deterministic edges and "
                        "catalogue lever ownership still apply.")
    p.add_argument("--json", action="store_true")
    return p


def _decisions(args, plan) -> list[PlanDecision]:
    """Turn the flags into per-KPI verdicts.

    `--accept-all` and an explicit `--accept` list are different instructions: the
    first takes what the planner recommended, the second takes exactly what was
    named, including a KPI the planner would only offer as opt-in. Naming one
    explicitly is how a caller says "yes, I know, do it anyway".
    """
    variants: dict[str, str] = {}
    for item in args.variant:
        name, _, variant = item.partition("=")
        if variant:
            variants[name.strip()] = variant.strip()

    bindings: dict[str, dict[str, str]] = {}
    for item in args.bind:
        target, _, column = item.partition("=")
        name, _, alias = target.rpartition(".")
        if name and alias and column:
            bindings.setdefault(name.strip(), {})[alias.strip()] = column.strip()

    named = {a.strip() for a in args.accept}
    rejected = {r.strip() for r in args.reject}
    take_all = args.accept_all or not named

    out: list[PlanDecision] = []
    for proposal in plan.proposed:
        name = proposal.name
        if name in rejected:
            out.append(PlanDecision(name=name, verdict="reject"))
            continue
        if name in named or (take_all and proposal.recommended):
            out.append(
                PlanDecision(
                    name=name,
                    verdict="accept",
                    variant_id=variants.get(name),
                    bindings=bindings.get(name),
                )
            )
        else:
            out.append(PlanDecision(name=name, verdict="reject"))
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = open_from_args(args)
    except (UnknownCompany, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 2

    from kpi_agent.onboard import commit, prepare
    from kpi_engine.onboarding import OnboardingError, UnknownPlan, load_draft

    try:
        plan = load_draft(paths)
    except UnknownPlan as exc:
        print(exc, file=sys.stderr)
        return 2

    confirmation = PlanConfirmation(
        plan_id=args.plan_id or plan.plan_id,
        decisions=_decisions(args, plan),
        date_column=args.date_column,
        entity_columns=args.entity_keys,
        time_grain=args.time_grain,
        contract_id=args.contract_id,
        warm_up=not args.no_warm_up,
    )

    # Merge and synthesise before writing anything, so a bad decision costs
    # nothing -- the same split the API uses to answer 422 rather than opening a
    # stream it will only fail inside.
    try:
        prepared = prepare(paths, confirmation)
    except (UnknownPlan, OnboardingError, KeyError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    llm = None
    if not args.no_llm:
        from kpi_agent.llm import LlmUnavailable, build_llm

        try:
            llm = build_llm(args.model)
        except LlmUnavailable as exc:
            print(f"note: {exc}\n      writing deterministic edges only.", file=sys.stderr)

    collected: dict[str, dict] = {}
    try:
        for name, payload in commit(
            paths, confirmation, prepared=prepared, llm=llm
        ):
            collected[name] = payload
            if args.json:
                continue
            if name == "configs":
                print(f"wrote {len(payload['kpis'])} KPI(s) to {paths.slug}")
                for kpi in payload["kpis"]:
                    print(f"  - {kpi}")
                print(f"  graph: {payload['edges']['deterministic']} deterministic, "
                      f"{payload['edges']['causal']} causal edge(s)")
                for lever in payload["levers"]:
                    print(f"  lever: {lever['node']} -> {lever['owner']}")
                print(f"  replaced files archived at configs/_superseded/{payload['superseded']}/")
            elif name == "data":
                print(f"accepted {payload['rows']} rows; ready={payload['ready']}")
                for warning in payload["warnings"]:
                    print(f"  warning: {warning}")
            elif name == "engine":
                if payload.get("error"):
                    print(f"  warm-up failed for {payload['source_id']}: {payload['error']}")
                elif payload.get("skipped"):
                    print(f"  warm-up skipped {payload['source_id']}")
                else:
                    print(f"  warm-up {payload['source_id']}: {payload['flags']} flags -> "
                          f"{payload['events']} events -> {payload['bundles']} bundles "
                          f"({payload['seconds']}s)")
    except OnboardingError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(collected, indent=2, default=str))
        return 0

    for problem in collected.get("configs", {}).get("problems", []):
        print(f"note: {problem}")
    print(f'\nnext: python -m kpi_engine.cli.ask --company {paths.slug} '
          f'"what needs attention?"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
