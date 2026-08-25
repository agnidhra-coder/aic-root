"""Descriptive series profiling.

Detection answers "is this point unusual?". This package answers the question a
human analyst asks first -- "what is this series doing at all?" -- and emits it
as structured evidence. It is deliberately descriptive: it never produces a Flag
and nothing downstream branches on its output, so it cannot regress detection.
"""

from kpi_engine.eda.runner import profile_panel

__all__ = ["profile_panel"]
