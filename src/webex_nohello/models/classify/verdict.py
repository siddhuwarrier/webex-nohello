"""The classifier's answer, schema-validated per Article IX.5.

The reason is required, not optional: it is written to the audit log so a misfire can be
explained after the fact, which is the only way to improve the prompt with evidence.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from webex_nohello.models.classify.verdict_kind import VerdictKind

MAX_REASON_LENGTH = 300


class Verdict(BaseModel):
    # Unknown keys are tolerated: a model that adds a field it was not asked for should
    # not cost a candidate its classification.
    model_config = ConfigDict(extra="ignore")

    verdict: VerdictKind
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=MAX_REASON_LENGTH)

    def is_confident_enough(self, threshold: float) -> bool:
        return self.confidence >= threshold
