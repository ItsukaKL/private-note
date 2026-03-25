# Private Note

## 项目简介

Private Note 是一个本地化的 RAG 笔记系统，目标是把“个人笔记沉淀”和“基于笔记的问答”放到同一个单体应用里。用户写入笔记后，系统会自动切分文本、生成向量并写入本地向量库；用户提问时，再基于已有笔记做检索增强生成。

这个项目的定位不是通用聊天应用，而是一个只围绕私有笔记内容工作的本地知识问答原型。数据、模型调用和前端页面都部署在本机，核心外部依赖是本地 Ollama 服务。

## 核心功能

- 笔记创建、查询、更新、删除
- 笔记自动切块并生成 embedding
- Chroma 本地向量检索
- 基于笔记上下文的本地 LLM 问答
- 单页前端笔记管理与聊天界面
- 启动入口自检、自动拉起服务、自动打开浏览器

## 技术架构

项目采用单体结构，一个 FastAPI 进程同时承担 API 服务和静态页面托管。

- 前端层：`static/index.html`
  负责笔记列表、编辑器、提问输入、聊天记录渲染
- 接口层：`main.py`
  暴露笔记 CRUD、`/chat`、`/healthz`
- 数据层：`db.py`
  使用 SQLite 保存原始笔记
- 检索层：`text_splitter.py` + `chroma_store.py`
  负责切块、向量写入、向量召回
- 模型接入层：`ollama_client.py`
  通过 Ollama HTTP API 完成 embedding 和生成
- 启动编排层：`run.ps1` / `run.bat`
  负责环境自检、依赖检查、自动启动后端并打开浏览器

模块关系：

1. 用户在前端录入笔记
2. FastAPI 将原文写入 SQLite
3. 后端切块并调用 Ollama 生成 embedding
4. chunk 和向量写入 Chroma
5. 用户提问时，系统对问题做 embedding
6. Chroma 召回相关笔记片段
7. 后端组装 prompt，交给 Ollama 生成回答

## 项目结构

```text
private-note/
├─ main.py               # FastAPI 入口
├─ serve_app.py          # 后端启动包装器
├─ db.py                 # SQLite 笔记存储
├─ chroma_store.py       # Chroma 向量读写
├─ ollama_client.py      # Ollama 客户端
├─ text_splitter.py      # 文本切块
├─ schemas.py            # Pydantic 模型
├─ run.ps1               # Windows 统一启动脚本
├─ run.bat               # Windows 双击启动入口
├─ stop.ps1              # 停止脚本
├─ stop.bat              # 双击停止入口
├─ justfile              # 开发命令入口
├─ static/
│  └─ index.html         # 前端单页应用
├─ data/
│  ├─ notes.db           # SQLite 数据文件
│  ├─ chroma/            # Chroma 持久化目录
│  ├─ logs/              # 启动日志，运行时生成
│  └─ run/               # PID 文件，运行时生成
├─ pyproject.toml        # Python 依赖定义
└─ uv.lock               # 锁文件
```

## 核心模块说明

### `main.py`

系统后端入口，负责把笔记 CRUD、向量化、检索和生成串成完整业务链路。

### `db.py`

SQLite 持久化层，保存原始笔记内容。当前表结构很轻量，适合本地单机原型。

### `chroma_store.py`

向量存储与召回层，使用本地 Chroma 集合保存 chunk、embedding 和元数据。

### `ollama_client.py`

模型接入层，统一封装对 Ollama 的 embedding 与 generate 调用。

### `run.ps1`

统一启动入口，负责：

- 检查 `uv` 是否可用
- 执行 `uv sync`
- 检查 Ollama 是否可用
- 检查目标模型是否存在
- 检查端口占用
- 启动后端
- 自动打开浏览器

## 快速启动

### 运行前提

- Windows
- Python 3.10 及以上
- `uv`
- 已安装 Ollama
- 本地已下载模型：
  - `qwen2:7b`
  - `nomic-embed-text`

### 1. 安装 Python 依赖

```bash
uv sync
```

### 2. 准备 Ollama 模型

```bash
ollama pull qwen2:7b
ollama pull nomic-embed-text
```

### 3. 一键启动

命令行方式：

```powershell
.\run.ps1
```

双击方式：

```text
run.bat
```

启动脚本会自动执行以下动作：

1. 检查 `uv`
2. 检查并同步 Python 依赖
3. 检查 Ollama 是否已启动，未启动时自动拉起
4. 检查模型是否已安装
5. 启动 FastAPI
6. 自动打开浏览器到首页

### 4. 停止服务

命令行方式：

```powershell
.\stop.ps1
```

双击方式：

```text
stop.bat
```

### 5. 开发命令

```powershell
just start
just stop
just restart
```

## 使用流程

### 笔记写入流程

1. 用户在前端输入笔记并保存
2. 前端调用 `POST /note` 或 `PUT /note/{id}`
3. 后端写入 SQLite
4. 后端切块并调用 embedding 模型
5. chunk 与向量写入 Chroma
6. 前端刷新笔记列表

### 问答流程

1. 用户输入问题
2. 前端调用 `POST /chat`
3. 后端为问题生成 embedding
4. Chroma 召回相关笔记片段
5. 后端拼接上下文 prompt
6. Ollama 返回答案
7. 前端展示结果并保存在本地聊天记录中

## 设计说明

- 这是一个典型的“本地单体 + 本地模型 + 本地向量库”的最小可运行 RAG 项目
- 原始数据和检索索引分层明确，SQLite 与 Chroma 各司其职
- 前端不依赖框架，当前重点更偏向功能验证而非复杂组件治理
- 新增的统一入口把“依赖检查”和“应用启动”收口到了项目内，减少手工步骤
- 当前启动入口主要面向 Windows 场景，属于工程集成优化，不是跨平台发布方案

## TODO / 可扩展方向

- 增加 `.env` 或集中配置文件，减少模型名和端口散落在脚本中
- 启动器增加首次安装引导，例如检测缺失模型后给出更友好的提示
- 为 `/chat` 返回召回来源，提升答案可解释性
- 区分 `created_at` 和 `updated_at`
- 补充自动化测试，覆盖 CRUD、检索和启动流程
- 如果后续需要跨平台一键启动，可补 Linux/macOS 脚本
