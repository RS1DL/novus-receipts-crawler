"""T6.1 — ``Mapper`` protocol + ``IdentityMapper`` (PLAN.md §7)."""

from __future__ import annotations

from novus_receipts.mapping.mappers import IdentityMapper, Mapper


def test_identity_mapper_returns_same_object() -> None:
    obj = object()
    assert IdentityMapper().map(obj) is obj


def test_identity_mapper_preserves_various_payloads() -> None:
    mapper = IdentityMapper()
    payload = {"id": "abc", "amount": "12.34"}
    assert mapper.map(payload) is payload
    assert mapper.map(42) == 42
    assert mapper.map(None) is None


def test_identity_mapper_satisfies_mapper_protocol() -> None:
    assert isinstance(IdentityMapper(), Mapper)


def test_mapper_protocol_rejects_object_without_map() -> None:
    class NotAMapper:
        pass

    assert not isinstance(NotAMapper(), Mapper)
