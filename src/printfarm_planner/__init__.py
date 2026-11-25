"""Plan a production run across a mixed 3D-printer fleet with operator labour windows.

The names in ``__all__`` are the library API, documented in ``docs/api.md``. From 0.6.0 they
follow semantic versioning: within a ``0.MINOR`` series (and from 1.0 within a major version)
they are not removed, renamed or changed incompatibly, and new parameters are keyword-only
with defaults that keep the earlier behaviour. Anything not exported here (other module
members, the CLI's text layout) may change in any release.
"""

from __future__ import annotations

__version__ = "0.5.0"

from .compare import Run, compare
from .compat import incompatibilities
from .exact import EXACT_PLATE_LIMIT, ExactResult, solve_exact
from .fleet import Fleet, FleetError, Labour, load_fleet, parse_fleet
from .gantt import render_html
from .jobs import (
    INSIGHT_SCHEMA_VERSION,
    JobError,
    job_from_insight,
    load_insight_file,
    load_jobs_csv,
)
from .labour import Calendar, LabourError, Weekly, parse_weekly
from .metrics import JobResult, Metrics, PrinterStats, compute_metrics
from .model import (
    BUILTIN_MODELS,
    Job,
    Plate,
    Printer,
    PrinterModel,
    base_material,
    builtin_model,
    canonical_model_name,
    spool_matches,
)
from .objective import TERMS, Objective, Weights, evaluate
from .report import to_json_dict
from .scheduler import (
    STRATEGIES,
    Assignment,
    Clearing,
    Dispatcher,
    PrinterState,
    Schedule,
    Unscheduled,
    plan,
    sequences_of,
    simulate,
)
from .search import NEIGHBOURHOODS, SearchConfig, SearchResult, improve

__all__ = [
    "BUILTIN_MODELS",
    "EXACT_PLATE_LIMIT",
    "INSIGHT_SCHEMA_VERSION",
    "NEIGHBOURHOODS",
    "STRATEGIES",
    "TERMS",
    "Assignment",
    "Calendar",
    "Clearing",
    "Dispatcher",
    "ExactResult",
    "Fleet",
    "FleetError",
    "Job",
    "JobError",
    "JobResult",
    "Labour",
    "LabourError",
    "Metrics",
    "Objective",
    "Plate",
    "Printer",
    "PrinterModel",
    "PrinterState",
    "PrinterStats",
    "Run",
    "Schedule",
    "SearchConfig",
    "SearchResult",
    "Unscheduled",
    "Weekly",
    "Weights",
    "__version__",
    "base_material",
    "builtin_model",
    "canonical_model_name",
    "compare",
    "compute_metrics",
    "evaluate",
    "improve",
    "incompatibilities",
    "job_from_insight",
    "load_fleet",
    "load_insight_file",
    "load_jobs_csv",
    "parse_fleet",
    "parse_weekly",
    "plan",
    "render_html",
    "sequences_of",
    "simulate",
    "solve_exact",
    "spool_matches",
    "to_json_dict",
]
