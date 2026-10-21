"""代查需求单生成（覆盖度缺口 → 可交给连接器执行的取数任务清单）。

为什么
----
涉诉/被执行是贷后必备信息域，但政务站点（裁判文书网、执行信息公开网）反爬
严格，程序无法稳定采集。缺口不能只停留在「提示一句缺源」，而要变成**一张能直接
发出去干活的单子**：把当前监控对象、每个缺源域要取哪些字段、用哪个连接器、
有哪些硬性规则，一次性列清楚。

产出
----
``build_request_doc(enterprises, problems)`` 返回 Markdown 文本，结构对齐
``docs/CONNECTOR-DATA-REQUEST.md``（schema 1.0），可直接保存为 .md 交给
连接器代查方。
"""
from typing import Dict, List, Optional, Sequence

# 缺源信息域 -> 代查要取的字段（与 CONNECTOR-DATA-REQUEST.md §3 保持一致）
DOMAIN_FIELDS: Dict[str, List[Dict[str, str]]] = {
    "biz_change": [
        {"field": "profile", "desc": "工商登记快照：法定代表人/注册资本/登记状态/"
                                     "成立日期/经营范围/股东与持股比例"},
        {"field": "changes[]", "desc": "工商变更记录：变更项/变更前/变更后/"
                                        "变更日期（最关键，必须带日期）"},
    ],
    "lawsuit": [
        {"field": "lawsuits[]", "desc": "在册立案与裁判文书：案由/案号/立案或裁判日期/"
                                          "涉案金额/当事人/法院"},
    ],
    "enforcement": [
        {"field": "enforcements[]", "desc": "被执行/失信/限高：案号/立案日期/"
                                              "执行标的金额/执行法院"},
        {"field": "enforcements[]（税务）", "desc": "欠税公告 / 税务非正常户"},
    ],
}

DOMAIN_LABELS = {
    "biz_change": "工商变更",
    "lawsuit": "涉诉",
    "enforcement": "被执行/失信",
}

# 连接器工具（与 CONNECTOR-DATA-REQUEST.md §4 一致，按优先级排列）
CONNECTOR_CHAIN = [
    ("qcc_company", "企查查", "get_company_registration_info / get_shareholder_info / "
                              "get_change_records / get_case_filing_info / "
                              "get_judgment_debtor_info"),
    ("tyc_mcp", "天眼查", "工商信息 / 变更记录 / 涉诉 / 被执行"),
    ("qixinhuiyan_mcp", "启信慧眼", "工商信息 / 变更记录 / 涉诉"),
    ("shuidi_credit", "水滴信用", "工商信息 / 司法风险 / 被执行"),
]

HARD_RULES = [
    "R1 日期只能是真实的发生日/变更日/立案日；取不到就填 \"\"，"
    "严禁把抓取当天写进去（否则放款前后判定全错）。",
    "R2 日期统一 YYYY-MM-DD；带时间戳会被截取日期部分，非法日期判为「日期缺失」。",
    "R3 name 必须与系统内企业名称**完全一致**（含行政区划前缀），否则会被跳过。",
    "R4 标题里不要写日期（去重键为 企业+类别+标题+来源，写日期会绕过幂等）。",
    "R5 编码 UTF-8，中文不转义，JSON 可被 json.load 直接解析。",
    "R6 无记录的企业对应数组给 []，不要省略字段（便于审计「已核查」）。",
]


def _ent_line(ent) -> str:
    """企业清单的一行：名称 + 信用代码 + 放款日。"""
    name = str(getattr(ent, "name", "") or "")
    code = str(getattr(ent, "credit_code", "") or "")
    note = str(getattr(ent, "credit_note", "") or "")
    cells = [name or "（未命名）"]
    cells.append(code or "-")
    cells.append(note or "-")
    return "| " + " | ".join(cells) + " |"


def build_request_doc(enterprises: Sequence, problems: Sequence[Dict],
                      author: str = "") -> str:
    """生成代查需求单（Markdown 文本）。

    参数
    ----
    enterprises
        监控对象列表（对象需有 ``name`` / ``credit_code`` / ``credit_note`` 属性，
        dict 亦可）。
    problems
        ``core.coverage.check_required_coverage`` 的结果；为空时按三个必备域
        全部生成（用户也可主动导出完整需求单）。
    """
    cats: List[str] = []
    for p in (problems or []):
        cat = str(p.get("category") or "")
        if cat and cat not in cats:
            cats.append(cat)
    if not cats:
        cats = ["biz_change", "lawsuit", "enforcement"]

    lines: List[str] = []
    lines.append("# BankRiskMonitor 代查需求单")
    lines.append("")
    lines.append("> **用途**：把本文档整份发给连接器代查方，按第 3 节输出"
                 "**标准格式 JSON 文件**（schema `1.0`），再按第 6 节导入监测库。")
    lines.append("> **生成时间**：%s" % _now())
    if author:
        lines.append("> **发起人**：%s" % author)
    lines.append("")

    # 1 目标域
    lines.append("## 1. 本次需要代查的信息域")
    lines.append("")
    lines.append("| 信息域 | 系统 category | 当前状态 | 说明 |")
    lines.append("|---|---|---|---|")
    hint_map = {str(p.get("category")): str(p.get("hint") or "")
                for p in (problems or [])}
    for cat in cats:
        label = DOMAIN_LABELS.get(cat, cat)
        state = "缺可靠源" if cat in hint_map else "主动导出"
        lines.append("| %s | `%s` | %s | %s |"
                     % (label, cat, state, hint_map.get(cat, "") or "—"))
    lines.append("")

    # 2 企业清单
    lines.append("## 2. 目标企业清单")
    lines.append("")
    ents = list(enterprises or [])
    if not ents:
        lines.append("（当前没有监控对象，请先在系统内添加企业）")
    else:
        lines.append("| 企业名称（须与系统内**完全一致**） | 统一社会信用代码 | 放款日/备注 |")
        lines.append("|---|---|---|")
        for e in ents:
            lines.append(_ent_line(e))
    lines.append("")
    lines.append("> **放款日的意义**：系统以放款日为界做「放款前基线 / 放款后变更」对比；"
                 "放款日之前的信息记录但不视为问题。因此**变更日期/立案日期必须准确**。")
    lines.append("")

    # 3 字段清单
    lines.append("## 3. 需要取的字段")
    lines.append("")
    for cat in cats:
        label = DOMAIN_LABELS.get(cat, cat)
        lines.append(f"### {label}（`{cat}`）")
        lines.append("")
        lines.append("| 目标字段 | 说明 |")
        lines.append("|---|---|")
        for f in DOMAIN_FIELDS.get(cat, []):
            lines.append("| `%s` | %s |" % (f["field"], f["desc"]))
        lines.append("")

    # 4 连接器
    lines.append("## 4. 用哪个连接器取")
    lines.append("")
    lines.append("| 优先级 | 连接器 | source 标注 | 可用工具 |")
    lines.append("|---|---|---|---|")
    for i, (src, label, tools) in enumerate(CONNECTOR_CHAIN, 1):
        lines.append("| %d | %s | `%s` | %s |" % (i, label, src, tools))
    lines.append("")

    # 5 硬规则
    lines.append("## 5. 硬性规则（违反将导致数据不可用）")
    lines.append("")
    for r in HARD_RULES:
        lines.append("- " + r)
    lines.append("")

    # 6 输出格式
    lines.append("## 6. 输出格式（schema `1.0`）")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "schema_version": "1.0",')
    lines.append('  "generated_at": "YYYY-MM-DDTHH:MM:SS",')
    lines.append('  "connector": "qcc_company",')
    lines.append('  "companies": [')
    lines.append('    {"name": "企业全称", "credit_code": "",')
    lines.append('     "profile": {}, "changes": [],')
    lines.append('     "lawsuits": [], "enforcements": []}')
    lines.append("  ]")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("## 7. 导入方法")
    lines.append("")
    lines.append("系统内：**设置 → 数据源 → 代查结果导入**，选择该 JSON 文件即可入库；"
                 "命令行等价操作为：")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/connector_ingest.py --file <批次.json> --dry-run  # 先干跑")
    lines.append("python tools/connector_ingest.py --file <批次.json>          # 正式导入")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_由 BankRiskMonitor 自动生成：监控对象 {len(ents)} 家，"
                 f"待补信息域 {len(cats)} 个。_")
    return "\n".join(lines)


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_filename() -> str:
    """默认导出文件名（含日期，便于归档）。"""
    from datetime import datetime
    return "代查需求单_%s.md" % datetime.now().strftime("%Y%m%d")


def export_request_doc(target_path: str, enterprises: Sequence,
                        problems: Sequence[Dict], author: str = "") -> Optional[str]:
    """把需求单写到文件；成功返回路径，失败返回 None（不抛异常）。"""
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(build_request_doc(enterprises, problems, author))
    except OSError:
        return None
    return target_path
