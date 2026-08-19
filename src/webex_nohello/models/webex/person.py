"""A Webex person, which may or may not be a human."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

HUMAN_PERSON_TYPE = "person"


class Person(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    emails: list[str] = Field(default_factory=list)
    display_name: str = Field(default="", alias="displayName")
    # Deliberately a plain string rather than an enum: Article VII.2 requires failing
    # closed, so an unrecognised future type must read as "not a human" rather than
    # breaking parsing or, worse, defaulting to human.
    type: str = HUMAN_PERSON_TYPE

    @property
    def is_human(self) -> bool:
        return self.type == HUMAN_PERSON_TYPE

    @property
    def primary_email(self) -> str:
        return self.emails[0] if self.emails else ""
