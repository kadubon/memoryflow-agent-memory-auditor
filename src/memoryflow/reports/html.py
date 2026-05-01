"""Safe static HTML rendering for MemoryFlow audit certificates."""

from __future__ import annotations

from html import escape
from typing import Any


def render_html_report(certificate: dict[str, Any]) -> str:
    """Render a self-contained HTML report without external assets."""

    status = escape(str(certificate.get("status", "UNKNOWN")))
    profile = escape(str(certificate.get("profile", "UNKNOWN")))
    metrics = certificate.get("metrics", [])
    diagnostics = certificate.get("diagnostics", [])

    metric_rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(item.get('name', '')))}</td>"
        f"<td>{escape(str(item.get('status', '')))}</td>"
        f"<td><code>{escape(str(item.get('value')))}</code></td>"
        f"<td>{escape(str(item.get('unit', '')))}</td>"
        "</tr>"
        for item in metrics
        if isinstance(item, dict)
    )
    diagnostic_rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(item.get('severity', '')))}</td>"
        f"<td>{escape(str(item.get('code', '')))}</td>"
        f"<td>{escape(str(item.get('line', '')))}</td>"
        f"<td>{escape(str(item.get('message', '')))}</td>"
        "</tr>"
        for item in diagnostics
        if isinstance(item, dict)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MemoryFlow Audit Report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #172026; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }}
    th, td {{ border: 1px solid #d7dde2; padding: 0.45rem; text-align: left; }}
    th {{ background: #eef2f5; }}
    code {{ white-space: pre-wrap; }}
    .status {{ font-weight: 700; }}
  </style>
</head>
<body>
  <h1>MemoryFlow Audit Report</h1>
  <p class="status">Status: {status}</p>
  <p>Profile: {profile}</p>
  <p>MemoryFlow verifies declared telemetry. It does not prove hidden memory-store truth.</p>
  <h2>Metrics</h2>
  <table>
    <thead><tr><th>Name</th><th>Status</th><th>Value</th><th>Unit</th></tr></thead>
    <tbody>{metric_rows}</tbody>
  </table>
  <h2>Diagnostics</h2>
  <table>
    <thead><tr><th>Severity</th><th>Code</th><th>Line</th><th>Message</th></tr></thead>
    <tbody>{diagnostic_rows}</tbody>
  </table>
</body>
</html>
"""

