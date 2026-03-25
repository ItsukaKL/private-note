from pydantic import BaseModel


class NoteCreate(BaseModel):
    content: str


class NoteOut(BaseModel):
    id: int


class NoteItem(BaseModel):
    id: int
    content: str
    created_at: str


class NoteUpdate(BaseModel):
    content: str


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    model: str


class ModelOption(BaseModel):
    name: str


class ModelSettings(BaseModel):
    current_model: str
    available_models: list[ModelOption]


class ModelUpdate(BaseModel):
    model: str
