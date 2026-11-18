"""Core data types: printer models, printers, jobs and the plates they expand into."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "BUILTIN_MODELS",
    "Job",
    "Plate",
    "Printer",
    "PrinterModel",
    "material_type",
    "normalise_spool",
]


def normalise_spool(text: str) -> str:
    """Canonical spool name: ``" pla : black "`` -> ``"PLA:black"``.

    A spool is a material type, optionally followed by ``:`` and a colour or
    other label. The type is upper-cased because it is compared against the
    printer's material list; the label is kept as written.
    """
    kind, sep, label = text.strip().partition(":")
    kind = kind.strip().upper()
    if not kind:
        raise ValueError(f"empty material in {text!r}")
    label = label.strip()
    return f"{kind}:{label}" if sep and label else kind


def material_type(spool: str) -> str:
    """``"PLA:black"`` -> ``"PLA"``."""
    return spool.partition(":")[0]


@dataclass(frozen=True, slots=True)
class PrinterModel:
    """Static properties of a printer model."""

    name: str
    bed_x_mm: float
    bed_y_mm: float
    bed_z_mm: float
    enclosed: bool
    materials: frozenset[str]
    nozzles_mm: frozenset[float]


_OPEN_FRAME = frozenset({"PLA", "PETG", "TPU", "PVA"})
_ENCLOSED = _OPEN_FRAME | {"ABS", "ASA", "PC", "PA"}
_NOZZLES = frozenset({0.2, 0.4, 0.6, 0.8})

#: Build volumes are Bambu Lab's published figures. The material lists are a
#: conservative default (open-frame printers are not offered ABS/ASA/PC/PA);
#: override them per printer in the fleet file to match your own practice.
BUILTIN_MODELS: dict[str, PrinterModel] = {
    "P1S": PrinterModel("P1S", 256.0, 256.0, 256.0, True, _ENCLOSED, _NOZZLES),
    "A1": PrinterModel("A1", 256.0, 256.0, 256.0, False, _OPEN_FRAME, _NOZZLES),
    "A1 MINI": PrinterModel("A1 Mini", 180.0, 180.0, 180.0, False, _OPEN_FRAME, _NOZZLES),
}


@dataclass(frozen=True, slots=True)
class Printer:
    """One physical printer in the fleet."""

    name: str
    model: PrinterModel
    nozzle_mm: float = 0.4
    #: Filament slots available without a manual swap: 4 per AMS / AMS lite,
    #: 0 means only the external spool holder (one spool).
    ams_slots: int = 0
    materials: frozenset[str] = frozenset()
    #: Multiplier applied to every job's print time on this printer.
    time_factor: float = 1.0
    #: Spools loaded at the start of the plan; empty means "whatever the first job needs".
    loaded: tuple[str, ...] = ()

    @property
    def spool_capacity(self) -> int:
        return max(1, self.ams_slots)

    @property
    def allowed_materials(self) -> frozenset[str]:
        return self.materials or self.model.materials


@dataclass(frozen=True, slots=True)
class Job:
    """A print job: ``copies`` identical plates of one sliced file."""

    name: str
    print_time_min: float
    spools: tuple[str, ...]
    copies: int = 1
    nozzle_mm: float | None = None
    size_mm: tuple[float, float, float] | None = None
    mass_g: float | None = None
    #: Minutes from plan start; ``None`` means no deadline.
    due_min: float | None = None
    priority: int = 0
    #: Printer model names (upper case) this file was sliced for; empty means any.
    models: frozenset[str] = frozenset()
    source: str = ""
    notes: tuple[str, ...] = field(default=())

    @property
    def materials(self) -> frozenset[str]:
        return frozenset(material_type(spool) for spool in self.spools)


@dataclass(frozen=True, slots=True)
class Plate:
    """One plate (copy) of a job; the unit the scheduler assigns."""

    job: Job
    copy: int  # 1-based

    @property
    def label(self) -> str:
        if self.job.copies == 1:
            return self.job.name
        return f"{self.job.name} #{self.copy}"
