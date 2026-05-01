# Security Policy

MemoryFlow core validation performs no outbound network calls and does not collect
API keys, secrets, or telemetry by default.

Optional `memoryflow serve` mode is unauthenticated. It defaults to loopback and
is intended only for local or private trusted networks.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately through the repository security
advisory channel when available. If that is unavailable, open a minimal public
issue that requests a private contact path and do not include exploit details.

## Threat Boundaries

MemoryFlow can validate declared telemetry. It cannot prove that source telemetry
truthfully represents hidden memory-store behavior.

Security goals for this tool include safe JSONL handling, bounded input sizes,
safe report generation, no unsafe deserialization, and no arbitrary code
execution through configuration files.
