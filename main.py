from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from chroma_store import add_chunks, delete_note_chunks, get_collection, query_chunks
from db import delete_note, get_note, init_db, insert_note, list_notes, update_note
from ollama_client import OLLAMA_LLM_MODEL, embed_text, generate_text
from schemas import ChatRequest, ChatResponse, NoteCreate, NoteItem, NoteOut, NoteUpdate
from text_splitter import split_text


app = FastAPI()
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    get_collection()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.post("/note", response_model=NoteOut)
def create_note(payload: NoteCreate) -> NoteOut:
    note_id = insert_note(payload.content)
    chunks = split_text(payload.content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    add_chunks(note_id, chunks, embeddings)
    return NoteOut(id=note_id)


@app.get("/notes", response_model=list[NoteItem])
def get_notes() -> list[NoteItem]:
    return [NoteItem(**item) for item in list_notes()]


@app.put("/note/{note_id}", response_model=NoteItem)
def update_note_item(note_id: int, payload: NoteUpdate) -> NoteItem:
    existing = get_note(note_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if not update_note(note_id, payload.content):
        raise HTTPException(status_code=500, detail="Update failed")
    delete_note_chunks(note_id)
    chunks = split_text(payload.content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    add_chunks(note_id, chunks, embeddings)
    updated = get_note(note_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Update failed")
    return NoteItem(**updated)


@app.delete("/note/{note_id}", response_model=NoteOut)
def remove_note(note_id: int) -> NoteOut:
    if not delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    delete_note_chunks(note_id)
    return NoteOut(id=note_id)


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    query_embedding = embed_text(payload.question)
    chunks = query_chunks(query_embedding, top_k=4, similarity_threshold=0.75)
    context = "\n\n".join(chunks)
    prompt = (
        "你是一个基于用户笔记的助手\n"
        "请严格根据以下内容回答问题，不要编造：\n"
        f"{context}\n"
        "问题：\n"
        f"{payload.question}\n"
        "规则：\n"
        "只能使用提供的内容回答\n"
        "如果内容不足，请回答：未找到相关信息\n"
        "回答要简洁清晰"
    )
    answer = generate_text(prompt)
    return ChatResponse(answer=answer, model=OLLAMA_LLM_MODEL)
