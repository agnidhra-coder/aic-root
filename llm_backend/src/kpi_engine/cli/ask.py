"""Ask the engine a question in plain language.

    python -m kpi_engine.cli.ask --company acme-retail "why did margin fall in the West?"
    python -m kpi_engine.cli.ask --company acme-retail "what needs attention?" --persona exec
    python -m kpi_engine.cli.ask --company acme-retail "why did ROAS drop?" --model gemini-3.7-flash
    python -m kpi_engine.cli.ask --company acme-retail "what needs attention?" --no-llm
    python -m kpi_engine.cli.ask --company acme-retail            # no question at all

Two model calls sit at the ends of this: one turns the question into a validated
pipeline configuration, one writes the prose. Everything that produces a number
between them is the deterministic engine, unchanged.

The question is optional, and three ways of not asking one land in the same place:
omitting it, asking something vague ("how are we doing?"), and stating context
without an ask ("we ran a billboard campaign in the West"). None of them names a
KPI, so all three leave `kpis` empty, which is what puts the run in survey mode.
The third still has its context transcribed -- `exogenous` is filled independently
of whether the question narrowed anything.

Ask about a KPI, not a driver column. Which KPIs are answerable depends on the
company: for `acme-retail` the set is CAC, ROAS, Net Profit Margin, Conversion
Rate, Inventory Turnover, Fill Rate, Weighted Lead Time, Stockout Rate and Days Of
Supply. COGS, Total Expenses and Cash are *drivers*: the
engine attributes movements to them, so naming one as the thing that moved gets you
an abstention rather than an answer.

Two questions that land on the injected scenario, for a demo:

    # the cross-source story: a supplier disruption reaching margin through COGS
    python -m kpi_engine.cli.ask --company acme-retail \\
      "Net Profit Margin and Inventory Turnover fell in August and September 2026. \\
    Did the Kestrel Logistics supplier disruption cause it? Compare Kestrel \\
    Logistics against Northwind Foods and trace the path through to COGS." \\
      --persona analyst --time-grain week --entity-keys Supplier

    # the clean one: a West-only ad shock with four untreated regions as controls
    python -m kpi_engine.cli.ask --company acme-retail \\
      "Why did CAC rise and ROAS fall in the West region between mid-March and \\
    early April 2026, and how much of the CAC move came from marketing spend \\
    versus lost new customers?" \\
      --persona analyst --time-grain week --entity-keys Region
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from kpi_engine.cli._common import banner, console, kv, open_from_args
from kpi_engine.tenancy import add_company_argument


def _setup_logging(level: int) -> None:
    """Route the graph's stage log somewhere a human can read it.

    Without this the terminal sits silent through two model calls of up to a few
    minutes each and a full pipeline run, which looks wedged. The log is also where
    the engine/model split is visible while the run is still happening -- each line
    is prefixed with which half produced it.
    """
    con = console(stderr=True)
    if con is not None:
        from rich.logging import RichHandler

        handler = RichHandler(
            console=con, show_path=False, show_level=False,
            omit_repeated_times=False, markup=False, rich_tracebacks=True,
            # The log is prose about paths and ids; the highlighter colours those
            # as literals and makes the engine/model prefix harder to pick out.
            highlighter=None,
        )
        logging.basicConfig(level=logging.WARNING, format="%(message)s",
                            datefmt="[%H:%M:%S]", handlers=[handler])
    else:
        logging.basicConfig(level=logging.WARNING, format="  %(message)s",
                            stream=sys.stderr)

    # The root logger stays at WARNING and only ours is raised. Turning the root up
    # instead pulls in httpx's request lines, which bury the dozen lines that
    # describe the actual run. The SDK's standing advice about automatic function
    # calling arrives at WARNING and is not about anything we do, so it is muted by
    # name rather than by level.
    logging.getLogger("kpi_agent").setLevel(level)
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)


def main(argv: list[str] | None = None) -> int:
    try:
        from kpi_agent import (
            DEFAULT_MODEL,
            LlmUnavailable,
            build_llm,
            run_agent,
        )
    except ImportError as exc:
        print(f"The agent extra is not installed ({exc}).\n"
              "  uv sync --extra agent", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "question", nargs="?", default="",
        help="What you want to know, in plain language. Omit it to let the engine "
             "sweep every KPI and report whatever needs attention.")
    parser.add_argument("--persona", default=None, choices=["analyst", "exec", "ops"],
                        help="Who is asking. Inferred from the question when omitted.")
    add_company_argument(parser)
    # One flag replaces the six per-source path overrides this used to carry
    # (--dataset/--scm/--source/--scm-source/--contract/--scm-contract). Those
    # encoded "index 0 is sales, index 1 is supply chain", which stopped being true
    # the moment a company could declare one source or five. A source is now named
    # by the id its own company.yaml gives it.
    parser.add_argument("--sources", nargs="*", default=None,
                        help="Restrict the run to these declared source ids. "
                             "Omit to use every source the company declares.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--top-events", type=int, default=5)
    parser.add_argument("--model", default=None,
                        help=f"Override the model id. Default: {DEFAULT_MODEL}.")

    # The planner is not deterministic, so a demo that depends on it choosing the
    # right grain is a demo that sometimes fails. These force the two fields that
    # decide whether the detector finds anything at all.
    parser.add_argument("--time-grain", default=None, choices=["day", "week", "month"],
                        help="Force the grain, overriding whatever the planner "
                             "proposes. Coarse grains starve the baseline: the "
                             "detector trains on a fixed number of periods.")
    parser.add_argument("--entity-keys", nargs="*", default=None,
                        help="Force the slicing dimensions (e.g. Region, or Supplier). "
                             "Pass with no values for total level. A dimension on "
                             "which every slice moved leaves the estimators no "
                             "control group.")

    parser.add_argument("--facts", action="store_true",
                        help="Print every fact, not only the ones the narrative cites.")
    parser.add_argument("--plain", action="store_true",
                        help="Print the raw report markdown instead of the terminal "
                             "view. Implied when stdout is not a terminal.")
    parser.add_argument("--json", action="store_true", help="Print the report JSON path only.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Run the deterministic path only: a broad sweep and a "
                             "template-rendered report. Needs no API key.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Include debug-level stage output.")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress the stage log; print only the report.")
    args = parser.parse_args(argv)

    paths = open_from_args(args)
    config: dict = {}
    if args.sources:
        unknown = [s for s in args.sources if s not in paths.spec.source_ids]
        if unknown:
            print(f"Company '{paths.slug}' declares no source(s) {unknown}. "
                  f"Known: {paths.spec.source_ids}", file=sys.stderr)
            return 2
        config["sources"] = list(args.sources)
    config["top_events"] = args.top_events
    config["time_grain"] = args.time_grain
    config["entity_keys"] = args.entity_keys

    llm = None
    if not args.no_llm:
        try:
            llm = build_llm(args.model)
        except LlmUnavailable as exc:
            # A missing key must not cost the analysis. The deterministic path still
            # runs and still reports; it just reports in plainer prose.
            print(f"No model available ({exc}); falling back to the deterministic path.\n"
                  "Run with --no-llm to make that explicit.", file=sys.stderr)

    # The log runs on the deterministic path too. It used to be gated on there being
    # a model, which left `--no-llm` completely silent through the longest part of
    # the run -- the pipeline, not the model calls.
    if not args.json and not args.quiet:
        _setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    run_id = args.run_id or f"ask-{dt.datetime.now():%Y%m%d-%H%M%S}"
    state = run_agent(
        args.question, company=paths, llm=llm, persona=args.persona,
        run_id=run_id, config=config,
    )

    if args.json:
        print(state.get("report_path", ""))
        return 0

    con = console(force_plain=args.plain or not sys.stdout.isatty())
    narrative = state.get("narrative")
    context = state.get("context")

    if con is not None and narrative is not None and context is not None:
        from kpi_agent.render import render_console

        render_console(
            con,
            narrative=narrative,
            context=context,
            verification=state["verification"],
            telemetry=state.get("telemetry") or {},
            intent=state.get("intent"),
            intent_problems=state.get("intent_problems"),
            results=state.get("results"),
            report_path=state.get("report_path", ""),
            show_all_facts=args.facts,
        )
    elif con is None:
        # `--plain`, a pipe, or no rich: hand over the artefact verbatim and nothing
        # else, so `ask --plain > report.md` and the written file are the same bytes.
        # `write`, not `print`: the markdown already ends in a newline, and an
        # extra one would make `ask --plain > x.md` differ from the written file.
        sys.stdout.write(state.get("report_markdown", "(no report produced)\n"))
    else:
        # Clarifications and no-findings runs never build a narrative. They have a
        # report but no facts, no verification and often no telemetry.
        print("\n" + state.get("report_markdown", "(no report produced)"))
        telemetry = state.get("telemetry")
        if telemetry:
            banner("RUNTIME TELEMETRY")
            kv("deterministic stages", telemetry["deterministic_stages"])
            kv("deterministic runtime", f"{telemetry['deterministic_ms']:.0f} ms")
            kv("model calls", telemetry["llm_calls"])
            kv("model", telemetry.get("model") or "—")
            kv("tokens in / out",
               f"{telemetry['llm_tokens_in']:,} / {telemetry['llm_tokens_out']:,}")
            cost = telemetry["llm_cost_usd"]
            kv("estimated cost", f"${cost:.4f}" if cost is not None else "not priced")
            if telemetry["used_fallback"]:
                kv("fallback used", telemetry["fallback_reason"])
            kv("report", state.get("report_path", "—"))

    for err in state.get("errors", []):
        print(f"  ! {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
