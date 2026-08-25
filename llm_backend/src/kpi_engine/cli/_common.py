"""Shared CLI plumbing: run directories, argument defaults, console reporting."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from typing import Any

from kpi_engine.config_io import project_root

DEFAULT_SOURCE = "configs/sources/retail_csv.yaml"
DEFAULT_CONTRACT = "configs/semantics/retail_kpis.yaml"
DEFAULT_DETECTION = "configs/detection/default.yaml"
DEFAULT_EDA = "configs/eda/default.yaml"
DEFAULT_GRAPH = "configs/causal/retail_dag.yaml"


def resolve(path: str | Path) -> Path:
    """Resolve a path relative to the project root when it is not absolute."""
    p = Path(path)
    return p if p.is_absolute() else project_root() / p


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{dt.datetime.now():%Y%m%d-%H%M%S}"


def run_dir(run_id: str, create: bool = True) -> Path:
    d = resolve("outputs") / run_id
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def latest_run_dir() -> Path:
    """Most recent run directory, so `explain_event` can default to the last detection."""
    outputs = resolve("outputs")
    runs = sorted((d for d in outputs.iterdir() if d.is_dir()), key=lambda d: d.name)
    if not runs:
        raise FileNotFoundError("No runs under outputs/. Run detect_anomalies first.")
    return runs[-1]


def add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source config YAML.")
    parser.add_argument("--contract", default=DEFAULT_CONTRACT, help="KPI contract YAML.")
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
