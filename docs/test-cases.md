# 知源 RAG 测试用例集（v0.13.0）

> 每条用例：ID | 模块 | 优先级 | 前置 | 步骤 | 预期 | 自动化映射
> 自动化映射为"已自动化"时，直接运行对应 pytest 函数；"手工"需按步骤在隔离环境操作。
> **样本口径（v0.13.0 测试专项）**：`tests/data/multiformat-2026-11/` 二进制样本未落盘。表中标「已自动化」且依赖磁盘样本的用例，当前以 `@pytest.mark.skip(reason="样本未落盘")` 占位；标「待补」的用例不受影响（内存构造或独立于样本）。详见该数据集 `README.md` 的「样本状态」一节。

## A. 多格式接入（RAG-PARSE）

| ID | 模块 | 优先级 | 前置 | 测试步骤 | 预期结果 | 自动化映射 |
| --- | --- | --- | --- | --- | --- | --- |
| RAG-PARSE-001 | RAG-02 | P0 | 有维护权限的知识库 | 1. 上传 `资料.md`（含 `# 混合检索` 标题）<br>2. 轮询 `/api/documents/{id}` 至非 uploading/processing<br>3. GET `/api/documents/{id}` | 状态 `ready`；`chunks[0].section=='混合检索'`；`page is None`；`sha256` 与原文字节一致 | 已自动化 `test_api.py::test_original_versions_chunks_search_and_archive` |
| RAG-PARSE-002 | RAG-02 | P0 | 同上 | 构造 2 页 PDF（Alpha/Beta 文本层）上传 | `page_count==2`；chunks 的 `page` 集合为 `{1,2}`；第 2 页含 `Beta` | 已自动化 `test_api.py::test_pdf_pages_docx_sections_and_excel_rows` （测试内构造 PDF，恒跑）；test_multiformat_v013 的磁盘样本项当前 skip|
| RAG-PARSE-003 | RAG-02 | P0 | 同上 | 上传流式 DOCX（Heading1 + 正文） | `chunks[1].section=='技术章节'` 且 `page is None`（不伪造页号） | 已自动化 同上（测试内构造 DOCX） |
| RAG-PARSE-004 | RAG-02 | P0 | 同上 | 上传 XLSX（表头+一行） | `modality=='table'`；`chunks[1].section=='召回指标 / 行 2'`；含 `0.95` | 已自动化 同上（测试内构造 XLSX） |
| RAG-PARSE-005 | RAG-02 | P0 | 同上 | 上传 `损坏文档.docx`（非 zip） | 状态 `parse_failed`；processing 面板错误含"扩展名正确后重新上传" | 已自动化 `test_api.py::test_invalid_docx_explains_reupload_in_processing_panel` |
| RAG-PARSE-006 | RAG-02 | P1 | 同上 | 上传 PPTX（两页，含已删除页标记） | `page_count==2`；`structured[0]` 含"第一页"；不含"已删除幻灯片"；原文件字节一致 | 已自动化 `test_api.py::test_powerpoint_text_slide_order_original_and_pipeline`（测试内构造 PPTX，恒跑）；磁盘样本项 skip |
| RAG-PARSE-007 | RAG-02 | P1 | 同上 | 上传 PPT（miniFAT + Current User 流） | `status=='ready'`；`page_count==2`；`structured[0]` 含"第一页" | 已自动化 `test_api.py::test_ppt_minifat_current_user_stream`（测试内构造 PPT，恒跑）；磁盘样本项 skip |
| RAG-PARSE-008 | RAG-02 | P1 | 同上 | 上传加密 PPT | 422/`parse_failed`，错误可读 | **待补** `test_multiformat_v013.py::test_encrypted_ppt_is_rejected` |
| RAG-PARSE-009 | RAG-02 | P1 | 同上 | 上传 16×16 PNG（未配置视觉识别） | 状态 `needs_extraction`；`can_continue_vision==true`；stages[1].progress==0 | 已自动化 `test_api.py::test_pending_image_can_continue_through_vision_and_index`（测试内构造 PNG，恒跑）；磁盘样本项 skip |
| RAG-PARSE-010 | RAG-02 | P1 | 视觉模型 mock | 1. 视觉返回空 → 任务失败，`can_continue_vision` 仍 true<br>2. 视觉返回 OCR 文本 → 任务完成，`version` 不变，`sha256` 不变，片段含识别文本 | 同左 | 已自动化 同上 |
| RAG-PARSE-011 | RAG-02 | P1 | ffmpeg 可用 | 上传 WAV，配置 ASR mock 返回分段 | `modality=='audio'`；片段章节以 `00:00:00` 开头；`media_meta` 含时长与模型 | 手工/`test_media.py`（上轮已记录） |
| RAG-PARSE-012 | RAG-02 | P1 | 同上 | 上传 MP4，mock 抽音轨 | `media_meta.audio_extracted==true`；至少 2 片段 | 手工/`test_media.py` |
| RAG-PARSE-013 | RAG-02 | P2 | 同上 | 视频帧 OCR 全部失败但转写成功 | 文档 `ready`；片段来自转写；无帧文本但不报错 | **待补** `test_multiformat_v013.py::test_video_frame_ocr_failure_keeps_transcript` |
| RAG-PARSE-014 | RAG-02 | P1 | 上传 HTML（含 script/style/table/alt） | 解析后片段不含 script 内容；标题成章节；表格行按 ` | ` 拼接；alt 保留 | `test_html_text.py`（上轮已记录，本轮纳入回归） |
| RAG-PARSE-015 | RAG-02 | P1 | 上传 GB18030 编码 TXT | 解码成功，片段可检索 | **待补** `test_multiformat_v013.py::test_gb18030_text_is_decoded` |
| RAG-PARSE-016 | RAG-02 | P2 | 上传 JSON | 按 `key: value` 展平为多行片段 | **待补** `test_multiformat_v013.py::test_json_is_flattened` |
| RAG-PARSE-017 | RAG-03 | P0 | 上传后解析失败，再修复 | 阶段事件 `upload=completed, parse=failed, chunk=pending`；重试后 100% 且事件含 attempt 1 failed / attempt 2 index | 已自动化 `test_api.py::test_document_stage_events_failure_retry_and_atomic_index` |
| RAG-PARSE-018 | RAG-03 | P0 | 长文档 600 段 | `compact=true&chunk_id=` 返回 3 片且含目标片段；`limit=101` 返回 422 | 已自动化 `test_api.py::test_document_compact_window_locates_cited_chunk` |

### 附：样本补全脚本

若后续决定落盘 `multiformat-2026-11/corpus/` 样本，按以下步骤：

1. 在 `tests/data/multiformat-2026-11/` 下新建 `build.py`，内容为本文件末尾「附录：build.py 草案」。
2. 执行：

   ```powershell
   cd backend
   .\.venv\Scripts\python.exe ..\tests\data\multiformat-2026-11\build.py
   ```

3. 脚本会生成 11 个 corpus 文件并回填 `manifest.json` 的 `sha256`。
4. 把 `manifest.json` 的 `sample_status` 改为 `"samples-committed"`，并移除每个 corpus 条目的 `"sample": "not-committed"`。
5. 在 `backend/tests/test_multiformat_v013.py` 中取消注释「磁盘样本用例模板」，并把 `_sample()` 的 skip 分支改为直接 `return path.read_bytes()`（或保留 skip 作为兜底，二者皆可）。
6. 重跑 `pytest tests/test_multiformat_v013.py -q`，确认磁盘样本用例由 skip/注释态转为 pass。

## B. 知识问答（QA）

| ID | 模块 | 优先级 | 前置 | 测试步骤 | 预期结果 | 自动化映射 |
| --- | --- | --- | --- | --- | --- | --- |
| QA-001 | RAG-06 | P0 | mock `fake_model` | 提问 → SSE | 事件序列含 `meta/token/verification/done`；`meta.citations[0]` 含 document_id/section/page/chunk_id/version/relevance/sha256；回答以 `[1]` 结尾 | 已自动化 `test_api.py::test_rag_stream_history_citations_export_feedback_and_refusal` |
| QA-002 | 同上 | P0 | 同上 | 续问（短问含"它"） | 检索 query 拼接上一问；会话 4 条消息；导出 DOCX 含"提问 02/回答 02" | 已自动化 同上 |
| QA-003 | 同上 | P0 | 空知识库 | 提问 | 409（继续会话范围不一致）或拒答"证据不足" | 已自动化 同上 |
| QA-004 | 同上 | P0 | 模型密钥为空 | 提问 | 503 | 已自动化 `test_api.py::test_model_failure_and_invalid_citation_do_not_create_fake_success` |
| QA-005 | 同上 | P0 | 模型返回 `[99]` 越界 | 提问 | 事件含 `verification status=rejected`；回答不含未通过内容；含"证据不足" | 已自动化 同上 |
| QA-006 | 同上 | P0 | 模型流中途抛错 | 提问 | 最后事件 `type=error`；消息状态 `error` | 已自动化 同上 |
| QA-007 | 同上 | P0 | 无引用/伪造引文/数字不符/partial | 参数化 | 逐条被拒；`response_text==REFUSAL` 或过滤；`checks` 含 `reason_code` | 已自动化 `test_quality_v010.py` 多例 + `test_api.py::test_grounding_*` |
| QA-008 | 同上 | P0 | judge 服务异常 | 提问 | 不发布候选；`reason` 含"校验服务失败"；`response_text==REFUSAL` | 已自动化 `test_api.py::test_grounding_judge_failure_does_not_publish_raw_answer` |
| QA-009 | 同上 | P0 | `answer_quality.enabled=true` | 缺要点 + 补写通过/不通过 | `coverage.items[1].status=='supplemented'` / 仍 `missing` 且不泄漏 | 已自动化 `test_quality_v010.py::test_missing_core_fact_supplement_*` |
| QA-010 | 同上 | P0 | 流式取消 | `verified_stream` 取消 | 只保留已核验单元；后续不推进 | 已自动化 `test_api.py::test_verified_stream_cancel_retains_only_checked_units` |
| QA-011 | 同上 | P1 | 会话导出含 PDF 引用 | 导出 DOCX | 无 hyperlink/hlinkClick；内嵌 PNG；原页文字高亮；含"引用索引/原版 PDF · 第 2 页" | 已自动化 `test_api.py::test_export_embeds_highlighted_original_page_without_links` |
| QA-012 | 同上 | P1 | 权限撤销后访问会话 | GET 会话/导出 | 403；列表不含该会话 | 已自动化 `test_api.py::test_revoked_scope_and_report_partial_evidence` 前半 |
| QA-013 | 同上 | P1 | citations 被篡改（content 不变） | GET 报告 | `verification.state=='unverified_manual'` | **待补** `test_qa_grounding_extra.py::test_citations_only_tamper_marks_unverified` |
| QA-014 | 同上 | P1 | 跨语言开启、改写超时 | 检索/问答 | `pipeline.query_expansion.status=='degraded'`；结果为空且不返回证据 | 已自动化 `test_retrieval_v08.py::test_rewrite_timeout_falls_back_without_disabling_gates` |
| QA-015 | 同上 | P1 | 改写过程中撤销授权 | 检索/问答 | 403；响应不含证据文本；会话数不变 | 已自动化 `test_retrieval_v08.py::test_revocation_during_rewrite_never_returns_evidence` |
| QA-016 | 同上 | P2 | 多事件循环 | 同一问题两次 expand | 单 loop 缓存命中；多 loop 不命中（记录边界） | 手工（`test_retrieval_v08.py::test_rewrite_cache_scope_and_failure` 已覆盖单 loop） |

## C. 模板库（TPL）

| ID | 模块 | 优先级 | 前置 | 测试步骤 | 预期结果 | 自动化映射 |
| --- | --- | --- | --- | --- | --- | --- |
| TPL-001 | 模板 | P0 | admin | `POST /api/templates/import-docx`（含合并单元格/嵌套表/域/SDT） | `201`；`sha256` 一致；`structure.blocks` 含 2 个 heading；fields 含 6 项；tables=2；warnings 含外部/图片 | 已自动化 `test_reports_v06.py::test_docx_structural_parse_versions_mapping_scope_original` |
| TPL-002 | 同上 | P0 | 同上 | `POST /api/templates/imports/{id}/save`（v1.0） | `201`；`structure` 回读一致；`original` 字节一致；重复保存 `409` | 已自动化 同上 |
| TPL-003 | 同上 | P0 | 同上 | 以 `parent_id` 保存 v2.0 并改 heading 级别 | 子版本 level 变更；父版本不变 | 已自动化 同上 |
| TPL-004 | 同上 | P0 | 同上 | 跨库报告、成员访问 structure/original | 403；授权后 200 | 已自动化 同上 |
| TPL-005 | 同上 | P0 | admin | 坏 zip / 实体 XML / 错误映射 / 重分类 | 422；重分类后可保存 | 已自动化 `test_reports_v06.py::test_docx_bad_zip_xml_entity_wrong_mapping_and_correction` |
| TPL-006 | 生命周期 | P0 | admin | `draft→published` | 200；回读一致；审计 detail 含 `draft` 与 `published` | 已自动化 `test_template_lifecycle.py::test_draft_to_published_ok` / `test_audit_records_old_and_new_state` |
| TPL-007 | 同上 | P0 | admin | 非法转移（含 `deleted` 走 PATCH） | `422`（到 deleted）/`409`（其余）；状态不变 | 已自动化 `test_template_lifecycle.py::test_published_cannot_revert_to_draft` |
| TPL-008 | 同上 | P0 | admin | `deleted` PATCH → `restore` | PATCH 409 含"回收站/恢复"；restore 后 `disabled` | 已自动化 `test_template_lifecycle.py::test_deleted_template_patch_rejected` |
| TPL-009 | 同上 | P0 | admin | 同状态 PATCH | 200；审计不新增 | 已自动化 `test_template_lifecycle.py::test_same_state_is_idempotent` |
| TPL-010 | 同上 | P0 | member 无 `templates.manage` | PATCH lifecycle | 403；状态不变 | 已自动化 `test_template_lifecycle.py::test_permission_denied_without_templates_manage` |
| TPL-011 | 契约 | P0 | disabled/draft/deleted/published 模板 | `POST /api/reports/preflight` | disabled/draft 409；deleted 404；published 200 | 已自动化 `test_template_lifecycle.py::test_report_preflight_rejects_non_published_template` |
| TPL-012 | 模板 | P1 | admin | 自然语言设计（mock 返回 blueprint） | 202 → completed；constraints 覆盖 format；保存后报告 format 生效；DOCX 页边距/字体/行距/页码符合 | 已自动化 `test_api.py::test_natural_template_schema_constraints_and_actual_word_format` |

## D. 报告生成（RPT）

| ID | 模块 | 优先级 | 前置 | 测试步骤 | 预期结果 | 自动化映射 |
| --- | --- | --- | --- | --- | --- | --- |
| RPT-001 | 报告 | P0 | mock `fake_model` | 创建模板 → 创建报告任务 → 轮询 | `progress==100`；`status=='completed'`；2 节均有 citations；内容含"本章来源" | 已自动化 `test_api.py::test_report_real_content_and_export` |
| RPT-002 | 同上 | P0 | 同上 | 编辑第 1 节 → 确认 → 改第 2 节 → 单节重生成 | 第 1 节不变；第 2 节内容被替换；revision 递增；历史保留 | 已自动化 `test_api.py::test_report_draft_revision_confirmation_history_and_export` + `test_single_section_regeneration_preserves_confirmed_chapter` |
| RPT-003 | 同上 | P0 | 同上 | 第 2 节生成中编辑/重生成 | 409；取消后状态 `cancelled`；第 1 节修改保留 | 已自动化 `test_api.py::test_section_busy_cancel_and_other_section_edit_do_not_overwrite` |
| RPT-004 | 同上 | P0 | 同上 | 单节失败 → 重试 → 旧任务重试 | 失败不覆盖正文；重试成功；旧任务重试 409 | 已自动化 `test_api.py::test_section_failed_retry_and_stale_old_retry` |
| RPT-005 | 同上 | P0 | 同上 | 成员读他人报告章节 | 404；本人任务可读 | 已自动化 `test_api.py::test_section_owner_acl_and_member_can_read_finished_job` |
| RPT-006 | 事实核验 | P0 | `mixed_judge` | 报告生成含 supported/contradicted/无引用 | `state='partial'`；accepted=1/rejected=2；正文不含被拒内容；导出 MD/DOCX 不含被拒内容且含"核验记录/原文" | 已自动化 `test_reports_v06.py::test_report_fact_judge_blocks_unsupported_before_events_storage_export` |
| RPT-007 | 同上 | P0 | 参数化 fault | forged_quote/missing_verdict/numbers/no_citation | `state='insufficient'`；正文"证据不足"；不含被拒内容 | 已自动化 `test_reports_v06.py::test_report_judge_fail_closed` |
| RPT-008 | 同上 | P0 | 手工编辑后 verify | verify 完成 | `verified`；revision 递增；历史含 `unverified_manual`；citations/content 篡改回 `unverified_manual` | 已自动化 `test_reports_v06.py::test_manual_verify_hash_invalidation_history_acl_and_regenerate` |
| RPT-009 | 同上 | P0 | legacy verification='{}' | GET 报告 / 导出 | `state=='legacy'`；导出含"历史章节未执行事实支撑核验" | 已自动化 `test_reports_v06.py::test_legacy_reports_not_retroactively_verified_and_no_evidence` |
| RPT-010 | 同上 | P0 | 空证据库 | 生成报告 | `needs_review`；`state='insufficient'` | 已自动化 同上 |
| RPT-011 | 定稿 | P1 | 报告含未审阅节 | `POST /api/reports/{tid}/finalize` | 409 + issues 列表 | **待补** `test_report_v013.py::test_finalize_blocked_by_unreviewed_sections` |
| RPT-012 | 定稿 | P1 | 全部审阅 + `fixed_text_reviewed=false` | finalize | 422 | **待补** `test_report_v013.py::test_finalize_requires_fixed_text_review` |
| RPT-013 | 定稿 | P1 | 定稿后导出 draft/final | `export?mode=draft|final` | final 有 release 200；无 release 409；draft 含"草稿"文件名 | **待补** `test_report_v013.py::test_final_export_requires_release` |
| RPT-014 | 候选 | P1 | preview 生成候选 | accept/discard | accept 覆盖正文并 revision+1；discard 不变；旧候选冲突 409 | **待补** `test_report_v013.py::test_candidate_accept_conflict` |
| RPT-015 | 质量门 | P1 | 部分节未审阅 | `quality()` | `issues` 含 `review`；`human_accepted_sections` 计数正确 | **待补** `test_report_v013.py::test_quality_counts_human_accepted` |
| RPT-016 | 跨语言分章 | P1 | 报告两章（UploadFile / OCR 边界） | preflight + regenerate | 第 2 章 evidence 含 optical 文档；focused query 不含 "UploadFile"；regenerate 查询与 preflight 一致 | 已自动化 `test_retrieval_v08.py::test_report_preflight_and_regeneration_share_section_focus` |

## E. 团队权限与系统管理（ACL）

| ID | 模块 | 优先级 | 前置 | 测试步骤 | 预期结果 | 自动化映射 |
| --- | --- | --- | --- | --- | --- | --- |
| ACL-001 | 权限 | P0 | member 无授权 | 访问 users/audits/document/original | 403；KB 列表空；search 403；dashboard documents=0 | 已自动化 `test_api.py::test_auth_acl_roles_and_revocation` |
| ACL-002 | 同上 | P0 | 授予 read | 访问文档/original；上传/删除 | 读 200；写 403 | 已自动化 同上 |
| ACL-003 | 同上 | P0 | 角色权限点变更 | 现有会话 | 变更后旧会话 401；重新登录后按新权限 403 | 已自动化 同上 |
| ACL-004 | 同上 | P0 | public 库 | 成员读/写 | 读 200；写 403；search 200 | 已自动化 `test_api.py::test_public_library_is_readable_without_grants` |
| ACL-005 | 同上 | P0 | team 库成员读写 | 成员管理 | 仅 admin 可管理；reader 读、writer 写；移除后 403 | 已自动化 `test_api.py::test_team_library_members_control_access` |
| ACL-006 | 同上 | P0 | 委托授权助理 | 修改他人授权 | 不能扩大本人范围；public 不扩大 | 已自动化 `test_api.py::test_public_read_access_does_not_expand_delegated_grants` |
| ACL-007 | 同上 | P0 | 本人密码/重置 | 改密/重置 | 旧会话失效；临时密码可登录；不能停用唯一管理员 409 | 已自动化 `test_api.py::test_profile_password_and_reset` |
| ACL-008 | 同上 | P0 | 知识库编辑/停用/归档 | 各状态检索/上传/会话 | 非 admin 403；停用后 409；历史会话导出 200 | 已自动化 `test_api.py::test_kb_admin_edit_disable_archive_and_historical_scope` |
| ACL-009 | 审计 | P0 | 合成审计 | 组合过滤/分页/时间非法 | total 正确；分页正确；非法时间 422；密码不入日志 | 已自动化 `test_api.py::test_audit_composite_filters_pagination_time_and_sensitive_redaction` |
| ACL-010 | 审计 | P0 | 导出 CSV/JSON | 注入防护/权限/超限 | BOM；`'=SUM`；`no-store`；非授权 403；超限 422 | 已自动化 `test_api.py::test_audit_export_filters_all_pages_csv_safety_and_permissions` |
| ACL-011 | 审计 | P1 | PostgreSQL 模式 | 导出 | 当前 `instr/julianday` 不兼容，预期失败（记录为待处理） | **待补** `test_pg_sqlite_parity.py::test_audit_export_postgres_incompatible`（`skipif` 非 PG） |
| ACL-012 | 配置 | P0 | admin | 保存配置/乐观锁/非法值/重建索引 | 409/422；重建后版本 v2.0；旧片段 ID 不变 | 已自动化 `test_api.py::test_settings_validation_real_effect_and_reindex_version` |
| ACL-013 | 队列 | P0 | 并发=1 | 排队/取消/重试/坏 JSON/权限 | 排队；取消；重试 attempts=2；非法 JSON failed；非本人 403 | 已自动化 `test_api.py::test_queue_wait_cancel_retry_and_bad_model_json` |
| ACL-014 | 运行 | P1 | admin | probe + operations | probe 200；storage/queue/model_calls 有效；不含密钥 | 已自动化 `test_api.py::test_model_probe_telemetry_and_storage_are_measured` |
| ACL-015 | 权限矩阵 | P0 | 角色×可见性×归属 | 参数化 | 与 `permissions.json` 期望一致 | **待补** `test_acl_matrix.py`（见第 3 节数据集） |

## F. 数据集用例（DS）

| ID | 数据集 | 用途 | 校验方式 |
| --- | --- | --- | --- |
| DS-001 | `web-2026-10-31`（32 题 + 8 核验 + 2 OCR + 3 报告） | 检索/问答/核验/OCR/报告 | 按 `results_template.csv` 记录；`manifest.json` 哈希校验 |
| DS-002 | `quality-v010-synthetic`（100 题，80 可答/20 不可答） | 质量门禁回归 | `labels/questions.jsonl` 的 `required_facts/required_numbers`；不把标签入库 |
| DS-003 | `multiformat-2026-11`（本轮新增） | 多格式接入边界 | `expected.json` 逐文件期望；见第 3 节 |
| DS-004 | `permissions.json`（本轮新增） | 权限矩阵 | 参数化用例 `test_acl_matrix.py` |

> 说明：`RAG-PARSE-008/013/015/016`、`QA-013/016`、`RPT-011~015`、`ACL-011/015` 为本轮**待补自动化**，对应新文件见第 4 节；其余为已自动化，直接运行现有 `test_*.py`。