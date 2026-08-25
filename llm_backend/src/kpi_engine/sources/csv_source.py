"""CSV adapter."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from kpi_engine.contracts.payloads import SourceDescription
from kpi_engine.sources.base import DataSource


class CsvSource(DataSource):
    @property
    def path(self) -> Path:
        return Path(self.spec.path)

    def describe(self) -> SourceDescription:
        head = pd.read_csv(self.path, nrows=1000)
        with self.path.open() as fh:
            n_rows = sum(1 for _ in fh) - 1
        return SourceDescription(
            source_id=self.spec.source_id,
            path=str(self.path),
            n_rows=n_rows,
            columns=list(head.columns),
            dtypes={c: str(t) for c, t in head.dtypes.items()},
        )

    def load(
        self,
        columns: list[str] | None = None,
        date_range: tuple[dt.date, dt.date] | None = None,
    ) -> pd.DataFrame:
        usecols = None
        if columns:
            needed = set(columns) | {self.spec.date_column} | set(self.spec.entity_columns)
            available = set(pd.read_csv(self.path, nrows=0).columns)
            usecols = sorted(needed & available)
        df = pd.read_csv(self.path, usecols=usecols)
        df = self._postprocess(df)
        return self._filter_dates(df, self.spec.date_column, date_range).reset_index(drop=True)

    def _max_date(self) -> dt.date | None:
        col = self.spec.date_column
        try:
            dates = pd.read_csv(self.path, usecols=[col])[col]
        except (ValueError, KeyError):
            return None
        parsed = pd.to_datetime(dates, errors="coerce").dropna()
        return parsed.max().date() if len(parsed) else None
