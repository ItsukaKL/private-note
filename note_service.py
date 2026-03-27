from __future__ import annotations

import re

import launcher_core
from chroma_store import add_chunks, delete_note_chunks, get_collection, query_chunks
from db import delete_note, get_note, init_db, insert_note, list_notes, update_note
from ollama_client import embed_text, generate_text, get_llm_model, init_runtime_settings
from text_splitter import split_text


CHAT_PROMPT_TEMPLATE = """
你是一个严格基于用户笔记回答问题的助手。
【已知信息】{context}

【用户问题】{question}

【回答规则】1. 只能使用【已知信息】中的内容回答
2. 不允许使用任何外部知识或自行推断
3. 如果【已知信息】中没有明确答案，必须回答：未找到相关信息
4. 优先保证答案准确，而不是完整
5. 回答要简洁、结构清晰
请开始回答：
"""

STRUCTURED_FIELD_ALIASES = {
    "歌手": ("歌手", "演唱", "原唱", "谁唱", "演唱者"),
    "作词": ("作词", "词作者", "词作", "填词"),
    "作曲": ("作曲", "曲作者", "谱曲"),
    "编曲": ("编曲", "编者"),
}
QUESTION_STOP_CHARS = set("的是了呢吗呀啊吧么什么请问一下告诉我这那哪几谁多少有无和与及并")


def init_storage() -> None:
    init_db()
    get_collection()


def init_services() -> None:
    init_storage()
    launcher_core.ensure_ollama_running()
    init_runtime_settings()


def create_note_item(content: str) -> int:
    launcher_core.ensure_ollama_running()
    note_id = insert_note(content)
    chunks = split_text(content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    add_chunks(note_id, chunks, embeddings)
    return note_id


def list_note_items() -> list[dict]:
    return list_notes()


def get_note_item(note_id: int) -> dict | None:
    return get_note(note_id)


def update_note_item_content(note_id: int, content: str) -> dict:
    launcher_core.ensure_ollama_running()
    existing = get_note(note_id)
    if existing is None:
        raise ValueError("Note not found")
    if not update_note(note_id, content):
        raise RuntimeError("Update failed")
    delete_note_chunks(note_id)
    chunks = split_text(content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    add_chunks(note_id, chunks, embeddings)
    updated = get_note(note_id)
    if updated is None:
        raise RuntimeError("Update failed")
    return updated


def delete_note_item(note_id: int) -> int:
    if not delete_note(note_id):
        raise ValueError("Note not found")
    delete_note_chunks(note_id)
    return note_id


def _note_title(content: str, fallback: str = "未命名笔记") -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback


def _parse_structured_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in content.splitlines()[:12]:
        stripped = line.strip()
        if not stripped or ("：" not in stripped and ":" not in stripped):
            continue
        key, value = re.split(r"\s*[:：]\s*", stripped, maxsplit=1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            continue
        for canonical, aliases in STRUCTURED_FIELD_ALIASES.items():
            if normalized_key == canonical or normalized_key in aliases:
                fields[canonical] = normalized_value
                break
    return fields


def _detect_question_field(question: str) -> str | None:
    normalized = question.replace(" ", "")
    for canonical, aliases in STRUCTURED_FIELD_ALIASES.items():
        if canonical in normalized:
            return canonical
        if any(alias in normalized for alias in aliases):
            return canonical
    return None


def _find_title_matched_notes(question: str, notes: list[dict]) -> list[dict]:
    normalized_question = question.replace(" ", "").lower()
    matches: list[tuple[int, int, dict]] = []
    for note in notes:
        title = _note_title(note["content"], f"笔记{note['id']}")
        normalized_title = title.replace(" ", "").lower()
        if normalized_title and normalized_title in normalized_question:
            matches.append((len(normalized_title), int(note["id"]), note))
    matches.sort(key=lambda item: (-item[0], -item[1]))
    return [item[2] for item in matches]


def _keyword_chars(text: str) -> set[str]:
    result: set[str] = set()
    for char in text.lower():
        if "a" <= char <= "z" or "0" <= char <= "9" or "\u4e00" <= char <= "\u9fff":
            if char not in QUESTION_STOP_CHARS:
                result.add(char)
    return result


def _select_context_notes(question: str, notes: list[dict], query_hits: list[dict], limit: int = 2) -> list[dict]:
    title_matched = _find_title_matched_notes(question, notes)
    if title_matched:
        return title_matched[:limit]

    keywords = _keyword_chars(question)
    notes_by_id = {int(note["id"]): note for note in notes}
    scored: dict[int, tuple[int, int, dict]] = {}

    for rank, hit in enumerate(query_hits):
        metadata = hit.get("metadata") or {}
        note_id = metadata.get("note_id")
        if note_id is None:
            continue
        note = notes_by_id.get(int(note_id))
        if note is None:
            continue
        content = note["content"]
        overlap = len(keywords & _keyword_chars(content[:800]))
        if overlap <= 0 and rank > 0:
            continue
        existing = scored.get(int(note_id))
        score = overlap * 10 - rank
        if existing is None or score > existing[0]:
            scored[int(note_id)] = (score, rank, note)

    if scored:
        ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[:limit]]

    fallback: list[dict] = []
    seen: set[int] = set()
    for hit in query_hits:
        metadata = hit.get("metadata") or {}
        note_id = metadata.get("note_id")
        if note_id is None:
            continue
        note_id = int(note_id)
        if note_id in seen:
            continue
        note = notes_by_id.get(note_id)
        if note is None:
            continue
        fallback.append(note)
        seen.add(note_id)
        if len(fallback) >= limit:
            break
    return fallback


def _format_context(notes: list[dict]) -> str:
    blocks: list[str] = []
    for note in notes:
        title = _note_title(note["content"], f"笔记{note['id']}")
        blocks.append(f"[笔记#{note['id']} {title}]\n{note['content']}")
    return "\n\n".join(blocks)


def _try_structured_answer(question: str, notes: list[dict]) -> str | None:
    target_field = _detect_question_field(question)
    if target_field is None:
        return None

    matched_notes = _find_title_matched_notes(question, notes)
    if not matched_notes:
        return None

    for note in matched_notes:
        fields = _parse_structured_fields(note["content"])
        value = fields.get(target_field)
        if value:
            title = _note_title(note["content"], f"笔记{note['id']}")
            return f"{title}的{target_field}是{value}。"
    return None


def ask_question(question: str) -> dict[str, str]:
    launcher_core.ensure_ollama_running()
    notes = list_note_items()

    structured_answer = _try_structured_answer(question, notes)
    if structured_answer is not None:
        return {"answer": structured_answer, "model": get_llm_model()}

    query_embedding = embed_text(question)
    query_hits = query_chunks(query_embedding, top_k=8)
    context_notes = _select_context_notes(question, notes, query_hits, limit=2)
    context = _format_context(context_notes)
    if not context.strip():
        return {"answer": "未找到相关信息", "model": get_llm_model()}

    prompt = CHAT_PROMPT_TEMPLATE.format(context=context, question=question)
    answer = generate_text(prompt)
    return {"answer": answer, "model": get_llm_model()}
