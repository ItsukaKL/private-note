from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
LOG_DIR = ROOT_DIR / "data" / "logs"
RUN_DIR = ROOT_DIR / "data" / "run"
APP_SETTINGS_PATH = ROOT_DIR / "data" / "app_settings.json"

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
LAUNCHER_HOST = os.getenv("LAUNCHER_HOST", "127.0.0.1")
LAUNCHER_PORT = int(os.getenv("LAUNCHER_PORT", "8010"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "qwen2:7b")
DEFAULT_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

APP_URL = f"http://{APP_HOST}:{APP_PORT}/"
APP_HEALTH_URL = f"http://{APP_HOST}:{APP_PORT}/healthz"
APP_MODEL_SETTINGS_URL = f"http://{APP_HOST}:{APP_PORT}/settings/model"

APP_PID_FILE = RUN_DIR / "app.pid"
LAUNCHER_PID_FILE = RUN_DIR / "launcher.pid"
APP_STDOUT_LOG = LOG_DIR / "app.stdout.log"
APP_STDERR_LOG = LOG_DIR / "app.stderr.log"
LAUNCHER_EVENT_LOG = LOG_DIR / "launcher.events.log"
PYTHON_EXE = ROOT_DIR / ".venv" / "Scripts" / "python.exe"

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

app = FastAPI(title="Private Note Launcher")


def _ensure_runtime_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def _append_event(message: str) -> None:
    _ensure_runtime_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LAUNCHER_EVENT_LOG.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")


def _tail_file(path: Path, lines: int = 40) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore") as file:
        return file.read().splitlines()[-lines:]


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="ascii").strip()
        return int(value) if value else None
    except (ValueError, OSError):
        return None


def _write_pid(path: Path, pid: int) -> None:
    _ensure_runtime_dirs()
    path.write_text(str(pid), encoding="ascii")


def _clear_pid(path: Path) -> None:
    if path.exists():
        path.unlink()


def _command_path(name: str) -> str | None:
    return shutil.which(name)


def _is_pid_running(pid: int | None) -> bool:
    if pid is None:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    output = result.stdout.strip()
    if not output or "No tasks are running" in output:
        return False
    return str(pid) in output


def _kill_process_tree(pid: int | None) -> bool:
    if pid is None:
        return False
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    return result.returncode == 0


def _is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _listening_pid_for_port(port: int) -> int | None:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if result.returncode != 0:
        return None
    port_suffix = f":{port}"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if "LISTENING" not in stripped:
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        local_address = parts[1]
        state = parts[3]
        pid = parts[4]
        if not local_address.endswith(port_suffix) or state != "LISTENING":
            continue
        try:
            return int(pid)
        except ValueError:
            return None
    return None


def _http_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def _wait_until(predicate, timeout_seconds: float, interval_seconds: float = 0.5) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_seconds)
    return False


def _run_command(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        check=False,
    )


def _test_model_installed(required_name: str, installed_names: list[str]) -> bool:
    lowered_required = required_name.lower()
    if ":" in lowered_required:
        return required_name in installed_names
    return any(item.lower().split(":")[0] == lowered_required for item in installed_names)


def _get_saved_llm_model() -> str:
    if not APP_SETTINGS_PATH.exists():
        return DEFAULT_LLM_MODEL
    try:
        with APP_SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_LLM_MODEL
    return data.get("llm_model") or DEFAULT_LLM_MODEL


def _ollama_available() -> bool:
    return _command_path("ollama") is not None


def _ollama_ready() -> bool:
    ollama_path = _command_path("ollama")
    if not ollama_path:
        return False
    result = _run_command([ollama_path, "list"], timeout=10)
    return result.returncode == 0


def _get_ollama_models() -> list[str]:
    ollama_path = _command_path("ollama")
    if not ollama_path:
        return []
    result = _run_command([ollama_path, "list"], timeout=10)
    if result.returncode != 0:
        return []
    models: list[str] = []
    for line in result.stdout.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("NAME "):
            continue
        models.append(trimmed.split()[0])
    return models


def _ensure_ollama_running() -> None:
    ollama_path = _command_path("ollama")
    if not ollama_path:
        raise RuntimeError("未检测到 ollama，可先安装 Ollama。")
    if _ollama_ready():
        return

    _append_event("检测到 Ollama 未运行，正在自动启动。")
    subprocess.Popen(
        [ollama_path, "serve"],
        cwd=ROOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )

    if not _wait_until(_ollama_ready, timeout_seconds=30):
        raise RuntimeError("Ollama 启动超时。")


def _get_current_model() -> str:
    if _http_ok(APP_MODEL_SETTINGS_URL, timeout=1.0):
        try:
            payload = _http_json(APP_MODEL_SETTINGS_URL, timeout=2.0)
            return payload.get("current_model") or _get_saved_llm_model()
        except Exception:
            return _get_saved_llm_model()
    return _get_saved_llm_model()


def _app_status_snapshot() -> dict[str, Any]:
    app_pid = _read_pid(APP_PID_FILE)
    managed_running = _is_pid_running(app_pid)
    app_port_open = _is_port_open(APP_PORT)
    app_healthy = _http_ok(APP_HEALTH_URL, timeout=1.5)

    if managed_running and app_healthy:
        status = "running"
    elif managed_running and app_port_open:
        status = "starting"
    elif managed_running:
        status = "unhealthy"
    elif app_healthy or app_port_open:
        status = "external"
    else:
        status = "stopped"

    if not managed_running and app_pid is not None:
        _clear_pid(APP_PID_FILE)
        app_pid = None

    return {
        "status": status,
        "managed": managed_running,
        "pid": app_pid,
        "port": APP_PORT,
        "port_open": app_port_open,
        "healthy": app_healthy,
        "url": APP_URL,
        "health_url": APP_HEALTH_URL,
        "current_model": _get_current_model(),
    }


def _status_payload() -> dict[str, Any]:
    uv_path = _command_path("uv")
    ollama_path = _command_path("ollama")
    models = _get_ollama_models()
    return {
        "launcher": {
            "status": "running",
            "pid": os.getpid(),
            "host": LAUNCHER_HOST,
            "port": LAUNCHER_PORT,
            "url": f"http://{LAUNCHER_HOST}:{LAUNCHER_PORT}/",
        },
        "environment": {
            "uv_available": uv_path is not None,
            "uv_path": uv_path,
            "python_ready": PYTHON_EXE.exists(),
            "python_path": str(PYTHON_EXE),
            "project_root": str(ROOT_DIR),
        },
        "app": _app_status_snapshot(),
        "ollama": {
            "installed": ollama_path is not None,
            "path": ollama_path,
            "running": _ollama_ready(),
            "base_url": OLLAMA_BASE_URL,
            "models": models,
            "default_llm_model": DEFAULT_LLM_MODEL,
            "default_embed_model": DEFAULT_EMBED_MODEL,
        },
        "logs": {
            "launcher_events": _tail_file(LAUNCHER_EVENT_LOG),
            "app_stdout": _tail_file(APP_STDOUT_LOG),
            "app_stderr": _tail_file(APP_STDERR_LOG),
        },
    }


def _assert_can_start_app() -> None:
    if not PYTHON_EXE.exists():
        raise RuntimeError(f"未找到虚拟环境 Python: {PYTHON_EXE}")

    uv_path = _command_path("uv")
    if not uv_path:
        raise RuntimeError("未检测到 uv，无法同步依赖。")

    _append_event("正在同步 Python 依赖。")
    sync_result = _run_command([uv_path, "sync"], timeout=300)
    if sync_result.returncode != 0:
        message = sync_result.stderr.strip() or sync_result.stdout.strip() or "uv sync failed"
        raise RuntimeError(f"依赖同步失败：{message}")

    _ensure_ollama_running()
    installed_models = _get_ollama_models()
    if not _test_model_installed(DEFAULT_EMBED_MODEL, installed_models):
        raise RuntimeError(f"缺少 embedding 模型：{DEFAULT_EMBED_MODEL}")


def _start_app() -> None:
    current_status = _app_status_snapshot()
    if current_status["status"] in {"running", "starting"}:
        return
    if current_status["status"] == "external":
        raise RuntimeError("检测到 8000 端口已有非启动器管理的实例，无法直接接管。")

    _assert_can_start_app()

    stdout_file = APP_STDOUT_LOG.open("w", encoding="utf-8")
    stderr_file = APP_STDERR_LOG.open("w", encoding="utf-8")
    try:
        env = os.environ.copy()
        env["APP_HOST"] = APP_HOST
        env["APP_PORT"] = str(APP_PORT)
        env["OLLAMA_BASE_URL"] = OLLAMA_BASE_URL
        env["OLLAMA_LLM_MODEL"] = DEFAULT_LLM_MODEL
        env["OLLAMA_EMBED_MODEL"] = DEFAULT_EMBED_MODEL

        process = subprocess.Popen(
            [str(PYTHON_EXE), "serve_app.py"],
            cwd=ROOT_DIR,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
        _write_pid(APP_PID_FILE, process.pid)
    finally:
        stdout_file.close()
        stderr_file.close()

    _append_event(f"主应用启动中，根进程 PID={process.pid}。")

    if not _wait_until(lambda: _is_port_open(APP_PORT), timeout_seconds=60):
        _kill_process_tree(process.pid)
        _clear_pid(APP_PID_FILE)
        raise RuntimeError("主应用启动超时，端口未就绪。")

    listener_pid = _listening_pid_for_port(APP_PORT)
    if listener_pid is not None:
        _write_pid(APP_PID_FILE, listener_pid)
        _append_event(f"主应用监听进程已锁定，PID={listener_pid}。")

    _append_event("主应用端口已就绪。")


def _stop_app() -> None:
    current_status = _app_status_snapshot()
    pid = _read_pid(APP_PID_FILE)
    if pid is None and current_status["status"] != "external":
        pid = _listening_pid_for_port(APP_PORT)

    if pid is None:
        _append_event("收到停止请求，但没有发现启动器管理的主应用进程。")
        return

    killed = _kill_process_tree(pid)
    _clear_pid(APP_PID_FILE)

    if killed:
        _append_event(f"主应用已停止，根进程 PID={pid}。")
    else:
        _append_event(f"主应用 PID={pid} 已不存在或停止失败。")

    _wait_until(lambda: not _is_port_open(APP_PORT), timeout_seconds=15)


@app.on_event("startup")
def startup() -> None:
    _ensure_runtime_dirs()
    _write_pid(LAUNCHER_PID_FILE, os.getpid())
    _append_event("启动器已启动。")


@app.get("/")
def launcher_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "launcher.html")


@app.get("/api/status")
def launcher_status() -> dict[str, Any]:
    return _status_payload()


@app.post("/api/app/start")
def start_app() -> dict[str, Any]:
    try:
        _start_app()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status_payload()


@app.post("/api/app/stop")
def stop_app() -> dict[str, Any]:
    _stop_app()
    return _status_payload()


@app.post("/api/app/restart")
def restart_app() -> dict[str, Any]:
    _stop_app()
    try:
        _start_app()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status_payload()
