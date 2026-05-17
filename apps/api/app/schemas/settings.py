from datetime import datetime

from pydantic import BaseModel, Field


class UserSettingsResponse(BaseModel):
    theme: str
    locale: str
    llm_trigger_min_comments: int
    updated_at: datetime | str | None = None


class UserSettingsUpdateRequest(BaseModel):
    theme: str = Field(..., min_length=4, max_length=10)
    locale: str = Field(..., min_length=5, max_length=5)
    llm_trigger_min_comments: int = Field(..., ge=1, le=10000)
