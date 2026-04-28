from __future__ import annotations

import re
from datetime import datetime, timezone

import launcher_core
from chroma_store import add_chunks, delete_note_chunks, get_collection, get_note_chunks, query_chunks
from db import (
    count_accounts,
    count_admin_accounts,
    create_account,
    delete_account,
    delete_note,
    get_account_by_id,
    get_note,
    init_db,
    insert_note,
    list_accounts,
    list_notes,
    record_account_login,
    update_account_password,
    update_note,
    verify_account,
)
from ollama_client import embed_text, generate_text, get_llm_model, init_runtime_settings
from text_splitter import split_text


CHAT_PROMPT_TEMPLATE = """
你是一个严格基于用户笔记回答问题的助手。

[已知信息]
{context}

[用户问题]
{question}

[回答规则]
1. 只能使用已知信息中的内容回答。
2. 不要使用外部知识或自行推断。
3. 如果已知信息中没有明确答案，就回答：未找到相关信息。
4. 优先保证准确，不追求扩写。
5. 回答尽量简洁清晰。

请开始回答：
"""

STRUCTURED_FIELD_ALIASES = {
    "\u6b4c\u624b": ("\u6b4c\u624b", "\u6f14\u5531", "\u539f\u5531", "\u8c01\u5531", "\u6f14\u5531\u8005"),
    "\u4f5c\u8bcd": ("\u4f5c\u8bcd", "\u8bcd\u4f5c\u8005", "\u8bcd\u4f5c", "\u586b\u8bcd"),
    "\u4f5c\u66f2": ("\u4f5c\u66f2", "\u66f2\u4f5c\u8005", "\u8c31\u66f2"),
    "\u7f16\u66f2": ("\u7f16\u66f2", "\u7f16\u8005"),
}
QUESTION_STOP_CHARS = set("\u7684\u662f\u4e86\u5462\u5417\u554a\u5427\u4e48\u4ec0\u4e48\u8bf7\u95ee\u4e00\u4e0b\u544a\u8bc9\u6211\u8fd9\u90a3\u54ea\u51e0\u8c01\u591a\u5c11\u6709\u65e0\u548c\u4e0e\u53ca\u5e74\u6708\u65e5")


def init_storage() -> None:
    init_db()
    get_collection()


def init_services() -> None:
    init_storage()
    launcher_core.ensure_ollama_running()
    init_runtime_settings()


def count_account_items() -> int:
    return count_accounts()


def list_account_items() -> list[dict]:
    return list_accounts()


def create_account_item(username: str, password: str, role: str = "member") -> dict:
    return create_account(username, password, username, role)


def authenticate_account(username: str, password: str) -> dict | None:
    account = verify_account(username, password)
    if account is None:
        return None
    refreshed = record_account_login(int(account["id"]))
    return refreshed or account


def delete_account_item(account_id: int, current_account_id: int | None = None) -> int:
    account = get_account_by_id(account_id)
    if account is None:
        raise ValueError("Account not found")
    if current_account_id is not None and int(account_id) == int(current_account_id):
        raise ValueError("Current account cannot be deleted.")
    if count_account_items() <= 1:
        raise ValueError("At least one account must remain.")
    if account["role"] == "admin" and count_admin_accounts() <= 1:
        raise ValueError("At least one admin account must remain.")
    if not delete_account(account_id):
        raise RuntimeError("Delete account failed")
    return account_id


def update_account_password_item(account_id: int, new_password: str) -> dict:
    updated = update_account_password(account_id, new_password)
    if updated is None:
        raise ValueError("Account not found")
    return updated


def create_note_item(payload: dict[str, str], actor: str) -> int:
    launcher_core.ensure_ollama_running()
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    content = _note_document(title, body)
    chunks = split_text(content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    note_id = insert_note(title, body, actor)
    try:
        add_chunks(note_id, chunks, embeddings)
    except Exception as exc:
        try:
            delete_note_chunks(note_id)
        except Exception:
            pass
        delete_note(note_id)
        raise RuntimeError("Create note failed while building the search index.") from exc
    return note_id


def list_note_items() -> list[dict]:
    return list_notes()


def get_note_item(note_id: int) -> dict | None:
    return get_note(note_id)


def update_note_item_content(note_id: int, payload: dict[str, str], actor: str) -> dict:
    launcher_core.ensure_ollama_running()
    existing = get_note(note_id)
    if existing is None:
        raise ValueError("Note not found")
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    content = _note_document(title, body)
    chunks = split_text(content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    previous_chunks, previous_embeddings = get_note_chunks(note_id)
    if (
        title == str(existing.get("title") or "").strip()
        and body == str(existing.get("body") or "").strip()
        and previous_chunks
    ):
        if not update_note(note_id, title, body, actor):
            raise RuntimeError("Update failed")
        updated = get_note(note_id)
        if updated is None:
            raise RuntimeError("Update failed")
        return updated
    try:
        delete_note_chunks(note_id)
        add_chunks(note_id, chunks, embeddings)
        if not update_note(note_id, title, body, actor):
            raise RuntimeError("Update failed")
    except Exception as exc:
        rollback_errors: list[Exception] = []
        try:
            delete_note_chunks(note_id)
        except Exception as rollback_exc:
            rollback_errors.append(rollback_exc)
        if previous_chunks and previous_embeddings:
            try:
                add_chunks(note_id, previous_chunks, previous_embeddings)
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise RuntimeError("Update failed and the previous search index could not be fully restored.") from exc
        raise RuntimeError("Update failed while rebuilding the search index.") from exc
    updated = get_note(note_id)
    if updated is None:
        raise RuntimeError("Update failed")
    return updated


def export_note_item(note_id: int) -> dict[str, object]:
    note = get_note(note_id)
    if note is None:
        raise ValueError("Note not found")
    return {
        "format": "private-note/single-note",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "note": {
            "title": str(note.get("title") or "").strip(),
            "body": str(note.get("body") or "").strip(),
            "created_at": str(note.get("created_at") or ""),
            "updated_at": str(note.get("updated_at") or note.get("created_at") or ""),
            "created_by": str(note.get("created_by") or ""),
            "updated_by": str(note.get("updated_by") or note.get("created_by") or ""),
        },
    }


def import_note_item(payload: dict[str, object], actor: str) -> dict:
    launcher_core.ensure_ollama_running()
    format_name = str(payload.get("format") or "")
    version = int(payload.get("version") or 0)
    if format_name not in {"private-note/single-note", "private-note/note"} or version != 1:
        raise ValueError("Unsupported note file format")
    note_payload = payload.get("note")
    if not isinstance(note_payload, dict):
        raise ValueError("Invalid note file")
    title = str(note_payload.get("title") or "").strip()
    body = str(note_payload.get("body") or "").strip()
    if not title:
        raise ValueError("Imported note title is required")
    content = _note_document(title, body)
    chunks = split_text(content)
    embeddings = [embed_text(chunk) for chunk in chunks]
    note_id = insert_note(
        title,
        body,
        actor,
        created_at=str(note_payload.get("created_at") or ""),
        updated_at=str(note_payload.get("updated_at") or note_payload.get("created_at") or ""),
        created_by=str(note_payload.get("created_by") or actor),
        updated_by=str(note_payload.get("updated_by") or note_payload.get("created_by") or actor),
    )
    try:
        add_chunks(note_id, chunks, embeddings)
    except Exception as exc:
        try:
            delete_note_chunks(note_id)
        except Exception:
            pass
        delete_note(note_id)
        raise RuntimeError("Import note failed while building the search index.") from exc
    created = get_note(note_id)
    if created is None:
        raise RuntimeError("Imported note could not be loaded")
    return created


def delete_note_item(note_id: int) -> int:
    note = get_note(note_id)
    if note is None:
        raise ValueError("Note not found")
    previous_chunks, previous_embeddings = get_note_chunks(note_id)
    try:
        delete_note_chunks(note_id)
        if not delete_note(note_id):
            raise RuntimeError("Delete note failed")
    except Exception as exc:
        if previous_chunks and previous_embeddings:
            try:
                add_chunks(note_id, previous_chunks, previous_embeddings)
            except Exception as rollback_exc:
                raise RuntimeError("Delete failed and the previous search index could not be fully restored.") from rollback_exc
        raise RuntimeError("Delete failed while removing the search index.") from exc
    return note_id


def _note_document(title: str, body: str) -> str:
    title_text = str(title or "").strip()
    body_text = str(body or "").strip()
    if title_text and body_text:
        return f"{title_text}\n{body_text}"
    return title_text or body_text


def _note_title(note: dict, fallback: str = "untitled note") -> str:
    title_text = str(note.get("title") or "").strip()
    if title_text:
        return title_text
    for line in str(note.get("content") or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback


def _parse_structured_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in content.splitlines()[:12]:
        stripped = line.strip()
        if not stripped or ("\uff1a" not in stripped and ":" not in stripped):
            continue
        key, value = re.split(r"\s*[:\uff1a]\s*", stripped, maxsplit=1)
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
        title = _note_title(note, f"note {note['id']}")
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


def _note_keyword_overlap(note: dict, keywords: set[str]) -> int:
    content = _note_document(str(note.get("title") or ""), str(note.get("body") or note.get("content") or ""))
    return len(keywords & _keyword_chars(content[:800]))


def _select_context_notes(question: str, notes: list[dict], query_hits: list[dict], limit: int = 2) -> list[dict]:
    title_matched = _find_title_matched_notes(question, notes)
    if title_matched:
        return title_matched[:limit]

    keywords = _keyword_chars(question)
    notes_by_id = {int(note["id"]): note for note in notes}
    scored: dict[int, tuple[int, int, dict]] = {}
    weak_scored: dict[int, tuple[int, int, dict]] = {}

    for rank, hit in enumerate(query_hits):
        metadata = hit.get("metadata") or {}
        note_id = metadata.get("note_id")
        if note_id is None:
            continue
        note = notes_by_id.get(int(note_id))
        if note is None:
            continue
        overlap = _note_keyword_overlap(note, keywords)
        score = overlap * 10 - rank
        bucket = scored if overlap > 0 else weak_scored
        existing = bucket.get(int(note_id))
        if existing is None or score > existing[0]:
            bucket[int(note_id)] = (score, rank, note)

    if scored:
        ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[:limit]]

    if weak_scored:
        ranked = sorted(weak_scored.values(), key=lambda item: item[1])
        return [ranked[0][2]]

    return []


def _format_context(notes: list[dict]) -> str:
    blocks: list[str] = []
    for note in notes:
        title = _note_title(note, f"note {note['id']}")
        content = _note_document(str(note.get("title") or ""), str(note.get("body") or note.get("content") or ""))
        blocks.append(f"[note #{note['id']} {title}]\n{content}")
    return "\n\n".join(blocks)


def _question_terms(question: str) -> list[str]:
    raw_terms = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", str(question or "").lower())
    seen: set[str] = set()
    terms: list[str] = []
    for term in sorted(raw_terms, key=len, reverse=True):
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _build_source_snippet(text: str, question: str, limit: int = 84) -> str:
    content = " ".join(str(text or "").split())
    if not content:
        return ""
    lower_content = content.lower()
    for term in _question_terms(question):
        index = lower_content.find(term)
        if index < 0:
            continue
        start = max(0, index - limit // 2)
        end = min(len(content), index + len(term) + limit // 2)
        snippet = content[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        return snippet
    if len(content) <= limit:
        return content
    return content[: limit - 3].rstrip() + "..."


def _build_answer_sources(question: str, context_notes: list[dict], query_hits: list[dict], limit: int = 1) -> list[dict[str, object]]:
    snippets_by_note_id: dict[int, str] = {}
    for hit in query_hits:
        metadata = hit.get("metadata") or {}
        note_id = metadata.get("note_id")
        if note_id is None:
            continue
        key = int(note_id)
        if key in snippets_by_note_id:
            continue
        snippets_by_note_id[key] = _build_source_snippet(str(hit.get("document") or ""), question)

    keywords = _keyword_chars(question)
    source_candidates: list[tuple[int, dict[str, object]]] = []
    seen: set[int] = set()
    for note in context_notes:
        note_id = int(note["id"])
        if note_id in seen:
            continue
        seen.add(note_id)
        fallback_text = _note_document(str(note.get("title") or ""), str(note.get("body") or note.get("content") or ""))
        source_candidates.append(
            (
                _note_keyword_overlap(note, keywords),
                {
                    "note_id": note_id,
                    "title": _note_title(note, f"note {note_id}"),
                    "snippet": snippets_by_note_id.get(note_id) or _build_source_snippet(fallback_text, question),
                },
            )
        )
        if len(source_candidates) >= limit:
            break
    if not source_candidates:
        return []

    matched_sources = [source for overlap, source in source_candidates if overlap > 0]
    if matched_sources:
        return matched_sources[:limit]

    return [source_candidates[0][1]]


def _try_structured_answer(question: str, notes: list[dict]) -> str | None:
    target_field = _detect_question_field(question)
    if target_field is None:
        return None

    matched_notes = _find_title_matched_notes(question, notes)
    if not matched_notes:
        return None

    for note in matched_notes:
        content = _note_document(str(note.get("title") or ""), str(note.get("body") or note.get("content") or ""))
        fields = _parse_structured_fields(content)
        value = fields.get(target_field)
        if value:
            title = _note_title(note, f"note {note['id']}")
            return f"{title} \u7684 {target_field} \u662f {value}\u3002"
    return None


def ask_question(question: str) -> dict[str, object]:
    launcher_core.ensure_ollama_running()
    init_runtime_settings()
    runtime_profile = launcher_core.get_saved_runtime_profile()
    notes = list_note_items()

    structured_answer = _try_structured_answer(question, notes)
    if structured_answer is not None:
        matched_notes = _find_title_matched_notes(question, notes)[:1]
        return {
            "answer": structured_answer,
            "model": get_llm_model(),
            "runtime_profile": runtime_profile,
            "sources": _build_answer_sources(question, matched_notes, [], limit=1),
        }

    query_embedding = embed_text(question)
    query_hits = query_chunks(query_embedding, top_k=8)
    context_notes = _select_context_notes(question, notes, query_hits, limit=2)
    context = _format_context(context_notes)
    if not context.strip():
        return {
            "answer": "\u672a\u627e\u5230\u76f8\u5173\u4fe1\u606f\u3002",
            "model": get_llm_model(),
            "runtime_profile": runtime_profile,
            "sources": [],
        }

    prompt = CHAT_PROMPT_TEMPLATE.format(context=context, question=question)
    answer = generate_text(prompt)
    return {
        "answer": answer,
        "model": get_llm_model(),
        "runtime_profile": runtime_profile,
        "sources": _build_answer_sources(question, context_notes, query_hits, limit=1),
    }
