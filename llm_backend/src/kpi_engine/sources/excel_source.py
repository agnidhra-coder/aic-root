"""Excel adapter. Also used to read reference sheets such as the KPI definition doc."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from kpi_engine.contracts.payloads import SourceDescription
from kpi_engine.sources.base import DataSource


class ExcelSource(DataSource):
    @property
    def path(self) -> Path:
        return Path(self.spec.path)

    def _read(self) -> pd.DataFrame:
        return pd.read_excel(self.path, sheet_name=self.spec.sheet or 0)

    def describe(self) -> SourceDescription:
        df = self._read()
        return SourceDescription(
            source_id=self.spec.source_id,
            path=str(self.path),
            n_rows=len(df),
            columns=list(df.columns),
            dtypes={c: str(t) for c, t in df.dtypes.items()},
        )

    def load(
        self,
        columns: list[str] | None = None,
        date_range: tuple[dt.date, dt.date] | None = None,
    ) -> pd.DataFrame:
        df = self._read()
        if columns:
            keep = set(columns) | {self.spec.date_column} | set(self.spec.entity_columns)
            df = df[[c for c in df.columns if c in keep]]
        df = self._postprocess(df)
        return self._filter_dates(df, self.spec.date_column, date_range).reset_index(drop=True)

    def _max_date(self) -> dt.date | None:
        df = self._read()
        if self.spec.date_column not in df.columns:
            return None
        parsed = pd.to_datetime(df[self.spec.date_column], errors="coerce").dropna()
        return parsed.max().date() if len(parsed) else None
