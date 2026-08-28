"""Matching a tenant's columns to the KPI vocabulary.

Three things live here, all deterministic and all testable without a model:
locating and loading the seed catalogue, recognising a column by name, and
turning a chosen variant plus a set of column bindings into a real `KpiDef`.

The matcher is deliberately exact. `normalise` lowercases and strips
non-alphanumerics, and a synonym matches only when its normalised form equals a
normalised header. No edit distance, no embeddings, no fuzz -- the claim a match
makes is "I recognised this header", and an approximate match cannot honestly
make it. Everything ambiguous is left for a human or a model to decide, and both
are shown exactly what was already settled.

That exactness is what makes the no-LLM path real rather than a stub: on a
well-named extract it binds most of the vocabulary on its own, and what it binds
it binds correctly.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from kpi_engine.config_io import load_seed_catalog
from kpi_engine.contracts.catalogue import SeedCatalog, SeedKpi, SeedVariant
from kpi_engine.contracts.configs import KpiDef, MeasureDef

CATALOG_FILENAME = "kpi_catalog.yaml"

_CACHE: dict[Path, tuple[float, SeedCatalog]] = {}
_LOCK = threading.Lock()


class UnbindableVariant(ValueError):
    """A variant was asked to bind with an alias missing or unknown."""


def catalog_path() -> Path:
    """Where the vocabulary lives.

    Resolved through `tenancy.templates_root()`, which is the one function
    allowed to locate `templates/`. This is product-level reference material, not
    a company config: it is never copied into a tenant, no `company.yaml` points
    at it, and `$KPI_USER_ROOT` does not move it. A tenant's own contract is
    *derived* from it and lives under that tenant, resolved the usual way.
    """
    from kpi_engine.tenancy import templates_root

    return templates_root() / "reference" / CATALOG_FILENAME


def load_catalog(path: Path | None = None) -> SeedCatalog:
    """The catalogue, memoised on mtime so an edit is picked up without a restart."""
    target = path or catalog_path()
    if not target.exists():
        raise FileNotFoundError(
            f"No KPI catalogue at {target}. It is the vocabulary every tenant's "
            "contract is derived from; without it no KPI can be proposed."
        )
    mtime = target.stat().st_mtime
    with _LOCK:
        cached = _CACHE.get(target)
        if cached and cached[0] == mtime:
            return cached[1]
    catalog = load_seed_catalog(target)
    with _LOCK:
        _CACHE[target] = (mtime, catalog)
    return catalog


def forget_catalog() -> None:
    """Drop the memoised catalogue. For tests."""
    with _LOCK:
        _CACHE.clear()


# --------------------------------------------------------------------------- matching


def normalise(name: str) -> str:
    """Fold a column header to its comparable form.

    The same rule `server/src/uploads/kpi-contracts.ts::normalizeColumnName`
    applies on the NestJS side, so a header the upload validator accepted is
    recognised here the same way.
    """
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def match_column(synonyms: list[str], header: list[str]) -> str | None:
    """The first header column a synonym names exactly, or None.

    Synonyms are walked in declared order, so the catalogue's own preference
    decides when a file carries more than one candidate -- `Total Revenue` before
    `Net Sales` before `Revenue`. Header order never decides, because a file's
    column order carries no meaning.
    """
    by_norm: dict[str, str] = {}
    for column in header:
        by_norm.setdefault(normalise(column), column)
    for synonym in synonyms:
        hit = by_norm.get(normalise(synonym))
        if hit is not None:
            return hit
    return None


def bind_by_synonym(variant: SeedVariant, header: list[str]) -> dict[str, str]:
    """Every alias of a variant that a header column names outright.

    Two aliases never take the same column: an alias that would collide with one
    already bound is left unbound instead, because a variant computing
    `revenue - revenue` is worse than one that does not compute at all.
    """
    bound: dict[str, str] = {}
    taken: set[str] = set()
    for measure in variant.measures:
        column = match_column(measure.synonyms, header)
        if column is None or column in taken:
            continue
        bound[measure.alias] = column
        taken.add(column)
    return bound


def select_variant(
    seed: SeedKpi, header: list[str], *, allow_unsafe: bool = False
) -> tuple[SeedVariant, dict[str, str]] | None:
    """The first fully bindable variant of a KPI, with its bindings.

    Order is the catalogue's, which is what turns the document's `OR` into a
    deterministic choice: `both_channels` before `digital_only`, a directly
    supplied `COGS` column before the stock-flow identity that reconstructs it.

    A variant that is not `aggregation_safe` is skipped unless asked for. Those
    are not wrong, they are wrong *once summed to a grain* -- and since the panel
    aggregates before it evaluates, choosing one automatically would produce a
    confidently wrong number. It stays visible and opt-in.
    """
    for variant in seed.variants:
        if not variant.aggregation_safe and not allow_unsafe:
            continue
        bound = bind_by_synonym(variant, header)
        if len(bound) == len(variant.measures):
            return variant, bound
    return None


def bindable_variants(seed: SeedKpi, header: list[str]) -> list[str]:
    """Every variant this header could satisfy, safe or not. For showing choices."""
    out = []
    for variant in seed.variants:
        bound = bind_by_synonym(variant, header)
        if len(bound) == len(variant.measures):
            out.append(variant.variant_id)
    return out


# --------------------------------------------------------------------------- binding


def bind_variant(
    seed: SeedKpi, variant: SeedVariant, columns: dict[str, str]
) -> KpiDef:
    """Turn a chosen variant plus alias->column into the real thing.

    The only place a `KpiDef` is constructed from the catalogue. It never
    fabricates a column: an alias without one raises, and the caller records the
    KPI as unavailable rather than emitting a definition that names a column the
    file does not carry.

    Name, unit, direction, category, drivers, materiality and guards all come
    from the catalogue. Nothing a model said reaches any of them.
    """
    missing = [a for a in variant.aliases if a not in columns]
    if missing:
        raise UnbindableVariant(
            f"KPI '{seed.name}' variant '{variant.variant_id}' has no column for "
            f"{missing}; it cannot be computed from this file."
        )
    unknown = [a for a in columns if a not in variant.aliases]
    if unknown:
        raise UnbindableVariant(
            f"KPI '{seed.name}' variant '{variant.variant_id}' declares no alias(es) "
            f"{unknown}. Known: {variant.aliases}."
        )
    chosen = [columns[a] for a in variant.aliases]
    dupes = sorted({c for c in chosen if chosen.count(c) > 1})
    if dupes:
        raise UnbindableVariant(
            f"KPI '{seed.name}' would bind {dupes} to more than one alias, which "
            "makes its expression degenerate."
        )
    return KpiDef(
        name=seed.name,
        category=seed.category,
        description=seed.description,
        measures={
            m.alias: MeasureDef(column=columns[m.alias], agg=m.agg)
            for m in variant.measures
        },
        expression=variant.expression,
        unit=seed.unit,
        direction=seed.direction,
        drivers=list(seed.drivers),
        materiality=seed.materiality,
        guards=seed.guards,
    )
