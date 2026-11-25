"""The declared library API (``printfarm_planner.__all__``) and the docs/api.md example."""

from __future__ import annotations

import contextlib
import io
import os
import re
from pathlib import Path

import pytest

import printfarm_planner

ROOT = Path(__file__).resolve().parent.parent
API_DOC = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")


def test_every_exported_name_exists_and_is_listed_once() -> None:
    names = printfarm_planner.__all__
    assert len(names) == len(set(names))
    for name in names:
        assert hasattr(printfarm_planner, name), name


@pytest.mark.parametrize(
    "name",
    [
        "plan",
        "improve",
        "simulate",
        "solve_exact",
        "compare",
        "PrinterState",
        "Plate",
        "Job",
        "Printer",
        "Fleet",
        "Schedule",
        "Dispatcher",
        "job_from_insight",
        "load_fleet",
        "load_jobs_csv",
        "evaluate",
        "compute_metrics",
    ],
)
def test_core_names_are_exported_and_documented(name: str) -> None:
    assert name in printfarm_planner.__all__
    assert f"`{name}" in API_DOC, f"{name} is not in docs/api.md"
    assert getattr(printfarm_planner, name).__doc__


def test_every_exported_name_is_in_the_api_doc() -> None:
    missing = [n for n in printfarm_planner.__all__ if n != "__version__" and n not in API_DOC]
    assert missing == []


def test_new_planner_parameters_are_keyword_only() -> None:
    import inspect

    for function in (
        printfarm_planner.plan,
        printfarm_planner.simulate,
        printfarm_planner.solve_exact,
        printfarm_planner.compare,
    ):
        parameters = inspect.signature(function).parameters
        for name in ("initial", "pinned"):
            assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, function
            assert parameters[name].default is None


def test_api_doc_example_prints_what_the_doc_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    code = re.search(r"## Example.*?```python\n(.*?)```", API_DOC, re.S)
    shown = re.search(r"Output \(0\.6\.0.*?```text\n(.*?)```", API_DOC, re.S)
    assert code is not None and shown is not None
    monkeypatch.chdir(ROOT)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code.group(1), "docs/api.md", "exec"), {})
    assert out.getvalue() == shown.group(1)
    assert os.getcwd() == str(ROOT)
