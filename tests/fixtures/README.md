# Test fixtures

The `*.json` files are unmodified output of `gcode-insight analyze --json` (gcode-insight 0.1.0)
for that project's synthetic fixtures `bambu_cube.gcode`, `prusa_cube.gcode` and
`marlin_generic.gcode`. Those G-code files were generated to imitate each slicer's conventions.
They are not real slicer output, and their "estimated time" comments are placeholders. Treat the
times and masses here as format samples, not as realistic jobs.

| File | What it exercises |
|------|-------------------|
| `bambu_cube.json` | slicer total estimate present, two tools (PLA + PETG), nozzle diameter |
| `prusa_cube.json` | single PETG tool, slicer total estimate |
| `marlin_generic.json` | no slicer metadata: unknown material, model time only |

To regenerate, run `gcode-insight analyze <file> --json > <name>.json` on each file in
gcode-insight's `tests/fixtures/`.
