# BankRiskMonitor 数据补录需求文档
## 连接器代查 → 标准格式文件 → 导入监测库

> **使用方式**：把本文档**整份**发给 WorkBuddy，并说明要处理的企业清单。WorkBuddy 按第 4 节调用工商连接器代查，按第 3 节输出**标准格式 JSON 文件**，再按第 6 节导入 BankRiskMonitor。
> **版本**：schema `1.0` ｜ **适用系统**：BankRiskMonitor（贷后风险监控）
> **背景**：涉诉、被执行是贷后检查的**必备信息**。政务站点（裁判文书网、执行信息公开网）反爬严格，系统内置采集不稳定；用连接器代查是当前**最稳定**的补齐方式。

---

## 1. 目的

为 BankRiskMonitor 补齐下列**必备信息域**（缺任何一项，贷后报告与风险分级都不完整）：

| 信息域 | 系统 category | 为什么必备 |
|---|---|---|
| 工商变更（股权/股东/法定代表人/注册资本/经营范围等） | `biz_change` | 用于识别**放款后**的股权与治理变动 |
| 涉诉（在册立案 / 裁判文书） | `lawsuit` | 判断法律纠纷与代偿风险 |
| 被执行 / 失信 | `enforcement` | 判断强制执行与信用恶化 |

---

## 2. 目标企业清单

| 企业名称（须与系统内**完全一致**） | 统一社会信用代码 | 放款日 |
|---|---|---|
| 辽宁省沈抚示范区鑫泰房地产开发有限公司 | 91211500MACJ4PXF9C | 2026-09-29 |
| 辽宁省沈抚示范区民泰房地产开发有限公司 | 91211500MACJTYUB8N | 2026-09-29 |
| 辽宁省沈抚示范区开发投资集团有限公司 | 91211500MA0UXCKD0L | 2026-10-10 |
| 抚顺同盛资产经营有限公司 | 91210400096371454Y | 2026-10-10（保证人，随开投） |
| 辽宁省沈抚新区国有资产管理有限公司 | 91211500MA0YLDFF6U | 2026-10-10（保证人，随开投） |
| 辽宁省沈抚示范区置业有限公司 | 91211500MA0YC5WR1Q | 2026-10-10（保证人，随开投） |
| 辽宁省沈抚示范区金研关键新材料技术有限公司 | 91211500MA1146YB4K | 2026-10-10（保证人，随开投） |

> **放款日的意义**：系统以放款日为界做「放款前基线 / 放款后变更」对比。放款日之前的信息一律**记录但不视为问题**；放款日当日及之后出现**变更**才算问题。因此**变更日期/立案日期必须准确**，否则分级会错。

---

## 3. 标准文件格式（冻结，schema_version = `1.0`）

- 编码 **UTF-8**，中文不转义（`ensure_ascii=false`）
- 顶层结构：

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-10-16T16:05:00",
  "connector": "qcc_company",
  "note": "可选说明",
  "companies": [ /* 每家企业一个对象，见下表 */ ]
}
```

- `companies[]` 元素字段：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `name` | ✅ | string | **企业全称**，必须与系统内名称完全一致 |
| `credit_code` | | string | 统一社会信用代码（`name` 匹配失败时的兜底匹配键） |
| `profile` | | object | 工商登记快照，见下表 |
| `changes` | | array | 工商变更记录，见下表 |
| `lawsuits` | | array | 涉诉记录（在册立案 / 裁判文书） |
| `enforcements` | | array | 被执行 / 失信记录 |
| `url` | | string | 来源页链接（可选） |

- `profile` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `legal_person` | string | 法定代表人 |
| `reg_capital` | string | 注册资本（如 `1100万元`） |
| `shareholders` | array | `[{"name": "股东名", "ratio": "100%"}]` |
| `business_scope` | string | 经营范围 |
| `business_status` | string | 登记状态（如 `存续`） |
| `abnormal` | bool | 是否列入经营异常名录 |
| `tax_status` | string | 税务状态（如 `一般纳税人` / `非正常户`） |
| `risk_tags` | array | 风险标签字符串数组 |

- `changes[]` 字段（**最重要，必须带日期**）：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `change_item` | ✅ | string | 变更项目，如 `投资人变更（股东）`、`负责人变更（法定代表人）`、`注册资本变更` |
| `before` | | string | 变更前内容 |
| `after` | | string | 变更后内容 |
| `change_date` | ✅ | string | **变更发生日期 `YYYY-MM-DD`**；取不到填 `""`（见第 5 节硬规则） |

- `lawsuits[]` 字段：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `title` | ✅ | string | 案由（建议附角色，如 `建设工程施工合同纠纷（被告）`） |
| `case_no` | | string | 案号，如 `（2025）辽0112民初10466号` |
| `date` | ✅ | string | **立案/裁判日期 `YYYY-MM-DD`** |
| `amount` | | string | 涉案金额（无则 `""`） |

- `enforcements[]` 字段：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `title` | ✅ | string | 如 `被执行人` / `失信被执行人` |
| `case_no` | | string | 案号，如 `（2026）辽0112执6028号` |
| `date` | ✅ | string | **立案日期 `YYYY-MM-DD`** |
| `amount` | | string | 执行标的金额（元，无则 `""`） |

---

## 4. 数据获取清单（WorkBuddy 用哪些连接器工具取什么）

> 优先用**企查查**（`qcc-company`）；不可用时依次退到天眼查（`tyc-mcp`）、启信慧眼（`qixinhuiyan-mcp`）、水滴信用（`shuidi-credit`）。

| 目标字段 | 连接器工具 | 说明 |
|---|---|---|
| `profile` | `get_company_registration_info` | 法定代表人/注册资本/登记状态/成立日期/经营范围等 |
| `profile.shareholders` | `get_shareholder_info` | 股东与持股比例 |
| `changes[]` | **`get_change_records`** | **聚合变更记录，含变更前后内容与变更日期——本项最关键** |
| `lawsuits[]` | `get_case_filing_info`（在册立案，推荐）／`get_judicial_documents`（裁判文书） | 立案信息含案由/当事人/法院/立案日期 |
| `enforcements[]` | `get_judgment_debtor_info`（被执行）／`get_dishonest_info`（失信）／`get_high_consumption_restriction`（限高） | 含案号/执行标的/立案日期 |
| `enforcements[]`（税务） | `get_tax_arrears_notice` / `get_tax_abnormal` | 欠税公告 / 税务非正常户 |

**取数要求**：
- 每家企业**逐项调用**，把结果按第 3 节 schema 归一。
- 涉诉/被执行若有大量历史记录：**全部返回**；若确需截断，须在顶层 `note` 写明截断规则。
- 无记录的企业，对应数组给 `[]`（不要省略字段，便于审计"已核查"）。

---

## 5. 硬性规则（违反将导致数据不可用）

| # | 规则 | 原因 |
|---|---|---|
| **R1** | **`occurred_at` / `date` / `change_date` 只能是真实的发生日/变更日/立案日。取不到就填 `""`。严禁把"抓取当天"写进去。** | 历史上曾把抓取日写进日期，导致系统无法判断放款前后，全部数据作废（已清洗 7 行） |
| **R2** | 日期统一 `YYYY-MM-DD`。带时间戳（如 `2026-09-29 10:00:00`）会被截取日期部分；非法日期（如 `2026-13-45`）会被判为"日期缺失"。 | 系统按日期做放款日前后判定 |
| **R3** | `name` 必须与系统内企业名称**完全一致**（含"辽宁省沈抚示范区…"全称）。 | 名称匹配不上会被**跳过**并记日志 |
| **R4** | 同一企业可**重复代查**，文件可重复导入，工具会幂等去重（二次导入 `inserted=0`）。 | 去重键为 `(企业, 类别, 标题, 来源)`；因此**标题里不要写日期**，否则会绕过去重产生重复 |
| **R5** | 编码 UTF-8，中文不转义；JSON 需可被 `json.load` 直接解析。 | 解析失败会整批拒绝 |
| **R6** | 文件建议命名 `connector_batch_<连接器>_<YYYYMMDD>.json`，放在 `data/samples/`。 | 便于归档与追溯 |

---

## 6. 导入方法

```bash
cd E:\软件工程项目\BankRiskMonitor

# 先干跑（只统计不落库，强烈建议）
python tools/connector_ingest.py --file data/samples/connector_batch_qcc_20261016.json --dry-run

# 确认无误后正式导入
python tools/connector_ingest.py --file data/samples/connector_batch_qcc_20261016.json
```

输出示例：

```json
{
  "connector": "qcc_company",
  "source": "qcc_company",
  "companies": 7,
  "inserted": 32,
  "skipped": 0,
  "reasons": [],
  "dry_run": false
}
```

- `inserted` = 新增条数；`skipped` = 命中唯一键被去重（**第二次导入应 `inserted=0`**）。
- 失败会记日志到 `data/logs/connector_<时间戳>.log`，含 `connector / company / reason`。
- `reason=no_enterprise` 表示企业名匹配不上（请核对第 2 节名称）。

---

## 7. 导入后自查清单

1. `inserted` 数量与预期一致；**再跑一次应 `inserted=0`**（幂等）。
2. 库内**不存在** `occurred_at == generated_at` 的行（防抓取日污染）：
   ```sql
   SELECT COUNT(*) FROM event WHERE occurred_at = substr(created_at,1,10);
   -- 期望 0（对连接器来源）
   ```
3. 打开 BankRiskMonitor → 左侧点选企业 → 右侧信息流能看到新增的**涉诉/被执行**条目，且标注为「放款前·基线」或「放款后·变更」。
4. 状态栏不再显示 `⚠必备信息域缺源`。

---

## 8. 附件：已按本规范完成的第一批（可作参考样例）

| 文件 | 内容 | 结果 |
|---|---|---|
| `data/samples/connector_batch_qcc_20261016.json` | 鑫泰/民泰/开投 工商变更 18 条 | inserted 18，二次 0 |
| `data/samples/connector_batch_qcc_20261016_b.json` | 同盛/国资管理/置业/金研 工商变更 25 条 | inserted 25 |
| `data/samples/connector_batch_qcc_judicial_20261016.json` | 7 家 涉诉 29 条 + 被执行 3 条 | inserted 32，二次 0 |

> 参考：以上 32 条涉诉/被执行中，**被执行立案日期为 2026-08-06 / 2026-08-20 / 2026-08-26**，均**早于**对应放款日（2026-09-29 / 2026-10-10），因此系统判定为「放款前·基线（非问题）」——这正是本机制要实现的贷后口径。
