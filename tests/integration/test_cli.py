import io
from pathlib import Path

from memoryflow.cli import main
from memoryflow.testing.samples import get_sample_jsonl


def test_cli_sample_prints_jsonl(capsys) -> None:
    exit_code = main(["sample", "--case", "stale-memory"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "MOS_DECLARE" in out
    assert out.endswith("\n")


def test_cli_init_writes_config_and_sample(tmp_path: Path, capsys) -> None:
    exit_code = main(["init", "--directory", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / ".memoryflow" / "config.json").exists()
    assert (tmp_path / "memoryflow-examples" / "stale-memory.jsonl").exists()
    assert "initialized MemoryFlow config" in capsys.readouterr().out


def test_cli_validate_text(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")

    exit_code = main(["validate", str(path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "valid: true" in out


def test_cli_validate_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")

    exit_code = main(["validate", str(path), "--format", "json", "--include-events"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"valid": true' in out
    assert '"events"' in out


def test_cli_doctor(capsys) -> None:
    exit_code = main(["doctor"])

    assert exit_code == 0
    assert "core_network_calls: False" in capsys.readouterr().out


def test_cli_audit_json(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")

    exit_code = main(["audit", str(path), "--profile", "P1", "--format", "json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"profile": "P1"' in out
    assert '"status": "VALID"' in out


def test_cli_audit_accepts_stdin_dash(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(get_sample_jsonl("stale-memory")))

    exit_code = main(["audit", "-", "--profile", "P0", "--format", "json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"profile": "P0"' in out
    assert '"status": "VALID"' in out


def test_cli_audit_uses_config_file(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    config = tmp_path / "memoryflow.json"
    path.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")
    config.write_text('{"profile":"P0","risk_weight_map":{"2":5}}\n', encoding="utf-8")

    exit_code = main(["audit", str(path), "--config", str(config), "--format", "json"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"profile": "P0"' in out
    assert '"risk_weight_map": {' in out


def test_cli_audit_writes_html_report(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"
    report_path = tmp_path / "report.html"
    path.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")

    exit_code = main(["audit", str(path), "--profile", "P1", "--out", str(report_path)])

    assert exit_code == 0
    assert "wrote audit report" in capsys.readouterr().out
    html = report_path.read_text(encoding="utf-8")
    assert "MemoryFlow Audit Report" in html
    assert "MemoryFlow verifies declared telemetry" in html


def test_cli_score_and_diff(tmp_path: Path, capsys) -> None:
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")
    after.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")

    score_exit = main(["score", str(after), "--format", "json"])
    diff_exit = main(["diff", str(before), str(after), "--format", "json"])

    out = capsys.readouterr().out
    assert score_exit == 0
    assert diff_exit == 0
    assert '"metrics"' in out
    assert '"before_status"' in out


def test_cli_explain_report(tmp_path: Path, capsys) -> None:
    events = tmp_path / "events.jsonl"
    report = tmp_path / "report.json"
    events.write_text(get_sample_jsonl("stale-memory"), encoding="utf-8")
    main(["audit", str(events), "--out", str(report)])

    exit_code = main(["explain-report", str(report)])

    assert exit_code == 0
    assert "Status meanings" in capsys.readouterr().out


def test_cli_convert_otel_to_memoryflow(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "otel.jsonl"
    output_path = tmp_path / "memoryflow.jsonl"
    input_path.write_text(
        (
            '{"body":"MOS_DECLARE","attributes":{'
            '"memoryflow.schema":"memoryflow/1.0",'
            '"memoryflow.event_type":"MOS_DECLARE",'
            '"memoryflow.collector_id":"collector-a",'
            '"memoryflow.collector_seq":1,'
            '"memoryflow.event_id":"evt-1",'
            '"memoryflow.obs_time":"2026-01-03T01:00:00.000Z",'
            '"memoryflow.skew_budget_ms":0,'
            '"memoryflow.entry_id":"entry-001"}}\n'
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "convert",
            "--from",
            "otel",
            str(input_path),
            "--to",
            "memoryflow",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert "converted 1 records" in capsys.readouterr().out
    assert "MOS_DECLARE" in output_path.read_text(encoding="utf-8")
