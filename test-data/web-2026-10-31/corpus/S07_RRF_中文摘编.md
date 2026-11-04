# S07：RRF排名融合（中文摘编）

来源：Elastic官方文档《Reciprocal rank fusion》
原文：https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
整理日期：2026-10-31
性质：仅作简短事实摘编，不是官方全文，不描述本项目的实际配置。

## 融合方法

RRF把多路检索排名合并。对同一文档累加各路的 1/(k+rank)；rank从1开始，k是排名常数。

## 参数边界

Elastic文档中的rank_constant默认值为60，且至少为1。该默认值只属于文档所述接口，不能据此认定本项目也使用60。
