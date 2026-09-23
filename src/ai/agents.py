"""AI Agent 实现（5 角色）：OpenRouter 优先，失败自动降级本地规则脚本。

返回结构与契约保持一致：
- assess_event / assess_events_batch → {sentiment, severity, relevance, verdict, engine, pending_review}
- analyze_credit_impact / generate_report_text / analyze_penetration → {text, engine, pending_review}
- triage_events → List[Event]
engine 取值 'ai' / 'rule'；所有降级结果 pending_review=1 且文本带统一前缀。
"""
import json
import logging
import re
from typing import List, Optional

from ..core import db
from ..core.models import CATEGORY_LABELS, SENTIMENTS, SEVERITIES, Event
from . import llm_client, rule_engine
from .rule_engine import DEGRADED_PREFIX

log = logging.getLogger(__name__)

VERDICT_MAX = 60  # 研判一句话字数上限
_RELEVANCES = ("high", "medium", "low")


# ---------- 通用工具 ----------
def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中容错提取 JSON 对象（容忍 ```json 围栏与多余文字）。"""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def _pick(val, allowed, default):
    return val if val in allowed else default


def _ent_name(event: Event) -> str:
    try:
        ent = db.get_enterprise(event.enterprise_id) if event.enterprise_id else None
        return ent.name if ent else ""
    except Exception:
        return ""


def _ent_names(enterprises: list) -> str:
    names = []
    for e in enterprises or []:
        n = e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")
        if n:
            names.append(n)
    return "、".join(names[:30]) or "（无）"


def _events_brief(events: List[Event], limit: int = 30) -> str:
    lines = []
    for e in (events or [])[:limit]:
        cat = CATEGORY_LABELS.get(e.category, e.category)
        lines.append(f"- [{e.severity}][{cat}] {e.title}（{e.occurred_at or '日期未知'}）")
    return "\n".join(lines) if lines else "（无）"


def _chat(prompt: str, temperature: float = 0.2) -> str:
    text, _model = llm_client.get_client().chat([{"role": "user", "content": prompt}], temperature)
    return text


# ---------- 1. 舆情研判 ----------
def _ai_assess(event: Event) -> dict:
    name = _ent_name(event)
    prompt = (
        "你是银行贷后风险监控助手，请站在银行贷后管理视角对以下企业事件进行研判。\n"
        f"企业名称：{name or '（未知）'}\n"
        f"事件类型：{CATEGORY_LABELS.get(event.category, event.category)}\n"
        f"标题：{event.title}\n"
        f"摘要：{event.summary or '（无）'}\n"
        f"信息来源：{event.source}\n\n"
        "严格只输出一个 JSON 对象，不要输出任何其他文字，格式：\n"
        '{"sentiment": "negative|neutral|positive", "severity": "high|medium|low", '
        '"relevance": "high|medium|low", "verdict": "不超过60字的研判一句话"}'
    )
    text = _chat(prompt)
    data = _extract_json(text)
    rule = rule_engine.assess_by_rule(event.title, event.summary, event.source, event.category, name)
    if not data:
        # JSON 解析失败：整体作为 verdict，字段按规则引擎兜底
        return {"sentiment": rule["sentiment"], "severity": rule["severity"],
                "relevance": rule["relevance"], "verdict": (text or "").strip(),
                "engine": "ai", "pending_review": 0}
    verdict = str(data.get("verdict") or "").strip()[:VERDICT_MAX] or rule["verdict"]
    return {
        "sentiment": _pick(data.get("sentiment"), SENTIMENTS, rule["sentiment"]),
        "severity": _pick(data.get("severity"), SEVERITIES, rule["severity"]),
        "relevance": _pick(data.get("relevance"), _RELEVANCES, rule["relevance"]),
        "verdict": verdict,
        "engine": "ai",
        "pending_review": 0,
    }


def assess_event(event: Event) -> dict:
    """舆情研判：AI 可用走模型；不可用走 queue_worker.assess_or_enqueue 同款降级逻辑（规则+入队）。"""
    if llm_client.is_available():
        try:
            return _ai_assess(event)
        except llm_client.AIUnavailableError:
            pass  # 请求过程中降级，转规则兜底
    from . import queue_worker
    return queue_worker.degraded_assess(event)


def assess_events_batch(events: List[Event], progress_cb=None) -> List[dict]:
    """批量研判（供 GUI 后台线程调用），progress_cb(done, total) 汇报进度。"""
    results = []
    total = len(events)
    for i, ev in enumerate(events):
        results.append(assess_event(ev))
        if progress_cb:
            try:
                progress_cb(i + 1, total)
            except Exception:
                pass
    return results


# ---------- 2. 授信影响分析 ----------
def _ai_credit_impact(material_text: str, events: List[Event]) -> dict:
    prompt = (
        "你是银行授信与贷后管理专家。请基于授信材料摘要与近期监测事件，输出结构化影响研判，"
        "分三部分：一、分类迁徙风险；二、第二还款来源与担保有效性；三、建议贷后管理动作。"
        "用中文分点陈述，简洁专业。\n\n"
        f"【授信材料摘要】\n{(material_text or '')[:6000]}\n\n"
        f"【近期监测事件】\n{_events_brief(events)}"
    )
    return {"text": _chat(prompt).strip(), "engine": "ai", "pending_review": 0}


def _rule_credit_impact(material_text: str, events: List[Event]) -> dict:
    high = [e for e in events if e.severity == "high"]
    med = [e for e in events if e.severity == "medium"]
    lines = [
        DEGRADED_PREFIX,
        "【授信影响自动研判】",
        f"授信材料共 {len(material_text or '')} 字；监测事件共 {len(events)} 条：高危 {len(high)} 条、中危 {len(med)} 条。",
        "一、分类迁徙风险：",
        ("  存在高危事件（被执行/查封/处罚等），分类下迁风险较高，建议下调内部风险分类并压降敞口。"
         if high else "  未见高危事件，分类迁徙风险总体可控。"),
        "二、第二还款来源与担保有效性：",
        "  请人工核查抵质押物状态、保证人代偿能力是否受上述事件影响，必要时要求追加担保。",
        "三、建议贷后管理动作：",
    ]
    for e in high[:5]:
        lines.append(f"  - 核实：{e.title}")
    lines.append("  - 对高危事件所在主体开展现场/非现场核查，加密监测频率并留存核查记录。")
    return {"text": "\n".join(lines), "engine": "rule", "pending_review": 1}


def analyze_credit_impact(material_text: str, events: List[Event]) -> dict:
    """授信影响分析：AI 可用走模型；不可用返回规则拼接文本（模板+事件汇总）。"""
    if llm_client.is_available():
        try:
            return _ai_credit_impact(material_text, events)
        except llm_client.AIUnavailableError:
            pass
    return _rule_credit_impact(material_text, events)


# ---------- 3. 报告生成 ----------
def _ai_report(scope: str, enterprises: list, events: List[Event]) -> dict:
    prompt = (
        "你是银行贷后监测报告撰写助手。请基于监测范围、企业名单与期间事件清单生成报告正文，"
        "包含两段：1）监测期间综述；2）风险提示。中文，简洁专业。\n\n"
        f"【监测范围】{scope}\n"
        f"【监控企业】{_ent_names(enterprises)}\n"
        f"【期间事件】\n{_events_brief(events, 50)}"
    )
    return {"text": _chat(prompt).strip(), "engine": "ai", "pending_review": 0}


def _rule_report(scope: str, enterprises: list, events: List[Event]) -> dict:
    high = [e for e in events if e.severity == "high"]
    med = [e for e in events if e.severity == "medium"]
    lines = [
        DEGRADED_PREFIX,
        f"【监测综述】监测范围：{scope}。监控企业 {len(enterprises or [])} 家"
        f"（{_ent_names(enterprises)}）。监测期间共采集事件 {len(events or [])} 条，"
        f"其中高危 {len(high)} 条、中危 {len(med)} 条。",
        "【风险提示】",
    ]
    if high:
        for e in high[:10]:
            cat = CATEGORY_LABELS.get(e.category, e.category)
            lines.append(f"- 高危：{e.title}（{cat}，{e.occurred_at or '日期未知'}）")
        lines.append("- 建议对上述高危事件逐一人工核实，评估对授信敞口与担保有效性的影响。")
    else:
        lines.append("- 监测期间未发现高危事件，建议保持常规频率持续监测。")
    return {"text": "\n".join(lines), "engine": "rule", "pending_review": 1}


def generate_report_text(scope: str, enterprises: list, events: List[Event]) -> dict:
    """报告生成：AI 可用生成综述+风险提示；不可用模板自动填充。"""
    if llm_client.is_available():
        try:
            return _ai_report(scope, enterprises, events)
        except llm_client.AIUnavailableError:
            pass
    return _rule_report(scope, enterprises, events)


# ---------- 4. 股权穿透解读 ----------
def _rel_rows(related: list) -> List[dict]:
    rows = []
    for r in related or []:
        if isinstance(r, dict):
            rows.append(r)
        else:
            rows.append({"name": getattr(r, "name", ""), "relation": getattr(r, "relation", ""),
                         "pen_level": getattr(r, "pen_level", 0),
                         "direction": getattr(r, "direction", "")})
    return rows


def _rel_line(r: dict) -> str:
    tag = "上游股东" if r.get("direction") == "up" else ("下游投资" if r.get("direction") == "down" else "关联")
    return f"- [{tag}][层级{r.get('pen_level') or 0}] {r.get('name') or '（未命名）'}（{r.get('relation') or '关系未知'}）"


def _ai_penetration(ent_name: str, related: list) -> dict:
    tree = "\n".join(_rel_line(r) for r in _rel_rows(related)[:40]) or "（无穿透关系数据）"
    prompt = (
        f"你是银行贷后风险审查专家。以下是企业「{ent_name}」的股权穿透关系：\n{tree}\n\n"
        "请识别以下风险并输出解读：1）互保/循环担保风险；2）关联交易风险；3）实际控制人变化风险。"
        "中文分点，简洁专业。"
    )
    return {"text": _chat(prompt).strip(), "engine": "ai", "pending_review": 0}


def _rule_penetration(ent_name: str, related: list) -> dict:
    rows = _rel_rows(related)
    ups = [r for r in rows if r.get("direction") == "up"]
    downs = [r for r in rows if r.get("direction") == "down"]
    max_level = max((int(r.get("pen_level") or 0) for r in rows), default=0)
    lines = [DEGRADED_PREFIX,
             f"【穿透结构】「{ent_name}」上游股东 {len(ups)} 家、下游投资 {len(downs)} 家，穿透层级最深 {max_level} 层。"]
    lines.extend(_rel_line(r) for r in rows[:20])
    lines.append("【风险提示】请人工核查：1）与上下游主体之间是否存在互保、循环担保；"
                 "2）关联交易规模与定价公允性；3）实际控制人及股权结构是否发生变更。")
    return {"text": "\n".join(lines), "engine": "rule", "pending_review": 1}


def analyze_penetration(ent_name: str, related: list) -> dict:
    """穿透解读：AI 可用识别互保/关联交易/实控人变化风险；不可用规则描述穿透树。"""
    if llm_client.is_available():
        try:
            return _ai_penetration(ent_name, related)
        except llm_client.AIUnavailableError:
            pass
    return _rule_penetration(ent_name, related)


# ---------- 5. 预警降噪 ----------
def _ai_relevant_check(event: Event) -> bool:
    """AI 复核单条事件与授信风险的相关性；无法解析时保守返回 True。"""
    prompt = (
        "你是银行贷后风险监控助手。请判断以下事件是否与该企业的授信风险直接相关（用于相关性降噪）。\n"
        f"事件标题：{event.title}\n摘要：{event.summary or '（无）'}\n"
        '严格只输出一个 JSON 对象：{"relevant": true} 或 {"relevant": false}'
    )
    data = _extract_json(_chat(prompt, 0.0)) or {}
    if isinstance(data.get("relevant"), bool):
        return data["relevant"]
    return True


def triage_events(events: List[Event]) -> List[Event]:
    """预警降噪：规则初筛（high，或 medium 且 relevance=high）→ AI 逐条复核降噪。"""
    cand = [e for e in events
            if e.severity == "high" or (e.severity == "medium" and e.relevance == "high")]
    if not cand or not llm_client.is_available():
        return cand  # AI 不可用直接返回规则初筛结果
    kept = []
    for e in cand:
        try:
            if _ai_relevant_check(e):
                kept.append(e)
        except llm_client.AIUnavailableError:
            kept.append(e)  # AI 中途降级：剩余事件保守保留
        except Exception as e:
            log.warning("AI 复核异常，保守保留事件: %s", e)
            kept.append(e)
    return kept
