"""The reply text together with where it came from.

The pair travels as one value because every command that mentions the reply needs both: an
operator asking "why did it send that?" is really asking which file to open, and answering
with the text alone leaves them guessing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class ReplySource(BaseModel):
    text: str
    # Where the text was read from, or — when it is the shipped default — the file to create
    # in order to replace it. Either way, the path worth printing.
    path: Path
    # False means no file was there and Appendix B is in force.
    is_customised: bool
