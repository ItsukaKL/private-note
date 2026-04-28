from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import launcher_core

MODEL_HIDE_LIMIT_BYTES = 10 * 1024 * 1024 * 1024


def format_model_size(size_value: object) -> str:
    try:
        value = int(size_value)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    if value >= 1024**3:
        return f"{value / (1024**3):.1f} GB"
    if value >= 1024**2:
        return f"{value / (1024**2):.1f} MB"
    return f"{value / 1024:.1f} KB"


def is_embedding_model_entry(model_info: dict[str, object]) -> bool:
    name = str(model_info.get("name") or "").lower()
    details = model_info.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    family = str(details.get("family") or "").lower()
    families = details.get("families") or []
    family_text = " ".join(str(item).lower() for item in families)
    haystack = f"{name} {family} {family_text}"
    return "embed" in haystack or "bert" in haystack


def model_exceeds_hide_limit(model_info: dict[str, object]) -> bool:
    try:
        return int(model_info.get("size") or 0) > MODEL_HIDE_LIMIT_BYTES
    except (TypeError, ValueError):
        return False


def build_model_entries() -> tuple[list[dict[str, Any]], str, int]:
    try:
        official_models = [dict(item) for item in launcher_core.fetch_official_model_catalog()]
        source_text = f"official catalog: {len(official_models)} models"
    except Exception as exc:
        official_models = []
        source_text = f"official catalog unavailable: {exc}"

    installed_entries_by_name = {
        str(item.get("name") or "").strip(): dict(item)
        for item in launcher_core.list_private_model_entries()
        if str(item.get("name") or "").strip()
    }
    installed_names = set(launcher_core.list_private_models_from_store())
    try:
        installed_names.update(launcher_core.get_ollama_models())
    except Exception:
        pass
    installed_list = sorted(installed_names)

    merged: dict[str, dict[str, Any]] = {}
    for item in official_models:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        payload = dict(item)
        if name in installed_entries_by_name:
            installed_payload = installed_entries_by_name[name]
            for key in ("size", "details", "modified_at", "digest", "model"):
                if not payload.get(key) and installed_payload.get(key):
                    payload[key] = installed_payload.get(key)
        payload["name"] = name
        payload["installed"] = launcher_core.test_model_installed(name, installed_list)
        payload["is_embedding"] = is_embedding_model_entry(payload)
        merged[name] = payload

    for name in {launcher_core.DEFAULT_LLM_MODEL, launcher_core.DEFAULT_EMBED_MODEL, *installed_names}:
        if not name:
            continue
        fallback_entry: dict[str, Any] = {
            "name": name,
            "installed": launcher_core.test_model_installed(name, installed_list),
            "is_embedding": "embed" in name.lower() or "bert" in name.lower(),
            "details": dict((installed_entries_by_name.get(name) or {}).get("details") or {}),
            "size": (installed_entries_by_name.get(name) or {}).get("size") or 0,
        }
        if name in merged:
            merged[name]["installed"] = launcher_core.test_model_installed(name, installed_list)
            continue
        merged[name] = fallback_entry

    entries: list[dict[str, Any]] = []
    hidden_large_count = 0
    for item in merged.values():
        model_name = str(item.get("name") or "").strip()
        if not model_name:
            continue
        installed = bool(item.get("installed"))
        if model_exceeds_hide_limit(item) and not installed:
            hidden_large_count += 1
            continue
        size_text = format_model_size(item.get("size"))
        model_type = "embed" if bool(item.get("is_embedding")) else "chat"
        hint_parts = [model_type]
        if size_text != "-":
            hint_parts.append(size_text)
        entries.append(
            {
                "id": f"model:{model_name}",
                "kind": "model",
                "title": model_name,
                "installed": installed,
                "status_text": "已安装" if installed else "未安装",
                "hint": " | ".join(hint_parts),
                "model_name": model_name,
                "is_embedding": bool(item.get("is_embedding")),
            }
        )

    entries.sort(key=lambda item: (0 if item.get("installed") else 1, str(item.get("title") or "").lower()))
    return entries, source_text, hidden_large_count


def build_dependency_entries() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str, int]:
    runtime_entries = launcher_core.list_managed_dependencies()
    model_entries, model_source, hidden_large_count = build_model_entries()
    entries = [*runtime_entries, *model_entries]
    return entries, runtime_entries, model_entries, model_source, hidden_large_count


def print_help() -> None:
    print("commands:")
    print("  help              show this help")
    print("  refresh           reload runtime and model catalog")
    print("  1                 install dependency #1")
    print("  1,3               install dependencies #1 and #3")
    print("  rm 12             remove installed model #12")
    print("  quit              exit cli")


def print_model_section(title: str, entries: list[dict[str, Any]], start_index: int) -> int:
    print(f"[{title}]")
    if not entries:
        print("   no entries")
        print("")
        return start_index

    index = start_index
    for entry in entries:
        print(f"{index}. {entry.get('title')} ({entry.get('status_text')})")
        hint = str(entry.get("hint") or "").strip()
        if hint:
            print(f"   {hint}")
        index += 1
    print("")
    return index


def print_dependency_entries(
    runtime_entries: list[dict[str, Any]],
    model_entries: list[dict[str, Any]],
    model_source: str,
    hidden_large_count: int,
) -> None:
    print("")
    print("[runtime]")
    for index, entry in enumerate(runtime_entries, start=1):
        print(f"{index}. {entry.get('title')} ({entry.get('status_text')})")
        hint = str(entry.get("hint") or "").strip()
        if hint:
            print(f"   {hint}")

    print("")
    print(f"{model_source}")
    if hidden_large_count:
        print(f"hidden models > 10 GB: {hidden_large_count}")
    print("")

    chat_entries = [entry for entry in model_entries if not bool(entry.get("is_embedding"))]
    embed_entries = [entry for entry in model_entries if bool(entry.get("is_embedding"))]
    next_index = len(runtime_entries) + 1
    next_index = print_model_section("chat-models", chat_entries, next_index)
    print_model_section("embed-models", embed_entries, next_index)


def install_entries(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        title = str(entry.get("title") or entry.get("id") or "dependency")
        if bool(entry.get("installed")):
            print(f"skip: {title}")
            continue

        print(f"install: {title}")
        last_text = ""

        def progress_callback(event: dict[str, Any]) -> None:
            nonlocal last_text
            text = str(event.get("status") or "processing")
            completed = event.get("completed")
            total = event.get("total")
            if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
                percent = max(0.0, min(100.0, float(completed) / float(total) * 100.0))
                text = f"{text} ({percent:.1f}%)"
            if text != last_text:
                print(f"  {text}")
                last_text = text

        launcher_core.install_managed_dependency(str(entry.get("id") or ""), progress_callback=progress_callback)
        print(f"done: {title}")


def remove_model_entry(entry: dict[str, Any]) -> None:
    model_name = str(entry.get("model_name") or entry.get("title") or "").strip()
    if not model_name:
        raise ValueError("Invalid model entry.")
    if not bool(entry.get("installed")):
        print(f"skip: {model_name}")
        return
    print(f"remove: {model_name}")
    launcher_core.remove_model_with_progress(model_name)
    print(f"done: {model_name}")


def dependency_cli_main() -> int:
    print("Private Note dependency-cli")
    print("Type `help` for commands.")

    entries, runtime_entries, model_entries, model_source, hidden_large_count = build_dependency_entries()
    print_dependency_entries(runtime_entries, model_entries, model_source, hidden_large_count)

    while True:
        try:
            raw_value = input("dep> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return 0

        if not raw_value:
            continue

        command = raw_value.lower()
        if command in {"quit", "exit", "q"}:
            return 0
        if command in {"help", "?"}:
            print_help()
            continue
        if command in {"refresh", "r", "status", "list", "ls"}:
            entries, runtime_entries, model_entries, model_source, hidden_large_count = build_dependency_entries()
            print_dependency_entries(runtime_entries, model_entries, model_source, hidden_large_count)
            continue

        if command.startswith("rm ") or command.startswith("del ") or command.startswith("uninstall "):
            token = raw_value.split(maxsplit=1)[1].strip() if len(raw_value.split(maxsplit=1)) > 1 else ""
            if not token.isdigit():
                print(f"invalid command: {raw_value}")
                continue
            index = int(token)
            if index < 1 or index > len(entries):
                print(f"invalid command: {raw_value}")
                continue
            entry = dict(entries[index - 1])
            if str(entry.get("kind") or "") != "model":
                print("rm only supports model entries")
                continue
            remove_model_entry(entry)
            entries, runtime_entries, model_entries, model_source, hidden_large_count = build_dependency_entries()
            print_dependency_entries(runtime_entries, model_entries, model_source, hidden_large_count)
            continue

        normalized = raw_value.replace("，", ",")
        tokens = [token for token in normalized.split(",") if token.strip()]
        if len(tokens) == 1 and " " in tokens[0]:
            tokens = [token for token in tokens[0].split() if token.strip()]
        invalid_tokens = [token for token in tokens if not token.isdigit()]
        if invalid_tokens:
            print(f"invalid command: {', '.join(invalid_tokens)}")
            continue

        selected_indexes: list[int] = []
        out_of_range: list[str] = []
        for token in tokens:
            value = int(token)
            if value < 1 or value > len(entries):
                out_of_range.append(token)
                continue
            if value not in selected_indexes:
                selected_indexes.append(value)
        if out_of_range:
            print(f"invalid command: {', '.join(out_of_range)}")
            continue

        install_entries([dict(entries[index - 1]) for index in selected_indexes])
        entries, runtime_entries, model_entries, model_source, hidden_large_count = build_dependency_entries()
        print_dependency_entries(runtime_entries, model_entries, model_source, hidden_large_count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Private Note launcher control CLI")
    parser.add_argument("command", choices=["status", "shutdown-all", "dependency-cli"])
    args = parser.parse_args()

    if args.command == "status":
        payload = launcher_core.get_status()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "shutdown-all":
        launcher_core.force_shutdown_all()
        return 0

    if args.command == "dependency-cli":
        return dependency_cli_main()

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
