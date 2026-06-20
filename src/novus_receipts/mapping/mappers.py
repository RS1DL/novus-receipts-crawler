"""Mapping extension point: ``Mapper`` protocol + ``IdentityMapper``.

This is the single, isolated seam where raw Novus DTOs will later be turned
into domain models (money ``str -> Decimal``, ``date`` normalisation, merging
summary + detail, etc.). For now the only implementation is an identity
passthrough that returns the DTO unchanged, keeping the API/DTO layers a pure
mirror of the HTTP responses. See PLAN.md §7.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

TIn = TypeVar("TIn", contravariant=True)
TOut = TypeVar("TOut", covariant=True)


@runtime_checkable
class Mapper(Protocol[TIn, TOut]):
    """Transforms an input DTO into some output (domain) representation."""

    def map(self, dto: TIn) -> TOut:
        """Map ``dto`` to its output representation."""
        ...


_T = TypeVar("_T")


class IdentityMapper:
    """Returns its argument unchanged (current 1:1 passthrough mapping)."""

    def map(self, dto: _T) -> _T:
        """Return ``dto`` exactly as received."""
        return dto
