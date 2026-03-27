from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
RUN_DIR = DATA_DIR / "run"
RUNTIME_DIR = ROOT_DIR / "runtime"
DOWNLOAD_DIR = DATA_DIR / "downloads"
APP_SETTINGS_PATH = DATA_DIR / "app_settings.json"
LEGACY_APP_PID_FILE = RUN_DIR / "app.pid"
LAUNCHER_PID_FILE = RUN_DIR / "launcher.pid"
PRIVATE_OLLAMA_PID_FILE = RUN_DIR / "ollama.pid"

PRIVATE_OLLAMA_DIR = RUNTIME_DIR / "ollama"
PRIVATE_OLLAMA_EXE = PRIVATE_OLLAMA_DIR / "ollama.exe"
PRIVATE_OLLAMA_MODELS_DIR = DATA_DIR / "ollama-models"
PRIVATE_OLLAMA_HOST = "127.0.0.1"
PRIVATE_OLLAMA_PORT = int(os.getenv("PRIVATE_OLLAMA_PORT", "11435"))
PRIVATE_OLLAMA_BASE_URL = f"http://{PRIVATE_OLLAMA_HOST}:{PRIVATE_OLLAMA_PORT}"
PRIVATE_OLLAMA_DOWNLOAD_URLS = [
    os.getenv("OLLAMA_PRIVATE_DOWNLOAD_URL", "").strip(),
    "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip",
]
SYSTEM_OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

DEFAULT_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "qwen2:7b")
DEFAULT_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

LAUNCHER_EVENT_LOG = LOG_DIR / "launcher.events.log"
DESKTOP_STDOUT_LOG = LOG_DIR / "launcher.desktop.stdout.log"
DESKTOP_STDERR_LOG = LOG_DIR / "launcher.desktop.stderr.log"
OLLAMA_STDOUT_LOG = LOG_DIR / "ollama.stdout.log"
OLLAMA_STDERR_LOG = LOG_DIR / "ollama.stderr.log"
PYTHON_EXE = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
PYTHONW_EXE = ROOT_DIR / ".venv" / "Scripts" / "pythonw.exe"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

LEGACY_APP_PROCESS_PATTERNS = ("serve_app.py", "main:app")
LAUNCHER_PROCESS_PATTERNS = ("launcher_desktop.py", "serve_launcher.py", "launcher_app:app")


def ensure_runtime_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)


def append_event(message: str) -> None:
    ensure_runtime_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LAUNCHER_EVENT_LOG.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")


def tail_file(path: Path, lines: int = 60) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore") as file:
        return file.read().splitlines()[-lines:]


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="ascii").strip()
        return int(value) if value else None
    except (ValueError, OSError):
        return None


def write_pid(path: Path, pid: int) -> None:
    ensure_runtime_dirs()
    path.write_text(str(pid), encoding="ascii")


def clear_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def register_launcher_pid(pid: int) -> None:
    write_pid(LAUNCHER_PID_FILE, pid)


def clear_launcher_pid() -> None:
    clear_pid(LAUNCHER_PID_FILE)


def command_path(name: str) -> str | None:
    return shutil.which(name)


def run_command(
    args: list[str],
    timeout: int = 300,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd or ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        check=False,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )


def is_pid_running(pid: int | None) -> bool:
    if pid is None:
        return False
    result = run_command(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10)
    output = result.stdout.strip()
    if not output or "No tasks are running" in output:
        return False
    return str(pid) in output


def kill_process_tree(pid: int | None) -> bool:
    if pid is None:
        return False
    result = run_command(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=30)
    return result.returncode == 0


def wait_until(predicate, timeout_seconds: float, interval_seconds: float = 0.5) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_seconds)
    return False


def find_process_ids_by_patterns(patterns: tuple[str, ...]) -> list[int]:
    if not PYTHON_EXE.exists():
        return []
    result = run_command(
        [
            str(PYTHON_EXE),
            "-c",
            (
                "import json, os, psutil, sys;"
                "patterns = json.loads(sys.argv[1]);"
                "current = os.getpid();"
                "matched = [];"
                "for proc in psutil.process_iter(['pid', 'cmdline']):"
                "    try:"
                "        if proc.info['pid'] == current:"
                "            continue;"
                "        cmd = ' '.join(proc.info.get('cmdline') or []);"
                "        if any(p in cmd for p in patterns):"
                "            matched.append(proc.info['pid']);"
                "    except Exception:"
                "        pass;"
                "print(json.dumps(matched))"
            ),
            json.dumps(list(patterns)),
        ],
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [int(item) for item in values if isinstance(item, int)]


def stop_process_ids(process_ids: list[int]) -> None:
    for process_id in sorted(set(process_ids), reverse=True):
        try:
            kill_process_tree(process_id)
        except Exception:
            pass


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def get_saved_llm_model() -> str:
    if not APP_SETTINGS_PATH.exists():
        return DEFAULT_LLM_MODEL
    try:
        with APP_SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_LLM_MODEL
    return data.get("llm_model") or DEFAULT_LLM_MODEL


def _private_ollama_env() -> dict[str, str]:
    ensure_runtime_dirs()
    env = os.environ.copy()
    env["OLLAMA_HOST"] = f"{PRIVATE_OLLAMA_HOST}:{PRIVATE_OLLAMA_PORT}"
    env["OLLAMA_MODELS"] = str(PRIVATE_OLLAMA_MODELS_DIR)
    return env


def private_ollama_installed() -> bool:
    return PRIVATE_OLLAMA_EXE.exists()


def system_ollama_path() -> str | None:
    return command_path("ollama")


def get_ollama_mode() -> str:
    if private_ollama_installed():
        return "private"
    if system_ollama_path():
        return "system"
    return "missing"


def get_active_ollama_executable() -> str | None:
    mode = get_ollama_mode()
    if mode == "private":
        return str(PRIVATE_OLLAMA_EXE)
    if mode == "system":
        return system_ollama_path()
    return None


def get_ollama_base_url() -> str:
    return PRIVATE_OLLAMA_BASE_URL if get_ollama_mode() == "private" else SYSTEM_OLLAMA_BASE_URL


def _run_ollama_list(mode: str) -> subprocess.CompletedProcess[str]:
    if mode == "private":
        return run_command([str(PRIVATE_OLLAMA_EXE), "list"], timeout=10, env=_private_ollama_env(), cwd=PRIVATE_OLLAMA_DIR)
    path = system_ollama_path()
    if not path:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="ollama not found")
    return run_command([path, "list"], timeout=10)


def private_ollama_ready() -> bool:
    if not private_ollama_installed():
        return False
    return _run_ollama_list("private").returncode == 0


def ollama_ready() -> bool:
    mode = get_ollama_mode()
    if mode == "missing":
        return False
    return _run_ollama_list(mode).returncode == 0


def get_ollama_models() -> list[str]:
    mode = get_ollama_mode()
    if mode == "missing":
        return []
    result = _run_ollama_list(mode)
    if result.returncode != 0:
        return []
    models: list[str] = []
    for line in result.stdout.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("NAME "):
            continue
        models.append(trimmed.split()[0])
    return models


def test_model_installed(required_name: str, installed_names: list[str]) -> bool:
    lowered_required = required_name.lower()
    if ":" in lowered_required:
        return required_name in installed_names
    return any(item.lower().split(":")[0] == lowered_required for item in installed_names)


def _download_file(url: str, target_path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "PrivateNoteDesktop/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, target_path.open("wb") as file:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file.write(chunk)


def install_private_ollama_runtime() -> dict[str, Any]:
    ensure_runtime_dirs()
    append_event("开始安装项目私有 Ollama 运行时。")

    if private_ollama_ready():
        stop_private_ollama()

    if PRIVATE_OLLAMA_DIR.exists():
        shutil.rmtree(PRIVATE_OLLAMA_DIR)

    with tempfile.TemporaryDirectory(prefix="private-note-ollama-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        archive_path = temp_dir / "ollama-windows-amd64.zip"
        last_error: Exception | None = None

        for url in [item for item in PRIVATE_OLLAMA_DOWNLOAD_URLS if item]:
            try:
                append_event(f"尝试下载 Ollama 运行时：{url}")
                _download_file(url, archive_path)
                last_error = None
                break
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise RuntimeError(f"下载 Ollama 运行时失败：{last_error}") from last_error

        extract_root = temp_dir / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(extract_root)

        exe_path: Path | None = None
        for candidate in extract_root.rglob("ollama.exe"):
            exe_path = candidate
            break
        if exe_path is None:
            raise RuntimeError("下载的 Ollama 压缩包中未找到 ollama.exe。")

        shutil.copytree(exe_path.parent, PRIVATE_OLLAMA_DIR)

    PRIVATE_OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    append_event("项目私有 Ollama 运行时安装完成。")
    return get_status()


def start_private_ollama() -> dict[str, Any]:
    ensure_runtime_dirs()
    if not private_ollama_installed():
        raise RuntimeError("项目内尚未安装 Ollama 运行时。")
    if private_ollama_ready():
        return get_status()

    stdout_file = OLLAMA_STDOUT_LOG.open("w", encoding="utf-8")
    stderr_file = OLLAMA_STDERR_LOG.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [str(PRIVATE_OLLAMA_EXE), "serve"],
            cwd=PRIVATE_OLLAMA_DIR,
            env=_private_ollama_env(),
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
        write_pid(PRIVATE_OLLAMA_PID_FILE, process.pid)
    finally:
        stdout_file.close()
        stderr_file.close()

    append_event(f"项目私有 Ollama 启动中，PID={process.pid}。")
    if not wait_until(private_ollama_ready, timeout_seconds=30):
        kill_process_tree(process.pid)
        clear_pid(PRIVATE_OLLAMA_PID_FILE)
        raise RuntimeError("项目私有 Ollama 启动超时。")
    append_event("项目私有 Ollama 已就绪。")
    return get_status()


def stop_private_ollama() -> dict[str, Any]:
    pid = read_pid(PRIVATE_OLLAMA_PID_FILE)
    if pid is not None:
        kill_process_tree(pid)
    clear_pid(PRIVATE_OLLAMA_PID_FILE)

    stop_process_ids(find_process_ids_by_patterns((str(PRIVATE_OLLAMA_EXE), f"{PRIVATE_OLLAMA_HOST}:{PRIVATE_OLLAMA_PORT}")))
    wait_until(lambda: not private_ollama_ready(), timeout_seconds=10)
    append_event("项目私有 Ollama 已停止。")
    return get_status()


def uninstall_private_ollama_runtime(*, remove_models: bool = False) -> dict[str, Any]:
    stop_private_ollama()
    if PRIVATE_OLLAMA_DIR.exists():
        shutil.rmtree(PRIVATE_OLLAMA_DIR)
    if remove_models and PRIVATE_OLLAMA_MODELS_DIR.exists():
        shutil.rmtree(PRIVATE_OLLAMA_MODELS_DIR)
        PRIVATE_OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    append_event("项目私有 Ollama 运行时已卸载。")
    return get_status()


def ensure_ollama_running() -> None:
    mode = get_ollama_mode()
    if mode == "private":
        start_private_ollama()
        return

    ollama_path = system_ollama_path()
    if not ollama_path:
        raise RuntimeError("未检测到 Ollama。请先在设置页安装项目私有运行时，或安装系统 Ollama。")
    if ollama_ready():
        return

    append_event("检测到系统 Ollama 未运行，正在自动启动。")
    subprocess.Popen(
        [ollama_path, "serve"],
        cwd=ROOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )
    if not wait_until(ollama_ready, timeout_seconds=30):
        raise RuntimeError("Ollama 启动超时。")


def pull_model(model_name: str) -> dict[str, Any]:
    ensure_ollama_running()
    executable = get_active_ollama_executable()
    if not executable:
        raise RuntimeError("未找到可用的 Ollama 可执行文件。")
    env = _private_ollama_env() if get_ollama_mode() == "private" else None
    cwd = PRIVATE_OLLAMA_DIR if get_ollama_mode() == "private" else ROOT_DIR
    append_event(f"开始拉取模型：{model_name}")
    result = run_command([executable, "pull", model_name], timeout=3600, env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"拉取模型 {model_name} 失败。")
    append_event(f"模型拉取完成：{model_name}")
    return get_status()


def remove_model(model_name: str) -> dict[str, Any]:
    executable = get_active_ollama_executable()
    if not executable:
        raise RuntimeError("未找到可用的 Ollama 可执行文件。")
    env = _private_ollama_env() if get_ollama_mode() == "private" else None
    cwd = PRIVATE_OLLAMA_DIR if get_ollama_mode() == "private" else ROOT_DIR
    append_event(f"开始删除模型：{model_name}")
    result = run_command([executable, "rm", model_name], timeout=600, env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"删除模型 {model_name} 失败。")
    append_event(f"模型删除完成：{model_name}")
    return get_status()


def shutdown_project_background() -> None:
    if private_ollama_installed():
        stop_private_ollama()
    stop_process_ids(find_process_ids_by_patterns(LEGACY_APP_PROCESS_PATTERNS))
    clear_pid(LEGACY_APP_PID_FILE)


def get_status(launcher_pid: int | None = None) -> dict[str, Any]:
    mode = get_ollama_mode()
    active_path = get_active_ollama_executable()
    payload: dict[str, Any] = {
        "environment": {
            "uv_available": command_path("uv") is not None,
            "uv_path": command_path("uv"),
            "python_ready": PYTHON_EXE.exists(),
            "python_path": str(PYTHON_EXE),
            "project_root": str(ROOT_DIR),
            "runtime_dir": str(RUNTIME_DIR),
            "data_dir": str(DATA_DIR),
            "log_dir": str(LOG_DIR),
        },
        "ollama": {
            "mode": mode,
            "managed": mode == "private",
            "installed": active_path is not None,
            "path": active_path,
            "private_installed": private_ollama_installed(),
            "private_path": str(PRIVATE_OLLAMA_EXE),
            "private_models_dir": str(PRIVATE_OLLAMA_MODELS_DIR),
            "download_urls": [item for item in PRIVATE_OLLAMA_DOWNLOAD_URLS if item],
            "running": ollama_ready(),
            "base_url": get_ollama_base_url(),
            "models": get_ollama_models(),
            "default_llm_model": DEFAULT_LLM_MODEL,
            "default_embed_model": DEFAULT_EMBED_MODEL,
        },
        "logs": {
            "launcher_events": tail_file(LAUNCHER_EVENT_LOG),
            "launcher_stdout": tail_file(DESKTOP_STDOUT_LOG),
            "launcher_stderr": tail_file(DESKTOP_STDERR_LOG),
            "ollama_stdout": tail_file(OLLAMA_STDOUT_LOG),
            "ollama_stderr": tail_file(OLLAMA_STDERR_LOG),
        },
        "legacy_web": {
            "present": LEGACY_APP_PID_FILE.exists() or bool(find_process_ids_by_patterns(LEGACY_APP_PROCESS_PATTERNS)),
        },
    }
    if launcher_pid is not None:
        payload["launcher"] = {
            "status": "running" if is_pid_running(launcher_pid) else "stopped",
            "pid": launcher_pid,
        }
    return payload


def force_shutdown_all() -> None:
    launcher_pid = read_pid(LAUNCHER_PID_FILE)
    if launcher_pid is not None:
        kill_process_tree(launcher_pid)

    if private_ollama_installed():
        stop_private_ollama()

    stop_process_ids(find_process_ids_by_patterns(LAUNCHER_PROCESS_PATTERNS))
    stop_process_ids(find_process_ids_by_patterns(LEGACY_APP_PROCESS_PATTERNS))
    clear_pid(LEGACY_APP_PID_FILE)
    clear_pid(LAUNCHER_PID_FILE)
