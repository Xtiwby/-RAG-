# 网络资料小型测试集 · web-2026-10-31

为当前RAG知识库项目收集的公开技术资料。仅本地整理，未自动导入知识库，未调用Kimi、Embedding或OCR服务，未修改应用代码、数据库和配置。

## 包含什么

- corpus/：10份可入库资料，含1份原始PDF、1份净化HTML、7份Markdown、1份TXT。
- scenarios/ocr/：从原论文前两页渲染的2张PNG，作为可选OCR输入；不是新增独立资料，也未做OCR。
- scenarios/duplicate/：与S03字节完全一致的副本，供哈希去重测试；不要加入正常检索基线。
- labels/qa.jsonl：32道问答，25道可回答、7道资料不足/敏感信息拒答；附参考答案、来源ID和证据短引文。
- labels/grounding.jsonl：8条事实核验陈述，含supported、contradicted、insufficient三类人工参考标签。
- labels/ocr_cases.json：2个图像识别参考用例。
- labels/report_cases.json：3个报告生成测试需求，附来源范围、章节建议与核查点。
- labels/results_template.csv：32题×3种检索模式，共96行空白记录模板；没有伪造任何测试成绩。
- manifest.json、sources.csv：来源、作者/机构、URL、采集时间、转换说明、许可、大小和SHA-256。
- references/raw/、licenses/：公开原始快照及许可依据，用于核对，不要入库。
- validation/：原PDF逐页文本、证据出现检查及当前项目解析器的离线检查结果，不要入库。

## 资料清单

| ID | 资料 | 来源与性质 | 建议分组 |
| --- | --- | --- | --- |
| S01 | Dense Passage Retrieval for Open-Domain Question Answering | [ACL原论文](https://aclanthology.org/2020.emnlp-main.550/)，2020年，13物理页；原PDF未修改 | 检索技术 |
| S02 | SQLite FTS5 Extension | [SQLite官方文档](https://www.sqlite.org/fts5.html)，公共领域；清理脚本、样式和交互元素后的HTML | 检索技术 |
| S03 | FastAPI请求文件 | [官方中文教程](https://fastapi.tiangolo.com/zh/tutorial/request-files/)，MIT；保留说明文字，移除独立代码示例和引用标记 | 后端与接入 |
| S04 | FastAPI OAuth2与JWT | [官方中文教程](https://fastapi.tiangolo.com/zh/tutorial/security/oauth2-jwt/)，MIT；同上，示例不代表本项目机制 | 后端与接入 |
| S05 | Vue 3简介 | [Vue官方中文文档](https://cn.vuejs.org/guide/introduction)，CC BY 4.0；文本整理，不包含原站图片文件 | 前端 |
| S06 | Vue 3响应式基础 | [Vue官方中文文档](https://cn.vuejs.org/guide/essentials/reactivity-fundamentals)，CC BY 4.0；官方网页正文转Markdown | 前端 |
| S07 | RRF排名融合 | [Elastic官方说明](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)的简短中文事实摘编，非全文 | 检索技术 |
| S08 | RAG论文摘要 | [Lewis等2020年论文摘要](https://arxiv.org/abs/2005.11401)的简短中文事实摘编，非全文 | 检索技术 |
| S09 | TF-IDF特征表示 | [scikit-learn官方API文档](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)的简短中文事实摘编，非全文 | 检索技术 |
| S10 | PDF与OCR | [pypdf官方文档](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)的简短中文事实摘编，非全文 | 后端与接入 |

采集日期不是文档发布日期或软件版本。来源网页后续可能变化，以本包快照及SHA-256为准。S06使用官方站点HTML作为备用下载地址，实际地址记录在manifest的download中；没有用失败下载冒充原文。四份摘编仅保留短事实，相关权利仍归原权利人，不将其标为MIT或公共领域。

## 如何用于当前系统

1. 解压后，只上传corpus目录中的10个文件；系统不接受ZIP作为知识文档。不要上传labels、validation、references、许可证或本说明，避免参考答案泄漏。
2. 建议创建三个独立测试知识库，分别放入“检索技术”“后端与接入”“前端”的资料。也可先将10个文件放入同一个独立测试库做快速体验。
3. 等待上传、解析、切分、索引各阶段完成。S01应为13个物理页；印刷页码6769对应物理页1，6770对应物理页2。网页/Markdown/TXT不能伪造物理页码。
4. 按qa.jsonl的source_ids选择所需知识库提问。ID对应文件名开头，不是数据库ID；导入后需按实际文档记录映射。
5. 相同问题分别选择向量、关键词、混合检索，保持TopK、阈值和重排设置相同，并在results_template.csv记录实际结果。空白表示未测，不等于0分。
6. 点击答案引用，核对文档名、版本、章节/物理页和原文片段。引用编号存在不代表陈述正确，还需检查证据是否支持结论。
7. 运行7道拒答题时，不允许把教程示例或论文成绩写成本系统的配置、性能和已实现功能；对没有的运行数据应明确说明资料不足。
8. 需要测试报告时使用report_cases.json。生成、逐句核验或图片视觉解析可能产生真实模型费用；本包没有执行这些操作。

## 专项场景

### 重复文件

先上传corpus/S03_FastAPI_请求文件.md，再上传scenarios/duplicate中的同名副本，检查哈希去重/版本处理提示。副本与原件字节完全相同。测试前记下目标知识库，勿覆盖真实业务文件；此包不自动选择覆盖或创建新版本。

### 图片与OCR

scenarios/ocr中的PNG由S01物理第1、2页在130dpi下渲染，没有内容裁切或文字改写。已目视检查清晰、无损坏，未调用OCR。请在独立图片测试库使用，按ocr_cases.json核对标题、实验条件和数字；勿把同一论文的PDF和图片当成相互独立的证据。

本包没有Word、Excel、PPT/PPTX样例，因此不能替代这些格式的专门验收。

### 授权隔离

可将“前端”库只授权给测试用户甲，“后端与接入”库只授权给测试用户乙，使用现有管理界面自行配置。检查未授权知识库不可选，服务端仍拒绝越权请求，问答及引用不得泄漏另一库内容。本包不会创建用户、分配权限或读取任何真实密钥；权限验收仍需实际运行系统。

## 判分建议

- 检索：可回答题检查TopK是否包含所列来源。跨文档题Q024/Q025需要覆盖全部指定来源，而不是任一来源即算完全通过。请区分Hit@K与多来源召回率。
- 回答：对照expected_answer判断含义与限定条件，不要求逐字相同。论文中的数字必须保留数据集、任务和“论文实验”的归属。
- 引用：核对其所绑定的具体片段是否真正支持该句，不仅检查文档名或编号。
- 拒答：Q026–Q032应拒绝编造请求的项目事实/秘密；允许给出带来源的一般解释，但不能用常识补齐不存在的项目数据。
- 核验：grounding用例用于检查陈述与给定证据之间的关系。人工标签是本次整理的参考，正式评测建议由第二位成员复核。

S01是英文论文，中文问题包含跨语言检索难度。本地TF-IDF不等于语义Embedding，可能无法召回中文同义问法；可另用DPR、dual-encoder或BM25等英文术语作对照，或在你们明确配置语义服务后复测。不能把检索失败、解析失败、模型拒答混成一个结论。

## 已做与未做

已检查10份文件哈希及非空解析结果；使用当前项目原有解析器，在临时隔离数据库下逐份读取；32题所用36处证据短引文都能在对应文件/指定物理页出现。结果见validation/collection_check.json。

这些检查只证明文件完整、能解析、所标短引文存在，不证明所有人工标签无误，更不代表模型已答对32题。未执行真实检索/问答/报告/OCR，未测准确率、召回率、延迟或成本；未操作现有业务数据库。

## 离线复核

在项目根目录，使用已有后端环境：

```powershell
.\backend\.venv\Scripts\python.exe -X utf8 test-data/web-2026-10-31/validate.py
```

collect.py仅用于重新整理已缓存的公开原始文件；缓存缺失时会访问清单内的HTTPS公开来源，不读取.env、不上传项目数据。运行后再执行validate.py；如需重新渲染图片，可用Poppler的pdftoppm按原PDF物理页号导出，保持页码、130dpi和来源关系。整个测试集是固定快照，勿无提示地用新版网页替换它。
