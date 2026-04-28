from __future__ import annotations


def test_notes_store_created_and_updated_actor(repo_modules):
    db = repo_modules.db

    note_id = db.insert_note("first title", "first body", "alice")
    created = db.get_note(note_id)

    assert created is not None
    assert created["title"] == "first title"
    assert created["body"] == "first body"
    assert created["created_by"] == "alice"
    assert created["updated_by"] == "alice"
    assert created["updated_at"] == created["created_at"]

    assert db.update_note(note_id, "second title", "second body", "bob") is True
    updated = db.get_note(note_id)

    assert updated is not None
    assert updated["title"] == "second title"
    assert updated["body"] == "second body"
    assert updated["content"] == "second title\nsecond body"
    assert updated["created_by"] == "alice"
    assert updated["updated_by"] == "bob"
    assert updated["updated_at"]


def test_init_db_seeds_default_admin_account(repo_modules):
    db = repo_modules.db

    accounts = db.list_accounts()
    admin = next((item for item in accounts if item["username"] == "admin"), None)

    assert admin is not None
    assert admin["display_name"] == "admin"
    assert admin["role"] == "admin"
    assert db.verify_account("admin", "admin") is not None


def test_accounts_use_username_as_canonical_identity(repo_modules):
    db = repo_modules.db

    account = db.create_account("AdminUser", "1234", "Ignored Display", "admin")
    verified = db.verify_account("ADMINUSER", "1234")
    listed = db.list_accounts()
    listed_account = next((item for item in listed if item["username"] == "adminuser"), None)

    assert account["username"] == "adminuser"
    assert account["display_name"] == "adminuser"
    assert verified is not None
    assert verified["username"] == "adminuser"
    assert verified["display_name"] == "adminuser"
    assert listed_account is not None
    assert listed_account["display_name"] == "adminuser"
    assert db.verify_account("adminuser", "wrong-password") is None


def test_init_db_normalizes_existing_display_name_values(repo_modules):
    db = repo_modules.db

    account = db.create_account("legacyuser", "1234", "legacyuser", "member")
    with db.get_connection() as conn:
        conn.execute("UPDATE accounts SET display_name = ? WHERE id = ?", ("legacy-name", account["id"]))
        conn.commit()

    db.init_db()
    normalized = db.get_account_by_id(account["id"])

    assert normalized is not None
    assert normalized["display_name"] == "legacyuser"


def test_insert_note_allows_preserving_imported_metadata(repo_modules):
    db = repo_modules.db

    note_id = db.insert_note(
        "imported title",
        "imported body",
        "admin",
        created_at="2026-01-02T03:04:05+00:00",
        updated_at="2026-01-03T04:05:06+00:00",
        created_by="alice",
        updated_by="bob",
    )
    note = db.get_note(note_id)

    assert note is not None
    assert note["title"] == "imported title"
    assert note["body"] == "imported body"
    assert note["created_at"] == "2026-01-02T03:04:05+00:00"
    assert note["updated_at"] == "2026-01-03T04:05:06+00:00"
    assert note["created_by"] == "alice"
    assert note["updated_by"] == "bob"
