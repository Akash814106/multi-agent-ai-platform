from typing import Annotated

from pydantic import BaseModel, Field

DocumentId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

class ChatRequest(BaseModel):
    query:str
    document_ids: list[DocumentId] = Field(default_factory=list, max_length=5)
