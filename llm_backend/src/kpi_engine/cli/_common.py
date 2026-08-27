"""Shared CLI plumbing: company selection, run ids, console reporting.

There are no path defaults here any more. Every command names a company with
`--company`, and every path it needs comes from that company's `company.yaml` by
way of `tenancy.CompanyPaths`. A default like
`configs/sources/retail_csv.yaml` would silently point every tenant at one
tenant's data.
"""

from __future__ import annotations

import argparse
import datetime as dt

from typing import Any

from kpi_engine.tenancy import add_company_argument, open_from_args  # noqa: F401 (re-export)


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{dt.datetime.now():%Y%m%d-%H%M%S}"


def add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """`--company` plus the four flags that shape an analysis.

    `--source-id` names a source the company has *declared*, not a file on disk.
    That is deliberate: the old `--source`/`--contract` took arbitrary paths, which
    made "which config does this run use" unanswerable from the company folder and
    let a caller read any YAML on the machine.
    """
    add_company_argument(parser)
    parser.add_argument(
        "--source-id",
        default=None,
        help="Which declared source to run against. Defaults to the company's primary.",
    )
    parser.add_argument(
        "--entity-keys",
        nargs="*",
        default=None,
        help="Dimensions to slice by, overriding the contract (e.g. Region Channel). "
        "Omit for total level.",
    )
    parser.add_argument("--kpis", nargs="*", default=None, help="Subset of KPIs to process.")
    parser.add_argument(
        "--time-grain",
        default=None,
        choices=["day", "week", "month"],
        help="Override the contract's time grain. Coarser grains trade resolution for "
        "signal-to-noise, which matters when cells are thin.",
    )
    return parser


def apply_overrides(contract, args):
    """Apply CLI overrides to a loaded contract."""
    if getattr(args, "time_grain", None):
        contract = contract.model_copy(update={"time_grain": args.time_grain})
    return contract


def selected_source(paths, args) -> str:
    """The source id this invocation runs against, validated against the company."""
    source_id = getattr(args, "source_id", None) or paths.primary_source_id
    paths.spec.binding(source_id)  # raises KeyError naming the declared ids
    return source_id


def banner(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def kv(label: str, value: object, indent: int = 2) -> None:
    print(f"{' ' * indent}{label:<34} {value}")


# --------------------------------------------------------------------------- #
# Optional rich rendering
#
# `rich` lives in the `cli` extra and is imported lazily, so every helper below
# has a plain-text answer. A missing dependency costs colour and box-drawing; it
# never costs a line of output, and it never changes what a command computes.
# --------------------------------------------------------------------------- #

_consoles: dict[bool, Any] = {}
_rich_available: bool | None = None


def console(force_plain: bool = False, stderr: bool = False):
    """The shared rich Console, or None when rich is absent or plain is forced.

    Two of them: the report goes to stdout so it can be piped, the stage log to
    stderr so piping the report does not carry the log along with it. Built once
    each. Callers branch on None rather than importing rich themselves, which
    keeps the dependency reachable from exactly one place.
    """
    global _rich_available
    if force_plain:
        return None
    if _rich_available is False:
        return None
    if stderr not in _consoles:
        try:
            from rich.console import Console
        except ImportError:
            _rich_available = False
            return None
        _rich_available = True
        _consoles[stderr] = Console(stderr=stderr, soft_wrap=False)
    return _consoles[stderr]
