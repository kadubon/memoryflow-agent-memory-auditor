from memoryflow.provenance import validate_provenance
from memoryflow.security import content_digest


def test_hash_link_provenance_validates_declared_structure() -> None:
    result = validate_provenance(
        {
            "kind": "hash_link",
            "hash_alg": "sha256",
            "source_uri": "urn:test:source",
            "source_digest": "sha256:abc",
        }
    )

    assert result.valid


def test_hash_link_provenance_verifies_embedded_source_content_digest() -> None:
    source_content = {"claim": "memory is stale", "confidence": "declared"}
    result = validate_provenance(
        {
            "kind": "hash_link",
            "hash_alg": "sha256",
            "source_uri": "urn:test:source",
            "source_digest": content_digest(source_content),
            "source_content": source_content,
        }
    )

    assert result.valid


def test_hash_link_provenance_rejects_embedded_source_digest_mismatch() -> None:
    result = validate_provenance(
        {
            "kind": "hash_link",
            "hash_alg": "sha256",
            "source_uri": "urn:test:source",
            "source_digest": "sha256:wrong",
            "source_content": {"claim": "memory is stale"},
        }
    )

    assert not result.valid
    assert result.reason is not None


def test_signature_provenance_validates_declared_structure() -> None:
    result = validate_provenance(
        {
            "kind": "signature",
            "signature_alg": "ed25519",
            "signature": "abc",
            "public_key_id": "key-1",
            "signed_digest": "sha256:abc",
        }
    )

    assert result.valid


def test_unknown_provenance_kind_is_not_comparable_for_puf() -> None:
    result = validate_provenance({"kind": "unknown"})

    assert not result.valid
    assert result.reason is not None
