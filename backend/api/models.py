from typing import Annotated

from pydantic import BaseModel, Field, field_validator

DocumentId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=12000)
    document_ids: list[DocumentId] = Field(default_factory=list, max_length=5)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Enter a message.")
        return value
