from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from project_paths import APP_SETTINGS_PATH, DATA_DIR, LOG_DIR, PRIVATE_OLLAMA_MODELS_DIR, ROOT_DIR, RUNTIME_DIR, RUN_DIR, VENDOR_DIR

LEGACY_APP_PID_FILE = RUN_DIR / "app.pid"
LAUNCHER_PID_FILE = RUN_DIR / "launcher.pid"
PRIVATE_OLLAMA_PID_FILE = RUN_DIR / "ollama.pid"

PRIVATE_OLLAMA_DIR = RUNTIME_DIR / "ollama"
PRIVATE_OLLAMA_EXE = PRIVATE_OLLAMA_DIR / "ollama.exe"
SYSTEM_OLLAMA_MODELS_DIR = Path(os.getenv("OLLAMA_SYSTEM_MODELS_DIR", str(Path.home() / ".ollama" / "models")))
PRIVATE_OLLAMA_HOST = "127.0.0.1"
PRIVATE_OLLAMA_PORT = int(os.getenv("PRIVATE_OLLAMA_PORT", "11435"))
PRIVATE_OLLAMA_BASE_URL = f"http://{PRIVATE_OLLAMA_HOST}:{PRIVATE_OLLAMA_PORT}"
SYSTEM_OLLAMA_HOST = "127.0.0.1"
SYSTEM_OLLAMA_PORT = int(os.getenv("SYSTEM_OLLAMA_PORT", "11434"))
SYSTEM_OLLAMA_BASE_URL = f"http://{SYSTEM_OLLAMA_HOST}:{SYSTEM_OLLAMA_PORT}"
OLLAMA_LIBRARY_BASE_URL = os.getenv("OLLAMA_LIBRARY_BASE_URL", "https://ollama.com")
SYSTEM_OLLAMA_APP_PATH = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama app.exe"
OLLAMA_RUNTIME_VERSION = "0.20.2"
OLLAMA_RUNTIME_RELEASE_BASE_URL = os.getenv(
    "OLLAMA_RUNTIME_RELEASE_BASE_URL",
    f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_RUNTIME_VERSION}",
)
DEFAULT_RUNTIME_PROFILE = "cpu"
RUNTIME_PROFILE_LABELS = {
    "cpu": "CPU 模式",
    "gpu": "GPU 模式 (NVIDIA)",
}
RUNTIME_PROFILE_SHORT_LABELS = {
    "cpu": "CPU",
    "gpu": "GPU",
}
RUNTIME_PROFILE_OPTIONS = tuple((key, RUNTIME_PROFILE_LABELS[key]) for key in ("cpu", "gpu"))
CPU_OLLAMA_REQUIRED_FILES = (
    Path("ollama.exe"),
    Path("lib/ollama/ggml-base.dll"),
    Path("lib/ollama/ggml-cpu-alderlake.dll"),
    Path("lib/ollama/ggml-cpu-haswell.dll"),
    Path("lib/ollama/ggml-cpu-icelake.dll"),
    Path("lib/ollama/ggml-cpu-sandybridge.dll"),
    Path("lib/ollama/ggml-cpu-skylakex.dll"),
    Path("lib/ollama/ggml-cpu-sse42.dll"),
    Path("lib/ollama/ggml-cpu-x64.dll"),
)
GPU_OLLAMA_REQUIRED_FILES = CPU_OLLAMA_REQUIRED_FILES + (
    Path("lib/ollama/cuda_v12/ggml-cuda.dll"),
    Path("lib/ollama/cuda_v13/ggml-cuda.dll"),
    Path("lib/ollama/mlx_cuda_v13/mlx.dll"),
    Path("lib/ollama/vulkan/ggml-vulkan.dll"),
)
GPU_ACCELERATION_REQUIRED_FILES = tuple(path for path in GPU_OLLAMA_REQUIRED_FILES if path not in CPU_OLLAMA_REQUIRED_FILES)
GPU_ACCELERATION_RELATIVE_PATHS = (
    Path("lib/ollama/cuda_v12"),
    Path("lib/ollama/cuda_v13"),
    Path("lib/ollama/mlx_cuda_v13"),
    Path("lib/ollama/vulkan"),
)
RUNTIME_PROFILES = {
    "cpu": {
        "label": RUNTIME_PROFILE_LABELS["cpu"],
        "short_label": RUNTIME_PROFILE_SHORT_LABELS["cpu"],
        "bundle_dirname": f"ollama-windows-amd64-cpu-{OLLAMA_RUNTIME_VERSION}",
        "required_files": CPU_OLLAMA_REQUIRED_FILES,
        "runtime_dir": RUNTIME_DIR / "ollama-cpu",
        "accelerator": "cpu",
        "download_assets": (
            {
                "filename": "ollama-windows-amd64.zip",
                "label": "Ollama Windows 运行时",
            },
        ),
    },
    "gpu": {
        "label": RUNTIME_PROFILE_LABELS["gpu"],
        "short_label": RUNTIME_PROFILE_SHORT_LABELS["gpu"],
        "bundle_dirname": f"ollama-windows-amd64-gpu-nvidia-{OLLAMA_RUNTIME_VERSION}",
        "required_files": GPU_OLLAMA_REQUIRED_FILES,
        "runtime_dir": RUNTIME_DIR / "ollama-gpu",
        "accelerator": "nvidia",
        "download_assets": (
            {
                "filename": "ollama-windows-amd64.zip",
                "label": "Ollama Windows 运行时",
            },
            {
                "filename": "ollama-windows-amd64-mlx.zip",
                "label": "Ollama CUDA 扩展",
            },
        ),
    },
}
VENDORED_PYTHON_VERSION = "3.11.7"
VENDORED_PYTHON_DIRNAME = f"python-{VENDORED_PYTHON_VERSION}-embed-amd64"
VENDORED_PYTHON_DIR_CANDIDATES = (
    VENDOR_DIR / VENDORED_PYTHON_DIRNAME,
    ROOT_DIR / "_internal" / "vendor" / VENDORED_PYTHON_DIRNAME,
    Path(__file__).resolve().parent / "vendor" / VENDORED_PYTHON_DIRNAME,
)
VENDORED_PYTHON_REQUIRED_FILES = (
    Path("python.exe"),
    Path("pythonw.exe"),
    Path("python311.dll"),
    Path("python311.zip"),
    Path("python311._pth"),
    Path("DLLs/_tkinter.pyd"),
    Path("Library/bin/tcl86t.dll"),
    Path("Library/bin/tk86t.dll"),
    Path("Library/lib/tcl8.6/init.tcl"),
    Path("Library/lib/tk8.6/tk.tcl"),
    Path("Lib/site-packages/chromadb/__init__.py"),
    Path("Lib/site-packages/psutil/__init__.py"),
    Path("Lib/site-packages/PyInstaller/__init__.py"),
)


def _resolve_first_existing_dir(candidates: tuple[Path, ...], required_files: tuple[Path, ...]) -> Path | None:
    for candidate in candidates:
        if all((candidate / relative_path).exists() for relative_path in required_files):
            return candidate
    return None


VENDORED_PYTHON_DIR = _resolve_first_existing_dir(VENDORED_PYTHON_DIR_CANDIDATES, VENDORED_PYTHON_REQUIRED_FILES)

DEFAULT_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:7b")
DEFAULT_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
RECOMMENDED_CHAT_MODELS = ("qwen2.5:3b", "qwen2.5:7b")
RECOMMENDED_MODEL_NAMES = (*RECOMMENDED_CHAT_MODELS, DEFAULT_EMBED_MODEL)

LAUNCHER_EVENT_LOG = LOG_DIR / "launcher.events.log"
DESKTOP_STDOUT_LOG = LOG_DIR / "launcher.desktop.stdout.log"
DESKTOP_STDERR_LOG = LOG_DIR / "launcher.desktop.stderr.log"
OLLAMA_STDOUT_LOG = LOG_DIR / "ollama.stdout.log"
OLLAMA_STDERR_LOG = LOG_DIR / "ollama.stderr.log"
PYTHON_EXE = (VENDORED_PYTHON_DIR / "python.exe") if VENDORED_PYTHON_DIR is not None else VENDOR_DIR / VENDORED_PYTHON_DIRNAME / "python.exe"
PYTHONW_EXE = (VENDORED_PYTHON_DIR / "pythonw.exe") if VENDORED_PYTHON_DIR is not None else VENDOR_DIR / VENDORED_PYTHON_DIRNAME / "pythonw.exe"
ENVIRONMENT_CACHE_TTL_SECONDS = 60.0
_environment_snapshot_cache: dict[str, Any] | None = None
_environment_snapshot_timestamp = 0.0

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

LEGACY_APP_PROCESS_PATTERNS = ("serve_app.py", "main:app")
LAUNCHER_PROCESS_PATTERNS = ("launcher_desktop.py", "serve_launcher.py", "launcher_app:app")


def ensure_runtime_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)


def append_event(message: str) -> None:
    ensure_runtime_dirs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LAUNCHER_EVENT_LOG.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _emit_progress(progress_callback, payload: dict[str, Any]) -> None:
    if callable(progress_callback):
        progress_callback(payload)


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


def vendored_python_available() -> bool:
    return VENDORED_PYTHON_DIR is not None and PYTHON_EXE.exists()


def vendored_python_env() -> dict[str, str]:
    env = os.environ.copy()
    if VENDORED_PYTHON_DIR is None:
        return env

    dll_dir = VENDORED_PYTHON_DIR / "Library" / "bin"
    tcl_dir = VENDORED_PYTHON_DIR / "Library" / "lib" / "tcl8.6"
    tk_dir = VENDORED_PYTHON_DIR / "Library" / "lib" / "tk8.6"
    path_parts = [str(VENDORED_PYTHON_DIR)]
    if dll_dir.exists():
        path_parts.append(str(dll_dir))
    current_path = env.get("PATH", "")
    if current_path:
        path_parts.append(current_path)
    env["PATH"] = os.pathsep.join(path_parts)
    if tcl_dir.exists():
        env["TCL_LIBRARY"] = str(tcl_dir)
    if tk_dir.exists():
        env["TK_LIBRARY"] = str(tk_dir)
    env["PYTHONHOME"] = str(VENDORED_PYTHON_DIR)
    return env


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


def detect_nvidia_gpus() -> list[str]:
    executable = command_path("nvidia-smi")
    if not executable:
        return []
    result = run_command([executable, "--query-gpu=name", "--format=csv,noheader"], timeout=15)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def detect_cpu_name() -> str:
    commands = (
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name).Trim()",
        ],
        ["wmic", "cpu", "get", "name"],
    )
    for args in commands:
        executable = command_path(args[0])
        if not executable:
            continue
        result = run_command([executable, *args[1:]], timeout=15)
        if result.returncode != 0:
            continue
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for line in lines:
            if line.lower() == "name":
                continue
            if line:
                return line
    fallback = str(os.environ.get("PROCESSOR_IDENTIFIER") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").strip()
    return fallback or "未知 CPU"


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
    current = os.getpid()
    matched: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["pid"] == current:
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
            if any(pattern in cmd for pattern in patterns):
                matched.append(int(proc.info["pid"]))
        except Exception:
            continue
    return matched


def stop_process_ids(process_ids: list[int]) -> None:
    for process_id in sorted(set(process_ids), reverse=True):
        try:
            kill_process_tree(process_id)
        except Exception:
            pass


def get_saved_llm_model() -> str:
    if not APP_SETTINGS_PATH.exists():
        return DEFAULT_LLM_MODEL
    try:
        with APP_SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_LLM_MODEL
    return data.get("llm_model") or DEFAULT_LLM_MODEL


def get_saved_embed_model() -> str:
    if not APP_SETTINGS_PATH.exists():
        return DEFAULT_EMBED_MODEL
    try:
        with APP_SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_EMBED_MODEL
    return data.get("embed_model") or DEFAULT_EMBED_MODEL


def _read_app_settings() -> dict[str, Any]:
    if not APP_SETTINGS_PATH.exists():
        return {}
    try:
        with APP_SETTINGS_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_app_settings(patch: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    payload = _read_app_settings()
    payload.update(patch)
    atomic_write_json(APP_SETTINGS_PATH, payload)


def normalize_runtime_profile(profile: str | None) -> str:
    normalized = str(profile or "").strip().lower()
    if normalized in RUNTIME_PROFILES:
        return normalized
    return DEFAULT_RUNTIME_PROFILE


def get_saved_runtime_profile() -> str:
    return normalize_runtime_profile(_read_app_settings().get("runtime_profile"))


def set_saved_runtime_profile(profile: str) -> str:
    normalized = normalize_runtime_profile(profile)
    _write_app_settings({"runtime_profile": normalized})
    return normalized


def get_runtime_profile_config(profile: str | None = None) -> dict[str, Any]:
    normalized = normalize_runtime_profile(profile if profile is not None else get_saved_runtime_profile())
    config = dict(RUNTIME_PROFILES[normalized])
    config["key"] = normalized
    return config


def _model_store_has_manifests(root: Path) -> bool:
    manifests_dir = root / "manifests"
    if not manifests_dir.exists():
        return False
    try:
        next(manifests_dir.rglob("*"))
    except StopIteration:
        return False
    return True


def _link_or_copy_file(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return "skipped"
    try:
        os.link(source, target)
        return "linked"
    except OSError:
        shutil.copy2(source, target)
        return "copied"


def import_system_models_into_private_store(*, force: bool = False) -> dict[str, int | bool | str]:
    source_root = SYSTEM_OLLAMA_MODELS_DIR
    target_root = PRIVATE_OLLAMA_MODELS_DIR
    if not _model_store_has_manifests(source_root):
        return {
            "imported": False,
            "linked": 0,
            "copied": 0,
            "manifests": 0,
            "source": str(source_root),
        }

    linked = 0
    copied = 0
    manifests = 0
    source_manifests = source_root / "manifests"
    source_blobs = source_root / "blobs"
    target_manifests = target_root / "manifests"
    target_blobs = target_root / "blobs"
    target_manifests.mkdir(parents=True, exist_ok=True)
    target_blobs.mkdir(parents=True, exist_ok=True)

    for manifest_path in sorted(path for path in source_manifests.rglob("*") if path.is_file()):
        relative = manifest_path.relative_to(source_manifests)
        target_path = target_manifests / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if force or not target_path.exists():
            shutil.copy2(manifest_path, target_path)
            manifests += 1

    for blob_path in sorted(path for path in source_blobs.rglob("*") if path.is_file()):
        relative = blob_path.relative_to(source_blobs)
        result = _link_or_copy_file(blob_path, target_blobs / relative)
        if result == "linked":
            linked += 1
        elif result == "copied":
            copied += 1

    imported = manifests > 0 or linked > 0 or copied > 0
    if imported:
        append_event(
            f"已将系统模型库导入项目私有模型目录：manifests={manifests}, linked={linked}, copied={copied}, source={source_root}"
        )
    return {
        "imported": imported,
        "linked": linked,
        "copied": copied,
        "manifests": manifests,
        "source": str(source_root),
    }


def _collect_environment_snapshot(*, force_refresh: bool = False) -> dict[str, Any]:
    global _environment_snapshot_cache, _environment_snapshot_timestamp
    now = time.monotonic()
    if (
        not force_refresh
        and _environment_snapshot_cache is not None
        and now - _environment_snapshot_timestamp < ENVIRONMENT_CACHE_TTL_SECONDS
    ):
        return dict(_environment_snapshot_cache)

    snapshot = {
        "uv_path": command_path("uv"),
        "cpu_name": detect_cpu_name(),
        "nvidia_smi": command_path("nvidia-smi"),
        "nvidia_gpu_names": detect_nvidia_gpus(),
    }
    _environment_snapshot_cache = dict(snapshot)
    _environment_snapshot_timestamp = now
    return snapshot


def _private_ollama_env() -> dict[str, str]:
    ensure_runtime_dirs()
    env = os.environ.copy()
    env["OLLAMA_HOST"] = f"{PRIVATE_OLLAMA_HOST}:{PRIVATE_OLLAMA_PORT}"
    env["OLLAMA_MODELS"] = str(PRIVATE_OLLAMA_MODELS_DIR)
    return env


def runtime_profile_label(profile: str | None) -> str:
    return RUNTIME_PROFILE_LABELS[normalize_runtime_profile(profile)]


def get_private_ollama_dir(profile: str | None = None) -> Path:
    return Path(get_runtime_profile_config(profile)["runtime_dir"])


def get_private_ollama_exe(profile: str | None = None) -> Path:
    return get_private_ollama_dir(profile) / "ollama.exe"


def _runtime_has_required_files(root: Path, required_files: tuple[Path, ...]) -> bool:
    return all((root / relative_path).exists() for relative_path in required_files)


def get_bundled_ollama_source_dir(profile: str | None = None) -> Path | None:
    config = get_runtime_profile_config(profile)
    candidates = (
        VENDOR_DIR / config["bundle_dirname"],
        ROOT_DIR / "_internal" / "vendor" / config["bundle_dirname"],
        Path(__file__).resolve().parent / "vendor" / config["bundle_dirname"],
    )
    required_files = tuple(config["required_files"])
    return _resolve_first_existing_dir(candidates, required_files)


def bundled_ollama_available(profile: str | None = None) -> bool:
    return get_bundled_ollama_source_dir(profile) is not None


def private_ollama_installed(profile: str | None = None) -> bool:
    config = get_runtime_profile_config(profile)
    return _runtime_has_required_files(get_private_ollama_dir(profile), tuple(config["required_files"]))


def get_ollama_mode() -> str:
    if private_ollama_installed():
        return "private"
    return "missing"


def get_active_ollama_executable() -> str | None:
    if private_ollama_installed():
        return str(get_private_ollama_exe())
    return None


def get_ollama_base_url() -> str:
    return PRIVATE_OLLAMA_BASE_URL


def _private_ollama_json(path: str, *, timeout: float = 0.6) -> dict[str, Any]:
    request = urllib.request.Request(f"{PRIVATE_OLLAMA_BASE_URL}{path}", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _system_ollama_json(path: str, *, timeout: float = 0.35) -> dict[str, Any]:
    request = urllib.request.Request(f"{SYSTEM_OLLAMA_BASE_URL}{path}", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def private_ollama_ready() -> bool:
    if not private_ollama_installed():
        return False
    try:
        _private_ollama_json("/api/tags")
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False


def system_ollama_ready() -> bool:
    try:
        _system_ollama_json("/api/tags")
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False


def system_ollama_app_running() -> bool:
    target = str(SYSTEM_OLLAMA_APP_PATH).lower()
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            exe = str(proc.info.get("exe") or "").lower()
            cmdline = " ".join(str(part).lower() for part in (proc.info.get("cmdline") or []))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if "ollama app" in name:
            return True
        if exe and exe == target:
            return True
        if target and target in cmdline:
            return True
    return False


def ollama_ready() -> bool:
    mode = get_ollama_mode()
    if mode == "missing":
        return False
    return private_ollama_ready()


def get_ollama_models() -> list[str]:
    mode = get_ollama_mode()
    if mode == "missing":
        return []
    try:
        response = _private_ollama_json("/api/tags")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return []
    models: list[str] = []
    for item in list(response.get("models") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            models.append(name)
    return models


def list_private_model_entries() -> list[dict[str, Any]]:
    mode = get_ollama_mode()
    if mode == "missing":
        return []
    try:
        response = _private_ollama_json("/api/tags")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return []
    return [item for item in list(response.get("models") or []) if isinstance(item, dict)]


def list_private_models_from_store() -> list[str]:
    manifests_root = PRIVATE_OLLAMA_MODELS_DIR / "manifests"
    if not manifests_root.exists():
        return []
    names: set[str] = set()
    for manifest_path in manifests_root.rglob("*"):
        if not manifest_path.is_file():
            continue
        parts = list(manifest_path.relative_to(manifests_root).parts)
        if len(parts) < 3:
            continue
        repo_parts = parts[1:-1]
        if repo_parts and repo_parts[0] == "library":
            repo_parts = repo_parts[1:]
        if not repo_parts:
            continue
        tag = parts[-1]
        names.add(f"{'/'.join(repo_parts)}:{tag}")
    return sorted(names)


def fetch_official_model_catalog(*, timeout: float = 6.0) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        f"{OLLAMA_LIBRARY_BASE_URL}/api/tags",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    models = payload.get("models") or []
    return [item for item in models if isinstance(item, dict)]


def test_model_installed(required_name: str, installed_names: list[str]) -> bool:
    lowered_required = required_name.lower()
    if ":" in lowered_required:
        return required_name in installed_names
    return any(item.lower().split(":")[0] == lowered_required for item in installed_names)


def _runtime_download_assets(profile: str) -> tuple[dict[str, str], ...]:
    config = get_runtime_profile_config(profile)
    return tuple(dict(item) for item in tuple(config.get("download_assets") or ()))


def _runtime_download_url(filename: str) -> str:
    return f"{OLLAMA_RUNTIME_RELEASE_BASE_URL}/{filename}"


def _download_file(url: str, target_path: Path, *, label: str, progress_callback=None) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "private-note-desktop"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, target_path.open("wb") as file:
            total_header = str(response.headers.get("Content-Length") or "").strip()
            total = int(total_header) if total_header.isdigit() else 0
            completed = 0
            last_report = 0.0
            _emit_progress(
                progress_callback,
                {
                    "phase": "download",
                    "status": f"正在下载 {label}",
                    "label": label,
                    "completed": completed,
                    "total": total,
                },
            )
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                completed += len(chunk)
                now = time.time()
                if now - last_report >= 0.25:
                    _emit_progress(
                        progress_callback,
                        {
                            "phase": "download",
                            "status": f"正在下载 {label}",
                            "label": label,
                            "completed": completed,
                            "total": total,
                        },
                    )
                    last_report = now
            _emit_progress(
                progress_callback,
                {
                    "phase": "download",
                    "status": f"已下载 {label}",
                    "label": label,
                    "completed": completed,
                    "total": total or completed,
                },
            )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"下载失败：{label} ({url})") from exc


def _extract_zip(archive_path: Path, target_dir: Path, *, label: str, progress_callback=None) -> None:
    _emit_progress(progress_callback, {"phase": "extract", "status": f"正在解压 {label}", "label": label})
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(target_dir)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"解压失败：{archive_path.name}") from exc
    _emit_progress(progress_callback, {"phase": "extract", "status": f"已解压 {label}", "label": label})


def _resolve_extracted_runtime_root(root: Path, required_files: tuple[Path, ...]) -> Path:
    if _runtime_has_required_files(root, required_files):
        return root
    for candidate in sorted(path for path in root.iterdir() if path.is_dir()):
        if _runtime_has_required_files(candidate, required_files):
            return candidate
    raise RuntimeError("下载的 Ollama 运行时文件不完整。")


def _copytree_merge(source_dir: Path, target_dir: Path) -> None:
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for child in source_dir.iterdir():
        destination = target_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, destination)


def _copy_relative_paths(source_root: Path, target_root: Path, relative_paths: tuple[Path, ...]) -> None:
    for relative_path in relative_paths:
        source_path = source_root / relative_path
        target_path = target_root / relative_path
        if not source_path.exists():
            continue
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)


def _download_runtime_to_dir(profile: str, target_dir: Path, *, progress_callback=None) -> str:
    normalized = normalize_runtime_profile(profile)
    config = get_runtime_profile_config(normalized)
    assets = _runtime_download_assets(normalized)
    if not assets:
        raise RuntimeError(f"{runtime_profile_label(normalized)} 暂不支持在线下载。")

    download_root = RUNTIME_DIR / "_downloads" / f"ollama-{normalized}"
    archives_dir = download_root / "archives"
    staging_dir = download_root / "staging"
    if download_root.exists():
        shutil.rmtree(download_root)
    archives_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        for asset in assets:
            filename = str(asset.get("filename") or "").strip()
            label = str(asset.get("label") or filename).strip() or filename
            if not filename:
                continue
            archive_path = archives_dir / filename
            _download_file(_runtime_download_url(filename), archive_path, label=label, progress_callback=progress_callback)
            _extract_zip(archive_path, staging_dir, label=label, progress_callback=progress_callback)

        source_dir = _resolve_extracted_runtime_root(staging_dir, tuple(config["required_files"]))
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
    finally:
        shutil.rmtree(download_root, ignore_errors=True)

    return f"{OLLAMA_RUNTIME_RELEASE_BASE_URL} ({normalized})"


def _download_runtime_assets_to_dir(
    *,
    assets: tuple[dict[str, str], ...],
    required_files: tuple[Path, ...],
    target_dir: Path,
    merge: bool = False,
    progress_callback=None,
) -> str:
    download_root = RUNTIME_DIR / "_downloads" / f"assets-{int(time.time() * 1000)}"
    archives_dir = download_root / "archives"
    staging_dir = download_root / "staging"
    if download_root.exists():
        shutil.rmtree(download_root)
    archives_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        for asset in assets:
            filename = str(asset.get("filename") or "").strip()
            label = str(asset.get("label") or filename).strip() or filename
            if not filename:
                continue
            archive_path = archives_dir / filename
            _download_file(_runtime_download_url(filename), archive_path, label=label, progress_callback=progress_callback)
            _extract_zip(archive_path, staging_dir, label=label, progress_callback=progress_callback)

        source_dir = _resolve_extracted_runtime_root(staging_dir, required_files)
        if merge:
            _copytree_merge(source_dir, target_dir)
        else:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir)
    finally:
        shutil.rmtree(download_root, ignore_errors=True)

    return OLLAMA_RUNTIME_RELEASE_BASE_URL


def _copy_bundled_runtime(profile: str, target_dir: Path) -> Path:
    source_dir = get_bundled_ollama_source_dir(profile)
    if source_dir is None:
        raise RuntimeError(
            f"未找到 {runtime_profile_label(profile)} 的项目内置 Ollama 运行时。"
        )

    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)
    return source_dir


def gpu_runtime_core_installed() -> bool:
    return _runtime_has_required_files(get_private_ollama_dir("gpu"), CPU_OLLAMA_REQUIRED_FILES)


def gpu_acceleration_installed() -> bool:
    return _runtime_has_required_files(get_private_ollama_dir("gpu"), GPU_ACCELERATION_REQUIRED_FILES)


def install_runtime_core(profile: str, progress_callback=None) -> str:
    normalized = normalize_runtime_profile(profile)
    target_dir = get_private_ollama_dir(normalized)
    if normalized == "cpu":
        bundled_source = get_bundled_ollama_source_dir("cpu")
        if bundled_source is not None:
            _emit_progress(progress_callback, {"phase": "prepare", "status": "正在部署 CPU 运行时", "label": "CPU 运行时"})
            return str(_copy_bundled_runtime("cpu", target_dir))
        return _download_runtime_assets_to_dir(
            assets=(
                {
                    "filename": "ollama-windows-amd64.zip",
                    "label": "Ollama Windows 运行时",
                },
            ),
            required_files=CPU_OLLAMA_REQUIRED_FILES,
            target_dir=target_dir,
            merge=False,
            progress_callback=progress_callback,
        )

    if normalized == "gpu":
        bundled_cpu_source = get_bundled_ollama_source_dir("cpu")
        if bundled_cpu_source is not None:
            _emit_progress(progress_callback, {"phase": "prepare", "status": "正在部署 GPU 运行时基础包", "label": "GPU 运行时"})
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(bundled_cpu_source, target_dir)
            return str(bundled_cpu_source)
        return _download_runtime_assets_to_dir(
            assets=(
                {
                    "filename": "ollama-windows-amd64.zip",
                    "label": "Ollama Windows 运行时",
                },
            ),
            required_files=CPU_OLLAMA_REQUIRED_FILES,
            target_dir=target_dir,
            merge=False,
            progress_callback=progress_callback,
        )

    raise ValueError(f"Unsupported runtime profile: {normalized}")


def install_gpu_acceleration(progress_callback=None) -> str:
    target_dir = get_private_ollama_dir("gpu")
    if not gpu_runtime_core_installed():
        install_runtime_core("gpu", progress_callback=progress_callback)

    bundled_gpu_source = get_bundled_ollama_source_dir("gpu")
    if bundled_gpu_source is not None:
        _emit_progress(progress_callback, {"phase": "prepare", "status": "正在部署 GPU 加速库", "label": "GPU 加速库"})
        _copy_relative_paths(bundled_gpu_source, target_dir, GPU_ACCELERATION_RELATIVE_PATHS)
        return str(bundled_gpu_source)

    return _download_runtime_assets_to_dir(
        assets=(
            {
                "filename": "ollama-windows-amd64-mlx.zip",
                "label": "Ollama GPU 加速库",
            },
        ),
        required_files=GPU_ACCELERATION_REQUIRED_FILES,
        target_dir=target_dir,
        merge=True,
        progress_callback=progress_callback,
    )


def get_runtime_profiles_status() -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    gpu_names = detect_nvidia_gpus()
    for profile_key in RUNTIME_PROFILES:
        config = get_runtime_profile_config(profile_key)
        bundled_source = get_bundled_ollama_source_dir(profile_key)
        runtime_dir = get_private_ollama_dir(profile_key)
        payload[profile_key] = {
            "key": profile_key,
            "label": config["label"],
            "short_label": config["short_label"],
            "accelerator": config["accelerator"],
            "bundled_available": bundled_source is not None,
            "bundled_source": str(bundled_source) if bundled_source is not None else None,
            "bundled_version": OLLAMA_RUNTIME_VERSION,
            "download_supported": bool(_runtime_download_assets(profile_key)),
            "private_installed": private_ollama_installed(profile_key),
            "runtime_dir": str(runtime_dir),
            "runtime_path": str(get_private_ollama_exe(profile_key)),
            "hardware_available": bool(gpu_names) if profile_key == "gpu" else True,
            "hardware_names": gpu_names if profile_key == "gpu" else [],
        }
    return payload


def list_managed_dependencies() -> list[dict[str, Any]]:
    profiles = get_runtime_profiles_status()

    dependencies: list[dict[str, Any]] = []
    cpu_state = dict(profiles.get("cpu") or {})
    gpu_state = dict(profiles.get("gpu") or {})

    cpu_source_text = "内置包部署" if bool(cpu_state.get("bundled_available")) else ("在线下载" if bool(cpu_state.get("download_supported")) else "当前不可用")
    dependencies.append(
        {
            "id": "runtime:cpu",
            "kind": "runtime",
            "title": "Ollama CPU 运行时",
            "description": "本地 CPU 版运行时，适合默认安装。",
            "installed": bool(cpu_state.get("private_installed")),
            "status": "installed" if bool(cpu_state.get("private_installed")) else "missing",
            "status_text": "已安装" if bool(cpu_state.get("private_installed")) else "未安装",
            "source_text": cpu_source_text,
            "hint": f"安装来源：{cpu_source_text}",
            "profile": "cpu",
        }
    )

    gpu_core_installed = gpu_runtime_core_installed()
    gpu_source_text = "内置包部署" if bool(cpu_state.get("bundled_available")) else ("在线下载" if bool(gpu_state.get("download_supported")) else "当前不可用")
    dependencies.append(
        {
            "id": "runtime:gpu",
            "kind": "runtime",
            "title": "Ollama GPU 运行时",
            "description": "GPU 模式的基础运行时，会部署到独立的 GPU 运行目录。",
            "installed": gpu_core_installed,
            "status": "installed" if gpu_core_installed else "missing",
            "status_text": "已安装" if gpu_core_installed else "未安装",
            "source_text": gpu_source_text,
            "hint": f"安装来源：{gpu_source_text}",
            "profile": "gpu",
        }
    )

    gpu_accel_installed = gpu_acceleration_installed()
    gpu_hint = "安装后可为 GPU 运行时补齐 CUDA / Vulkan / MLX 加速库。"
    if not bool(gpu_state.get("hardware_available")):
        gpu_hint += " 当前未检测到 NVIDIA GPU。"
    dependencies.append(
        {
            "id": "runtime:gpu-accel",
            "kind": "runtime",
            "title": "GPU 加速库",
            "description": "为 GPU 模式补齐 CUDA、Vulkan 与 MLX 加速文件。",
            "installed": gpu_accel_installed,
            "status": "installed" if gpu_accel_installed else "missing",
            "status_text": "已安装" if gpu_accel_installed else "未安装",
            "source_text": "内置包部署" if bool(gpu_state.get("bundled_available")) else ("在线下载" if bool(gpu_state.get("download_supported")) else "当前不可用"),
            "hint": gpu_hint,
            "profile": "gpu",
        }
    )
    return dependencies


def install_private_ollama_runtime(profile: str | None = None, progress_callback=None) -> dict[str, Any]:
    normalized = normalize_runtime_profile(profile)
    ensure_runtime_dirs()
    append_event(f"开始部署 {runtime_profile_label(normalized)} 的项目内置 Ollama 运行时。")

    if private_ollama_ready() and get_saved_runtime_profile() == normalized:
        stop_private_ollama()

    target_dir = get_private_ollama_dir(normalized)
    if normalized == "cpu":
        source_text = install_runtime_core("cpu", progress_callback=progress_callback)
    else:
        source_runtime = install_runtime_core("gpu", progress_callback=progress_callback)
        source_accel = install_gpu_acceleration(progress_callback=progress_callback)
        source_text = f"{source_runtime}; {source_accel}"
    PRIVATE_OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    import_system_models_into_private_store()
    append_event(
        f"{runtime_profile_label(normalized)} 部署完成，固定版本 {OLLAMA_RUNTIME_VERSION}，来源：{source_text}"
    )
    _emit_progress(
        progress_callback,
        {
            "phase": "complete",
            "status": f"{runtime_profile_label(normalized)} 已完成部署",
            "label": runtime_profile_label(normalized),
        },
    )
    return get_status()


def start_private_ollama() -> dict[str, Any]:
    profile = get_saved_runtime_profile()
    runtime_dir = get_private_ollama_dir(profile)
    runtime_exe = get_private_ollama_exe(profile)
    ensure_runtime_dirs()
    if not private_ollama_installed(profile):
        raise RuntimeError(f"{runtime_profile_label(profile)} 尚未部署。")
    if private_ollama_ready():
        return get_status()

    stdout_file = OLLAMA_STDOUT_LOG.open("w", encoding="utf-8")
    stderr_file = OLLAMA_STDERR_LOG.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [str(runtime_exe), "serve"],
            cwd=runtime_dir,
            env=_private_ollama_env(),
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
        write_pid(PRIVATE_OLLAMA_PID_FILE, process.pid)
    finally:
        stdout_file.close()
        stderr_file.close()

    append_event(f"{runtime_profile_label(profile)} 启动中，PID={process.pid}。")
    if not wait_until(private_ollama_ready, timeout_seconds=30):
        kill_process_tree(process.pid)
        clear_pid(PRIVATE_OLLAMA_PID_FILE)
        raise RuntimeError(f"{runtime_profile_label(profile)} 启动超时。")
    append_event(f"{runtime_profile_label(profile)} 已就绪。")
    return get_status()


def stop_private_ollama() -> dict[str, Any]:
    pid = read_pid(PRIVATE_OLLAMA_PID_FILE)
    if pid is not None:
        kill_process_tree(pid)
    clear_pid(PRIVATE_OLLAMA_PID_FILE)

    patterns = tuple(
        str(get_private_ollama_exe(profile_key))
        for profile_key in RUNTIME_PROFILES
        if get_private_ollama_exe(profile_key).exists()
    )
    stop_process_ids(find_process_ids_by_patterns(patterns + (f"{PRIVATE_OLLAMA_HOST}:{PRIVATE_OLLAMA_PORT}",)))
    wait_until(lambda: not private_ollama_ready(), timeout_seconds=10)
    append_event("项目私有 Ollama 已停止。")
    return get_status()


def uninstall_private_ollama_runtime(profile: str | None = None, *, remove_models: bool = False) -> dict[str, Any]:
    normalized = normalize_runtime_profile(profile)
    if get_saved_runtime_profile() == normalized:
        stop_private_ollama()
    target_dir = get_private_ollama_dir(normalized)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    if remove_models and PRIVATE_OLLAMA_MODELS_DIR.exists():
        shutil.rmtree(PRIVATE_OLLAMA_MODELS_DIR)
        PRIVATE_OLLAMA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    append_event(f"{runtime_profile_label(normalized)} 已卸载。")
    if get_saved_runtime_profile() == normalized and not bundled_ollama_available(normalized):
        set_saved_runtime_profile(DEFAULT_RUNTIME_PROFILE)
    return get_status()


def apply_runtime_profile(profile: str, *, restart_if_running: bool = True, ensure_installed: bool = True) -> dict[str, Any]:
    normalized = normalize_runtime_profile(profile)
    previous = get_saved_runtime_profile()
    was_running = bool(previous and private_ollama_ready())
    if was_running:
        stop_private_ollama()

    if ensure_installed and not private_ollama_installed(normalized):
        install_private_ollama_runtime(normalized)

    set_saved_runtime_profile(normalized)

    if restart_if_running and was_running:
        start_private_ollama()
    return get_status()


def ensure_ollama_running() -> None:
    profile = get_saved_runtime_profile()
    import_system_models_into_private_store()
    if not private_ollama_installed(profile):
        install_private_ollama_runtime(profile)
    start_private_ollama()


def install_managed_dependency(dependency_id: str, progress_callback=None) -> dict[str, Any]:
    normalized_id = str(dependency_id or "").strip()
    if not normalized_id:
        raise ValueError("Dependency id is required.")

    if normalized_id.startswith("runtime:"):
        runtime_key = normalized_id.split(":", 1)[1].strip().lower()
        if runtime_key == "cpu":
            install_runtime_core("cpu", progress_callback=progress_callback)
            return get_status()
        if runtime_key == "gpu":
            install_runtime_core("gpu", progress_callback=progress_callback)
            return get_status()
        if runtime_key == "gpu-accel":
            install_gpu_acceleration(progress_callback=progress_callback)
            return get_status()
        raise ValueError(f"Unsupported runtime dependency: {normalized_id}")

    if normalized_id.startswith("model:"):
        model_name = normalized_id.split(":", 1)[1].strip()
        if not model_name:
            raise ValueError("Model name is required.")

        def forward_progress(event: dict[str, Any]) -> None:
            payload: dict[str, Any] = {
                "phase": "model",
                "status": str(event.get("status") or "正在拉取模型"),
                "label": model_name,
            }
            if "completed" in event:
                payload["completed"] = event.get("completed")
            if "total" in event:
                payload["total"] = event.get("total")
            _emit_progress(progress_callback, payload)

        return pull_model_with_progress(model_name, forward_progress)

    raise ValueError(f"Unsupported dependency id: {normalized_id}")


def pull_model(model_name: str) -> dict[str, Any]:
    ensure_ollama_running()
    executable = get_active_ollama_executable()
    if not executable:
        raise RuntimeError("未找到可用的 Ollama 可执行文件。")
    append_event(f"开始拉取模型：{model_name}")
    result = run_command([executable, "pull", model_name], timeout=3600, env=_private_ollama_env(), cwd=get_private_ollama_dir())
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"拉取模型 {model_name} 失败。")
    append_event(f"模型拉取完成：{model_name}")
    return get_status()


def pull_model_with_progress(model_name: str, progress_callback=None) -> dict[str, Any]:
    ensure_ollama_running()
    append_event(f"开始拉取模型：{model_name}")
    payload = json.dumps({"model": model_name, "stream": True}).encode("utf-8")
    request = urllib.request.Request(
        f"{PRIVATE_OLLAMA_BASE_URL}/api/pull",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                event = json.loads(line)
                if callable(progress_callback):
                    progress_callback(event)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(detail or f"拉取模型 {model_name} 失败。") from exc
    append_event(f"模型拉取完成：{model_name}")
    return get_status()


def remove_model(model_name: str) -> dict[str, Any]:
    executable = get_active_ollama_executable()
    if not executable:
        raise RuntimeError("未找到可用的 Ollama 可执行文件。")
    append_event(f"开始删除模型：{model_name}")
    result = run_command([executable, "rm", model_name], timeout=600, env=_private_ollama_env(), cwd=get_private_ollama_dir())
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"删除模型 {model_name} 失败。")
    append_event(f"模型删除完成：{model_name}")
    return get_status()


def remove_model_with_progress(model_name: str, progress_callback=None) -> dict[str, Any]:
    ensure_ollama_running()
    if callable(progress_callback):
        progress_callback({"status": "starting"})
    append_event(f"开始删除模型：{model_name}")
    payload = json.dumps({"model": model_name}).encode("utf-8")
    request = urllib.request.Request(
        f"{PRIVATE_OLLAMA_BASE_URL}/api/delete",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            text = response.read().decode("utf-8", errors="ignore").strip()
            event = json.loads(text) if text else {"status": "success"}
            if callable(progress_callback):
                progress_callback(event)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(detail or f"删除模型 {model_name} 失败。") from exc
    append_event(f"模型删除完成：{model_name}")
    return get_status()


def shutdown_project_background() -> None:
    stop_private_ollama()
    stop_process_ids(find_process_ids_by_patterns(LEGACY_APP_PROCESS_PATTERNS))
    clear_pid(LEGACY_APP_PID_FILE)


def get_status(launcher_pid: int | None = None) -> dict[str, Any]:
    mode = get_ollama_mode()
    active_path = get_active_ollama_executable()
    environment_snapshot = _collect_environment_snapshot()
    cpu_name = str(environment_snapshot.get("cpu_name") or "")
    nvidia_gpu_names = list(environment_snapshot.get("nvidia_gpu_names") or [])
    private_running = private_ollama_ready()
    system_running = system_ollama_ready()
    system_app_running = system_ollama_app_running()
    selected_profile = get_saved_runtime_profile()
    profile_statuses = get_runtime_profiles_status()
    selected_profile_status = profile_statuses[selected_profile]
    frozen = bool(getattr(sys, "frozen", False))
    python_ready = vendored_python_available() or frozen
    python_path = str(PYTHON_EXE if vendored_python_available() else Path(sys.executable).resolve())
    python_mode = "vendored" if vendored_python_available() else ("frozen" if frozen else "missing")
    python_home = str(VENDORED_PYTHON_DIR) if VENDORED_PYTHON_DIR is not None else (str(ROOT_DIR) if frozen else None)
    payload: dict[str, Any] = {
        "environment": {
            "uv_available": bool(environment_snapshot.get("uv_path")),
            "uv_path": environment_snapshot.get("uv_path"),
            "python_ready": python_ready,
            "python_path": python_path,
            "python_mode": python_mode,
            "python_version": VENDORED_PYTHON_VERSION,
            "python_home": python_home,
            "project_root": str(ROOT_DIR),
            "runtime_dir": str(RUNTIME_DIR),
            "data_dir": str(DATA_DIR),
            "log_dir": str(LOG_DIR),
            "cpu_name": cpu_name,
            "nvidia_smi": environment_snapshot.get("nvidia_smi"),
            "nvidia_gpu_names": nvidia_gpu_names,
        },
        "ollama": {
            "mode": mode,
            "managed": True,
            "installed": active_path is not None,
            "path": active_path,
            "selected_profile": selected_profile,
            "selected_profile_label": selected_profile_status["label"],
            "private_installed": selected_profile_status["private_installed"],
            "private_path": selected_profile_status["runtime_path"],
            "private_models_dir": str(PRIVATE_OLLAMA_MODELS_DIR),
            "bundled_available": selected_profile_status["bundled_available"],
            "bundled_source": selected_profile_status["bundled_source"],
            "bundled_version": OLLAMA_RUNTIME_VERSION,
            "profiles": profile_statuses,
            "running": private_running,
            "private_running": private_running,
            "system_running": system_running,
            "system_app_running": system_app_running,
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

    stop_private_ollama()

    stop_process_ids(find_process_ids_by_patterns(LAUNCHER_PROCESS_PATTERNS))
    stop_process_ids(find_process_ids_by_patterns(LEGACY_APP_PROCESS_PATTERNS))
    clear_pid(LEGACY_APP_PID_FILE)
    clear_pid(LAUNCHER_PID_FILE)
