from datetime import datetime

from pydantic import BaseModel, Field


class ChatThreadCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ChatThreadResponse(BaseModel):
    id: str
    thread_id: str
    title: str
    locale: str | None = None
    archived: bool | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    last_message_at: datetime | str | None = None


class ChatMessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=3000)


class ChatMessageResponse(BaseModel):
    id: str
    message_id: str
    thread_id: str
    role: str
    content: str
    created_at: datetime | str | None = None


class ChatThreadListResponse(BaseModel):
    items: list[ChatThreadResponse]


class ChatMessageListResponse(BaseModel):
    items: list[ChatMessageResponse]


class ChatSendResponse(BaseModel):
    thread: ChatThreadResponse
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
