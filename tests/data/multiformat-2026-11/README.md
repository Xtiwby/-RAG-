# 多格式接入测试集 multiformat-2026-11

## 目录

    multiformat-2026-11/
    ├─ manifest.json          # 数据集注册与哈希
    ├─ expected.json          # 每个文件的解析期望
    ├─ corpus/                # 可入库样本（上传用）
    │  ├─ md/                 simple.md
    │  ├─ docx/               chapter.docx
    │  ├─ pdf/                pages.pdf
    │  ├─ xlsx/               evaluation.xlsx
    │  ├─ pptx/               demo.pptx
    │  ├─ ppt/                demo.ppt
    │  ├─ html/               page.html
    │  ├─ txt/                gb18030.txt
    │  ├─ json/               data.json
    │  ├─ csv/                table.csv
    │  ├─ image/              vision.png
    │  ├─ audio/              sample.wav   # 需 ASR mock，默认不参与基线
    │  └─ video/              sample.mp4   # 需 ffmpeg + ASR mock
    └─ broken/                # 故意损坏样本（仅断言 parse_failed，不入库）
       ├─ invalid.docx
       ├─ encrypted.ppt
       └─ entity.docx

## 约定

- `corpus/` 下文件才可上传；`broken/` 只用于断言 `parse_failed`。
- 每个文件在 `expected.json` 有对应条目：
  `{ "path": "...", "status": "ready|parse_failed|needs_extraction", "page_count": n, "sections": [...], "contains": [...], "sha256": "..." }`
- 不包含真实项目数据、密钥或第三方受限全文；第三方资料仅短摘编并保留 license 说明。
- 与 `web-2026-10-31` 的 `manifest.json` 字段风格保持一致（`dataset_id/created_at/sources/...`）。
- 合成样本（HTML/JSON/GB18030/加密 PPT/帧 OCR 失败）以最小可复现为准，不追求真实业务语义。

## 样本状态

本轮（v0.12.0 测试专项）**只建目录与约定，不落盘二进制样本**。原因：PDF/DOCX/PPTX/XLSX/图片样本需要脚本或人工生成，时间成本高于本轮收益；可内存构造的边界用例（加密 PPT、GB18030 字符串、JSON 展平、非法 JSON）已由 `backend/tests/test_multiformat_v013.py` 覆盖，不依赖磁盘文件。

| 类别 | 路径 | 本轮状态 | 对应用例 | 执行方式 |
| --- | --- | --- | --- | --- |
| 可内存构造 | `broken/*`（加密 PPT、DTD 实体 DOCX、非法 DOCX） | 不落盘，测试代码内构造 | `test_multiformat_v013.py::test_encrypted_ppt_is_rejected`、`test_reports_v06.py::test_docx_bad_zip_xml_entity_wrong_mapping_and_correction` | 自动化，恒跑 |
| 字符串类 | `corpus/txt/gb18030.txt`、`corpus/json/data.json`、`corpus/csv/table.csv` | 不落盘，测试内构造字节 | `test_multiformat_v013.py::test_gb18030_text_is_decoded`、`::test_json_is_flattened`、`::test_invalid_json_reports_readable_error` | 自动化，恒跑 |
| 二进制类 | `corpus/pdf/pages.pdf`、`corpus/docx/chapter.docx`、`corpus/xlsx/evaluation.xlsx`、`corpus/pptx/demo.pptx`、`corpus/ppt/demo.ppt`、`corpus/image/vision.png` | **未落盘** | `test_multiformat_v013.py` 中对应用例、`test-cases.md` RAG-PARSE-002/003/004/006/007/009 | `@pytest.mark.skip(reason="样本未落盘，见 README 样本状态")` 或手工 |
| 音视频 | `corpus/audio/sample.wav`、`corpus/video/sample.mp4` | 未落盘（本来 `include_in_baseline:false`） | 手工 | 手工，需 ffmpeg + ASR mock |

**如何补样本**：见 `docs/test-cases.md` 末尾「附：样本补全脚本」一节，或按 `manifest.json` 的 `path` 逐项生成后回填 `sha256`。补全后把 `sample_status` 改为 `"samples-committed"`、`sha256` 填真实值、移除对应 `skip` 标记。