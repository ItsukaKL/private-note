from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from project_paths import SQLITE_PATH as DEFAULT_DB_PATH


DB_PATH = Path(DEFAULT_DB_PATH)
PASSWORD_ITERATIONS = 120_000
VALID_ACCOUNT_ROLES = {"admin", "member"}
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    _ensure_dir()
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    return {str(row["name"]) for row in cursor.fetchall()}


def _split_legacy_note_content(content: str) -> tuple[str, str]:
    lines = str(content or "").splitlines()
    title = ""
    body_lines: list[str] = []
    found_title = False
    for line in lines:
        stripped = line.strip()
        if not found_title and stripped:
            title = stripped
            found_title = True
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip("\n")
    return title.strip(), body


def _compose_note_content(title: str, body: str) -> str:
    title_text = str(title or "").strip()
    body_text = str(body or "").strip()
    if title_text and body_text:
        return f"{title_text}\n{body_text}"
    return title_text or body_text


def _note_from_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "title": str(row["title"] or ""),
        "body": str(row["body"] or ""),
        "content": str(row["content"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"] or ""),
        "created_by": str(row["created_by"] or ""),
        "updated_by": str(row["updated_by"] or ""),
    }


def _ensure_notes_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
        )
        """
    )

    columns = _table_columns(conn, "notes")
    if "title" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    if "body" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN body TEXT NOT NULL DEFAULT ''")
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    if "created_by" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")
    if "updated_by" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN updated_by TEXT NOT NULL DEFAULT ''")

    conn.execute(
        """
        UPDATE notes
        SET updated_at = created_at
        WHERE updated_at IS NULL OR updated_at = ''
        """
    )

    if "title" not in columns or "body" not in columns:
        cursor = conn.execute("SELECT id, content FROM notes")
        for row in cursor.fetchall():
            title, body = _split_legacy_note_content(str(row["content"] or ""))
            conn.execute(
                "UPDATE notes SET title = ?, body = ? WHERE id = ?",
                (title, body, int(row["id"])),
            )


def _ensure_accounts_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            last_login_at TEXT
        )
        """
    )
    columns = _table_columns(conn, "accounts")
    if "password_plain" in columns:
        conn.execute("UPDATE accounts SET password_plain = '' WHERE password_plain IS NOT NULL AND password_plain != ''")
    conn.execute(
        """
        UPDATE accounts
        SET display_name = username
        WHERE display_name IS NULL OR display_name = '' OR display_name != username
        """
    )


def _ensure_default_admin_account(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS total FROM accounts").fetchone()
    existing_total = int(row["total"]) if row is not None else 0
    if existing_total > 0:
        return

    username = _normalize_username(DEFAULT_ADMIN_USERNAME)
    created_at = _utc_now()
    password_salt = secrets.token_hex(16)
    password_hash = _hash_password(DEFAULT_ADMIN_PASSWORD, password_salt)
    conn.execute(
        """
        INSERT INTO accounts (username, password_hash, password_salt, display_name, role, created_at, last_login_at)
        VALUES (?, ?, ?, ?, 'admin', ?, '')
        """,
        (
            username,
            password_hash,
            password_salt,
            username,
            created_at,
        ),
    )


def _account_from_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"]),
        "role": str(row["role"]),
        "created_at": str(row["created_at"]),
        "last_login_at": str(row["last_login_at"] or ""),
    }


def _normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not value:
        raise ValueError("Username is required.")
    if len(value) < 3:
        raise ValueError("Username must be at least 3 characters.")
    return value


def _normalize_display_name(display_name: str, username: str) -> str:
    _ = display_name
    return username


def _normalize_role(role: str) -> str:
    value = role.strip().lower()
    if value not in VALID_ACCOUNT_ROLES:
        raise ValueError("Unsupported account role.")
    return value


def _validate_password(password: str) -> None:
    if len(password) < 4:
        raise ValueError("Password must be at least 4 characters.")


def _hash_password(password: str, salt_hex: str) -> str:
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PASSWORD_ITERATIONS,
    )
    return derived.hex()


def init_db() -> None:
    with get_connection() as conn:
        _ensure_notes_schema(conn)
        _ensure_accounts_schema(conn)
        _ensure_default_admin_account(conn)
        conn.commit()


def insert_note(
    title: str,
    body: str,
    actor: str,
    *,
    created_at: str | None = None,
    updated_at: str | None = None,
    created_by: str | None = None,
    updated_by: str | None = None,
) -> int:
    created_timestamp = str(created_at or _utc_now())
    updated_timestamp = str(updated_at or created_timestamp)
    created_actor = str(created_by or actor)
    updated_actor = str(updated_by or created_actor)
    content = _compose_note_content(title, body)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO notes (title, body, content, created_at, updated_at, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                body.strip(),
                content,
                created_timestamp,
                updated_timestamp,
                created_actor,
                updated_actor,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_notes() -> list[dict]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, title, body, content, created_at, updated_at, created_by, updated_by
            FROM notes
            ORDER BY updated_at DESC, id DESC
            """
        )
        rows = cursor.fetchall()
        return [_note_from_row(row) for row in rows if row is not None]


def get_note(note_id: int) -> dict | None:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, title, body, content, created_at, updated_at, created_by, updated_by
            FROM notes
            WHERE id = ?
            """,
            (note_id,),
        )
        return _note_from_row(cursor.fetchone())


def update_note(note_id: int, title: str, body: str, actor: str) -> bool:
    updated_at = _utc_now()
    content = _compose_note_content(title, body)
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE notes
            SET title = ?, body = ?, content = ?, updated_at = ?, updated_by = ?
            WHERE id = ?
            """,
            (title.strip(), body.strip(), content, updated_at, actor, note_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_note(note_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return cursor.rowcount > 0


def count_accounts() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM accounts").fetchone()
        return int(row["total"]) if row is not None else 0


def count_admin_accounts() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM accounts WHERE role = 'admin'").fetchone()
        return int(row["total"]) if row is not None else 0


def list_accounts() -> list[dict]:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, username, display_name, role, created_at, last_login_at
            FROM accounts
            ORDER BY id ASC
            """
        )
        return [_account_from_row(row) for row in cursor.fetchall() if row is not None]


def get_account_by_id(account_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, display_name, role, created_at, last_login_at
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        return _account_from_row(row)


def create_account(username: str, password: str, display_name: str, role: str = "member") -> dict:
    normalized_username = _normalize_username(username)
    normalized_display_name = _normalize_display_name(display_name, normalized_username)
    normalized_role = _normalize_role(role)
    _validate_password(password)

    created_at = _utc_now()
    password_salt = secrets.token_hex(16)
    password_hash = _hash_password(password, password_salt)

    with get_connection() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO accounts (username, password_hash, password_salt, display_name, role, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, '')
                """,
                (
                    normalized_username,
                    password_hash,
                    password_salt,
                    normalized_display_name,
                    normalized_role,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists.") from exc
        conn.commit()
        account_id = int(cursor.lastrowid)

    account = get_account_by_id(account_id)
    if account is None:
        raise RuntimeError("Failed to load created account.")
    return account


def verify_account(username: str, password: str) -> dict | None:
    normalized_username = _normalize_username(username)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, display_name, role, created_at, last_login_at, password_hash, password_salt
            FROM accounts
            WHERE username = ?
            """,
            (normalized_username,),
        ).fetchone()

    if row is None:
        return None

    expected_hash = str(row["password_hash"])
    actual_hash = _hash_password(password, str(row["password_salt"]))
    if not hmac.compare_digest(expected_hash, actual_hash):
        return None

    return _account_from_row(row)


def record_account_login(account_id: int) -> dict | None:
    last_login_at = _utc_now()
    with get_connection() as conn:
        conn.execute("UPDATE accounts SET last_login_at = ? WHERE id = ?", (last_login_at, account_id))
        conn.commit()
    return get_account_by_id(account_id)


def update_account_password(account_id: int, new_password: str) -> dict | None:
    _validate_password(new_password)
    password_salt = secrets.token_hex(16)
    password_hash = _hash_password(new_password, password_salt)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET password_hash = ?, password_salt = ?
            WHERE id = ?
            """,
            (password_hash, password_salt, account_id),
        )
        conn.commit()
    return get_account_by_id(account_id)


def delete_account(account_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.commit()
        return cursor.rowcount > 0
