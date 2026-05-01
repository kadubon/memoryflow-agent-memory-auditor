import pytest

from memoryflow.testing.samples import get_sample_jsonl


def test_optional_server_validate_and_audit_endpoints() -> None:
    pytest.importorskip("fastapi")
    testclient_module = pytest.importorskip("fastapi.testclient")

    from memoryflow.server import create_app

    app = create_app(max_body_bytes=1024 * 1024)
    client = testclient_module.TestClient(app)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["core_network_calls"] is False

    validate = client.post("/validate", content=get_sample_jsonl("stale-memory"))
    assert validate.status_code == 200
    assert validate.json()["valid"] is True

    audit = client.post("/audit?profile=P0", content=get_sample_jsonl("stale-memory"))
    assert audit.status_code == 200
    assert audit.json()["status"] == "VALID"


def test_optional_server_rejects_oversized_body() -> None:
    pytest.importorskip("fastapi")
    testclient_module = pytest.importorskip("fastapi.testclient")

    from memoryflow.server import create_app

    app = create_app(max_body_bytes=1)
    client = testclient_module.TestClient(app)

    response = client.post("/validate", content="{}\n")

    assert response.status_code == 413


def test_optional_server_rejects_invalid_audit_config() -> None:
    pytest.importorskip("fastapi")
    testclient_module = pytest.importorskip("fastapi.testclient")

    from memoryflow.server import create_app

    app = create_app(max_body_bytes=1024 * 1024)
    client = testclient_module.TestClient(app)

    response = client.post(
        "/audit?profile=P0&uptake_horizon_ms=0",
        content=get_sample_jsonl("stale-memory"),
    )

    assert response.status_code == 400
    assert "uptake_horizon_ms" in response.json()["detail"]
