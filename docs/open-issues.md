# 仍待处理问题（v0.12.0 测试专项）

> **2026-11-19 实机验收更新**：以下编号保留为历史记录，最新结论见[遗留问题修复与实机验收](testing/remaining-2026-11-19.md)。
>
> 1、2、5、9、10、11、12、13 的实现与回归问题已处理；扫描 PDF、PostgreSQL、MinIO、真实音视频解码和离线转写均有实机结果。第 3、4、6 项已接入真实语义模型与神经重排，公开评测三模式均覆盖全部必要来源，并单独核验生成答案。第 8 项真实模型 token 已记录，当前实际配置模型为 deepseek-flash。
>
> 第 7 项已完成真实 DOCX → LibreOffice PDF 与渲染检查；Microsoft Word 客户端的复杂排版仍未验收。模型准确性和生产规模长时间负载的适用范围详见最新报告，不将来源命中率当成答案正确率。

> 2026-11-19 第一轮历史状态：以下原始清单保留用于追溯，当时结论见[首轮诊断报告](testing/diagnosis-2026-11-19.md)。
>
> - 第 2 项 PG 审计 SQL 已修复并补合同测试，真实 PG 集成待环境。
> - 第 9 项已验证：报告模块独立运行 9 项通过。
> - 第 11 项已补充 409 后章节状态不变断言并通过。
> - 第 12 项已修复引用变化的状态判定，独立用例通过。
> - 第 13 项的视频帧 OCR 失败测试已改为真实处理逻辑、严格断言转写保留；S3 Range、多 loop 与真实解码边界仍待验收。
> - 第 3/4 项已修复本机 Python 3.10 查询改写失效及问答本地拆分未接入，模拟回归通过，真实数据集质量仍未复测。
> - 第 8 项历史描述需结合当前已有 telemetry 与 model_calls 审查，本轮未进行真实模型 token 账单核对。

## 一、继承自 v0.12.0 报告

1. **RAG / 扫描 PDF** | 扫描版 PDF 无逐页 OCR，仅提示"转为逐页图片上传" | 上传扫描 PDF → `needs_extraction` | 长文档验收体验差 | 接入逐页 OCR 或批量图片入口 | P1 | RAG 模块
2. **审计 / PG** | `audit_filters` 用 `instr`/`julianday`，PG 模式导出失败 | PG 模式 `GET /api/audits/export` | 正式部署审计导出不可用 | 抽象时间/模糊匹配为适配层 | P1 | 系统管理

## 二、继承自 web-live-2026-11-01

3. **RAG / 跨语言** | 中文问英文资料召回不足（Q001/Q002/Q003/Q004/Q005/Q024） | 用 `web-2026-10-31` 三模式检索 | 跨语言验收不达标 | 接入双语语义 Embedding；保留 BM25；用外置标签集评测 | P1 | RAG
4. **报告 / 分章** | 跨文档问答与分章检索不完整（Q025、报告 #13） | 复合问题 + 报告预检 | 回答不完整 | 分解子问题、保证子问题来源覆盖、显示缺失主题 | P1 | 报告
5. **接入 / 重复上传** | 同 SHA-256 文件直接生成 v1.0/v2.0，无交互 | 连续上传同一文件 | 用户困惑、版本膨胀 | 返回重复提示与取消/覆盖/新版本选项 | P1 | 接入
6. **检索 / 重排** | 规则重排使 31/32 题三模式前 5 一致 | 关闭/开启 rerank 对照 | 模式差异弱 | 保留路由/RRF 可解释性，评估重排权重或语义重排器 | P2 | RAG
7. **导出 / 核验** | 标题/无引用说明/句段格式被核验拦截；PDF 英文混排不自然；DOCX 未完成 Word 版式验收 | 报告导出 | 完整性损失、版式待验 | 区分格式性拦截与事实矛盾；补 DOCX 渲染环境 | P2 | 报告
8. **遥测 / token** | 模型 token 未记录，运行状态不能宣称零消耗 | `GET /api/operations` | 费用不可见 | 接入 usage 记录并展示"未知"而非零 | P2 | 系统管理

## 三、本轮测试视角新增

9. **测试基建** | `test_reports_v06.py` 依赖 `test_api.generated_report`/`section_path`，需确认二者在 `test_api.py` 内且可独立导入 | 单独运行 `pytest tests/test_reports_v06.py` | 若缺失则该文件无法独立跑 | 在 `test_api.py` 显式定义并加注释；或抽到 `tests/support.py` | P0 | 测试
10. **报告 / 核验路径** | `admin_features.report_handler` legacy 分支走 `report_grounding.generate`（不分批），`workflow` 分支走 `generate_section`（8 句/批），语义不一致 | 对比两分支生成同一节 | 核验结果可能不同 | 统一分批策略或显式标注差异 | P1 | 报告
11. **队列 / 旧任务重试** | `jobs.retry` 对 `report_section` 要求 `section.job_id==row.id`，旧任务重试被拒后章节状态缺显式断言 | 生成中取消→旧任务 retry | 状态可能残留 `queued` | 补 `test_report_v013.py` 断言"409 后章节状态不变" | P2 | 测试/队列
12. **报告 / citations 篡改** | `report_grounding.current` 在仅 citations 变、content 不变时回 `unverified_manual`，现有用例与 content 篡改合并 | 仅改 citations | 断言粒度不足 | 已补 `test_qa_grounding_extra.py::test_citations_only_tamper_marks_unverified` | P2 | 测试
13. **多模态 / 边界** | 加密 PPT、帧 OCR 全失败、多 loop 查询改写缓存、S3 Range 下载未校验哈希 | 见 `test_multiformat_v013.py`、`test_retrieval_v08.py` | 边界未覆盖 | 已补加密 PPT/帧 OCR 用例；多 loop 与 S3 Range 列为手工/已知边界 | P2 | 测试/接入
