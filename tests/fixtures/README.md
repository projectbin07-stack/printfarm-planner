# Test fixtures

`gcode/` holds copies of gcode-insight's synthetic G-code fixtures (`bambu_cube.gcode`,
`prusa_cube.gcode`, `orca_cube.gcode`, `marlin_generic.gcode`, from gcode-insight v0.2.0). They were
generated to imitate each slicer's conventions. They are not real slicer output, and their
"estimated time" comments are placeholders. Treat the times and masses here as format samples, not
as realistic jobs.

The `*.json` files next to this README are unmodified output of gcode-insight 0.2.0
(`"schema_version": 2`, with filament colour and printer model) for those files.
`gcode-insight-0.1.0/` keeps the output of gcode-insight 0.1.0 (no `schema_version`, no colour, no
printer model) to test that older files still load.

| File | What it exercises |
|------|-------------------|
| `bambu_cube.json` | slicer total estimate, two tools (PLA white + PETG near-black), sliced for Bambu Lab P1S |
| `prusa_cube.json` | single PETG tool (orange), slicer total estimate, sliced for MK4S |
| `orca_cube.json` | single ABS tool, sliced for a Voron 2.4 350 (matches no built-in model) |
| `marlin_generic.json` | no slicer metadata: unknown material, model time only |

To regenerate, run in `tests/fixtures/gcode/`:

```console
gcode-insight analyze <name>.gcode --json --out ../<name>.json
```

`tests/test_interop.py` compares these files with the output of the installed gcode-insight; CI
runs it against the pinned release.
