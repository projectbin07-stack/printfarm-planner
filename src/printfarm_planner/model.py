"""Core data types: printer models, printers, jobs and the plates they expand into."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

__all__ = [
    "BUILTIN_MODELS",
    "Job",
    "Plate",
    "Printer",
    "PrinterModel",
    "colour_key",
    "material_type",
    "normalise_spool",
    "spool_matches",
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


#: Reference colours for matching slicer hex codes against named spools. A ``#RRGGBB`` label
#: is treated as the nearest of these (straight-line distance in RGB), so ``#161616`` from a
#: slicer matches a spool labelled ``black``. Labels that are neither hex nor in this table
#: (``galaxy``, ``matte-sakura``) are compared as written, ignoring case.
NAMED_COLOURS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "red": (255, 0, 0),
    "maroon": (128, 0, 0),
    "orange": (255, 140, 0),
    "yellow": (255, 230, 0),
    "gold": (212, 175, 55),
    "green": (0, 160, 0),
    "olive": (128, 128, 0),
    "cyan": (0, 200, 220),
    "blue": (0, 70, 255),
    "navy": (0, 0, 128),
    "purple": (128, 0, 160),
    "magenta": (255, 0, 255),
    "pink": (255, 150, 190),
    "brown": (120, 70, 20),
    "beige": (225, 210, 170),
}
_COLOUR_ALIASES = {"gray": "grey", "violet": "purple"}


@lru_cache(maxsize=1024)
def colour_key(label: str) -> str:
    """What a spool label is compared by: a reference colour name, or the label itself.

    ``"#161616"`` -> ``"black"``, ``"Gray"`` -> ``"grey"``, ``"Galaxy"`` -> ``"galaxy"``.
    Bambu Studio writes ``#RRGGBBAA``; the alpha byte is ignored.
    """
    text = label.strip().lower()
    hexpart = text[1:] if text.startswith("#") else ""
    if len(hexpart) in (6, 8) and all(c in "0123456789abcdef" for c in hexpart):
        rgb = tuple(int(hexpart[i : i + 2], 16) for i in (0, 2, 4))
        return min(
            NAMED_COLOURS,
            key=lambda n: sum((a - b) ** 2 for a, b in zip(rgb, NAMED_COLOURS[n], strict=True)),
        )
    return _COLOUR_ALIASES.get(text, text)


@lru_cache(maxsize=4096)
def spool_matches(loaded: str, needed: str) -> bool:
    """True when a loaded spool can serve a needed one without a manual swap.

    The material types must be equal. When either side has no colour label (``"PLA"``) the
    colour is unknown and any spool of that type is accepted; otherwise the labels must name
    the same colour by :func:`colour_key`.
    """
    have_type, _, have_label = loaded.partition(":")
    need_type, _, need_label = needed.partition(":")
    if have_type != need_type:
        return False
    if not have_label or not need_label:
        return True
    return colour_key(have_label) == colour_key(need_label)


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
