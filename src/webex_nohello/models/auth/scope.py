"""One OAuth scope, paired with the reason this program asks for it."""

from __future__ import annotations

from typing import NamedTuple


class Scope(NamedTuple):
    name: str
    reason: str
