import os
import sqlite3
from datetime import datetime, timezone


DB_PATH = os.getenv("SQLITE_PATH", "data/notes.db")


def _ensure_dir() -> None:
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    _ensure_dir()
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def insert_note(content: str) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (content, created_at) VALUES (?, ?)",
            (content, created_at),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_notes() -> list[dict]:
    with get_connection() as conn:
        cursor = conn.execute("SELECT id, content, created_at FROM notes ORDER BY id DESC")
        rows = cursor.fetchall()
        return [{"id": row[0], "content": row[1], "created_at": row[2]} for row in rows]


def get_note(note_id: int) -> dict | None:
    with get_connection() as conn:
        cursor = conn.execute("SELECT id, content, created_at FROM notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return {"id": row[0], "content": row[1], "created_at": row[2]}


def update_note(note_id: int, content: str) -> bool:
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE notes SET content = ?, created_at = ? WHERE id = ?",
            (content, created_at, note_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_note(note_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return cursor.rowcount > 0
