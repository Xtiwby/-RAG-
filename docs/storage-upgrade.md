# 存储、数据库、检索索引与独立工作进程升级

下文在 backend 目录执行的 `python`，Windows 上可替换为 `.\.venv\Scripts\python.exe`。此部署路径面向本地或内网的单工作进程。默认配置仍是 SQLite + 本地原件 + 嵌入式任务循环，不会自动搬迁或删除已有数据。

## Windows 本地联调命令

先停止旧 API 和 worker，确认 `http://127.0.0.1:8000/api/health` 已无法连接。项目根目录的 `compose.yaml` 仅使用本机回环地址与开发口令。以下命令在 `backend` 目录的同一个 PowerShell 窗口执行；执行失败时停止，不要继续下一阶段。

```powershell
$env:RAG_FILE_STORAGE = 's3'
$env:RAG_S3_ENDPOINT = 'http://127.0.0.1:9000'
$env:RAG_S3_REGION = 'us-east-1'
$env:RAG_S3_BUCKET = 'rag-originals'
$env:RAG_S3_ACCESS_KEY = 'rag-local-dev'
$env:RAG_S3_SECRET_KEY = 'change-me-local'
.\.venv\Scripts\python.exe init_object_store.py
.\.venv\Scripts\python.exe migrate_originals.py
.\.venv\Scripts\python.exe migrate_originals.py --apply
.\.venv\Scripts\python.exe check_object_consistency.py
$env:RAG_DATABASE_URL = 'postgresql://rag:rag-local-dev@127.0.0.1:5432/rag'
.\.venv\Scripts\python.exe migrate_postgres.py --source data/rag_knowledge.db
.\.venv\Scripts\python.exe migrate_postgres.py --source data/rag_knowledge.db --apply
.\.venv\Scripts\python.exe rebuild_search_index.py
.\.venv\Scripts\python.exe verify_postgres_migration.py
```

以上命令在当前窗口设置临时环境变量。所有检查通过后，才将相同的 `RAG_*` 值写入现有 `backend/.env`，并增加 `RAG_WORKER_MODE=external`。然后分别启动 `start-backend.cmd` 和 `start-worker.cmd`。此开发口令只用于本地联调；对外部署前应更换。

## 1. 对象存储

1. 停止现有后端与任务工作进程，复制备份 backend/data/（数据库和 uploads 目录必须一起备份）。
2. 在项目根目录运行 docker compose up -d postgres minio；compose.yaml 中的账号仅用于本地开发，部署前应改成独立强凭据。
3. 在 backend/.venv 安装 requirements-server.txt。复制 backend/.env.example 的新增项目到已有 backend/.env，不覆盖原有模型配置。先仅设置 RAG_FILE_STORAGE=s3、RAG_S3_ENDPOINT=http://127.0.0.1:9000、RAG_S3_BUCKET=rag-originals、RAG_S3_ACCESS_KEY 与 RAG_S3_SECRET_KEY；暂不设置 RAG_DATABASE_URL。
4. 运行 python init_object_store.py 创建私有桶。先运行 python migrate_originals.py 查看待迁移数量，再运行 python migrate_originals.py --apply。脚本核对旧文件 SHA-256、上传后核对对象大小和 SHA-256 元数据，更新数据库指针；旧目录文件仍保留。运行 python check_object_consistency.py 检查缺失和未引用对象，并对原文下载、模板下载、视觉解析和重新索引做抽样检查。
5. 新上传走对象存储，旧目录文件仍可读取。文档原件、模板原件都保存桶、对象键、对象版本 ID 和 SHA-256；引用所用片段与业务版本继续存在数据库。

## 2. 服务端数据库

1. 确认 PostgreSQL 服务和 pgvector 扩展可用；在 backend/.env 设置 RAG_DATABASE_URL=postgresql://rag:<password>@127.0.0.1:5432/rag。迁移脚本会创建目标表和演示种子，并要求目标库没有真实业务数据。
2. 保持 API 和 worker 停止。运行 python migrate_postgres.py --source data/rag_knowledge.db 预览行数，再运行 python migrate_postgres.py --source data/rag_knowledge.db --apply。脚本仅对空目标库执行，逐表比对行数，在一个事务内复制并校准序列。
3. 迁移后不要删除旧 SQLite 文件。检查用户登录、知识库授权、历史引用、模板和报告版本。新库接受业务写入前，可去掉 RAG_DATABASE_URL 回到旧库；上线后若要回退，必须先停写并回迁新数据。切换期间禁止两套库同时接收写入。

## 3. 检索索引

PostgreSQL 模式创建 chunk_search：GIN 全文索引保存现有中英分词后的词项，pgvector HNSW 保存 BGE-M3 的 1024 维向量。运行 python rebuild_search_index.py 回填历史片段。新文档索引提交会同事务更新 chunk_search。查询先从数据库索引取候选，再沿用现有的授权检查、版本过滤、BM25/RRF、模型重排和引用绑定。

索引不完整时退回原应用层扫描；未启用本地 BGE-M3 时，向量/混合模式仍由现有实现计算，关键词模式可使用全文索引。索引模式先从授权知识库读取少量语言样本，再通过 GIN/HNSW 取候选 ID，最后读取候选片段；索引未完成或 Embedding 配置不兼容时回退到原应用层扫描。

## 4. 独立工作进程

设置 RAG_WORKER_MODE=external，再分别运行 start-backend.cmd 和 start-worker.cmd。API 只提交数据库任务；worker 独立领取、处理和更新心跳。当前设计只支持一个 worker 进程；多 worker 扩展还需要任务租约和恢复策略。运行状态页读取持久心跳和数据库任务状态。若不启动 worker，上传和报告任务会保持排队。

## 验收

先在 backend 目录执行 `python -m pytest tests/test_storage_upgrade.py -q` 和 `python -m pytest tests -q`；对象存储测试使用内存模拟，不调用云服务。随后在已启动的 PostgreSQL、MinIO 和 worker 上执行以下联调：

- 上传 PDF、PPTX、图片、DOCX 模板，下载原文；确认文档的桶/对象键/版本/哈希保存，旧文件可继续访问。
- 停止并重启 API 与 worker；确认已排队任务继续处理，失败任务可重试，取消请求会被 worker 发现。
- 对关键词、向量、混合检索分别核对授权范围、最新版本、引用原件定位和结果；观察 retrieval 元数据的 search_index 字段。
- 比对迁移前后表行数、用户权限、会话、报告发布快照，做一次完整备份恢复演练。

本地 compose 的口令不可用于公网；生产环境需要单独配置对象桶权限、传输加密、备份和监控。不要把 backend/.env 提交到 Git。
