set shell := ["powershell", "-NoProfile", "-Command"]

sync:
    uv sync

start:
    $env:OLLAMA_LLM_MODEL="qwen2:7b"; $env:OLLAMA_EMBED_MODEL="nomic-embed-text"; uv run uvicorn main:app --host 127.0.0.1 --port 8000

note:
    chcp 65001 | Out-Null; $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $body = @{ content = "Linux 权限是指文件/目录的读(r)、写(w)、执行(x)权限，可通过 chmod 修改" } | ConvertTo-Json; Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/note -ContentType "application/json; charset=utf-8" -Body $body

chat:
    chcp 65001 | Out-Null; $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $body = @{ question = "Linux 权限是什么" } | ConvertTo-Json; Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json; charset=utf-8" -Body $body | ConvertTo-Json
