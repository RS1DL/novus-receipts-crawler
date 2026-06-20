"""Tests for the shared DTO base model (TASKS.md T3.0, PLAN.md §3)."""

from __future__ import annotations

from pydantic import Field

from novus_receipts.dto._base import BaseDTO


class _Sample(BaseDTO):
    """Minimal subclass exercising the shared config: a plain field plus an
    aliased one whose JSON key differs from the Python name."""

    name: str
    item_id: str = Field(alias="raw_end_time_stamp")


def test_base_config_is_ignore_and_populate_by_name() -> None:
    assert BaseDTO.model_config["extra"] == "ignore"
    assert BaseDTO.model_config["populate_by_name"] is True


def test_subclass_inherits_config() -> None:
    assert _Sample.model_config["extra"] == "ignore"
    assert _Sample.model_config["populate_by_name"] is True


def test_unknown_key_is_ignored() -> None:
    model = _Sample.model_validate(
        {"name": "n", "raw_end_time_stamp": "42", "totally_unknown": 999}
    )

    assert model.name == "n"
    assert model.item_id == "42"
    assert not hasattr(model, "totally_unknown")


def test_value_accepted_by_field_alias() -> None:
    model = _Sample.model_validate({"name": "n", "raw_end_time_stamp": "ts-1"})

    assert model.item_id == "ts-1"


def test_value_accepted_by_field_name_via_populate_by_name() -> None:
    model = _Sample(name="n", item_id="by-name")

    assert model.item_id == "by-name"


def test_unknown_key_does_not_raise_with_only_known_fields() -> None:
    model = _Sample.model_validate(
        {"name": "n", "raw_end_time_stamp": "0", "extra1": "x", "extra2": [1, 2]}
    )

    assert model.model_dump() == {"name": "n", "item_id": "0"}
