"""Can this printer run this job? Returns the reasons it cannot."""

from __future__ import annotations

from .model import Job, Printer, base_material, material_allowed

__all__ = ["fits_bed", "incompatibilities"]

_NOZZLE_TOLERANCE_MM = 1e-6


def fits_bed(size_mm: tuple[float, float, float], printer: Printer) -> bool:
    """True if the footprint fits the bed, allowing a 90 degree rotation about Z."""
    x, y, z = size_mm
    model = printer.model
    if z > model.bed_z_mm:
        return False
    straight = x <= model.bed_x_mm and y <= model.bed_y_mm
    rotated = y <= model.bed_x_mm and x <= model.bed_y_mm
    return straight or rotated


def incompatibilities(printer: Printer, job: Job) -> list[str]:
    """Every reason ``printer`` cannot run ``job``; an empty list means it can."""
    reasons: list[str] = []
    if job.models and printer.model.name.upper() not in job.models:
        allowed = ", ".join(sorted(job.models))
        reasons.append(f"sliced for {allowed}, printer is {printer.model.name}")
    permitted = printer.allowed_materials
    missing = sorted(m for m in job.materials if not material_allowed(m, permitted))
    if missing:
        named = [
            f"{m} ({base})" if (base := base_material(m)) is not None and base != m else m
            for m in missing
        ]
        reasons.append(f"material {', '.join(named)} not allowed on this printer")
    if len(job.spools) > printer.spool_capacity:
        reasons.append(f"needs {len(job.spools)} filaments, printer holds {printer.spool_capacity}")
    if job.nozzle_mm is not None and abs(job.nozzle_mm - printer.nozzle_mm) > _NOZZLE_TOLERANCE_MM:
        reasons.append(f"needs {job.nozzle_mm:g} mm nozzle, printer has {printer.nozzle_mm:g} mm")
    if job.size_mm is not None and not fits_bed(job.size_mm, printer):
        x, y, z = job.size_mm
        m = printer.model
        reasons.append(
            f"{x:g} x {y:g} x {z:g} mm does not fit "
            f"{m.bed_x_mm:g} x {m.bed_y_mm:g} x {m.bed_z_mm:g} mm"
        )
    return reasons
