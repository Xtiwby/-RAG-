# S10：PDF文本提取与OCR（中文摘编）

来源：pypdf官方文档《Extract Text from a PDF》
原文：https://pypdf.readthedocs.io/en/stable/user/extract-text.html
整理日期：2026-10-31
性质：简短事实摘编，非官方全文；不描述本系统已完成的OCR能力。

## 工具边界

pypdf不是OCR软件。只有扫描图像而没有文字层的PDF，直接抽取可能几乎没有文字，应考虑OCR。

## 区分文件情况

带有OCR文字层的扫描件可能被提取出文字，但这不等于pypdf识别了图像；识别文字与解析文字层是不同步骤。
