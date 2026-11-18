# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private reporting instead:
<https://github.com/projectbin07-stack/printfarm-planner/security/advisories/new>.

Include a description, a minimal input that triggers the problem, and the version you used.
Reports are handled on a best-effort basis by a single maintainer.

## Scope

`printfarm-planner` reads JSON and CSV files. It never executes file content, writes only to the
paths given to `--html` and `--csv`, and makes no network requests. A jobs CSV can name
`gcode_json` files relative to its own folder, and those files are read. Reports of interest
include crashes or excessive CPU or memory use on crafted input, and HTML injection through job
or printer names into the generated report.
