"""Shared base model for all Novus DTOs.

Every DTO mirrors the Novus JSON one-to-one (PLAN.md §3). Field names equal the
JSON keys (snake_case); only diverging keys use ``Field(alias=...)``. The shared
``model_config`` lives here so subclasses stay declarative:

- ``extra="ignore"`` tolerates undocumented JSON keys instead of raising, making
  the models resilient to API additions.
- ``populate_by_name=True`` lets models be built either by the JSON alias or by
  the Python field name.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseDTO(BaseModel):
    """Base class for Novus DTOs: ignore unknown keys, accept aliases."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
