from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class IngestionComment(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=120)
    author_name: str = Field(..., min_length=1, max_length=160)
    author_email: str | None = Field(default="", max_length=320)
    author_phone: str | None = Field(default="", max_length=40)
    text: str = Field(..., min_length=1, max_length=5000)
    rating: float | None = Field(default=None, ge=0, le=5)
    created_at: datetime
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("text nao pode estar vazio")
        return text


class IngestionBatchRequest(BaseModel):
    batch_name: str = Field(..., min_length=3, max_length=120)
    source: str = Field(..., min_length=2, max_length=50)
    channel: str = Field(default="manual", min_length=2, max_length=50)
    brand: str = Field(default="indefinida", min_length=1, max_length=120)
    locale: str = Field(default="pt-BR", min_length=2, max_length=12)
    comments: list[IngestionComment] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_unique_external_ids(self) -> "IngestionBatchRequest":
        duplicated = set()
        seen = set()
        for comment in self.comments:
            if comment.external_id in seen:
                duplicated.add(comment.external_id)
            seen.add(comment.external_id)

        if duplicated:
            sorted_ids = ", ".join(sorted(duplicated))
            raise ValueError(f"external_id duplicado no payload: {sorted_ids}")

        return self


class IngestionRejectedItem(BaseModel):
    external_id: str
    reason: str


class IngestionBatchResponse(BaseModel):
    batch_id: str
    received: int
    accepted: int
    rejected: int
    status: str
    rejected_items: list[IngestionRejectedItem] = Field(default_factory=list)


class IngestionBatchSummary(BaseModel):
    batch_id: str
    batch_name: str
    source: str
    channel: str
    brand: str
    locale: str
    status: str
    received_count: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    processing_count: int
    processed_count: int
    error_count: int
    created_at: datetime
    updated_at: datetime
