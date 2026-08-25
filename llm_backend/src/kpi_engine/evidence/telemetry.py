"""Runtime telemetry.

Records wall time and row counts per stage. The LLM cost fields exist and stay
at zero: the split between what the deterministic pipeline costs and what a
narration layer would add is only meaningful if the deterministic side is
already being measured.
"""

from __future__ import annotations

import datetime as dt
import time
from contextlib import contextmanager
from typing import Any, Iterator

from kpi_engine.contracts.payloads import RunManifest, StageTelemetry


class Telemetry:
    def __init__(self, run_id: str, dataset: str, configs: dict[str, str] | None = None) -> None:
        self.manifest = RunManifest(
            run_id=run_id,
            started_at=dt.datetime.now(),
            dataset=dataset,
            configs=configs or {},
        )
        self._t0 = time.perf_counter()

    @contextmanager
    def stage(self, name: str, rows_in: int | None = None) -> Iterator[dict[str, Any]]:
        """Time one stage. Yields a dict the caller fills with rows_out and notes."""
        started = dt.datetime.now()
        t0 = time.perf_counter()
        box: dict[str, Any] = {"rows_out": None, "notes": {}}
        try:
            yield box
        finally:
            self.manifest.stages.append(
                StageTelemetry(
                    stage=name,
                    started_at=started,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                    rows_in=rows_in,
                    rows_out=box.get("rows_out"),
                    notes=box.get("notes", {}),
                )
            )

    def count(self, key: str, value: int) -> None:
        self.manifest.counts[key] = value

    def finish(self) -> RunManifest:
        self.manifest.finished_at = dt.datetime.now()
        self.manifest.total_duration_ms = round((time.perf_counter() - self._t0) * 1000, 2)
        return self.manifest

    def summary_lines(self) -> list[str]:
        """Human-readable stage timings, slowest first."""
        lines = []
        for stage in sorted(self.manifest.stages, key=lambda s: -s.duration_ms):
            share = (
                stage.duration_ms / self.manifest.total_duration_ms * 100
                if self.manifest.total_duration_ms else 0.0
            )
            rows = f"{stage.rows_out:,} rows out" if stage.rows_out is not None else ""
            lines.append(f"{stage.stage:<28} {stage.duration_ms:>10,.1f} ms  {share:>5.1f}%  {rows}")
        return lines
