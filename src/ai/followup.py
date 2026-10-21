"""贷后检查追问生成：结合**人工输入**与**系统已查到的信息**产出分组追问清单。

为什么需要「先追问、再出报告」
--------------------------------
贷后报告最容易出问题的地方是**人工检查内容写得太笼统**（"经营正常""无异常"），
而系统其实已经发现了高危预警、或人工输入漏了必填项就直接出报告——报告就成了
流程材料而不是风险材料。

所以工作台的流程是：人工输入 → 点「生成」→ 系统对照人工输入与系统信息，
产出**分组追问清单**（必须补充 / 建议补充 / 确认项）→ 用户逐条作答 →
 才允许「生成报告」。必答项未答时禁止出报告。

三条产出路径
------------
1. **AI 路径**：把人工输入 + 事件/预警摘要喂给模型，让它针对缺口提问；
2. **规则降级路径**（AI 不可用）：由 :mod:`src.ai.rule_engine` 的模板拼装，
   清单顶部标注 ``DEGRADED_PREFIX`` 且 ``pending_review=1``；
3. **人工输入自检**（始终执行）：无论走哪条路径，**漏填的必填项**都会被补成
   「必须补充」项——这是硬约束，不依赖 AI。
"""
from typing import Dict, List, Optional

from ..core.models import CATEGORY_LABELS, Event
from . import llm_client
from .rule_engine import DEGRADED_PREFIX

# 追问分组
GROUP_REQUIRED = "must"      # 必须补充（未答不得出报告）
GROUP_SUGGEST = "suggest"    # 建议补充
GROUP_CONFIRM = "confirm"    # 确认项（针对系统已发现的风险）

GROUP_LABELS = {
    GROUP_REQUIRED: "必须补充",
    GROUP_SUGGEST: "建议补充",
    GROUP_CONFIRM: "确认项",
}

GROUP_ORDER = [GROUP_REQUIRED, GROUP_SUGGEST, GROUP_CONFIRM]

# 人工输入字段定义：key -> (标签, 是否必填, 提示)
INPUT_FIELDS: List[Dict] = [
    {"key": "inspector", "label": "检查人员", "required": True,
     "hint": "参与本次贷后检查的客户经理/风险经理姓名"},
    {"key": "inspect_date", "label": "检查日期", "required": True,
     "hint": "本次现场/非现场检查的实际日期（YYYY-MM-DD）"},
    {"key": "method", "label": "检查方式", "required": True,
     "hint": "如：现场走访 / 电话访谈 / 财务报表分析 / 经营场所查看"},
    {"key": "operation", "label": "企业经营情况", "required": True,
     "hint": "主营业务、在建项目、生产/销售情况、订单与回款等"},
    {"key": "finance", "label": "财务情况", "required": True,
     "hint": "资产负债、收入利润、现金流、负债率、对外担保等关键数据"},
    {"key": "repayment", "label": "还款情况", "required": True,
     "hint": "本期还本付息情况、是否逾期/欠息、下一还款安排"},
    {"key": "risk", "label": "风险事项", "required": True,
     "hint": "本次检查发现的负面/异常事项；无则写「未发现」"},
    {"key": "guarantee", "label": "担保情况", "required": False,
     "hint": "保证人/抵质押物现状与有效性变化"},
    {"key": "advice", "label": "贷后管理建议", "required": False,
     "hint": "拟采取的贷后管理措施与下次检查安排"},
]

INPUT_LABELS = {f["key"]: f["label"] for f in INPUT_FIELDS}
REQUIRED_KEYS = [f["key"] for f in INPUT_FIELDS if f["required"]]

# 追问项：{"id", "group", "question", "field", "hint"}
Question = Dict[str, object]


def _qid(group: str, field: str, idx: int = 0) -> str:
    return f"{group}_{field}_{idx}" if idx else f"{group}_{field}"


def _missing_questions(inputs: Dict[str, str]) -> List[Question]:
    """人工输入自检：漏填的必填项 → 必须补充；可选但重要的空项 → 建议补充。"""
    out: List[Question] = []
    for f in INPUT_FIELDS:
        key = f["key"]
        val = str((inputs or {}).get(key, "") or "").strip()
        if val:
            continue
        if f["required"]:
            out.append({
                "id": _qid(GROUP_REQUIRED, key),
                "group": GROUP_REQUIRED,
                "field": key,
                "question": f"「{f['label']}」尚未填写，请补充该项内容。",
                "hint": f["hint"],
            })
        elif key in ("guarantee", "advice"):
            out.append({
                "id": _qid(GROUP_SUGGEST, key),
                "group": GROUP_SUGGEST,
                "field": key,
                "question": f"是否需要补充「{f['label']}」？如无相关情况请填写「无」。",
                "hint": f["hint"],
            })
    return out


def _confirm_questions(events: List[Event], inputs: Dict[str, str]) -> List[Question]:
    """针对系统已发现的高危事件生成确认项（要人工核实系统结论）。"""
    out: List[Question] = []
    risk_text = str((inputs or {}).get("risk", "") or "")
    for i, e in enumerate(list(events or [])[:8]):
        if e.severity != "high":
            continue
        cat = CATEGORY_LABELS.get(e.category, e.category)
        out.append({
            "id": _qid(GROUP_CONFIRM, f"event{i}", i),
            "group": GROUP_CONFIRM,
            "field": f"event_{getattr(e, 'id', '')}",
            "question": (
                f"系统监测到高危事件「{e.title}」（{cat}"
                f"，{e.occurred_at or '日期未知'}），请确认该事件的真实性与影响；"
 f"如已核实请说明结论。"),
            "hint": "人工结论优先于系统结论；如系统误报请注明原因。",
        })
    if not out and risk_text:
        out.append({
            "id": _qid(GROUP_CONFIRM, "risk_text"),
            "group": GROUP_CONFIRM,
            "field": "risk_text",
            "question": "人工填写的风险事项是否已与系统监测结论相互印证？如有差异请说明。",
            "hint": "人工与系统结论不一致时，以核实后的事实为准并记录差异原因。",
        })
    return out


def _rule_questions(inputs: Dict[str, str], events: List[Event]) -> List[Question]:
    """规则降级路径：模板化追问（AI 不可用时使用）。"""
    out = _missing_questions(inputs)
    out.extend(_confirm_questions(events, inputs))
    # 通用建议项（不依赖 AI）
    out.append({
        "id": _qid(GROUP_SUGGEST, "comparison"),
        "group": GROUP_SUGGEST,
        "field": "comparison",
        "question": "本次经营/财务数据与上一期相比是否有明显变化？如有请量化说明。",
        "hint": "同比/环比数据有助于判断风险趋势。",
    })
    out.append({
        "id": _qid(GROUP_SUGGEST, "next_plan"),
        "group": GROUP_SUGGEST,
        "field": "next_plan",
        "question": "下次检查的时间安排与关注重点是什么？",
        "hint": "建议与风险等级挂钩：高危主体缩短间隔。",
    })
    return out


def _ai_questions(inputs: Dict[str, str], events: List[Event],
                  ent_name: str = "") -> List[Question]:
    """AI 路径：让模型针对当前企业的人工输入与系统信息提出追问。"""
    filled = []
    for f in INPUT_FIELDS:
        val = str((inputs or {}).get(f["key"], "") or "").strip()
        filled.append(f"- {f['label']}：{val if val else '（未填写）'}")
    brief = []
    for e in list(events or [])[:20]:
        cat = CATEGORY_LABELS.get(e.category, e.category)
        brief.append(f"- [{e.severity}][{cat}] {e.title}（{e.occurred_at or '日期未知'}）")
    prompt = (
        "你是银行贷后管理专家。请针对下面这次贷后检查，"
        "指出**信息缺口**并提出需要向客户经理追问的问题。\n"
        "要求：只问「从已有信息无法判断」的问题，不重复问已填写清楚的项；"
        "问题要具体、可执行、便于直接答复。\n"
        "严格输出 JSON，格式为：\n"
        '{"questions": [{"group": "must|suggest|confirm", "question": "问题", "hint": "答题提示"}]}\n'
        "group 取值：must=必须补充（缺了没法出报告）、suggest=建议补充、"
        "confirm=需要人工确认系统已发现的风险。\n\n"
        f"【企业】{ent_name or '（未指定）'}\n"
        f"【人工检查输入】\n{chr(10).join(filled)}\n"
        f"【系统监测到的事件】\n{chr(10).join(brief) if brief else '（无）'}"
    )
    text = _chat_json(prompt)
    out: List[Question] = []
    for i, item in enumerate(text.get("questions") or []):
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()
        if not q:
            continue
        group = str(item.get("group") or GROUP_SUGGEST)
        if group not in GROUP_ORDER:
            group = GROUP_SUGGEST
        out.append({
            "id": _qid(group, f"ai{i}", i),
            "group": group,
            "field": "",
            "question": q,
            "hint": str(item.get("hint") or ""),
        })
    return out


def _chat_json(prompt: str) -> dict:
    """调用模型并容错提取 JSON（与 agents._extract_json 同思路）。"""
    import json
    import re

    text, _model = llm_client.get_client().chat(
        [{"role": "user", "content": prompt}], 0.2)
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def generate_followup_questions(inputs: Dict[str, str], events: List[Event],
                                ent_name: str = "") -> dict:
    """生成贷后检查追问清单。

    返回 ``{"questions": [...], "engine": "ai"|"rule", "pending_review": int,
    "prefix": str}``。

    **无论走 AI 还是规则路径，人工输入自检（漏填必填项）都会被强制合并进去**，
    确保「必答项未答 → 禁止生成报告」这条硬约束不依赖模型表现。
    """
    inputs = dict(inputs or {})
    events = list(events or [])

    ai_ok = False
    questions: List[Question] = []
    if llm_client.is_available():
        try:
            questions = _ai_questions(inputs, events, ent_name)
            ai_ok = True
        except llm_client.AIUnavailableError:
            questions = []
        except Exception:  # noqa: BLE001 - AI 异常一律降级，不阻断工作台
            questions = []

    if not questions:
        questions = _rule_questions(inputs, events)
        engine = "rule"
        pending = 1
        prefix = DEGRADED_PREFIX
    else:
        engine = "ai"
        pending = 0
        prefix = ""

    # 合并人工输入自检结果（去重：同一 field 只保留一条 must/suggest）
    existing = {q.get("field") for q in questions if q.get("field")}
    for q in _missing_questions(inputs):
        if q.get("field") in existing:
            continue
        questions.append(q)
        existing.add(q.get("field"))

    # 合并确认项（系统已发现的高危事件必须有人工确认）
    have_confirm = any(q.get("group") == GROUP_CONFIRM and q.get("field", "").startswith("event")
                        for q in questions)
    if not have_confirm:
        for q in _confirm_questions(events, inputs):
            questions.append(q)

    # 排序：必须补充 -> 建议补充 -> 确认项
    questions.sort(key=lambda q: GROUP_ORDER.index(q.get("group", GROUP_SUGGEST)))
    return {
        "questions": questions,
        "engine": engine,
        "pending_review": pending,
        "prefix": prefix,
        "ai_ok": ai_ok,
    }


def unanswered_required(questions: List[Question], answers: Dict[str, str]) -> List[Question]:
    """返回**尚未作答**的必答项（用于禁用「生成报告」按钮）。"""
    out = []
    for q in questions or []:
        if q.get("group") != GROUP_REQUIRED:
            continue
        ans = str((answers or {}).get(str(q.get("id")), "") or "").strip()
        if not ans:
            out.append(q)
    return out


def can_generate_report(questions: List[Question], answers: Dict[str, str]) -> tuple:
    """返回 ``(是否可生成, 未答必答项列表)``。"""
    missing = unanswered_required(questions, answers)
    return (not missing), missing
