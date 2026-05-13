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
├─ vendor/                 # 固定 Python 运行时；Ollama 本地包可选放这里
├─ runtime/                # 项目私有 Ollama 运行时目录，运行后生成
├─ data/                   # SQLite、Chroma、模型、日志等本地数据，运行后生成
└─ .venv/                  # uv 创建的项目虚拟环境，源码仓库不跟踪
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

- Windows x64
- Windows x64
- 源码仓库已内置固定 Python 运行时；只做源码调试时也可以使用自己的 Python 3.10+ 与 `uv`

### 源码运行

源码仓库会提交打包用的固定 Python 环境：

```text
vendor/python-3.11.7-embed-amd64/
```

因此另一台 Windows x64 电脑 clone 后，可以直接使用 `run.bat` / `run.ps1` 启动，或使用 `packaging` 下的脚本重新打包。

如果你想用普通源码开发环境，也可以用 `uv` 创建 `.venv/`：

```powershell
uv sync
```

然后使用当前 Python 环境启动桌面客户端：

```powershell
uv run python launcher_desktop.py
```

也可以使用常用命令封装：

```powershell
just sync
just test
```

> `run.bat` / `run.ps1` 会优先使用仓库内置的 `vendor/python-3.11.7-embed-amd64/python.exe`。

### 需要手动准备的依赖

从源码仓库拉下来后，需要自己准备以下内容：

- Python 3.10+：可选；仅当你不使用仓库内置 Python，而要自己用源码开发环境运行时需要。
- `uv`：可选；用于根据 `pyproject.toml` / `uv.lock` 创建 `.venv/` 并安装依赖。
- `just`：可选；只在使用 `just sync`、`just test`、`just build`、`just portable` 这些快捷命令时需要。
- Ollama 运行时：可在客户端设置页的“管理依赖”里安装，也可以手动准备到 `runtime/` 或 `vendor/`。默认固定版本为 `0.20.2`，客户端默认从本项目 GitHub Release assets 下载 CPU/GPU 运行时分包，不再直接依赖 Ollama 上游 release 地址。
- 本地模型：问答推荐轻量档 `qwen2.5:3b` 或默认档 `qwen2.5:7b`，向量推荐 `nomic-embed-text`。模型不会进入 Git，需要在客户端内安装，或通过 Ollama 手动拉取。
- 内置 Python 运行时：已随源码仓库提交，目录为 `vendor/python-3.11.7-embed-amd64/`，包含 Tkinter、`chromadb`、`psutil`、`PyInstaller` 等运行/构建依赖。

这些目录和文件都属于本地环境或用户数据，已经由 `.gitignore` 排除：

```text
.venv/
runtime/
data/notes.db
data/chroma/
data/ollama-models/
data/logs/
data/run/
```

### 本地发布环境启动

双击：

```text
run.bat
```

或命令行：

```powershell
.\run.ps1
```

注意：这种方式要求已经准备好 `vendor/python-3.11.7-embed-amd64/`。
当前源码仓库已包含该目录。

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

当前仓库已经内置 PyInstaller 打包脚手架，不需要额外单独新建一个打包项目。当前推荐发布形态是 one-folder 便携版压缩包。

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

### 便携版压缩包

生成 one-folder 便携版压缩包：

```powershell
.\packaging\package-portable.ps1 -Clean
```

或使用：

```powershell
just portable
```

输出路径：

```text
packaging/output/PrivateNoteDesktop-portable-v<version>-win-x64.zip
```

压缩包展开后会包含：

- `PrivateNoteDesktop.exe`
- `_internal/`
- `README.txt`

其中 Python 运行时与普通依赖已经随便携包一起分发，`Ollama` 与模型仍然在客户端设置页中按需安装。

### 打包成品运行依赖

打包完成后的便携版面向普通使用者，不需要再安装 Python、`uv`、`just`、PyInstaller，也不需要保留源码目录。将压缩包完整解压到可写目录后，直接运行：

```text
PrivateNoteDesktop/PrivateNoteDesktop.exe
```

成品运行时仍然需要按需准备：

- Windows x64：当前便携包只面向 Windows x64。
- 可写目录：不要放在 `Program Files` 等受限目录，否则本地数据库、日志、模型和运行时可能无法写入。
- Ollama 运行时：CPU 运行时可随发布包内置；如果发布包没有内置，或需要 GPU 模式，可在客户端设置页的“管理依赖”里在线安装。
- 本地模型：问答模型 `qwen2.5:3b` / `qwen2.5:7b` 和向量模型 `nomic-embed-text` 不会预置在 Git 中，通常也不会放进便携包，需要首次使用时在客户端内安装。
- 网络连接：首次在线安装 Ollama 运行时或拉取模型时需要访问外网；如果目标机器离线，需要提前把运行时和模型准备到对应目录。
- 磁盘空间：模型文件通常较大，至少预留数 GB 空间；GPU 运行时和多模型场景需要更多空间。
- GPU 依赖：CPU 模式不需要显卡；GPU 模式仅面向 NVIDIA 环境，并依赖本机显卡驱动可用。
- 运行时下载源：默认使用本项目 release assets；如需自建镜像，可通过 `OLLAMA_RUNTIME_RELEASE_BASE_URL` 覆盖为兼容的 asset 基础 URL。

源码仓库会跟踪打包所需的 `vendor/python-3.11.7-embed-amd64/`，所以新机器 clone 后可以直接重新打包。

`vendor/` 中可能额外出现两个 Ollama 固定运行时目录：

- `vendor/ollama-windows-amd64-cpu-0.20.2/`：CPU 版 Ollama 固定运行时。体积较小，可被打包脚本内置到便携包中，供客户端首次部署 CPU 模式时直接复制。
- `vendor/ollama-windows-amd64-gpu-nvidia-0.20.2/`：GPU 版 Ollama 固定运行时，包含 CUDA / Vulkan / MLX 加速库。该目录体积很大，不跟踪到 Git；需要 GPU 模式时，建议在客户端“管理依赖”里在线安装，或在离线发布环境中手动准备。

发布 runtime assets：

```powershell
.\packaging\package-runtime-assets.ps1 -Clean
```

该脚本会从 `vendor/ollama-windows-amd64-cpu-0.20.2/` 和 `vendor/ollama-windows-amd64-gpu-nvidia-0.20.2/` 生成 release 附件。GPU 运行时会拆成多个 zip，避免单个 GitHub Release 附件过大。

### 图标文件

源码运行时优先读取根目录图标：

```text
icon.png
```

Windows 可执行文件图标仍然使用：

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
   - `qwen2.5:3b`（轻量，适合配置较弱或希望启动更快的机器）
   - `qwen2.5:7b`（默认，回答质量和速度更均衡）
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
