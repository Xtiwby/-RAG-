# S08：RAG原始论文摘要（中文摘编）

来源：Patrick Lewis等，Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks，2020。
原文：https://arxiv.org/abs/2005.11401
整理日期：2026-10-31
性质：摘要的简短事实摘编，非论文全文；不代表本项目实现或实测成绩。

## 两类记忆

该论文的RAG结合参数化记忆与非参数化记忆。论文使用预训练seq2seq模型，以及由神经检索器访问的维基百科稠密向量索引。

## 两种条件化方式

一种形式对整段生成使用相同检索片段，另一种允许不同token使用不同片段。论文实验结论不能当作本系统的测量结果。
