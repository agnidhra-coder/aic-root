"""Profiles a source before any modelling touches it.

The load-bearing part is redundancy detection. This dataset carries columns that
are exact copies of one another (Net Sales / Total Revenue / Retail Sales) and
columns that are exact linear functions of others (Total Gross Profit = Total
Revenue - COGS). Handing those to a regression as independent drivers produces
an arbitrary split of the true effect across collinear terms -- the fit looks
fine and the attribution is meaningless. So we find them here and the causal
layer refuses to use them.
"""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Iterable

import pandas as pd

from kpi_engine.contracts.payloads import (
    ColumnProfile,
    DataProfile,
    GrainCoverage,
    IdentityFinding,
)
from kpi_engine.sources.base import DataSource

DUPLICATE_TOLERANCE = 1e-6
IDENTITY_TOLERANCE = 1e-6
MIN_ROWS_PER_CELL_SUFFICIENT = 3.0


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _column_profiles(df: pd.DataFrame) -> list[ColumnProfile]:
    profiles: list[ColumnProfile] = []
    for col in df.columns:
        s = df[col]
        numeric = pd.api.types.is_numeric_dtype(s)
        profiles.append(
            ColumnProfile(
                name=str(col),
                dtype=str(s.dtype),
                non_null=int(s.notna().sum()),
                nulls=int(s.isna().sum()),
                zeros=int((s == 0).sum()) if numeric else 0,
                min=float(s.min()) if numeric and s.notna().any() else None,
                max=float(s.max()) if numeric and s.notna().any() else None,
                mean=float(s.mean()) if numeric and s.notna().any() else None,
            )
        )
    return profiles


def find_duplicate_groups(df: pd.DataFrame, columns: Iterable[str]) -> list[list[str]]:
    """Group columns that are numerically identical row-for-row.

    Grouped by rounded column hash first so this stays linear in practice rather
    than comparing every pair.
    """
    buckets: dict[tuple, list[str]] = {}
    for col in columns:
        s = df[col].astype("float64")
        key = (round(float(s.sum()), 4), round(float(s.std()), 6), int(s.notna().sum()))
        buckets.setdefault(key, []).append(col)

    groups: list[list[str]] = []
    for candidates in buckets.values():
        if len(candidates) < 2:
            continue
        remaining = list(candidates)
        while remaining:
            head = remaining.pop(0)
            group = [head]
            for other in list(remaining):
                diff = (df[head].astype("float64") - df[other].astype("float64")).abs().max()
                if pd.notna(diff) and diff <= DUPLICATE_TOLERANCE:
                    group.append(other)
                    remaining.remove(other)
            if len(group) > 1:
                groups.append(group)
    return groups


def find_linear_identities(
    df: pd.DataFrame, columns: list[str], max_terms: int = 3, sample: int = 3000
) -> list[IdentityFinding]:
    """Find columns exactly reproducible as a signed sum of up to `max_terms` others.

    Restricted to unit coefficients (+1/-1) because that is what accounting
    identities look like, and searching arbitrary coefficients over 38 columns
    would be both slow and prone to spurious fits on noise.
    """
    work = df[columns].dropna()
    if len(work) > sample:
        work = work.sample(sample, random_state=0)
    if work.empty:
        return []

    findings: list[IdentityFinding] = []
    scales = {c: max(abs(float(work[c].abs().max())), 1.0) for c in columns}
    explained: set[str] = set()

    for target in columns:
        if target in explained:
            continue
        others = [c for c in columns if c != target]
        found = None
        for n_terms in range(2, max_terms + 1):
            for combo in itertools.combinations(others, n_terms):
                for signs in itertools.product((1.0, -1.0), repeat=n_terms):
                    if signs[0] < 0:  # sign-flipped duplicate of another candidate
                        continue
                    approx = sum(s * work[c] for s, c in zip(signs, combo))
                    resid = float((work[target] - approx).abs().max()) / scales[target]
                    if resid <= IDENTITY_TOLERANCE:
                        found = IdentityFinding(
                            target=target,
                            components=list(combo),
                            coefficients=list(signs),
                            max_abs_residual=resid,
                            kind="linear_identity",
                        )
                        break
                if found:
                    break
            if found:
                break
        if found:
            findings.append(found)
            explained.add(target)
    return findings


def measure_coverage(
    df: pd.DataFrame, date_column: str, grains: list[list[str]]
) -> list[GrainCoverage]:
    """Rows per (slice, period) cell at each candidate grain.

    A grain whose cells hold roughly one row cannot support time-series methods;
    reporting that here is what lets the pipeline abstain instead of modelling noise.
    """
    n_periods = df[date_column].nunique()
    out: list[GrainCoverage] = []
    for keys in grains:
        present = [k for k in keys if k in df.columns]
        if len(present) != len(keys):
            continue
        n_slices = int(df[present].drop_duplicates().shape[0]) if present else 1
        filled = int(df.groupby(present + [date_column], observed=True).ngroups) if present \
            else n_periods
        possible = n_slices * n_periods
        mean_rows = len(df) / filled if filled else 0.0
        out.append(
            GrainCoverage(
                keys=keys,
                n_slices=n_slices,
                filled_cells=filled,
                possible_cells=possible,
                mean_rows_per_cell=round(mean_rows, 3),
                sufficient=mean_rows >= MIN_ROWS_PER_CELL_SUFFICIENT,
            )
        )
    return out


def _candidate_grains(entity_columns: list[str]) -> list[list[str]]:
    """Total, each dimension alone, then progressively deeper combinations."""
    grains: list[list[str]] = [[]]
    grains += [[c] for c in entity_columns]
    for size in (2, 3):
        if len(entity_columns) >= size:
            grains.append(entity_columns[:size])
    return grains


def resolve_redundancy(
    column_order: list[str],
    duplicate_groups: list[list[str]],
    identities: list[IdentityFinding],
) -> tuple[list[list[str]], dict[str, str]]:
    """Decide the minimal set of columns to bar as independent drivers.

    Two distinct kinds of redundancy, and they need different accounting:

    * A duplicate group of k identical columns carries one column of information,
      so k-1 are dropped and the first is kept.
    * An identity group of k columns tied by one equation carries k-1 independent
      columns, so exactly *one* is dropped -- not all k. Dropping every member
      would delete revenue and customer counts from the driver set entirely.

    The member dropped is the first in dataset order that is not already the
    surviving representative of a duplicate group, which keeps the choice
    deterministic and avoids compounding the two rules.
    """
    order = {c: i for i, c in enumerate(column_order)}
    redundant: dict[str, str] = {}

    dup_representatives = {group[0] for group in duplicate_groups}
    for group in duplicate_groups:
        for col in group[1:]:
            redundant[col] = f"exact duplicate of '{group[0]}'"

    # Collapse the findings into one group per underlying constraint: an identity
    # reported from three different subjects is still a single equation.
    seen: set[frozenset[str]] = set()
    identity_groups: list[list[str]] = []
    for ident in identities:
        members = frozenset([ident.target, *ident.components])
        if members in seen:
            continue
        seen.add(members)
        identity_groups.append(sorted(members, key=lambda c: order.get(c, 10**6)))

    for members in identity_groups:
        if any(m in redundant for m in members):
            continue  # already down to k-1 through the duplicate rule
        droppable = [m for m in members if m not in dup_representatives] or members
        victim = droppable[0]
        others = [m for m in members if m != victim]
        redundant[victim] = f"exact linear function of {others}"

    return identity_groups, redundant


def profile_source(source: DataSource, df: pd.DataFrame | None = None) -> DataProfile:
    """Full profile: schema, freshness, redundancy, coverage."""
    frame = source.load() if df is None else df
    spec = source.spec
    numeric = _numeric_columns(frame)

    duplicate_groups = find_duplicate_groups(frame, numeric)
    # Only one representative of each duplicate group takes part in identity search,
    # otherwise every duplicate generates a trivially true "identity".
    dupes_to_skip = {c for group in duplicate_groups for c in group[1:]}
    identity_candidates = [c for c in numeric if c not in dupes_to_skip]

    identities = find_linear_identities(frame, identity_candidates)
    identity_groups, redundant = resolve_redundancy(
        list(frame.columns), duplicate_groups, identities
    )

    return DataProfile(
        source_id=spec.source_id,
        generated_at=dt.datetime.now(),
        description=source.describe(),
        freshness=source.freshness(),
        columns=_column_profiles(frame),
        duplicate_groups=duplicate_groups,
        identities=identities,
        identity_groups=identity_groups,
        redundant_columns=redundant,
        coverage=measure_coverage(frame, spec.date_column, _candidate_grains(spec.entity_columns)),
    )
