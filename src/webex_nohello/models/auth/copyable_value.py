"""A value the operator is meant to copy into a form field, with the field's name."""

from __future__ import annotations

from typing import NamedTuple


class CopyableValue(NamedTuple):
    label: str
    value: str
