# Private Note Desktop

## 项目简介

Private Note Desktop 是一个基于本地笔记的桌面端 RAG 知识助手。当前版本已经收敛为纯 Python 桌面客户端，不再提供浏览器页面或 Web API 入口。

项目的核心目标是把“笔记管理 + 本地检索 + 本地模型问答 + 运行时管理”整合到一个可直接运行的客户端里：左侧维护笔记，右侧直接提问，设置页承担模型切换、Ollama 运行时管理、环境诊断和日志查看。

## 核心功能

- 本地创建、编辑、删除笔记
- 保存笔记后自动切块、向量化并写入 Chroma
- 基于本地笔记内容进行问答
- 结构化字段优先命中，减少答非所问
- 设置页热切换对话模型
- 客户端内置运行时诊断、日志查看
- 支持项目私有 Ollama 运行时与项目私有模型目录

## 技术架构

### 客户端层

- `launcher_desktop.py`
  - 桌面客户端主入口
  - 承担主界面、编辑页、设置页、聊天记录持久化、关闭流程

### 业务层

- `note_service.py`
  - 核心业务收口层
  - 负责笔记 CRUD、结构化字段提取、检索上下文组织、问答入口

### 数据层

- `db.py`
  - SQLite 持久化层
  - 存储笔记正文与更新时间
- `chroma_store.py`
  - Chroma 向量存储读写
  - 管理 chunk 写入、删除、检索

### 模型与运行时层

- `ollama_client.py`
  - Ollama API 调用封装
  - 提供模型列表、模型切换、embedding、文本生成
- `launcher_core.py`
  - 客户端运行时辅助层
  - 负责私有 Ollama 安装、启动、停止、卸载、模型目录、状态汇总、日志和 PID 管理

## 运行流程

1. 用户在桌面客户端中编辑笔记
2. `note_service.py` 调用 `db.py` 落 SQLite，同时调用 `text_splitter.py`、`ollama_client.py`、`chroma_store.py` 更新向量索引
3. 用户提问时，`note_service.py` 先做结构化字段命中，再做向量召回与上下文拼装
4. `ollama_client.py` 通过当前生效的 Ollama 运行时生成答案
5. 设置页通过 `launcher_core.py` 管理私有运行时、模型目录和日志

## 项目结构

```text
private-note/
├─ launcher_desktop.py     # 桌面客户端主入口
├─ launcher_core.py        # 运行时辅助、状态与日志
├─ launcher_cli.py         # stop.ps1 使用的命令行入口
├─ note_service.py         # 核心业务逻辑
├─ db.py                   # SQLite 读写
├─ chroma_store.py         # Chroma 向量存储
├─ ollama_client.py        # Ollama 调用与模型管理
├─ text_splitter.py        # 文本切块
├─ run.ps1                 # 启动桌面客户端
├─ run.bat                 # Windows 双击启动
├─ stop.ps1                # 停止桌面客户端及其自管进程
├─ stop.bat                # Windows 双击停止
├─ justfile                # 常用命令封装
├─ pyproject.toml          # Python 依赖定义
├─ runtime/
│  └─ ollama/              # 项目私有 Ollama 运行时目录
├─ data/
│  ├─ notes.db             # SQLite 数据
│  ├─ chroma/              # Chroma 数据
│  ├─ ollama-models/       # 项目私有模型目录
│  ├─ logs/                # 客户端与运行时日志
│  └─ run/                 # PID 文件
└─ .venv/                  # 项目虚拟环境
```

## 核心模块说明

### 1. 桌面客户端

`launcher_desktop.py` 是当前唯一正式入口。主界面直接就是业务 UI，设置页不再承担 Web 服务控制，而是聚焦模型切换、私有 Ollama 管理、环境信息和日志排查。

### 2. 笔记与检索服务

`note_service.py` 负责笔记增删改查、问答前的检索组织，以及结构化字段优先命中。像“歌手 / 作词 / 作曲 / 编曲”这类问题会优先尝试直接从笔记字段提取，避免模型在混杂上下文中答偏。

### 3. 私有运行时管理

`launcher_core.py` 现在负责接管项目私有 Ollama：

- 安装项目私有运行时
- 启动/停止私有 Ollama
- 管理项目私有模型目录
- 汇总环境状态与日志

如果项目私有运行时尚未安装，当前版本会临时兼容系统级 Ollama，避免旧环境立即失效。

## 快速启动

### 环境要求

- Windows
- Python 3.10 及以上
- `uv`

### 安装依赖

```powershell
uv sync
```

### 启动客户端

双击：

```text
run.bat
```

或命令行：

```powershell
.\run.ps1
```

### 停止客户端

双击：

```text
stop.bat
```

或命令行：

```powershell
.\stop.ps1
```

## 打包构建

当前仓库已经内置 PyInstaller 打包脚手架，不需要额外单独新建一个打包项目。

### 构建命令

```powershell
.\packaging\build.ps1
```

或使用：

```powershell
just build
```

### 构建输出

默认输出到：

```text
dist/PrivateNoteDesktop/
```

### 图标文件

如果你已经准备好软件图标，把它放到：

```text
packaging/assets/app.ico
```

构建时会自动带上这个图标。

## 使用流程

### 首次使用建议

1. 启动客户端
2. 打开右上角设置页
3. 安装项目私有 Ollama
4. 安装推荐模型：
   - `qwen2:7b`
   - `nomic-embed-text`

### 笔记管理

1. 左侧点击“新建”
2. 在编辑页输入笔记内容
3. 点击“保存”
4. 系统自动写入 SQLite 并更新 Chroma 向量索引

### 提问流程

1. 在主界面右侧输入问题
2. 点击“发送”
3. 系统优先尝试结构化字段命中
4. 若未命中，则执行向量检索并组织上下文
5. 调用当前生效的 Ollama 运行时生成回答

### 模型切换

1. 点击主界面右上角齿轮
2. 进入设置页
3. 在模型下拉框中选择已安装模型
4. 新模型立即热生效

## 设计说明

- 当前版本明确放弃浏览器入口，只保留桌面客户端交互
- 关闭窗口时会顺带清理项目自管的后台残留进程
- 设置页保留“诊断台”定位，但进一步承担私有运行时管理
- 问答链路采用“结构化字段优先 + 检索补充”的策略，减少答非所问
- 私有 Ollama 与私有模型目录会逐步替代系统共享运行时

## 当前限制

- 私有 Ollama 的下载依赖外网访问
- 当前优先支持标准 Windows x64 私有运行时
- 若未安装私有运行时，客户端仍会暂时兼容系统级 Ollama

## TODO / 可扩展方向

- 将客户端打包为独立 `exe`
- 私有 Ollama 下载进度与失败重试
- 托盘运行与最小化后台
- 更细粒度的模型管理能力
- 笔记标签、分类与全文过滤
- 对检索结果增加可视化引用展示
