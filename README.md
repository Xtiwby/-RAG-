# 知源 RAG 知识库管理系统

知源 RAG 是一个面向课程实践和内部知识管理场景的多模态知识库系统。系统以 Vue 3 和 FastAPI 为应用框架，使用 PostgreSQL、pgvector、MinIO 和独立 worker 构成服务端工程骨架，通过本地 BGE 模型完成语义向量与重排，并调用 Kimi 完成受证据约束的问答、事实核验和报告生成。

当前版本：`v0.11.0`

> 本仓库不包含模型权重、运行数据库、对象存储数据、虚拟环境、前端依赖或 API 密钥。

## 核心能力

### 知识库与多模态接入

- 创建、编辑、停用和归档知识库，支持按用户授予读取或维护权限。
- 接入 PDF、DOCX、TXT、Markdown、HTML、CSV、Excel、PPT、PPTX 和图片。
- 文档按照“上传、解析、切分、索引”四个阶段处理，保存阶段进度、事件和失败原因。
- 保存原文件、结构化文本、页码或章节、片段、SHA-256 和文档版本。
- 引用可定位至相应文档版本、原文片段和上下文。

### 混合检索与 RAG

- 本地 `BAAI/bge-m3` 生成 1024 维语义向量。
- PostgreSQL `pgvector HNSW` 执行向量候选召回。
- PostgreSQL `GIN` 全文索引及 BM25 信号执行关键词召回。
- 使用 RRF 融合向量和关键词结果，再由 `BAAI/bge-reranker-v2-m3` 重排。
- 支持中英文查询改写、跨语言召回、知识库权限过滤和报告分章检索。
- 保存查询参数、候选排名、各阶段分数、模型版本和检索快照。

### 知识问答

- 用户可选择一个或多个已授权知识库进行自然语言提问。
- Kimi 仅接收通过权限和阈值检查的检索证据。
- 候选回答按句段执行引用检查、原文引文检查、数字一致性检查和独立模型核验。
- 证据不足时明确拒答或提示补充资料，不发布未通过校验的候选句段。
- 通过 SSE 流式展示 `meta`、`token`、`citation`、`done` 和 `error` 事件。
- 支持会话历史、重命名、继续、删除、反馈及 Markdown、DOCX 导出。

### 模板库与报告生成

- 上传 DOCX 模板并解析标题层级、固定文本、占位符和表格结构。
- 支持人工校正字段映射，保存不可变模板版本。
- 模板具备草稿、发布、停用、回收站和恢复状态。
- 报告按章节独立检索、生成和事实核验，每节保留引用及修订历史。
- 支持编辑保存、单节重新生成、候选对比、人工审阅和正式发布。
- 证据风险不会伪装成模型通过；报告所有者完成审阅后可作最终决策。
- 正式版本保存不可变发布快照，可导出 DOCX、PDF 和 Markdown。

### 系统管理与运行监控

- 用户、角色、权限点和知识库授权管理。
- 模型、Embedding、切分、TopK、阈值和任务参数配置。
- 登录、授权、文档、索引、问答、报告及配置变更审计。
- 任务队列、阶段事件、失败原因、取消和重试入口。
- PostgreSQL、MinIO、本地模型、外部模型调用和 worker 心跳状态展示。

## 系统架构

```mermaid
flowchart LR
    U[浏览器] --> V[Vue 3 前端]
    V -->|REST / SSE| A[FastAPI API]
    A --> P[(PostgreSQL 16)]
    A --> M[(MinIO 私有桶)]
    A -->|提交任务| J[(持久任务队列)]
    W[独立 worker] -->|领取任务| J
    W --> P
    W --> M
    W --> B[本地 BGE-M3 / Reranker]
    W --> K[Kimi HTTPS API]
    P --> X[GIN 全文索引 / pgvector HNSW]
```

### 数据分工

| 组件 | 职责 |
| --- | --- |
| PostgreSQL 16 | 用户、权限、知识库、文档元数据、片段、会话、报告、任务和审计数据 |
| pgvector | BGE-M3 语义向量和 HNSW 近邻检索 |
| PostgreSQL GIN | 关键词全文检索候选 |
| MinIO | 文档原件和 DOCX 模板原件 |
| 独立 worker | 文档处理、索引、问答辅助任务和报告任务 |
| 本地 BGE | Embedding 与 Cross-Encoder 重排 |
| Kimi | 受证据约束的正文生成、查询改写和事实核验 |

当前课程部署使用单 API 和单 worker。扩展为多个 worker 前，需要补充任务租约、抢占恢复和并发压测。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vue Router、Ant Design Vue、Vite |
| API | FastAPI、Pydantic、SSE、HTTPX |
| 数据库 | PostgreSQL 16、psycopg、pgvector |
| 对象存储 | MinIO、Boto3、S3 API |
| 检索模型 | BGE-M3、BGE-reranker-v2-m3、PyTorch、Transformers |
| 生成模型 | Kimi OpenAI-compatible API |
| 测试 | Pytest、前端生产构建、接口集成测试 |

## 页面预览

| 工作台 | 知识问答 |
| --- | --- |
| ![工作台](screenshots-ui-v010/01-dashboard.jpg) | ![知识问答](screenshots-ui-v010/04-chat-rag.jpg) |

| 报告编辑 | 运行状态 |
| --- | --- |
| ![报告编辑](screenshots-ui-v010/06-report-editor.jpg) | ![运行状态](screenshots-ui-v010/11-operations.jpg) |

## 目录结构

```text
.
├─ backend/
│  ├─ app/
│  │  ├─ main.py                 FastAPI 接口、鉴权与授权
│  │  ├─ store.py                数据模型、迁移和事务入口
│  │  ├─ db_postgres.py          SQLite 风格接口的 PostgreSQL 适配
│  │  ├─ object_storage.py       本地目录和 S3/MinIO 存储抽象
│  │  ├─ search_index.py         GIN 与 pgvector 索引读写
│  │  ├─ rag.py                  文档处理和混合检索
│  │  ├─ local_models.py         本地 BGE 推理
│  │  ├─ grounding.py            问答逐句证据校验
│  │  ├─ template_docx.py        DOCX 模板结构解析
│  │  ├─ report_workflow.py      报告编辑、审阅、发布与导出
│  │  ├─ jobs.py                 持久任务队列
│  │  └─ worker.py               独立工作进程入口
│  ├─ tests/                     隔离测试
│  ├─ migrate_postgres.py        SQLite 至 PostgreSQL 迁移
│  ├─ migrate_originals.py       本地原件至 MinIO 迁移
│  ├─ rebuild_search_index.py    历史片段索引回填
│  └─ verify_postgres_migration.py
├─ frontend/                     Vue 3 管理端
├─ docs/                         项目文档和升级说明
├─ examples/                     功能验收样例
├─ screenshots-ui-v010/          新版界面截图
├─ compose.yaml                  PostgreSQL 与 MinIO 本地开发环境
├─ start-backend.cmd
├─ start-worker.cmd
└─ start-frontend.cmd
```

## 快速开始

以下步骤面向 Windows 本地开发和课程展示环境。

### 1. 环境要求

- Python 3.12
- Node.js LTS
- pnpm
- Docker Desktop 或兼容的 Docker Compose 环境
- 可访问 Kimi API 的网络
- 可选：NVIDIA GPU。没有 GPU 时本地 BGE 可使用 CPU，但索引和重排速度较慢。

### 2. 安装后端依赖

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-server.txt
```

如需启用本地 BGE-M3 和模型重排，再安装模型依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-models.txt
.\.venv\Scripts\python.exe prepare_models.py
```

模型脚本下载固定版本并校验权重，不会把知识库内容上传至模型仓库。

### 3. 启动 PostgreSQL 和 MinIO

在项目根目录执行：

```powershell
docker compose up -d postgres minio
docker compose ps
```

`compose.yaml` 中的账号和密码只用于本机开发。公网或生产部署必须更换密码并限制网络访问。

### 4. 配置后端

```powershell
cd backend
Copy-Item .env.example .env
```

编辑 `backend/.env`：

```dotenv
KIMI_API_KEY=请填写重新生成的有效密钥
KIMI_BASE_URL=https://api.moonshot.cn/v1
KIMI_MODEL=kimi-k2.6

RAG_DATABASE_URL=postgresql://rag:rag-local-dev@127.0.0.1:5432/rag
RAG_WORKER_MODE=external

RAG_FILE_STORAGE=s3
RAG_S3_ENDPOINT=http://127.0.0.1:9000
RAG_S3_REGION=us-east-1
RAG_S3_BUCKET=rag-originals
RAG_S3_ACCESS_KEY=rag-local-dev
RAG_S3_SECRET_KEY=change-me-local
```

不要提交 `backend/.env`。前端不会读取或返回模型密钥。

首次使用 MinIO 时创建私有桶：

```powershell
.\.venv\Scripts\python.exe init_object_store.py
```

全新 PostgreSQL 数据库会在后端启动时创建表、pgvector 扩展及索引。若需要从旧 SQLite 数据迁移，请严格按照[存储与工作进程升级指南](docs/storage-upgrade.md)操作，不要跳过预检查和迁移验证。

### 5. 启动系统

回到项目根目录，分别打开三个 PowerShell 或 CMD 窗口：

```powershell
.\start-backend.cmd
```

```powershell
.\start-worker.cmd
```

```powershell
.\start-frontend.cmd
```

访问地址：

- 前端：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health
- MinIO 控制台：http://127.0.0.1:9001

如果没有启动 worker，上传、索引和报告任务会保持排队。

文档管理的 PPT/PPTX 原文预览由后端使用 LibreOffice 生成静态幻灯片。请在运行后端的机器上安装 [LibreOffice](https://www.libreoffice.org/download/)。如果安装在自定义位置，可在 `backend/.env` 中用 `RAG_SOFFICE_PATH` 指定 `soffice`（Windows 上为 `soffice.com`）的完整路径。页面上的「下载原文件」仍可获取 PowerPoint 原件。

### 6. 初始账户

| 账号 | 初始密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | 系统管理员 |
| `member` | `admin123` | 项目成员 |
| `teacher` | `admin123` | 指导教师 |

首次登录后应立即修改密码。部署前可通过 `INITIAL_ADMIN_PASSWORD` 设置管理员初始密码。

## SQLite 本地回退模式

代码仍保留 SQLite、本地文件目录和嵌入式任务循环，便于最小化开发验证。清空 `RAG_DATABASE_URL`、设置 `RAG_FILE_STORAGE=local` 和 `RAG_WORKER_MODE=embedded` 即可使用该模式。

该模式不是当前推荐的完整工程部署，不应与 PostgreSQL 模式同时接收业务写入。

## 旧数据迁移

从 SQLite 和本地原件目录升级时，必须先停止 API 与 worker，并完整备份 `backend/data`。迁移顺序为：

1. 初始化 MinIO 私有桶。
2. 预览并迁移文档、模板原件。
3. 执行对象一致性检查。
4. 预览并迁移 SQLite 数据至 PostgreSQL。
5. 重建 GIN 和 pgvector 检索索引。
6. 执行逐表、索引和向量完整性验证。
7. 切换 `.env` 后启动 API 与独立 worker。

完整命令见[存储与工作进程升级指南](docs/storage-upgrade.md)。迁移脚本不会自动删除旧数据库和旧原件。

## 测试

后端测试使用隔离数据、模拟网络和模拟模型，不修改正式数据库：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_quality_v010.py tests/test_retrieval_v08.py tests/test_api.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests/test_storage_upgrade.py tests/test_db_postgres_adapter.py -q -p no:cacheprovider
```

执行完整后端测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

前端构建：

```powershell
cd frontend
pnpm install
pnpm build
```

真实 Kimi、真实 MinIO 和 PostgreSQL 联调可能产生模型费用或写入测试记录，应在隔离知识库或测试环境中执行。

## 安全说明

- 不要把 Kimi 密钥、对象存储密钥或生产数据库口令提交到 Git。
- 曾通过聊天、截图或日志公开的密钥应立即撤销并重新生成。
- 服务端对知识库选择、原文下载、引用查看、问答和报告操作执行二次授权检查。
- 审计日志不保存密码、密钥、完整提示词或模型响应正文。
- MinIO 桶保持私有，原文件访问必须经过服务端权限校验。
- 本地 compose 口令仅用于回环地址开发环境。

## 已知边界

- 模型核验可能误判，不能等同于绝对无幻觉保证，正式内容仍需人工审阅。
- 当前只支持单个独立 worker，多 worker 需要进一步完善任务租约和故障恢复。
- 扫描 PDF 和图片需要显式执行视觉解析；未解析图片不会进入问答证据池。
- PDF 可定位到页和结构化片段，当前不提供版面坐标级像素高亮。
- 复杂 DOCX 图形、动态目录、特殊域和完全一致的分页无法保证无损保留。
- 网页、数据库、企业网盘等通用连接器尚未纳入当前版本。

## 项目文档

- [软件项目计划](docs/基于RAG的知识库管理系统_软件项目计划_V1.0.docx)
- [软件需求规格说明书](docs/基于RAG的知识库管理系统_软件需求规格说明书_V1.0.docx)
- [软件系统设计文档](docs/基于RAG的知识库管理系统_软件系统设计文档_V1.0.docx)
- [软件详细设计文档](docs/基于RAG的知识库管理系统_软件详细设计文档_V1.0.docx)
- [存储与工作进程升级指南](docs/storage-upgrade.md)
- [本地语义检索升级说明](docs/semantic-retrieval-v09.md)
- [中英召回与报告分章检索记录](docs/retrieval-v08.md)
- [第一轮质量改进记录](docs/quality-v010.md)
- [界面与交互改版记录](docs/ui-redesign.md)

## 版本摘要

| 版本 | 主要变化 |
| --- | --- |
| 0.11.0 | PostgreSQL、pgvector、MinIO、独立 worker 和服务端检索索引 |
| 0.10.0 | 原文定位、数字门禁、问题关键点检查和质量改进 |
| 0.9.0 | 本地 BGE-M3 Embedding 与 BGE Reranker |
| 0.8.0 | 中英查询改写、跨语言召回和报告分章检索 |
| 0.7.x | 报告编辑、审阅、发布快照、模板版本和回收站 |

## 使用范围

本项目用于软件工程课程训练、RAG 技术验证和内部知识管理原型。正式生产部署还需要补充多实例并发、备份恢复、密钥管理、TLS、可观测性、容量评估及安全审计。
