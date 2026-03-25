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

note:
    chcp 65001 | Out-Null; $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $body = @{ content = "Linux 鏉冮檺鏄寚鏂囦欢/鐩綍鐨勮(r)銆佸啓(w)銆佹墽琛?x)鏉冮檺锛屽彲閫氳繃 chmod 淇敼" } | ConvertTo-Json; Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/note -ContentType "application/json; charset=utf-8" -Body $body

chat:
    chcp 65001 | Out-Null; $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $body = @{ question = "Linux 鏉冮檺鏄粈涔? } | ConvertTo-Json; Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -ContentType "application/json; charset=utf-8" -Body $body | ConvertTo-Json
