"""Generic ``Data[T]`` envelope wrapping simple Novus responses (PLAN.md §3.1).

Most simple Novus endpoints wrap their payload in an envelope
(``NOVUS_API.md §4``)::

    { "code": int, "data": T, "message": String?, "has_more": Bool? }

The "big" purchase models are returned directly (not in ``Data``) and have their
own top-level models elsewhere. This module provides only the generic envelope.
"""

from __future__ import annotations

from novus_receipts.dto._base import BaseDTO


class Data[T](BaseDTO):
    """Generic envelope ``Data<T>`` mirroring the Novus JSON one-to-one.

    Parameterise it with the concrete payload type, e.g. ``Data[SomeModel]`` or
    ``Data[list[SomeModel]]``.
    """

    code: int
    data: T
    message: str | None = None
    has_more: bool | None = None
