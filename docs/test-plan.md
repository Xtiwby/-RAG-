# 知源 RAG v0.13.0 测试计划（测试用例与数据集专项）

- 被测版本：`0.13.0`
- 编写日期：2026-11-16
- 适用范围：RAG 检索、知识问答、模板库、报告生成、UI 与系统管理（含团队权限）
- 交付边界：本计划只新增/整理测试用例与数据集，不修改业务代码、不调整模型与阈值、不宣称真实模型验收通过

## 一、目标

1. 把可复用的测试数据与用例固化到仓库，覆盖多格式接入、知识问答、报告生成、团队权限四类场景。
2. 每条用例写清测试步骤与预期结果，并标注"已自动化 / 待补自动化 / 手工联调"，便于其他成员按 ID 联调。
3. 执行单元测试并留存终端截图与 JUnit 结果，形成测试报告与仍待处理问题清单。

## 二、测试分层与范围

| 层 | 覆盖内容 | 工具 | 是否计入本轮 |
| --- | --- | --- | --- |
| 单元测试 | 引用切分、证据定位、数字守卫、字段映射、权限判定、状态机、PostgreSQL 适配层 | pytest（隔离 SQLite、mock 模型） | 是 |
| 集成测试 | 上传→解析→切分→索引、检索三模式、SSE 问答、报告单节/候选/定稿、审计导出 | pytest + TestClient | 是 |
| 数据集 | 多格式文档集、问答对集、报告模板集、权限矩阵 | 文件 + manifest | 是 |
| 前端 | 账户校验函数 | node --test | 仅记录现有结果 |
| 真实模型联调 | Kimi/BGE/MinIO/PostgreSQL | 手工 | 否（需隔离环境与额度） |

## 三、环境与隔离口径

- 数据：`test_api.py` 顶部设置 `RAG_DATA_DIR` 临时目录、清空 `RAG_DATABASE_URL`、`RAG_FILE_STORAGE=local`、`RAG_WORKER_MODE=embedded`，测试不触碰开发库。
- 模型：`monkeypatch.setattr(rag,'llm_stream',fake_model)` 或 `httpx.MockTransport`，**不发起计费调用**。
- 配置：`configured` fixture 保存/恢复 `system_settings`，用例用 `expected_revision` 乐观锁。
- 依赖：`requirements-server.txt`（`pypdfium2`、`boto3`、`psycopg`）。

## 四、通过标准

1. `pytest tests -q` 全绿，`0 failed / 0 error`。
2. 新增用例全部通过；无法在本轮自动化的项在报告中标注"未覆盖"并给出手工步骤。
3. 报告含终端截图与 JUnit 计数，用例分布与新增明细可核对。
4. 待处理问题按模块列出现象、复现、影响、建议、优先级。

## 五、用例编号与追溯

- `RAG-PARSE-xxx` / `RAG-RET-xxx` → 追溯 `rag-module-spec.md` 的 `RAG-01`~`RAG-06`
- `QA-xxx` → 知识问答
- `TPL-xxx` → 模板库
- `RPT-xxx` → 报告生成
- `ACL-xxx` → 团队权限与系统管理
- 每条用例在 `test-cases.md` 标注对应自动化：`test_xxx.py::test_xxx` 或"手工"

## 六、风险与不覆盖项

- 真实 ASR/视觉/ffmpeg 解码、PostgreSQL+MinIO 联调、前端浏览器自动化不在本轮。
- 扫描 PDF 逐页 OCR、PG 模式下审计导出（`instr`/`julianday`）为已知未覆盖，列入待处理问题。