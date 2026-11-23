# 知识库多模态与数据源接入（0.12.0）

日期：2026年11月15日。负责人：胡艺镭。此文区分已实施、验证结果与未做的范围，不代表全部规划完成。

## 已实施

1. **语音接入。** 支持 `.mp3`、`.m4a`、`.wav`、`.ogg`、`.flac`、`.aac`，`modality='audio'`。转写走 OpenAI 兼容 `POST {base_url}/audio/transcriptions`，请求 `response_format=verbose_json`，按返回的 `segments` 生成 `00:01:23 – 00:02:10` 形式的章节名，每个分段一个解析单元；没有 `segments` 时整段作为一个单元。WAV 时长用标准库 `wave` 读取，其余格式用 ffprobe 或 ffmpeg 探测。超过 `media.max_duration_seconds`（默认 7200 秒）直接失败并提示分段，不静默截断。
2. **视频接入。** 支持 `.mp4`、`.mov`、`.mkv`、`.webm`、`.avi`，先用 ffmpeg 抽出 16 kHz 单声道 mp3 音轨，再走同一转写链路。`media.frame_ocr` 打开时按 `frame_interval_seconds` 抽样至多 `max_frames` 张画面，逐帧走 OCR 并以 `视频画面 00:03:00` 作章节名；单帧识别失败不丢弃整份转写。画面 OCR 默认关闭，因为每帧一次视觉调用。
3. **触发方式。** 本地或云端 ASR 已就绪且 `media.auto_transcribe=true` 时上传后自动转写；否则状态为 `needs_extraction`，处理面板出现「开始语音转写」，对应 `POST /api/documents/{did}/transcribe`。提供方未就绪时返回 `503` 并给出模型准备或云端配置提示。
4. **HTML 补齐。** 新增 `html_text.py`，用标准库 `HTMLParser` 替换原来的正则剥标签：`script`/`style`/`noscript`/`template`/`svg` 整棵丢弃，`h1..h6` 成为章节名，表格行按 ` | ` 拼接，列表项与段落各自成段，`img@alt` 保留，`title` 作为无正文时的兜底。编码按 `<meta charset>`、UTF-8、GB18030 依次回退，总字符数上限 200 万。`.htm` 与 `.html` 同等处理。
5. **图片 OCR 补齐。** 新增 `ocr.py`：先按 EXIF 方向校正并统一转成 PNG（长边超过 4000 降采样，像素超过 4000 万返回 `413`），再交给 `vision`（Kimi 视觉，默认）或 `local`（`rapidocr-onnxruntime`，未安装返回 `503`）引擎。支持的图片格式在 PNG/JPEG/WebP 之外新增 BMP/TIFF/GIF。空结果仍按 `needs_extraction` 处理，不产生空索引的「已就绪」。
6. **数据源接入。** 补齐 `data_sources.py` 的路由与导入：`GET /api/data-sources` 只返回 `{id,label,kind}`，`POST /api/data-sources/import` 下载快照、校验扩展名、计算 SHA-256、落对象存储、创建 `ingest` 任务并返回 `201`，与上传共用四个阶段和同名版本规则。新增 `url_env`/`dsn_env`/`auth_env` 三个字段，配置文件中只写环境变量名；`object` 类型支持 `s3://bucket/key` 直取；`web`/`api`/`drive` 走 HTTP，不跟随重定向（返回 `502` 而不是把凭据转给别的地址）。
7. **旧版 Office。** `.doc`/`.xls` 先经 LibreOffice 转 `.docx`/`.xlsx` 再解析，缺少 `soffice` 时返回可读的 `422`，而不是 500。该能力原本已由 `tests/test_data_sources.py` 写明，本次补齐实现。
8. **配置与界面。** 运行时配置新增 `asr`、`ocr`、`media` 三段（缺省自动补齐，旧配置可直接加载）；系统配置页新增「多模态与语音转写」卡片；新增 `/data-sources` 页面与导航入口；上传 accept 与帮助文案同步；文档页对音频/视频提供播放器，并按引用章节的时间戳定位播放位置；仪表盘内容分布扩展到五类。

## 配置

| 环境变量 | 作用 |
| --- | --- |
| `RAG_ASR_PROVIDER` | `local`（默认）、`api` 或 `disabled` |
| `RAG_ASR_MODEL` / `RAG_ASR_DEVICE` | 本地 faster-whisper 模型与推理设备；默认 `small` / `auto` |
| `RAG_ASR_BASE_URL` / `RAG_ASR_API_KEY` | 仅云端模式使用；密钥只在 `.env`，界面不读取 |
| `RAG_MEDIA_AUTO_TRANSCRIBE` | `1` 上传后自动转写，`0` 保留手动入口 |
| `RAG_VIDEO_FRAME_OCR` | `1` 打开视频抽帧 OCR，默认关 |
| `RAG_OCR_PROVIDER` | `vision`（默认）或 `local` |
| `RAG_FFMPEG_PATH` | ffmpeg 不在 PATH 时的绝对路径 |
| `RAG_DATA_SOURCES_FILE` | 数据源 JSON 配置路径 |

数据源配置文件示例：

```json
[
  {"id": "intranet", "label": "内网规范", "kind": "web", "knowledge_base_id": 3,
   "filename": "规范.html", "url_env": "RAG_SOURCE_INTRANET_URL"},
  {"id": "ledger", "label": "台账", "kind": "database", "knowledge_base_id": 3,
   "filename": "台账.csv", "sqlite_path": "D:/data/ledger.sqlite",
   "query": "SELECT id, title FROM entries LIMIT 5000"}
]
```

## 验证记录

- 后端全量测试：181 项通过（`pytest tests -q`，0 失败、0 错误、0 跳过）。测试范围、用例分布、判定标准与待补充的执行记录见 [0.12.0 单元测试与回归报告](testing/v0.12-test-report.md)。其中 `tests/test_data_sources.py` 的 6 项（HTML 去脚本、旧版 Office、五类数据源导入、跨库 404、重定向 502、同名 `v2.0`）在本次实现前是红灯。
- 新增 `tests/test_html_text.py`（7 项）、`tests/test_media.py`（8 项）：覆盖脚本剥离、表格/列表/alt、GB18030、截断、JSON 展平、WAV 转写落库、未配置时 `needs_extraction` 与 `503`、视频抽音轨、缺 ffmpeg 的可读报错、超长拒绝、BMP 转 PNG 后 OCR、本地 OCR 缺依赖、时长解析。
- 前端：`pnpm build` 通过（Vite 6.4.5）。
- 未验证：真实 ASR 服务调用、真实 ffmpeg 抽轨与抽帧、真实 RapidOCR 识别。这些需要实际服务/依赖，本机当前没有 ffmpeg，测试均以替换函数或跳过方式覆盖。真实调用会产生费用与耗时，应在隔离知识库中执行。

## 已知边界

- 扫描版 PDF 仍未接入逐页 OCR，仍按「转为逐页图片上传」提示；原因是逐页视觉调用的成本与失败重试策略还没有定。
- 对象存储只支持单对象（含 `s3://` 直取），不支持前缀批量导入；网盘只支持 HTTP/WebDAV 直链，未接入 OAuth 授权流。
- 不跟随重定向、不封禁内网地址：数据源配置文件属于部署方信任边界，等同服务器凭据，不能交给非受信人员编辑。
- 视频画面 OCR 默认关闭；开启后费用随帧数线性增长。
- 音频/视频引用没有原版页面级高亮，只有时间戳章节名与播放器定位。
- 转写质量取决于所选 ASR 服务和音频质量，本系统不宣称逐字准确。
