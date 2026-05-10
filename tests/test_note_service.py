from __future__ import annotations

import pytest


def test_authenticate_account_records_last_login(repo_modules):
    note_service = repo_modules.note_service

    created = note_service.create_account_item("rootuser", "1234", role="admin")
    authenticated = note_service.authenticate_account("ROOTUSER", "1234")

    assert authenticated is not None
    assert authenticated["id"] == created["id"]
    assert authenticated["last_login_at"]


def test_delete_account_item_blocks_removing_last_admin(repo_modules):
    note_service = repo_modules.note_service

    admin = next(item for item in note_service.list_account_items() if item["role"] == "admin")
    member = note_service.create_account_item("memberuser", "1234", role="member")

    with pytest.raises(ValueError, match="At least one admin account must remain."):
        note_service.delete_account_item(admin["id"], current_account_id=member["id"])


def test_create_note_item_indexes_chunks_and_stores_actor(repo_modules, monkeypatch):
    note_service = repo_modules.note_service

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "split_text", lambda content: ["alpha", "beta"])
    monkeypatch.setattr(note_service, "embed_text", lambda chunk: [float(len(chunk))])

    captured = {}

    def fake_add_chunks(note_id, chunks, embeddings):
        captured["note_id"] = note_id
        captured["chunks"] = chunks
        captured["embeddings"] = embeddings

    monkeypatch.setattr(note_service, "add_chunks", fake_add_chunks)

    note_id = note_service.create_note_item({"title": "test note", "body": "body"}, "alice")
    stored = note_service.get_note_item(note_id)

    assert stored is not None
    assert stored["title"] == "test note"
    assert stored["body"] == "body"
    assert stored["created_by"] == "alice"
    assert stored["updated_by"] == "alice"
    assert captured == {
        "note_id": note_id,
        "chunks": ["alpha", "beta"],
        "embeddings": [[5.0], [4.0]],
    }


def test_update_note_item_rebuilds_chunks_and_updates_actor(repo_modules, monkeypatch):
    note_service = repo_modules.note_service
    db = repo_modules.db

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "split_text", lambda content: ["gamma"])
    monkeypatch.setattr(note_service, "embed_text", lambda chunk: [1.0])

    deleted = []
    added = []

    monkeypatch.setattr(note_service, "delete_note_chunks", lambda note_id: deleted.append(note_id))
    monkeypatch.setattr(note_service, "add_chunks", lambda note_id, chunks, embeddings: added.append((note_id, chunks, embeddings)))

    note_id = db.insert_note("old title", "old body", "alice")
    updated = note_service.update_note_item_content(note_id, {"title": "new title", "body": "new body"}, "bob")

    assert deleted == [note_id]
    assert added == [(note_id, ["gamma"], [[1.0]])]
    assert updated["title"] == "new title"
    assert updated["body"] == "new body"
    assert updated["updated_by"] == "bob"
    assert updated["created_by"] == "alice"


def test_export_and_import_note_item_preserve_metadata(repo_modules, monkeypatch):
    note_service = repo_modules.note_service

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "split_text", lambda content: ["chunk"])
    monkeypatch.setattr(note_service, "embed_text", lambda chunk: [2.0])
    added = []
    monkeypatch.setattr(note_service, "add_chunks", lambda note_id, chunks, embeddings: added.append((note_id, chunks, embeddings)))

    original_id = note_service.create_note_item({"title": "export title", "body": "export body"}, "alice")
    exported = note_service.export_note_item(original_id)
    exported["note"]["created_at"] = "2026-01-02T03:04:05+00:00"
    exported["note"]["updated_at"] = "2026-01-03T03:04:05+00:00"
    exported["note"]["created_by"] = "alice"
    exported["note"]["updated_by"] = "bob"

    imported = note_service.import_note_item(exported, "admin")

    assert imported["id"] != original_id
    assert imported["title"] == "export title"
    assert imported["body"] == "export body"
    assert imported["created_at"] == "2026-01-02T03:04:05+00:00"
    assert imported["updated_at"] == "2026-01-03T03:04:05+00:00"
    assert imported["created_by"] == "alice"
    assert imported["updated_by"] == "bob"
    assert added[-1] == (imported["id"], ["chunk"], [[2.0]])


def test_ask_question_returns_sources(repo_modules, monkeypatch):
    note_service = repo_modules.note_service

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "init_runtime_settings", lambda: None)
    monkeypatch.setattr(note_service.launcher_core, "get_saved_runtime_profile", lambda: "gpu")
    monkeypatch.setattr(note_service, "get_llm_model", lambda: "qwen2.5:7b")
    monkeypatch.setattr(note_service, "embed_text", lambda text: [1.0])
    monkeypatch.setattr(
        note_service,
        "query_chunks",
        lambda embedding, top_k: [
            {
                "document": "这里记录了关于 RAG 检索链路的设计想法和实现步骤。",
                "distance": 0.1,
                "metadata": {"note_id": 1, "chunk_index": 0},
            }
        ],
    )
    monkeypatch.setattr(note_service, "generate_text", lambda prompt: "这是基于笔记的回答。")

    note_service.create_note_item({"title": "RAG 方案", "body": "这里记录了关于 RAG 检索链路的设计想法和实现步骤。"}, "alice")

    payload = note_service.ask_question("RAG 设计想法是什么？")

    assert payload["answer"] == "这是基于笔记的回答。"
    assert payload["runtime_profile"] == "gpu"
    assert payload["sources"]
    first_source = payload["sources"][0]
    assert first_source["note_id"] == 1
    assert first_source["title"] == "RAG 方案"
    assert "RAG" in first_source["snippet"]


def test_ask_question_filters_unrelated_sources(repo_modules, monkeypatch):
    note_service = repo_modules.note_service
    db = repo_modules.db

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "init_runtime_settings", lambda: None)
    monkeypatch.setattr(note_service.launcher_core, "get_saved_runtime_profile", lambda: "cpu")
    monkeypatch.setattr(note_service, "get_llm_model", lambda: "qwen2.5:7b")
    monkeypatch.setattr(note_service, "embed_text", lambda text: [1.0])

    relevant_id = db.insert_note("\u5408\u5e76 git \u5206\u652f", "\u4f7f\u7528 git merge <branch-name> \u5408\u5e76\u5206\u652f", "alice")
    unrelated_id = db.insert_note("\u68a6\u6e38\u5929\u59e5", "\u6d77\u5ba2\u8c08\u701b\u6d32\uff0c\u70df\u6d9b\u5fae\u832b\u4fe1\u96be\u6c42\u3002", "alice")

    monkeypatch.setattr(
        note_service,
        "query_chunks",
        lambda embedding, top_k: [
            {
                "document": "\u6d77\u5ba2\u8c08\u701b\u6d32\uff0c\u70df\u6d9b\u5fae\u832b\u4fe1\u96be\u6c42\u3002",
                "distance": 0.01,
                "metadata": {"note_id": unrelated_id, "chunk_index": 0},
            },
            {
                "document": "\u4f7f\u7528 git merge <branch-name> \u5408\u5e76\u5206\u652f",
                "distance": 0.20,
                "metadata": {"note_id": relevant_id, "chunk_index": 0},
            },
        ],
    )

    captured: dict[str, str] = {}

    def fake_generate(prompt: str) -> str:
        captured["prompt"] = prompt
        return "git merge <branch-name>"

    monkeypatch.setattr(note_service, "generate_text", fake_generate)

    payload = note_service.ask_question("\u5982\u4f55\u5408\u5e76git\u5206\u652f\uff1f")

    assert payload["sources"]
    assert len(payload["sources"]) == 1
    assert payload["sources"][0]["note_id"] == relevant_id
    assert "git merge" in payload["sources"][0]["snippet"]
    assert "\u6d77\u5ba2" not in captured["prompt"]


def test_ask_question_returns_only_single_best_source(repo_modules, monkeypatch):
    note_service = repo_modules.note_service
    db = repo_modules.db

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "init_runtime_settings", lambda: None)
    monkeypatch.setattr(note_service.launcher_core, "get_saved_runtime_profile", lambda: "gpu")
    monkeypatch.setattr(note_service, "get_llm_model", lambda: "qwen2.5:7b")
    monkeypatch.setattr(note_service, "embed_text", lambda text: [1.0])

    thesis_id = db.insert_note("\u8bba\u6587\u63d0\u4ea4\u65f6\u95f4\u662f4\u670822\u65e5", "\u8bba\u6587\u63d0\u4ea4\u65f6\u95f4\u662f4\u670822\u65e5\u3002", "alice")
    git_id = db.insert_note("git\u7b14\u8bb0\u901f\u67e5", "git commit \u7528\u4e8e\u63d0\u4ea4\uff0cgit push \u7528\u4e8e\u63d0\u4ea4\u8fdc\u7a0b\u4ed3\u5e93\u3002", "alice")

    monkeypatch.setattr(
        note_service,
        "query_chunks",
        lambda embedding, top_k: [
            {
                "document": "git commit \u7528\u4e8e\u63d0\u4ea4\uff0cgit push \u7528\u4e8e\u63d0\u4ea4\u8fdc\u7a0b\u4ed3\u5e93\u3002",
                "distance": 0.05,
                "metadata": {"note_id": git_id, "chunk_index": 0},
            },
            {
                "document": "\u8bba\u6587\u63d0\u4ea4\u65f6\u95f4\u662f4\u670822\u65e5\u3002",
                "distance": 0.08,
                "metadata": {"note_id": thesis_id, "chunk_index": 0},
            },
        ],
    )
    monkeypatch.setattr(note_service, "generate_text", lambda prompt: "\u8bba\u6587\u63d0\u4ea4\u65f6\u95f4\u662f4\u670822\u65e5\u3002")

    payload = note_service.ask_question("\u8bba\u6587\u63d0\u4ea4\u65f6\u95f4\u662f\u4ec0\u4e48\u65f6\u5019\uff1f")

    assert payload["sources"]
    assert len(payload["sources"]) == 1
    assert payload["sources"][0]["note_id"] == thesis_id
    assert "4\u670822\u65e5" in payload["sources"][0]["title"] or "4\u670822\u65e5" in payload["sources"][0]["snippet"]


def test_create_note_item_rolls_back_database_when_index_build_fails(repo_modules, monkeypatch):
    note_service = repo_modules.note_service

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "split_text", lambda content: [content])
    monkeypatch.setattr(note_service, "embed_text", lambda chunk: [1.0])
    monkeypatch.setattr(note_service, "add_chunks", lambda note_id, chunks, embeddings: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="search index"):
        note_service.create_note_item({"title": "rollback", "body": "body"}, "alice")

    assert note_service.list_note_items() == []


def test_update_note_item_restores_previous_chunks_when_reindex_fails(repo_modules, monkeypatch):
    note_service = repo_modules.note_service
    db = repo_modules.db

    monkeypatch.setattr(note_service.launcher_core, "ensure_ollama_running", lambda: None)
    monkeypatch.setattr(note_service, "split_text", lambda content: [content])
    monkeypatch.setattr(note_service, "embed_text", lambda chunk: [float(len(chunk))])

    note_id = db.insert_note("old title", "old body", "alice")
    monkeypatch.setattr(note_service, "get_note_chunks", lambda current_note_id: (["old title\nold body"], [[7.0]]) if current_note_id == note_id else ([], []))

    deleted: list[int] = []
    restored: list[list[str]] = []

    monkeypatch.setattr(note_service, "delete_note_chunks", lambda current_note_id: deleted.append(current_note_id))

    def fake_add_chunks(current_note_id: int, chunks: list[str], embeddings: list[list[float]]) -> None:
        restored.append(list(chunks))
        if len(restored) == 1:
            raise RuntimeError("index failed")

    monkeypatch.setattr(note_service, "add_chunks", fake_add_chunks)

    with pytest.raises(RuntimeError, match="rebuilding the search index"):
        note_service.update_note_item_content(note_id, {"title": "new title", "body": "new body"}, "bob")

    note = note_service.get_note_item(note_id)
    assert note is not None
    assert note["title"] == "old title"
    assert note["body"] == "old body"
    assert deleted == [note_id, note_id]
    assert restored == [["new title\nnew body"], ["old title\nold body"]]


def test_delete_note_item_restores_previous_chunks_when_note_delete_fails(repo_modules, monkeypatch):
    note_service = repo_modules.note_service
    db = repo_modules.db

    note_id = db.insert_note("keep title", "keep body", "alice")
    monkeypatch.setattr(note_service, "get_note_chunks", lambda current_note_id: (["keep title\nkeep body"], [[3.0]]) if current_note_id == note_id else ([], []))

    deleted: list[int] = []
    restored: list[tuple[int, list[str], list[list[float]]]] = []

    monkeypatch.setattr(note_service, "delete_note_chunks", lambda current_note_id: deleted.append(current_note_id))
    monkeypatch.setattr(note_service, "delete_note", lambda current_note_id: False)
    monkeypatch.setattr(
        note_service,
        "add_chunks",
        lambda current_note_id, chunks, embeddings: restored.append((current_note_id, list(chunks), list(embeddings))),
    )

    with pytest.raises(RuntimeError, match="removing the search index"):
        note_service.delete_note_item(note_id)

    assert note_service.get_note_item(note_id) is not None
    assert deleted == [note_id]
    assert restored == [(note_id, ["keep title\nkeep body"], [[3.0]])]
