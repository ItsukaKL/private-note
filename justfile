set shell := ["powershell", "-NoProfile", "-Command"]

sync:
    uv sync

start:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1

stop:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1

restart:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1

build:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1
