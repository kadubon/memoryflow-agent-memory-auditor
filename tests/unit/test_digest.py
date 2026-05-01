import pytest

from memoryflow.security import (
    CanonicalizationError,
    canonical_json_bytes,
    claim_digest,
    content_digest,
)


def test_canonical_json_bytes_sort_keys_and_remove_whitespace() -> None:
    assert canonical_json_bytes({"b": 2, "a": [1, True, None]}) == b'{"a":[1,true,null],"b":2}'


def test_content_digest_is_deterministic_for_key_order() -> None:
    left = content_digest({"b": 2, "a": 1})
    right = content_digest({"a": 1, "b": 2})

    assert left == right
    assert left.startswith("sha256:")


def test_canonical_json_rejects_floats() -> None:
    with pytest.raises(CanonicalizationError, match="floating point"):
        canonical_json_bytes({"weight": 0.3})


def test_canonical_json_rejects_integers_outside_ijson_safe_range() -> None:
    with pytest.raises(CanonicalizationError, match="safe range"):
        canonical_json_bytes({"counter": 9_007_199_254_740_992})


def test_claim_digest_is_order_independent_for_members() -> None:
    left = claim_digest(
        [
            {"entry_id": "b", "update_id": "u2", "content_digest": "sha256:bbb"},
            {"entry_id": "a", "update_id": "u1", "content_digest": "sha256:aaa"},
        ]
    )
    right = claim_digest(
        [
            {"entry_id": "a", "update_id": "u1", "content_digest": "sha256:aaa"},
            {"entry_id": "b", "update_id": "u2", "content_digest": "sha256:bbb"},
        ]
    )

    assert left == right
    assert left.startswith("sha256:")


def test_claim_digest_rejects_missing_member_binding() -> None:
    with pytest.raises(CanonicalizationError, match="update_id"):
        claim_digest([{"entry_id": "a", "content_digest": "sha256:aaa"}])
