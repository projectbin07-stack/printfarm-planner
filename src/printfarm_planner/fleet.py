"""Fleet file: printers, optional custom models and labour settings (JSON)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .labour import LabourError, Weekly, parse_weekly
from .model import BUILTIN_MODELS, Printer, PrinterModel, builtin_model, normalise_spool
from .textio import read_json

__all__ = ["Fleet", "FleetError", "Labour", "load_fleet", "parse_fleet"]

_PRINTER_KEYS = {
    "name",
    "model",
    "count",
    "nozzle_mm",
    "ams_slots",
    "materials",
    "time_factor",
    "loaded",
}
_LABOUR_KEYS = {"windows", "operators", "plate_change_min", "material_change_min"}


class FleetError(ValueError):
    """The fleet file is invalid."""


@dataclass(frozen=True, slots=True)
class Labour:
    weekly: Weekly
    operators: int = 1
    plate_change_min: float = 5.0
    material_change_min: float = 10.0


@dataclass(frozen=True, slots=True)
class Fleet:
    printers: tuple[Printer, ...]
    labour: Labour


def _number(value: Any, what: str, *, minimum: float = 0.0, strict: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FleetError(f"{what} must be a number, got {value!r}")
    if value < minimum or (strict and value == minimum):
        op = ">" if strict else ">="
        raise FleetError(f"{what} must be {op} {minimum:g}, got {value!r}")
    return float(value)


def _string_list(value: Any, what: str) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise FleetError(f"{what} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise FleetError(f"{what} must be a list of strings")
    return list(value)


def _parse_models(raw: Any) -> dict[str, PrinterModel]:
    models = dict(BUILTIN_MODELS)
    if raw is None:
        return models
    if not isinstance(raw, Mapping):
        raise FleetError("models must be an object of name -> definition")
    for name, spec in raw.items():
        if not isinstance(spec, Mapping):
            raise FleetError(f"model {name}: definition must be an object")
        bed = spec.get("bed_mm")
        if not isinstance(bed, Sequence) or isinstance(bed, str) or len(bed) != 3:
            raise FleetError(f"model {name}: bed_mm must be [x, y, z]")
        x, y, z = (_number(v, f"model {name} bed_mm", strict=True) for v in bed)
        materials = _string_list(spec.get("materials"), f"model {name} materials")
        nozzles = spec.get("nozzles_mm", [0.2, 0.4, 0.6, 0.8])
        if isinstance(nozzles, str) or not isinstance(nozzles, Sequence):
            raise FleetError(f"model {name}: nozzles_mm must be a list")
        models[str(name).upper()] = PrinterModel(
            name=str(name),
            bed_x_mm=x,
            bed_y_mm=y,
            bed_z_mm=z,
            enclosed=bool(spec.get("enclosed", False)),
            materials=frozenset(m.strip().upper() for m in materials),
            nozzles_mm=frozenset(
                _number(n, f"model {name} nozzles_mm", strict=True) for n in nozzles
            ),
        )
    return models


def _resolve_model(models: Mapping[str, PrinterModel], name: str) -> PrinterModel | None:
    """A model by its name in the fleet file, or by a registry alias (``X1C``, ``a1-mini``).

    A custom model with the same name as a built-in one replaces it, also under its aliases.
    """
    found = models.get(name.upper())
    if found is not None:
        return found
    builtin = builtin_model(name)
    return models.get(builtin.name.upper()) if builtin is not None else None


def _parse_printer(spec: Any, models: Mapping[str, PrinterModel]) -> list[Printer]:
    if not isinstance(spec, Mapping):
        raise FleetError("each printer must be an object")
    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        raise FleetError("printer without a name")
    unknown = sorted(set(spec) - _PRINTER_KEYS)
    if unknown:
        raise FleetError(f"printer {name}: unknown key(s) {', '.join(unknown)}")
    model_name = spec.get("model")
    model = _resolve_model(models, model_name) if isinstance(model_name, str) else None
    if model is None:
        known = ", ".join(sorted(m.name for m in models.values()))
        raise FleetError(f"printer {name}: unknown model {model_name!r} (known: {known})")
    nozzle = _number(spec.get("nozzle_mm", 0.4), f"printer {name} nozzle_mm", strict=True)
    if model.nozzles_mm and not any(abs(nozzle - n) < 1e-6 for n in model.nozzles_mm):
        sizes = ", ".join(f"{n:g}" for n in sorted(model.nozzles_mm))
        raise FleetError(f"printer {name}: {model.name} takes {sizes} mm nozzles, not {nozzle:g}")
    slots = spec.get("ams_slots", 0)
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 0:
        raise FleetError(f"printer {name}: ams_slots must be a whole number >= 0")
    if model.max_spools is not None and slots > model.max_spools:
        raise FleetError(
            f"printer {name}: ams_slots {slots} is more than the {model.max_spools} filament slots "
            f"{model.name} supports"
        )
    materials = frozenset(
        m.strip().upper() for m in _string_list(spec.get("materials", []), f"{name} materials")
    )
    try:
        loaded = tuple(
            normalise_spool(s) for s in _string_list(spec.get("loaded", []), f"{name} loaded")
        )
    except ValueError as exc:
        raise FleetError(f"printer {name}: {exc}") from None
    if len(loaded) > max(1, slots):
        raise FleetError(
            f"printer {name}: {len(loaded)} spools loaded but only {max(1, slots)} fit"
        )
    factor = _number(spec.get("time_factor", 1.0), f"printer {name} time_factor", strict=True)
    count = spec.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise FleetError(f"printer {name}: count must be a whole number >= 1")
    names = [name] if count == 1 else [f"{name}-{i}" for i in range(1, count + 1)]
    return [
        Printer(
            name=n,
            model=model,
            nozzle_mm=nozzle,
            ams_slots=slots,
            materials=materials,
            time_factor=factor,
            loaded=loaded,
        )
        for n in names
    ]


def _parse_labour(raw: Any) -> Labour:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise FleetError("labour must be an object")
    unknown = sorted(set(raw) - _LABOUR_KEYS)
    if unknown:
        raise FleetError(f"labour: unknown key(s) {', '.join(unknown)}")
    try:
        weekly = parse_weekly(raw.get("windows", {"mon-fri": "08:00-18:00"}))
    except LabourError as exc:
        raise FleetError(f"labour windows: {exc}") from None
    operators = raw.get("operators", 1)
    if isinstance(operators, bool) or not isinstance(operators, int) or operators < 1:
        raise FleetError("labour operators must be a whole number >= 1")
    return Labour(
        weekly=weekly,
        operators=operators,
        plate_change_min=_number(raw.get("plate_change_min", 5.0), "plate_change_min"),
        material_change_min=_number(raw.get("material_change_min", 10.0), "material_change_min"),
    )


def parse_fleet(data: Any) -> Fleet:
    if not isinstance(data, Mapping):
        raise FleetError("fleet file must contain a JSON object")
    models = _parse_models(data.get("models"))
    raw_printers = data.get("printers")
    if not isinstance(raw_printers, Sequence) or isinstance(raw_printers, str) or not raw_printers:
        raise FleetError("fleet needs a non-empty 'printers' list")
    printers: list[Printer] = []
    for spec in raw_printers:
        printers.extend(_parse_printer(spec, models))
    names = [p.name for p in printers]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise FleetError(f"duplicate printer name(s): {', '.join(duplicates)}")
    return Fleet(printers=tuple(printers), labour=_parse_labour(data.get("labour")))


def load_fleet(path: Path) -> Fleet:
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        raise FleetError(f"{path}: {exc}") from None
    try:
        return parse_fleet(data)
    except FleetError as exc:
        raise FleetError(f"{path}: {exc}") from None
