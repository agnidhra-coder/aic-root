"""The DataSource contract every backend implements.

Nothing downstream of this module knows whether the data came from a CSV, a
spreadsheet, or a warehouse. Swapping the database therefore means writing one
adapter, not touching the pipeline.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import pandas as pd

from kpi_engine.contracts.configs import SourceSpec
from kpi_engine.contracts.payloads import Freshness, SourceDescription

_CADENCE_TOLERANCE_DAYS = {"daily": 2, "weekly": 9, "monthly": 35, "unknown": 10**6}


class DataSource(ABC):
    """Read-only access to one dataset, plus the metadata the engine needs to trust it."""

    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec

    @abstractmethod
    def describe(self) -> SourceDescription:
        """Columns, dtypes and row count without committing to loading everything."""

    @abstractmethod
    def load(
        self,
        columns: list[str] | None = None,
        date_range: tuple[dt.date, dt.date] | None = None,
    ) -> pd.DataFrame:
        """Materialise rows, optionally projected and date-filtered."""

    @abstractmethod
    def _max_date(self) -> dt.date | None:
        """Latest date present, used for freshness."""

    def freshness(self) -> Freshness:
        """How far behind `as_of` this source is, and whether that breaches its cadence."""
        as_of = self.spec.as_of or dt.date.today()
        max_date = self._max_date()
        lag = (as_of - max_date).days if max_date else None
        tolerance = _CADENCE_TOLERANCE_DAYS[self.spec.refresh_cadence]
        return Freshness(
            source_id=self.spec.source_id,
            max_date=max_date,
            as_of=as_of,
            lag_days=lag,
            refresh_cadence=self.spec.refresh_cadence,
            is_stale=bool(lag is not None and lag > tolerance),
        )

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """Shared normalisation: parse the date column, apply dtype overrides, sort."""
        if self.spec.date_column in df.columns:
            df[self.spec.date_column] = pd.to_datetime(df[self.spec.date_column])
        for col, dtype in self.spec.dtype_overrides.items():
            if col in df.columns:
                df[col] = df[col].astype(dtype)
        sort_cols = [c for c in [self.spec.date_column, *self.spec.entity_columns] if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols).reset_index(drop=True)
        return df

    @staticmethod
    def _filter_dates(
        df: pd.DataFrame, date_column: str, date_range: tuple[dt.date, dt.date] | None
    ) -> pd.DataFrame:
        if date_range is None or date_column not in df.columns:
            return df
        start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
        return df[(df[date_column] >= start) & (df[date_column] <= end)]
