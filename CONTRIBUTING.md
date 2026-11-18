# Contributing

Bug reports, scheduling scenarios that produce a bad plan, and pull requests are welcome.

## Development setup

```console
git clone https://github.com/projectbin07-stack/printfarm-planner.git
cd printfarm-planner
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
```

## Checks

All four must pass. CI runs them on Ubuntu and Windows with Python 3.10 and 3.13.

```console
ruff check .
ruff format --check .
mypy
pytest --cov
```

## Guidelines

- Keep the runtime free of third-party dependencies.
- Scheduler changes need a test with a hand-derived timeline. Write out the minute arithmetic in
  a comment, as `tests/test_scheduler.py` does. Do not assert against numbers copied from the
  tool's own output.
- Do not state utilisation, throughput or accuracy figures in documentation unless they come from
  a real farm and you say how they were measured.
- Do not commit real customer order books or files you have no right to share.
- Keep commits small, with imperative messages (`Add ...`, `Fix ...`).

## Reporting a bad plan

Include the smallest fleet file and jobs CSV that reproduce it, the `--start` and `--strategy`
you used, what the plan did, and what you expected instead.
