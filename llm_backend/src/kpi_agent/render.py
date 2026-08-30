"""Render the verified narrative as markdown.

The fact table is printed in full at the end, with lineage. That is not an appendix
nobody reads -- it is the artefact that makes the prose checkable by a human the
same way the verifier checks it mechanically. A report whose numbers cannot be
traced back is exactly what this system exists not to produce.
"""

from __future__ import annotations

from typing import Any

from kpi_agent.models import GroundedContext, Narrative, VerificationResult


def render_markdown(
    narrative: Narrative,
    context: GroundedContext,
    verification: VerificationResult,
    telemetry: dict[str, Any],
) -> str:
    lines: list[str] = []
    a = lines.append

    a(f"# {narrative.headline}\n")
    a(f"**Question** — {context.question}  ")
    a(f"**Understood as** — {context.question_restated}  ")
    a(f"**Audience** — {context.persona}  ")
    a(f"**Grain** — {context.time_grain}"
      + (f", sliced by {', '.join(context.entity_keys)}" if context.entity_keys else ", total level")
      + "  ")
    a(f"**Run** — `{context.run_id}`\n")

    _section(a, "What happened", narrative.what_happened)
    _section(a, "Why", narrative.why)
    _section(a, "Needs attention", narrative.needs_attention)

    if narrative.actions:
        a("## Recommended actions\n")
        a("| Driver | Lever | Action | Owner | Expected impact | Confidence | Monitoring | Evidence |")
        a("|---|---|---|---|---|---|---|---|")
        for act in narrative.actions:
            a(
                f"| {act.driver} | {act.lever} | {act.action} | {act.owner} | "
                f"{act.expected_impact} | {_confidence(act.confidence)} | {act.monitoring} | "
                f"{', '.join(act.evidence_ids) or '—'} |"
            )
        a("")

    if narrative.general_recommendations:
        a("## Other suggestions\n")
        a("General practice for KPIs that moved this way, from the model's own "
          "knowledge rather than this data. Nothing below was measured, nothing "
          "cites the evidence table, and none of it is a cause.\n")
        for rec in narrative.general_recommendations:
            a(f"- **{rec.related_kpi}** — {rec.action} _{rec.rationale}_")
        a("")

    if narrative.abstained_from:
        a("## Not answered\n")
        a("The engine abstained rather than explain these. Each entry names what is "
          "missing and what would resolve it.\n")
        for item in narrative.abstained_from:
            a(f"- {item}")
        a("")

    if narrative.uncertainty:
        a("## Uncertainty\n")
        a(narrative.uncertainty + "\n")

    if context.exogenous:
        a("## Context you provided\n")
        a("Stated in the question, not measured by the engine. Where a row lines up "
          "with a detected event, that is a coincidence in time and not a causal "
          "path — unlike the cross-source links below, nothing licenses it.\n")
        a("| Factor | As stated | Window | Scope | Lines up with |")
        a("|---|---|---|---|---|")
        by_label: dict[str, list] = {}
        for alignment in context.alignments:
            by_label.setdefault(alignment.factor_label, []).append(alignment)
        for factor in context.exogenous:
            label = factor.get("label", "")
            hits = by_label.get(label, [])
            window = " to ".join(
                x for x in (factor.get("date_start"), factor.get("date_end")) if x
            ) or "no date given"
            scope = (
                f"{factor['entity_key']}={factor['entity_value']}"
                if factor.get("entity_key") and factor.get("entity_value")
                else "unscoped"
            )
            lines_up = ", ".join(
                f"{h.event_id} ({h.overlap_days}d overlap)" if h.overlap_days
                else f"{h.event_id} ({h.lag_days}d apart)"
                for h in hits
            ) or "nothing detected"
            a(f"| {label} | {factor.get('detail', '')} | {window} | {scope} | {lines_up} |")
        a("")

    if context.links:
        a("## Cross-source links\n")
        a("Each row required a declared edge in the causal graph. Overlapping windows "
          "alone do not produce a link.\n")
        a("| Supply event | Sales event | Path through the graph | Slice | Overlap | Lag |")
        a("|---|---|---|---|---|---|")
        for link in context.links:
            slice_ = ", ".join(f"{k}={v}" for k, v in link.shared_entity.items()) or "total"
            a(
                f"| {link.scm_event_id} | {link.sales_event_id} | "
                f"{' → '.join(link.dag_path)} | {slice_} | "
                f"{link.overlap_days}d | {link.lag_days}d |"
            )
        a("")

    a("## Verification\n")
    if verification.passed:
        a(f"Passed. {verification.numbers_checked} numeric claims and "
          f"{verification.claims_checked} statements were checked against the fact "
          f"table; every one resolved.\n")
    else:
        a(f"**Failed** with {len(verification.violations)} violation(s); this report "
          f"was rendered from the deterministic fallback rather than the model's text.\n")
        for v in verification.violations:
            a(f"- `{v.code}` at {v.where}: {v.detail}")
        a("")

    a("## Evidence\n")
    a("| id | fact | method | kind | source | slice |")
    a("|---|---|---|---|---|---|")
    for fact in context.facts:
        slice_ = ", ".join(f"{k}={v}" for k, v in fact.entity.items()) or "total"
        method = fact.method or "—"
        if fact.exact is True:
            method += " (exact)"
        elif fact.exact is False:
            method += " (estimated)"
        a(f"| {fact.id} | {fact.display} | {method} | {fact.kind} | "
          f"{fact.source_id or '—'} | {slice_} |")
    a("")

    if context.data_caveats:
        a("## Data caveats\n")
        for caveat in context.data_caveats:
            a(f"- {caveat}")
        a("")

    a("## Deterministic vs LLM\n")
    a("| | |")
    a("|---|---|")
    a(f"| Deterministic stages | {telemetry.get('deterministic_stages', 0)} |")
    a(f"| Deterministic runtime | {telemetry.get('deterministic_ms', 0):.0f} ms |")
    a(f"| Model | {telemetry.get('model') or '—'} |")
    a(f"| Model calls | {telemetry.get('llm_calls', 0)} |")
    a(f"| Tokens in / out | {telemetry.get('llm_tokens_in', 0):,} / "
      f"{telemetry.get('llm_tokens_out', 0):,} |")
    cost = telemetry.get("llm_cost_usd")
    a("| Estimated model cost | "
      + (f"${cost:.4f} |" if cost is not None else "not priced for this model |"))
    a("")
    a("Every number above the Verification section was computed by the deterministic "
      "engine. The model chose the analysis configuration and wrote the prose; it "
      "produced no quantity.\n")

    return "\n".join(lines)


def _confidence(score: float | None) -> str:
    """An em dash for a recommendation nothing measured a confidence for.

    `0.00` would read as "we are confident this is worthless", which is a
    different and much stronger claim than "no event backed this, so there is no
    score to quote". Shared by the markdown artefact and the console view so the
    two cannot say different things about the same action.
    """
    return "—" if score is None else f"{score:.2f}"


def _section(a, title: str, claims: list) -> None:
    if not claims:
        return
    a(f"## {title}\n")
    for claim in claims:
        cites = f" [{', '.join(claim.evidence_ids)}]" if claim.evidence_ids else ""
        a(f"- {claim.text}{cites}")
    a("")


# --------------------------------------------------------------------------- #
# Terminal rendering
#
# A second view of the same objects, not a second source of truth. `render_markdown`
# above stays byte-identical because `agent_report.md` is the checkable artefact;
# this exists because that artefact, printed raw, is markdown source -- pipe tables
# and all -- and a reader cannot tell from it which sentences a model wrote and
# which numbers the engine computed. Here that boundary is drawn explicitly:
# everything the model produced sits inside a labelled panel, and everything else
# is labelled as the engine's.
# --------------------------------------------------------------------------- #

MODEL_STYLE = "magenta"
ENGINE_STYLE = "cyan"


def engine_summary(results: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per-source counts, as data rather than as a table.

    The console's engine table and the API's `engine` event are two renderings of
    this one list. Computing it twice is how the terminal and the wire come to
    disagree about how many events a run found.
    """
    rows = []
    for source_id, result in (results or {}).items():
        rows.append({
            "source_id": source_id,
            "time_grain": result.contract.time_grain,
            "kpis": len(result.panel.kpi_names()),
            "flags": len(result.flags),
            "events": len(result.events),
            "explained": len(result.bundles),
        })
    return rows


def abstention_summary(context: GroundedContext) -> list[dict[str, Any]]:
    """Abstentions collapsed by (kpi, reason).

    Five events abstaining for one reason on one KPI is one finding, not five --
    `fallback_narrative` collapses them for the same reason, and a panel that did
    not would read as a wall of identical yellow.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for a in context.abstentions:
        grouped.setdefault((a.get("kpi", "?"), a.get("reason_code", "?")), []).append(a)
    return [
        {
            "kpi": kpi,
            "reason_code": code,
            "events": len(group),
            "message": str(group[0].get("message", "")).strip(),
        }
        for (kpi, code), group in grouped.items()
    ]


def render_console(
    console: Any,
    *,
    narrative: Narrative,
    context: GroundedContext,
    verification: VerificationResult,
    telemetry: dict[str, Any],
    intent: Any = None,
    intent_problems: list[str] | None = None,
    results: dict[str, Any] | None = None,
    report_path: str = "",
    show_all_facts: bool = False,
) -> None:
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    used_fallback = bool(telemetry.get("used_fallback"))

    # ---- 1. what the model decided to analyse ------------------------------ #
    console.print()
    plan = Table.grid(padding=(0, 2))
    plan.add_column(style="dim", justify="right")
    plan.add_column()
    plan.add_row("question", context.question)
    plan.add_row("understood as", Text(context.question_restated, style="italic"))
    if intent is not None and getattr(intent, "reasoning", None):
        plan.add_row("planner's reasoning", Text(intent.reasoning, style="italic dim"))
    plan.add_row("audience", context.persona)
    plan.add_row("grain", context.time_grain + (
        f", sliced by {', '.join(context.entity_keys)}"
        if context.entity_keys else ", total level"))
    if intent is not None and getattr(intent, "sources", None):
        plan.add_row("sources", ", ".join(intent.sources))
    if intent is not None and getattr(intent, "kpis", None):
        plan.add_row("KPIs", ", ".join(intent.kpis))
    if context.period_start or context.period_end:
        plan.add_row("report window",
                     f"{context.period_start or 'start'} to {context.period_end or 'end'}")
    plan.add_row("run", context.run_id)
    for problem in intent_problems or []:
        plan.add_row(Text("adjusted", style="yellow"), Text(problem, style="yellow"))

    console.print(Panel(
        plan,
        title="[bold]ANALYSIS PLAN[/bold]",
        subtitle=("[dim]chosen by the deterministic default[/dim]" if used_fallback
                  and not telemetry.get("model")
                  else f"[dim]model call #1 · {telemetry.get('model') or 'no model'}[/dim]"),
        border_style=MODEL_STYLE, padding=(1, 2),
    ))

    # ---- 2. what the engine computed --------------------------------------- #
    engine = Table.grid(padding=(0, 1))
    engine.add_column()
    if results:
        per_source = Table(box=None, pad_edge=False, header_style="dim")
        for col, justify in (("source", "left"), ("grain", "left"), ("KPIs", "right"),
                             ("flags", "right"), ("events", "right"), ("explained", "right")):
            per_source.add_column(col, justify=justify)
        for row in engine_summary(results):
            n_events = row["events"]
            per_source.add_row(
                row["source_id"],
                row["time_grain"],
                str(row["kpis"]),
                str(row["flags"]),
                Text(str(n_events), style="" if n_events else "yellow"),
                str(row["explained"]),
            )
        engine.add_row(per_source)

    if context.exogenous:
        engine.add_row("")
        engine.add_row(Text(
            "context you provided — stated, not measured; overlap is coincidence, "
            "not a causal path",
            style="dim",
        ))
        supplied = Table(box=None, pad_edge=False, header_style="dim")
        for col in ("factor", "window", "scope", "lines up with"):
            supplied.add_column(col, overflow="fold")
        by_label: dict[str, list] = {}
        for alignment in context.alignments:
            by_label.setdefault(alignment.factor_label, []).append(alignment)
        for factor in context.exogenous:
            hits = by_label.get(factor.get("label", ""), [])
            window = " to ".join(
                x for x in (factor.get("date_start"), factor.get("date_end")) if x
            ) or "no date given"
            scope = (
                f"{factor['entity_key']}={factor['entity_value']}"
                if factor.get("entity_key") and factor.get("entity_value")
                else "unscoped"
            )
            supplied.add_row(
                factor.get("label", ""), window, scope,
                Text(", ".join(h.event_id for h in hits) or "nothing detected",
                     style="" if hits else "yellow"),
            )
        engine.add_row(supplied)

    if context.links:
        engine.add_row("")
        engine.add_row(Text("cross-source links — each one required a declared DAG edge",
                            style="dim"))
        links = Table(box=None, pad_edge=False, header_style="dim")
        for col in ("supply event", "sales event", "path through the graph", "slice", "overlap", "lag"):
            links.add_column(col)
        for link in context.links:
            slice_ = ", ".join(f"{k}={v}" for k, v in link.shared_entity.items()) or "total"
            links.add_row(link.scm_event_id, link.sales_event_id,
                          " → ".join(link.dag_path), slice_,
                          f"{link.overlap_days}d", f"{link.lag_days}d")
        engine.add_row(links)

    if context.abstentions:
        engine.add_row("")
        engine.add_row(Text("abstentions — the engine declined to explain these",
                            style="dim"))
        rows = Table.grid(padding=(0, 1))
        rows.add_column(width=1, no_wrap=True)
        rows.add_column(overflow="fold", style="yellow")
        for item in abstention_summary(context):
            count = f" ({item['events']} events)" if item["events"] > 1 else ""
            rows.add_row("•", Text(f"{item['kpi']} ({item['reason_code']}){count}: "
                                   f"{item['message']}"))
        engine.add_row(rows)

    console.print(Panel(
        engine,
        title="[bold]WHAT THE ENGINE COMPUTED[/bold]",
        subtitle="[dim]deterministic · no model involved[/dim]",
        border_style=ENGINE_STYLE, padding=(1, 2),
    ))

    # ---- 3. the prose ------------------------------------------------------ #
    body = Table.grid(padding=(0, 1))
    body.add_column()
    body.add_row(Text(narrative.headline, style="bold"))
    for title, claims in (("What happened", narrative.what_happened),
                          ("Why", narrative.why),
                          ("Needs attention", narrative.needs_attention)):
        if not claims:
            continue
        body.add_row("")
        body.add_row(Text(title.upper(), style="bold dim"))
        bullets = Table.grid(padding=(0, 1))
        bullets.add_column(width=1, no_wrap=True)
        bullets.add_column(overflow="fold")
        for claim in claims:
            line = Text(claim.text)
            if claim.evidence_ids:
                line += Text(f"  [{', '.join(claim.evidence_ids)}]", style="dim")
            bullets.add_row("•", line)
        body.add_row(bullets)

    if narrative.actions:
        body.add_row("")
        body.add_row(Text("RECOMMENDED ACTIONS", style="bold dim"))
        acts = Table(box=None, pad_edge=False, header_style="dim")
        for col in ("lever", "owner", "action", "expected impact", "conf", "evidence"):
            acts.add_column(col, overflow="fold")
        for act in narrative.actions:
            acts.add_row(act.lever, act.owner, act.action, act.expected_impact,
                         _confidence(act.confidence),
                         Text(", ".join(act.evidence_ids) or "—", style="dim"))
        body.add_row(acts)

    if narrative.general_recommendations:
        body.add_row("")
        body.add_row(Text("OTHER SUGGESTIONS", style="bold dim"))
        body.add_row(Text(
            "general practice, not measured — no evidence backs these",
            style="dim",
        ))
        suggestions = Table.grid(padding=(0, 1))
        suggestions.add_column(width=1, no_wrap=True)
        suggestions.add_column(overflow="fold")
        for rec in narrative.general_recommendations:
            line = Text(f"{rec.related_kpi} — ", style="bold")
            line += Text(rec.action)
            line += Text(f" {rec.rationale}", style="italic dim")
            suggestions.add_row("•", line)
        body.add_row(suggestions)

    if narrative.abstained_from:
        body.add_row("")
        body.add_row(Text("NOT ANSWERED", style="bold dim"))
        bullets = Table.grid(padding=(0, 1))
        bullets.add_column(width=1, no_wrap=True)
        bullets.add_column(overflow="fold")
        for item in narrative.abstained_from:
            bullets.add_row("•", Text(item))
        body.add_row(bullets)

    if narrative.uncertainty:
        body.add_row("")
        body.add_row(Text("UNCERTAINTY", style="bold dim"))
        body.add_row(Text(f"  {narrative.uncertainty}", style="italic"))

    if used_fallback:
        title = "[bold]NARRATIVE — deterministic template[/bold]"
        subtitle = f"[dim]no model prose: {telemetry.get('fallback_reason') or 'fallback'}[/dim]"
        border = ENGINE_STYLE
    else:
        title = "[bold]NARRATIVE[/bold]"
        subtitle = (f"[dim]model call #2 · written by {telemetry.get('model')} · "
                    f"every figure quoted from the table below[/dim]")
        border = MODEL_STYLE
    console.print(Panel(body, title=title, subtitle=subtitle,
                        border_style=border, padding=(1, 2)))

    # ---- 4. verification --------------------------------------------------- #
    if verification.passed:
        console.print(
            f"  [green]✓[/green] verified — {verification.numbers_checked} numeric "
            f"claims and {verification.claims_checked} statements resolved against "
            f"the fact table", highlight=False,
        )
    else:
        console.print(
            f"  [red]✗[/red] verification failed with "
            f"{len(verification.violations)} violation(s)", highlight=False,
        )
        for v in verification.violations:
            console.print(f"      [red]{v.code}[/red] at {v.where}: {v.detail}",
                          highlight=False)

    # ---- 5. the facts the prose actually rests on -------------------------- #
    # Showing all 90 is what made this unreadable. The cited ones are the ones a
    # reader needs to check a sentence against; the rest are one file away.
    cited = _cited_ids(narrative)
    shown = context.facts if show_all_facts else [f for f in context.facts if f.id in cited]

    if shown:
        evidence = Table(
            box=None, pad_edge=False, header_style="dim", padding=(0, 1),
            title=("[bold]EVIDENCE[/bold]  "
                   "[dim]computed by the engine — the model quoted, never produced, these[/dim]"),
            title_justify="left",
        )
        evidence.add_column("id", style="dim")
        evidence.add_column("fact", overflow="fold")
        evidence.add_column("method")
        evidence.add_column("kind", style="dim")
        evidence.add_column("slice", style="dim")
        for fact in shown:
            slice_ = ", ".join(f"{k}={v}" for k, v in fact.entity.items()) or "total"
            if fact.exact is True:
                method = Text((fact.method or "definition") + " · exact", style="green")
            elif fact.exact is False:
                method = Text((fact.method or "estimated") + " · estimated", style="yellow")
            else:
                method = Text(fact.method or "—", style="dim")
            evidence.add_row(fact.id, fact.display, method, fact.kind, slice_)
        console.print()
        console.print(evidence)
        if not show_all_facts and len(shown) < len(context.facts):
            console.print(
                f"  [dim]{len(shown)} of {len(context.facts)} facts shown — the rest "
                f"are cited by nothing. --facts prints all of them.[/dim]",
                highlight=False,
            )

    if context.data_caveats:
        console.print()
        console.print("  [bold dim]DATA CAVEATS[/bold dim]")
        for caveat in context.data_caveats:
            console.print(f"  [dim]• {caveat}[/dim]", highlight=False)

    # ---- 6. telemetry ------------------------------------------------------ #
    console.print()
    split = Table(box=None, pad_edge=False, show_header=False, padding=(0, 2),
                  title="[bold]DETERMINISTIC vs MODEL[/bold]", title_justify="left")
    split.add_column("", style="dim")
    split.add_column("")
    cost = telemetry.get("llm_cost_usd")
    split.add_row("deterministic stages", str(telemetry.get("deterministic_stages", 0)))
    split.add_row("deterministic runtime", f"{telemetry.get('deterministic_ms', 0):.0f} ms")
    split.add_row("model", telemetry.get("model") or "—")
    split.add_row("model calls", str(telemetry.get("llm_calls", 0)))
    split.add_row("tokens in / out",
                  f"{telemetry.get('llm_tokens_in', 0):,} / {telemetry.get('llm_tokens_out', 0):,}")
    split.add_row("estimated cost",
                  f"${cost:.4f}" if cost is not None else "not priced for this model")
    if report_path:
        split.add_row("full report", report_path)
    console.print(split)

    per_call = telemetry.get("llm_per_call") or []
    if per_call:
        calls = Table(box=None, pad_edge=False, header_style="dim", padding=(0, 2))
        for col in ("model call", "tokens in", "tokens out", "cache read"):
            calls.add_column(col, justify="right")
        calls.columns[0].justify = "left"
        for call in per_call:
            calls.add_row(call.get("label", "?"), f"{call.get('tokens_in', 0):,}",
                          f"{call.get('tokens_out', 0):,}", f"{call.get('cache_read', 0):,}")
        console.print()
        console.print(calls)

    console.print()
    console.print(
        "  [dim]Every figure above came from the deterministic engine. The model "
        "chose the configuration and wrote the prose; it produced no quantity.[/dim]",
        highlight=False,
    )
    console.print()


def _cited_ids(narrative: Narrative) -> set[str]:
    cited: set[str] = set()
    for claims in (narrative.what_happened, narrative.why, narrative.needs_attention):
        for claim in claims:
            cited.update(claim.evidence_ids)
    for action in narrative.actions:
        cited.update(action.evidence_ids)
    return cited
