# private-note

## 运行与刷新

### 安装依赖

```bash
uv sync
```

### 安装 Just（可选）

如果要使用 `just start/stop/restart` 命令，需要先安装 Just。

```bash
winget install --id Casey.Just --source winget
```

### 启动服务

```bash
$env:OLLAMA_LLM_MODEL="qwen2:7b"
$env:OLLAMA_EMBED_MODEL="nomic-embed-text"
uv run uvicorn main:app --host 127.0.0.1 --port 8002
```

### 刷新服务

在运行的终端按 `Ctrl + C` 退出后，重新执行启动命令即可刷新服务。

### 一键停止 / 重启（Just）

```bash
just start
just stop
just restart
```

## 常用指令

### API 调用（PowerShell）

写入笔记：

```bash
$body = @{ content = "示例笔记内容" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8002/note -ContentType "application/json; charset=utf-8" -Body $body
```

查看笔记：

```bash
Invoke-RestMethod http://127.0.0.1:8002/notes | ConvertTo-Json
```

提问：

```bash
$body = @{ question = "示例问题" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8002/chat -ContentType "application/json; charset=utf-8" -Body $body | ConvertTo-Json
```

## 前端页面

浏览器访问：

```
http://127.0.0.1:8002/
```
