from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from helpers import EXAMPLES, FIXTURES
from printfarm_planner import __version__
from printfarm_planner.cli import _start, main

FLEET = str(EXAMPLES / "fleet.json")
JOBS = str(EXAMPLES / "jobs.csv")
START = "2026-11-23T08:00"


def test_plan_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", "--fleet", FLEET, JOBS, "--start", START]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Production plan (strategy: fill)")
    assert "26 plates" in out
    # ASA is only allowed on the enclosed P1S printers and needs the 0.6 mm nozzle of P1S-2
    asa_rows = [line for line in out.splitlines() if "asa-bracket #" in line]
    assert len(asa_rows) == 4 and all(row.strip().startswith("P1S-2") for row in asa_rows)


def test_plan_writes_html_csv_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    html, csv_path = tmp_path / "plan.html", tmp_path / "plan.csv"
    args = ["plan", "--fleet", FLEET, JOBS, "--start", START, "--strategy", "edd"]
    assert main([*args, "--html", str(html), "--csv", str(csv_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["strategy"] == "edd"
    assert len(data["assignments"]) == 26
    assert "<svg" in html.read_text(encoding="utf-8")
    assert csv_path.read_text(encoding="utf-8").count("\n") == 27


def test_plan_with_json_inputs_and_unschedulable_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bambu = str(FIXTURES / "bambu_cube.json")
    marlin = str(FIXTURES / "marlin_generic.json")
    args = ["plan", "--fleet", FLEET, bambu, marlin, "--start", START, "--material", "abs"]
    # marlin_generic gets ABS: allowed on P1S, so everything schedules
    assert main([*args, "--copies", "2"]) == 0
    out = capsys.readouterr().out
    assert "bambu_cube #2" in out
    assert "marlin_generic: T0 material assumed ABS" in out


def test_unschedulable_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jobs = tmp_path / "jobs.csv"
    jobs.write_text("name,print_time,material\nbig,60,PC+PA+ABS+ASA+PLA\n", encoding="utf-8")
    assert main(["plan", "--fleet", FLEET, str(jobs), "--start", START]) == 1
    assert "needs 5 filaments" in capsys.readouterr().out


def test_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--fleet", FLEET, JOBS, "--start", START]) == 0
    out = capsys.readouterr().out
    assert "asa-bracket       P1S-2\n" in out


def test_check_reports_reasons(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jobs = tmp_path / "jobs.csv"
    jobs.write_text("name,print_time,material,size_mm\nhuge,60,PLA,300x300x10\n", encoding="utf-8")
    assert main(["check", "--fleet", FLEET, str(jobs)]) == 1
    out = capsys.readouterr().out
    assert "NO PRINTER" in out
    assert "A1-Mini: 300 x 300 x 10 mm does not fit 180 x 180 x 180 mm" in out


def test_models(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert "A1 mini    bed 180 x 180 x 180 mm  open frame  nozzles 0.2/0.4/0.6/0.8 mm" in out
    assert "P1S        bed 256 x 256 x 256 mm  enclosed    nozzles" in out
    assert "X1 Carbon  bed 256 x 256 x 256 mm  enclosed" in out
    assert "P1P        bed 256 x 256 x 256 mm  open frame" in out
    assert "up to 16 spools" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["plan", "--fleet", "missing.json", JOBS], "missing.json"),
        (["plan", "--fleet", FLEET, str(FIXTURES / "marlin_generic.json")], "material of T0"),
        (["plan", "--fleet", FLEET, JOBS, JOBS], "duplicate job name"),
        (["plan", "--fleet", FLEET, "nope.json"], "nope.json"),
    ],
)
def test_input_errors_exit_2(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert message in capsys.readouterr().err


def test_empty_csv_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jobs = tmp_path / "jobs.csv"
    jobs.write_text("name,print_time,material\n", encoding="utf-8")
    assert main(["plan", "--fleet", FLEET, str(jobs)]) == 2
    assert "no jobs" in capsys.readouterr().err


def test_bad_start_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["plan", "--fleet", FLEET, JOBS, "--start", "monday"])
    assert exc.value.code == 2
    assert "bad --start" in capsys.readouterr().err


def test_bad_copies_is_a_usage_error() -> None:
    with pytest.raises(SystemExit):
        main(["plan", "--fleet", FLEET, JOBS, "--copies", "0"])


def test_default_start_is_next_full_hour() -> None:
    start = _start(None)
    assert start.minute == 0 and start.second == 0
    assert start >= datetime.now().replace(second=0, microsecond=0)
    assert _start("2026-11-23T08:30") == datetime(2026, 11, 23, 8, 30)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "printfarm_planner", "models"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent.parent,
        env=None,
        stdin=subprocess.DEVNULL,
    )
    assert "P1S" in result.stdout


def test_check_reports_ignored_csv_columns(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    jobs = tmp_path / "semi.csv"
    jobs.write_text("name;print_time;material;notes\nA;1:30:00;PLA Silk;rush\n", encoding="utf-8")
    assert main(["check", "--fleet", FLEET, str(jobs)]) == 0
    captured = capsys.readouterr()
    assert "A  P1S-1, P1S-2, A1-1, A1-2, A1-Mini" in captured.out
    assert "warning: semi.csv: ignored column(s) notes" in captured.err
