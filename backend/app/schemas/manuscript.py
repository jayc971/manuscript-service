from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class StageResponse(BaseModel):
    name: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    attempts: int = 0
    last_error: Optional[str] = None

class ManuscriptResponse(BaseModel):
    id: str
    title: str
    author_email: str
    status: str
    editor_status: str
    priority: str
    needs_attention: bool
    word_count: Optional[int] = None
    preview: Optional[str] = None
    similarity_score: Optional[float] = None
    created_at: datetime
    stages: list[StageResponse] = []

class ManuscriptSubmitResponse(BaseModel):
    id: str
    status: str

class EditorStatusUpdate(BaseModel):
    status: str
