"""Load jobs from a CSV plan and/or gcode-insight JSON analyses."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import Job, material_type, normalise_spool, spool_matches
from .textio import read_json, read_text

__all__ = [
    "CSV_COLUMNS",
    "JobError",
    "job_from_insight",
    "load_insight_file",
    "load_jobs_csv",
    "model_from_slicer",
    "parse_duration_min",
]

#: Newest gcode-insight ``analyze --json`` layout this version understands. 0.1.0 output has
#: no ``schema_version`` (layout 1); layout 2 adds filament colour and the printer model.
INSIGHT_SCHEMA_VERSION = 2

#: Relative difference above which a CSV value that overrides the G-code analysis is reported.
_CONFLICT_TOLERANCE = 0.10

CSV_COLUMNS = (
    "name",
    "copies",
    "print_time",
    "material",
    "nozzle_mm",
    "size_mm",
    "mass_g",
    "due",
    "priority",
    "models",
    "gcode_json",
)

_HM = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+(?:\.\d+)?)\s*m(?:in)?)?\s*$", re.I)
_CLOCK = re.compile(r"^\s*(\d+):(\d{2})\s*$")


class JobError(ValueError):
    """A job could not be read."""


def parse_duration_min(text: str) -> float:
    """``"90"`` / ``"1:30"`` / ``"1h30m"`` / ``"1.5h"`` -> ``90.0`` minutes."""
    raw = text.strip()
    try:
        value = float(raw)
    except ValueError:
        pass
    else:
        if value <= 0:
            raise JobError(f"duration must be positive, got {text!r}")
        return value
    clock = _CLOCK.match(raw)
    if clock is not None:
        value = int(clock.group(1)) * 60 + int(clock.group(2))
    else:
        match = _HM.match(raw)
        if match is None or not any(match.groups()):
            raise JobError(f"bad duration {text!r}; use minutes, H:MM or 1h30m")
        value = float(match.group(1) or 0) * 60 + float(match.group(2) or 0)
    if value <= 0:
        raise JobError(f"duration must be positive, got {text!r}")
    return value


def _parse_spools(text: str) -> tuple[str, ...]:
    try:
        spools = tuple(normalise_spool(part) for part in text.split("+"))
    except ValueError as exc:
        raise JobError(str(exc)) from None
    return spools


def _parse_size(text: str) -> tuple[float, float, float]:
    parts = re.split(r"\s*[x*,]\s*", text.strip().lower())
    if len(parts) != 3:
        raise JobError(f"bad size {text!r}; expected X x Y x Z in mm, e.g. 120x80x40")
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError:
        raise JobError(f"bad size {text!r}") from None
    if min(x, y, z) <= 0:
        raise JobError(f"size must be positive, got {text!r}")
    return (x, y, z)


def _parse_due(text: str, start: datetime) -> float:
    """Hours after the plan start (``"36"``) or an ISO datetime -> minutes after start.

    A datetime with a UTC offset (``2026-11-24T14:00+01:00``) is converted to the plan's time
    zone: local time when ``--start`` has no offset, otherwise ``--start``'s offset. A datetime
    without one is read in the plan's time zone.
    """
    try:
        return float(text) * 60.0
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        raise JobError(f"bad due {text!r}; use hours from start or YYYY-MM-DDTHH:MM") from None
    if moment.tzinfo is not None and start.tzinfo is None:
        moment = moment.astimezone().replace(tzinfo=None)
    elif moment.tzinfo is None and start.tzinfo is not None:
        moment = moment.replace(tzinfo=start.tzinfo)
    return (moment - start).total_seconds() / 60.0


def _float(text: str, column: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise JobError(f"{column}: {text!r} is not a number") from None
    if value <= 0:
        raise JobError(f"{column} must be positive, got {text!r}")
    return value


def _int(text: str, column: str, minimum: int | None = None) -> int:
    try:
        value = int(text)
    except ValueError:
        raise JobError(f"{column}: {text!r} is not a whole number") from None
    if minimum is not None and value < minimum:
        raise JobError(f"{column} must be at least {minimum}, got {value}")
    return value


def _get(data: Mapping[str, Any], *path: str) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def model_from_slicer(text: str) -> str:
    """Printer model name as the planner compares it: ``"Bambu Lab A1 mini"`` -> ``"A1 MINI"``.

    Bambu Studio and OrcaSlicer prefix Bambu models with the vendor; other names
    (``"MK4S"``, ``"Voron 2.4 350"``) are only upper-cased, so they match a custom model of the
    same name in the fleet file.
    """
    name = " ".join(text.split())
    for prefix in ("bambu lab ", "bambulab ", "bambu "):
        if name.lower().startswith(prefix):
            name = name[len(prefix) :]
            break
    return name.upper()


def job_from_insight(
    data: Mapping[str, Any],
    name: str,
    *,
    copies: int = 1,
    default_material: str | None = None,
    prep_min: float = 0.0,
) -> Job:
    """Build a job from the dict printed by ``gcode-insight analyze --json``.

    Print time is the slicer's total estimate when the file has one, otherwise
    gcode-insight's motion model plus ``prep_min`` (the model excludes heating,
    homing and levelling). Size is the extent of all moves, which includes any
    purge line, so it can overstate the part's footprint.

    From gcode-insight 0.2.0 (``schema_version`` 2) each spool carries the
    filament colour (``PLA:#161616``) and the job is limited to the printer
    model the file was sliced for. 0.1.0 output has neither, so its spools are
    bare material types and the job may run on any compatible model.
    """
    if "estimated_time_s" not in data or "tools" not in data:
        raise JobError(f"{name}: not a gcode-insight analysis (no estimated_time_s/tools)")
    notes: list[str] = []
    version = data.get("schema_version", 1)
    if isinstance(version, int) and version > INSIGHT_SCHEMA_VERSION:
        notes.append(
            f"gcode-insight JSON schema {version} is newer than {INSIGHT_SCHEMA_VERSION}; "
            "unknown fields ignored"
        )
    slicer_total = _get(data, "metadata", "estimated_time_s")
    if isinstance(slicer_total, (int, float)) and slicer_total > 0:
        minutes = float(slicer_total) / 60.0
        notes.append("time: slicer total estimate")
    else:
        model = data.get("estimated_time_s")
        if not isinstance(model, (int, float)) or model <= 0:
            raise JobError(f"{name}: analysis has no usable time estimate")
        minutes = float(model) / 60.0 + prep_min
        notes.append(f"time: gcode-insight model + {prep_min:g} min preparation")

    type_list = _get(data, "metadata", "filament_type") or []
    colour_list = _get(data, "metadata", "filament_colour") or []
    spools: list[str] = []
    for tool in data["tools"]:
        if not isinstance(tool, Mapping) or float(tool.get("filament_mm") or 0) <= 0:
            continue
        index = int(tool.get("tool", 0))
        material = tool.get("material")
        if not material and index < len(type_list):
            material = type_list[index]
        if not material:
            if default_material is None:
                raise JobError(
                    f"{name}: material of T{index} is unknown; set it in the jobs CSV "
                    "or with --material"
                )
            material = default_material
            notes.append(f"T{index} material assumed {default_material}")
        spool = normalise_spool(str(material))
        colour = tool.get("colour")
        if not colour and isinstance(colour_list, list) and index < len(colour_list):
            colour = colour_list[index]
        if colour and ":" not in spool:
            spool = f"{spool}:{str(colour).strip()}"
        spools.append(spool)
    if not spools:
        raise JobError(f"{name}: analysis shows no extrusion")

    nozzle = _get(data, "metadata", "nozzle_diameter")
    box = data.get("bounding_box")
    size: tuple[float, float, float] | None = None
    if isinstance(box, Mapping):
        size = (
            round(float(box["max_x"]) - float(box["min_x"]), 3),
            round(float(box["max_y"]) - float(box["min_y"]), 3),
            round(float(box["max_z"]), 3),
        )
    mass = data.get("mass_g")
    sliced_for = _get(data, "metadata", "printer_model")
    models: frozenset[str] = frozenset()
    if isinstance(sliced_for, str) and sliced_for.strip():
        models = frozenset({model_from_slicer(sliced_for)})
        notes.append(f"sliced for {sliced_for.strip()}")
    return Job(
        name=name,
        print_time_min=minutes,
        spools=tuple(spools),
        copies=copies,
        nozzle_mm=float(nozzle) if isinstance(nozzle, (int, float)) else None,
        size_mm=size,
        mass_g=float(mass) if isinstance(mass, (int, float)) else None,
        models=models,
        source=str(data.get("source") or name),
        notes=tuple(notes),
    )


def _job_name(path: Path) -> str:
    name = path.name
    for suffix in (".json", ".gcode", ".3mf"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name


def load_insight_file(
    path: Path,
    *,
    copies: int = 1,
    default_material: str | None = None,
    prep_min: float = 0.0,
) -> Job:
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        raise JobError(f"{path}: {exc}") from None
    if not isinstance(data, Mapping):
        raise JobError(f"{path}: expected a JSON object")
    return job_from_insight(
        data,
        _job_name(path),
        copies=copies,
        default_material=default_material,
        prep_min=prep_min,
    )


def load_jobs_csv(path: Path, start: datetime, *, prep_min: float = 0.0) -> list[Job]:
    """Read a jobs CSV. Columns are listed in :data:`CSV_COLUMNS`; see the README."""
    try:
        text = read_text(path)
    except (OSError, ValueError) as exc:
        raise JobError(f"{path}: {exc}") from None
    reader = csv.DictReader(line for line in text.splitlines() if not line.startswith("#"))
    header = [h.strip().lower() for h in reader.fieldnames or []]
    if "name" not in header:
        raise JobError(f"{path}: missing required column 'name'")
    unknown = sorted(set(header) - set(CSV_COLUMNS))
    if unknown:
        raise JobError(f"{path}: unknown column(s) {', '.join(unknown)}")

    jobs: list[Job] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(reader, start=2):
        row = {
            (k or "").strip().lower(): (v or "").strip()
            for k, v in raw.items()
            if isinstance(v, str) or v is None
        }
        if not any(row.values()):
            continue
        where = f"{path.name} row {line_no}"
        try:
            jobs.append(_row_to_job(row, path.parent, start, prep_min))
        except JobError as exc:
            raise JobError(f"{where}: {exc}") from None
        if jobs[-1].name in seen:
            raise JobError(f"{where}: duplicate job name {jobs[-1].name!r}")
        seen.add(jobs[-1].name)
    return jobs


def _row_to_job(row: dict[str, str], base: Path, start: datetime, prep_min: float) -> Job:
    name = row.get("name", "")
    if not name:
        raise JobError("name is empty")
    material = row.get("material", "")
    base_job: Job | None = None
    if row.get("gcode_json"):
        json_path = base / row["gcode_json"]
        try:
            data = read_json(json_path)
        except (OSError, ValueError) as exc:
            raise JobError(f"gcode_json {row['gcode_json']}: {exc}") from None
        if not isinstance(data, Mapping):
            raise JobError(f"gcode_json {row['gcode_json']}: expected a JSON object")
        base_job = job_from_insight(
            data,
            name,
            default_material=material.split("+")[0] if material else None,
            prep_min=prep_min,
        )

    if row.get("print_time"):
        minutes = parse_duration_min(row["print_time"])
    elif base_job is not None:
        minutes = base_job.print_time_min
    else:
        raise JobError("print_time is empty and no gcode_json given")

    if material:
        spools = _parse_spools(material)
    elif base_job is not None:
        spools = base_job.spools
    else:
        raise JobError("material is empty and no gcode_json given")

    nozzle = base_job.nozzle_mm if base_job else None
    if row.get("nozzle_mm"):
        nozzle = _float(row["nozzle_mm"], "nozzle_mm")
    size = base_job.size_mm if base_job else None
    if row.get("size_mm"):
        size = _parse_size(row["size_mm"])
    mass = base_job.mass_g if base_job else None
    if row.get("mass_g"):
        mass = _float(row["mass_g"], "mass_g")
    models = frozenset(m.strip().upper() for m in row.get("models", "").split("+") if m.strip())
    warnings = _conflicts(row, base_job, minutes, spools, mass, nozzle, models)
    if base_job is not None and not models:
        models = base_job.models
    notes = list(base_job.notes) if base_job and not row.get("print_time") else []
    return Job(
        name=name,
        print_time_min=minutes,
        spools=spools,
        copies=_int(row["copies"], "copies", 1) if row.get("copies") else 1,
        nozzle_mm=nozzle,
        size_mm=size,
        mass_g=mass,
        due_min=_parse_due(row["due"], start) if row.get("due") else None,
        priority=_int(row["priority"], "priority") if row.get("priority") else 0,
        models=models,
        source=row.get("gcode_json") or "csv",
        notes=tuple(notes + warnings),
    )


def _differs(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) > _CONFLICT_TOLERANCE * max(abs(a), abs(b))


def _conflicts(
    row: dict[str, str],
    base: Job | None,
    minutes: float,
    spools: tuple[str, ...],
    mass: float | None,
    nozzle: float | None,
    models: frozenset[str],
) -> list[str]:
    """Where a CSV value overrides a clearly different value from the G-code analysis.

    The CSV wins (that is how a value is corrected by hand), but a stale estimate left in the
    CSV after slicing is the more common cause, so each one is reported as a warning note.
    """
    if base is None:
        return []
    out: list[str] = []
    src = row.get("gcode_json", "")
    if row.get("print_time") and _differs(minutes, base.print_time_min):
        out.append(
            f"warning: CSV print_time {minutes:.0f} min overrides {base.print_time_min:.0f} min "
            f"from {src}"
        )
    if row.get("material"):
        same = len(spools) == len(base.spools) and all(
            material_type(a) == material_type(b) and spool_matches(a, b)
            for a, b in zip(spools, base.spools, strict=False)
        )
        if not same:
            out.append(
                f"warning: CSV material {'+'.join(spools)} overrides {'+'.join(base.spools)} "
                f"from {src}"
            )
    if row.get("mass_g") and _differs(mass, base.mass_g):
        out.append(f"warning: CSV mass_g {mass:g} overrides {base.mass_g:.3g} g from {src}")
    if row.get("nozzle_mm") and base.nozzle_mm is not None and nozzle != base.nozzle_mm:
        out.append(f"warning: CSV nozzle_mm {nozzle:g} overrides {base.nozzle_mm:g} from {src}")
    if models and base.models and not base.models <= models:
        out.append(
            f"warning: CSV models {'+'.join(sorted(models))} override "
            f"{'+'.join(sorted(base.models))} that {src} was sliced for"
        )
    return out
