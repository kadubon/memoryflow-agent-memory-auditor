"""Stdlib-first CLI for MemoryFlow validation, audit, reports, and integrations."""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from ipaddress import ip_address
from pathlib import Path

from memoryflow import __version__
from memoryflow.adapters import read_jsonl_records, write_jsonl
from memoryflow.config import load_audit_config, merge_audit_config
from memoryflow.integrations.otel import otel_log_to_memoryflow_event
from memoryflow.profiles import ConformanceProfile
from memoryflow.reports import render_html_report
from memoryflow.schema import ValidationReport, validate_jsonl_file
from memoryflow.testing.samples import get_sample_jsonl, list_sample_cases
from memoryflow.verifier import AuditCertificate, AuditConfig, audit_jsonl_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="memoryflow",
        description="Validate MemoryFlow telemetry event streams.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize MemoryFlow files")
    init_parser.add_argument("--directory", type=Path, default=Path("."))
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=_cmd_init)

    validate_parser = subparsers.add_parser("validate", help="validate a MemoryFlow JSONL file")
    validate_parser.add_argument("path", type=Path)
    validate_parser.add_argument("--format", choices=("text", "json"), default="text")
    validate_parser.add_argument("--include-events", action="store_true")
    validate_parser.set_defaults(func=_cmd_validate)

    audit_parser = subparsers.add_parser("audit", help="run profile-aware MemoryFlow audit")
    audit_parser.add_argument("path", type=Path)
    audit_parser.add_argument(
        "--profile",
        choices=[item.value for item in ConformanceProfile],
        default=None,
    )
    audit_parser.add_argument("--uptake-horizon-ms", type=_positive_int_arg)
    audit_parser.add_argument("--correction-horizon-ms", type=_nonnegative_int_arg)
    audit_parser.add_argument("--verify-deadline-ms", type=_nonnegative_int_arg)
    audit_parser.add_argument("--config", type=Path)
    audit_parser.add_argument("--format", choices=("text", "json"), default="text")
    audit_parser.add_argument("--out", type=Path)
    audit_parser.set_defaults(func=_cmd_audit)

    score_parser = subparsers.add_parser("score", help="print compact MemoryFlow metric scores")
    _add_audit_common_args(score_parser)
    score_parser.set_defaults(func=_cmd_score)

    diff_parser = subparsers.add_parser("diff", help="compare two MemoryFlow event streams")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)
    _add_profile_and_horizon_args(diff_parser)
    diff_parser.add_argument("--format", choices=("text", "json"), default="text")
    diff_parser.set_defaults(func=_cmd_diff)

    explain_parser = subparsers.add_parser(
        "explain-report",
        help="explain statuses in a MemoryFlow JSON audit report",
    )
    explain_parser.add_argument("path", type=Path)
    explain_parser.set_defaults(func=_cmd_explain_report)

    convert_parser = subparsers.add_parser(
        "convert",
        help="convert external telemetry to MemoryFlow JSONL",
    )
    convert_parser.add_argument("--from", dest="source_format", choices=("otel",), required=True)
    convert_parser.add_argument("input", type=Path)
    convert_parser.add_argument(
        "--to",
        dest="target_format",
        choices=("memoryflow",),
        required=True,
    )
    convert_parser.add_argument("output", type=Path)
    convert_parser.set_defaults(func=_cmd_convert)

    sample_parser = subparsers.add_parser("sample", help="print or write a sample event stream")
    sample_parser.add_argument("--case", choices=list_sample_cases(), default="stale-memory")
    sample_parser.add_argument("--out", type=Path)
    sample_parser.set_defaults(func=_cmd_sample)

    doctor_parser = subparsers.add_parser("doctor", help="print environment diagnostics")
    doctor_parser.add_argument("--format", choices=("text", "json"), default="text")
    doctor_parser.set_defaults(func=_cmd_doctor)

    serve_parser = subparsers.add_parser("serve", help="run optional local HTTP server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument(
        "--max-body-bytes",
        type=_nonnegative_int_arg,
        default=5 * 1024 * 1024,
    )
    serve_parser.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _cmd_validate(args: argparse.Namespace) -> int:
    with _materialized_input_path(args.path) as source:
        report = validate_jsonl_file(source)
    if args.format == "json":
        data = report.to_dict(include_events=args.include_events)
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_validation_text(report)
    return 0 if report.is_valid else 2


def _cmd_init(args: argparse.Namespace) -> int:
    root = args.directory.resolve()
    config_dir = root / ".memoryflow"
    examples_dir = root / "memoryflow-examples"
    config_path = config_dir / "config.json"
    sample_path = examples_dir / "stale-memory.jsonl"

    config = {
        "schema": "memoryflow/config/1.0",
        "profile": "P1",
        "uptake_horizon_ms": 300000,
        "correction_horizon_ms": 300000,
        "verify_deadline_ms": 300000,
        "risk_weight": "identity",
        "note": "MemoryFlow verifies declared telemetry, not hidden memory-store truth.",
    }
    _write_init_file(config_path, json.dumps(config, indent=2, sort_keys=True) + "\n", args.force)
    _write_init_file(sample_path, get_sample_jsonl("stale-memory"), args.force)
    print(f"initialized MemoryFlow config at {config_path}")
    print(f"wrote sample events at {sample_path}")
    return 0


def _write_init_file(path: Path, text: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; use --force to overwrite")
    path.write_text(text, encoding="utf-8")


def _print_validation_text(report: ValidationReport) -> None:
    # Avoid importing rich or other CLI dependencies in the runtime core.
    data = report.to_dict(include_events=False)
    print("MemoryFlow validation")
    print(f"  valid: {str(data['valid']).lower()}")
    print(f"  total lines: {data['total_lines']}")
    print(f"  parsed events: {data['event_count']}")
    print(f"  kept events: {data['kept_event_count']}")
    print(f"  duplicates: {data['duplicate_count']}")
    print(f"  missing event_id: {data['missing_event_id_count']}")
    diagnostics = data["diagnostics"]
    if diagnostics:
        print("  diagnostics:")
        for item in diagnostics:
            location = f" line={item['line']}" if "line" in item else ""
            field = f" field={item['field']}" if "field" in item else ""
            print(f"    [{item['severity']}] {item['code']}{location}{field}: {item['message']}")


def _cmd_sample(args: argparse.Namespace) -> int:
    text = get_sample_jsonl(args.case)
    if args.out is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.case} sample to {args.out}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    certificate = _audit_from_args(args.path, args)
    data = certificate.to_dict()
    if args.out is not None:
        _write_audit_output(args.out, data, html=args.out.suffix.lower() in {".html", ".htm"})
        print(f"wrote audit report to {args.out}")
    elif args.format == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print("MemoryFlow audit")
        print(f"  status: {data['status']}")
        print(f"  profile: {data['profile']}")
        print(f"  events processed: {data['counters']['events_processed']}")
        print(f"  final entries: {len(data['entries'])}")
        if data["diagnostics"]:
            print("  diagnostics:")
            for item in data["diagnostics"]:
                location = f" line={item['line']}" if "line" in item else ""
                print(f"    [{item['severity']}] {item['code']}{location}: {item['message']}")
    return 0 if certificate.status.value != "INVALID" else 2


def _cmd_score(args: argparse.Namespace) -> int:
    certificate = _audit_from_args(args.path, args)
    data = certificate.to_dict()
    score = {
        "status": data["status"],
        "profile": data["profile"],
        "metrics": {
            item["name"]: {"status": item["status"], "value": item.get("value")}
            for item in data["metrics"]
        },
    }
    if args.format == "json":
        print(json.dumps(score, indent=2, sort_keys=True))
    else:
        print(f"MemoryFlow score: {score['status']} ({score['profile']})")
        for name, item in score["metrics"].items():
            print(f"  {name}: {item['status']} {item['value']}")
    return 0 if certificate.status.value != "INVALID" else 2


def _cmd_diff(args: argparse.Namespace) -> int:
    before = _audit_from_args(args.before, args).to_dict()
    after = _audit_from_args(args.after, args).to_dict()
    before_metrics = {item["name"]: item for item in before["metrics"]}
    after_metrics = {item["name"]: item for item in after["metrics"]}
    names = sorted(set(before_metrics) | set(after_metrics))
    diff = {
        "before_status": before["status"],
        "after_status": after["status"],
        "metrics": [
            {
                "name": name,
                "before": before_metrics.get(name, {}).get("value"),
                "before_status": before_metrics.get(name, {}).get("status"),
                "after": after_metrics.get(name, {}).get("value"),
                "after_status": after_metrics.get(name, {}).get("status"),
            }
            for name in names
        ],
    }
    if args.format == "json":
        print(json.dumps(diff, indent=2, sort_keys=True))
    else:
        print(f"MemoryFlow diff: {diff['before_status']} -> {diff['after_status']}")
        for item in diff["metrics"]:
            print(
                f"  {item['name']}: {item['before_status']} {item['before']} -> "
                f"{item['after_status']} {item['after']}"
            )
    return 0 if diff["after_status"] != "INVALID" else 2


def _cmd_explain_report(args: argparse.Namespace) -> int:
    data = json.loads(args.path.read_text(encoding="utf-8"))
    print("MemoryFlow report explanation")
    print(f"  status: {data.get('status')}")
    print("  MemoryFlow verifies declared telemetry, not hidden memory-store truth.")
    print("  Status meanings:")
    print("    VALID: required telemetry and bindings are sufficient for the selected profile.")
    print("    INVALID: fail-closed condition; affected results must not be trusted.")
    print("    DEGRADED: computed with explicit comparability limitation.")
    print("    NONCOMPARABLE: value is not comparable across systems/runs.")
    print("    NOT_COMPUTABLE: required configuration or events are absent.")
    print("    BEST_EFFORT: legacy or P2 result with explicit limitations.")
    diagnostics = data.get("diagnostics", [])
    if diagnostics:
        print("  diagnostics:")
        for item in diagnostics:
            if isinstance(item, dict):
                print(f"    {item.get('code')}: {item.get('message')}")
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    if args.source_format != "otel" or args.target_format != "memoryflow":
        raise ValueError("only --from otel --to memoryflow is supported")
    converted = [otel_log_to_memoryflow_event(record) for record in read_jsonl_records(args.input)]
    count = write_jsonl(converted, args.output)
    print(f"converted {count} records to {args.output}")
    return 0


def _add_profile_and_horizon_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=[item.value for item in ConformanceProfile],
        default=None,
    )
    parser.add_argument("--uptake-horizon-ms", type=_positive_int_arg)
    parser.add_argument("--correction-horizon-ms", type=_nonnegative_int_arg)
    parser.add_argument("--verify-deadline-ms", type=_nonnegative_int_arg)
    parser.add_argument("--config", type=Path)


def _add_audit_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", type=Path)
    _add_profile_and_horizon_args(parser)
    parser.add_argument("--format", choices=("text", "json"), default="text")


def _nonnegative_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return parsed


def _positive_int_arg(value: str) -> int:
    parsed = _nonnegative_int_arg(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _audit_from_args(path: Path, args: argparse.Namespace) -> AuditCertificate:
    base = load_audit_config(args.config) if args.config is not None else AuditConfig()
    profile = ConformanceProfile(args.profile) if args.profile is not None else None
    config = merge_audit_config(
        base,
        profile=profile,
        uptake_horizon_ms=args.uptake_horizon_ms,
        correction_horizon_ms=args.correction_horizon_ms,
        verify_deadline_ms=args.verify_deadline_ms,
    )
    with _materialized_input_path(path) as source:
        return audit_jsonl_file(source, config=config)


def _write_audit_output(path: Path, data: dict[str, object], *, html: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if html:
        path.write_text(render_html_report(data), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@contextmanager
def _materialized_input_path(path: Path) -> Iterator[Path]:
    if str(path) != "-":
        yield path
        return
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".jsonl",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(sys.stdin.read())
        yield temp_path
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _cmd_doctor(args: argparse.Namespace) -> int:
    data = {
        "memoryflow_version": __version__,
        "python_version": platform.python_version(),
        "python_executable": Path(sys.executable).name,
        "platform": platform.platform(),
        "core_network_calls": False,
        "runtime_dependencies": [],
    }
    if args.format == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print("MemoryFlow doctor")
        for key, value in data.items():
            print(f"  {key}: {value}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        uvicorn = importlib.import_module("uvicorn")
        from memoryflow.server import create_app
    except ImportError as exc:
        raise RuntimeError(
            "install memoryflow-agent-memory-auditor[server] to use memoryflow serve"
        ) from exc
    if not _is_loopback_host(args.host):
        print(
            "warning: memoryflow serve is unauthenticated; bind only on trusted networks",
            file=sys.stderr,
        )
    app = create_app(max_body_bytes=args.max_body_bytes)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
