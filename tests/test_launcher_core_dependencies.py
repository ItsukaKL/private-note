from __future__ import annotations

from pathlib import Path
import zipfile

import launcher_core


def test_list_managed_dependencies_reports_runtime_and_models(monkeypatch):
    monkeypatch.setattr(
        launcher_core,
        "get_runtime_profiles_status",
        lambda: {
            "cpu": {
                "private_installed": True,
                "bundled_available": True,
                "download_supported": True,
                "hardware_available": True,
            },
            "gpu": {
                "private_installed": False,
                "bundled_available": False,
                "download_supported": True,
                "hardware_available": False,
            },
        },
    )
    monkeypatch.setattr(launcher_core, "gpu_runtime_core_installed", lambda: False)
    monkeypatch.setattr(launcher_core, "gpu_acceleration_installed", lambda: False)

    entries = launcher_core.list_managed_dependencies()

    assert [entry["id"] for entry in entries] == [
        "runtime:cpu",
        "runtime:gpu",
    ]
    assert entries[0]["status_text"] == "已安装"
    assert entries[1]["status_text"] == "未安装"
    assert "加速库" in entries[1]["title"]
    assert "NVIDIA GPU" in str(entries[1]["hint"])


def test_install_private_ollama_runtime_falls_back_to_download(monkeypatch, tmp_path):
    downloads: list[tuple[str, Path]] = []

    monkeypatch.setattr(launcher_core, "private_ollama_ready", lambda: False)
    monkeypatch.setattr(launcher_core, "get_saved_runtime_profile", lambda: "cpu")
    monkeypatch.setattr(launcher_core, "stop_private_ollama", lambda: None)
    monkeypatch.setattr(launcher_core, "append_event", lambda message: None)
    monkeypatch.setattr(launcher_core, "import_system_models_into_private_store", lambda: None)
    monkeypatch.setattr(launcher_core, "get_status", lambda: {"ok": True})
    monkeypatch.setattr(launcher_core, "get_private_ollama_dir", lambda profile=None: tmp_path / "runtime")

    def fake_install_runtime_core(profile: str, progress_callback=None) -> str:
        target_dir = tmp_path / "runtime"
        downloads.append((profile, target_dir))
        target_dir.mkdir(parents=True, exist_ok=True)
        return "downloaded"

    monkeypatch.setattr(launcher_core, "install_runtime_core", fake_install_runtime_core)

    payload = launcher_core.install_private_ollama_runtime("cpu")

    assert payload == {"ok": True}
    assert downloads == [("cpu", tmp_path / "runtime")]


def test_install_managed_dependency_forwards_model_progress(monkeypatch):
    captured: list[dict[str, object]] = []

    def fake_pull(model_name: str, progress_callback=None):
        if callable(progress_callback):
            progress_callback({"status": "pulling manifest", "completed": 5, "total": 10})
        return {"model": model_name}

    monkeypatch.setattr(launcher_core, "pull_model_with_progress", fake_pull)

    payload = launcher_core.install_managed_dependency(
        f"model:{launcher_core.DEFAULT_LLM_MODEL}",
        progress_callback=lambda event: captured.append(dict(event)),
    )

    assert payload == {"model": launcher_core.DEFAULT_LLM_MODEL}
    assert captured
    assert captured[0]["label"] == launcher_core.DEFAULT_LLM_MODEL
    assert captured[0]["phase"] == "model"


def test_install_managed_dependency_gpu_installs_core_and_accel(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(launcher_core, "gpu_runtime_core_installed", lambda: False)
    monkeypatch.setattr(launcher_core, "gpu_acceleration_installed", lambda: False)
    monkeypatch.setattr(launcher_core, "install_runtime_core", lambda profile, progress_callback=None: called.append(f"core:{profile}"))
    monkeypatch.setattr(launcher_core, "install_gpu_acceleration", lambda progress_callback=None: called.append("gpu-accel"))
    monkeypatch.setattr(launcher_core, "get_status", lambda: {"ok": True})

    payload = launcher_core.install_managed_dependency("runtime:gpu")

    assert payload == {"ok": True}
    assert called == ["core:gpu", "gpu-accel"]


def test_install_managed_dependency_gpu_only_installs_missing_accel(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(launcher_core, "gpu_runtime_core_installed", lambda: True)
    monkeypatch.setattr(launcher_core, "gpu_acceleration_installed", lambda: False)
    monkeypatch.setattr(launcher_core, "install_runtime_core", lambda profile, progress_callback=None: called.append(f"core:{profile}"))
    monkeypatch.setattr(launcher_core, "install_gpu_acceleration", lambda progress_callback=None: called.append("gpu-accel"))
    monkeypatch.setattr(launcher_core, "get_status", lambda: {"ok": True})

    payload = launcher_core.install_managed_dependency("runtime:gpu")

    assert payload == {"ok": True}
    assert called == ["gpu-accel"]


def test_private_ollama_env_keeps_models_loaded(monkeypatch):
    monkeypatch.setattr(launcher_core, "OLLAMA_MODEL_KEEP_ALIVE", "-1")

    env = launcher_core._private_ollama_env()

    assert env["OLLAMA_KEEP_ALIVE"] == "-1"


def test_list_managed_dependencies_reports_partial_gpu_runtime(monkeypatch):
    monkeypatch.setattr(
        launcher_core,
        "get_runtime_profiles_status",
        lambda: {
            "cpu": {
                "private_installed": True,
                "bundled_available": True,
                "download_supported": True,
                "hardware_available": True,
            },
            "gpu": {
                "private_installed": False,
                "bundled_available": False,
                "download_supported": True,
                "hardware_available": True,
            },
        },
    )
    monkeypatch.setattr(launcher_core, "gpu_runtime_core_installed", lambda: True)
    monkeypatch.setattr(launcher_core, "gpu_acceleration_installed", lambda: False)

    entries = launcher_core.list_managed_dependencies()
    gpu_entry = next(entry for entry in entries if entry["id"] == "runtime:gpu")

    assert gpu_entry["installed"] is False
    assert gpu_entry["status"] == "partial"
    assert gpu_entry["status_text"] == "缺少加速库"
    assert "点击安装会补齐缺失部分" in str(gpu_entry["hint"])


def test_install_managed_dependency_rejects_legacy_gpu_accel_id():
    try:
        launcher_core.install_managed_dependency("runtime:gpu-accel")
    except ValueError as exc:
        assert "Unsupported runtime dependency" in str(exc)
    else:
        raise AssertionError("legacy gpu acceleration dependency id should be rejected")


def test_extract_zip_validates_archive_before_extracting(tmp_path):
    archive_path = tmp_path / "broken.zip"
    archive_path.write_bytes(b"not a zip")

    try:
        launcher_core._extract_zip(archive_path, tmp_path / "out", label="broken")
    except RuntimeError as exc:
        assert "解压失败" in str(exc)
    else:
        raise AssertionError("broken zip should fail before extraction")


def test_extract_zip_extracts_valid_archive(tmp_path):
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("ollama.exe", "ok")

    launcher_core._extract_zip(archive_path, tmp_path / "out", label="runtime")

    assert (tmp_path / "out" / "ollama.exe").read_text(encoding="utf-8") == "ok"


def test_install_gpu_acceleration_downloads_all_gpu_accel_archives(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    monkeypatch.setattr(launcher_core, "gpu_runtime_core_installed", lambda: True)
    monkeypatch.setattr(launcher_core, "get_bundled_ollama_source_dir", lambda profile: None)
    monkeypatch.setattr(launcher_core, "get_private_ollama_dir", lambda profile=None: tmp_path / "ollama-gpu")

    def fake_download_runtime_assets_to_dir(**kwargs):
        captured.update(kwargs)
        return "downloaded"

    monkeypatch.setattr(launcher_core, "_download_runtime_assets_to_dir", fake_download_runtime_assets_to_dir)

    source = launcher_core.install_gpu_acceleration()

    version = launcher_core.OLLAMA_RUNTIME_VERSION
    assert source == "downloaded"
    assert [item["filename"] for item in captured["assets"]] == [
        f"PrivateNote-ollama-gpu-cuda-v12-core-{version}.zip",
        f"PrivateNote-ollama-gpu-cuda-v12-deps-{version}.zip",
        f"PrivateNote-ollama-gpu-cuda-v13-{version}.zip",
        f"PrivateNote-ollama-gpu-mlx-cuda-v13-{version}.zip",
        f"PrivateNote-ollama-gpu-vulkan-{version}.zip",
    ]
    assert captured["required_files"] == launcher_core.GPU_ACCELERATION_REQUIRED_FILES
    assert captured["merge"] is True


def test_import_system_models_into_private_store_adds_missing_files_incrementally(monkeypatch, tmp_path):
    source_root = tmp_path / "system-models"
    target_root = tmp_path / "private-models"

    source_manifest_a = source_root / "manifests" / "registry.ollama.ai" / "library" / "foo" / "latest"
    source_manifest_b = source_root / "manifests" / "registry.ollama.ai" / "library" / "bar" / "latest"
    source_blob_a = source_root / "blobs" / "sha256-a"
    source_blob_b = source_root / "blobs" / "sha256-b"
    target_manifest_a = target_root / "manifests" / "registry.ollama.ai" / "library" / "foo" / "latest"
    target_blob_a = target_root / "blobs" / "sha256-a"

    for path, content in (
        (source_manifest_a, "foo"),
        (source_manifest_b, "bar"),
        (source_blob_a, "blob-a"),
        (source_blob_b, "blob-b"),
        (target_manifest_a, "foo"),
        (target_blob_a, "blob-a"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    events: list[str] = []
    monkeypatch.setattr(launcher_core, "SYSTEM_OLLAMA_MODELS_DIR", source_root)
    monkeypatch.setattr(launcher_core, "PRIVATE_OLLAMA_MODELS_DIR", target_root)
    monkeypatch.setattr(launcher_core, "append_event", lambda message: events.append(message))

    payload = launcher_core.import_system_models_into_private_store(force=False)

    assert payload["imported"] is True
    assert payload["manifests"] == 1
    assert (target_root / "manifests" / "registry.ollama.ai" / "library" / "bar" / "latest").exists()
    assert (target_root / "blobs" / "sha256-b").exists()
    assert events


def test_collect_environment_snapshot_uses_cache(monkeypatch):
    cpu_calls: list[str] = []
    gpu_calls: list[str] = []

    monkeypatch.setattr(launcher_core, "_environment_snapshot_cache", None)
    monkeypatch.setattr(launcher_core, "_environment_snapshot_timestamp", 0.0)
    monkeypatch.setattr(launcher_core, "command_path", lambda name: f"C:/mock/{name}.exe")
    monkeypatch.setattr(launcher_core, "detect_cpu_name", lambda: cpu_calls.append("cpu") or "CPU")
    monkeypatch.setattr(launcher_core, "detect_nvidia_gpus", lambda: gpu_calls.append("gpu") or ["GPU"])

    first = launcher_core._collect_environment_snapshot(force_refresh=True)
    second = launcher_core._collect_environment_snapshot()

    assert first == second
    assert cpu_calls == ["cpu"]
    assert gpu_calls == ["gpu"]
