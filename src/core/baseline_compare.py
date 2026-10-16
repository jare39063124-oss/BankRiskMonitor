"""放款前基线 + 放款后差异对比引擎（v2 增量 R1，**全系统唯一真源**）。

设计要点（ARCH-v2-increment §1.2/§1.3/§3.3/附录 B）：

* ``classify(event, ent, baseline) -> Verdict`` 为**纯函数**：只做字符串/日期运算，
  不查库、不发网，100% 可单测（无需 DB/网络）。
* 判定结果**不落库**（不新增 ``event`` 列、不改 ``severity``），在读取层派生。
* 三态判定：``change``（放款后变更 → 问题） / ``baseline``（放款前基线 → 非问题，
  但完整留存） / ``undetermined``（无法判定 → 按现状披露，**严禁自动降级为非问题**）。
* 边界口径（用户已拍板）：``变更日期 >= 放款日期`` 即 ``change``（**同日也算问题**）。
* 降级规则：无放款日 → ``undetermined(no_loan_date)``；无事件日期 →
  ``undetermined(no_date)``；有放款日但无基线 → ``undetermined(no_baseline)``。

基线构建 ``build_baseline`` 按三源优先级逐维降级：
① 数据源带日期的变更记录（含连接器代查档案） → ② 用户手动补录的放款时点状态
（``baseline_snapshot`` 表 ``source='manual'`` 行） → ③ 放款前已采集的历史事件。
逐维来源记入 ``dim_sources``；混合来源时整体 ``source='mixed'``；三维皆无返回 ``None``。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .models import BaselineSnapshot, Enterprise, Event

try:  # 允许以顶层 src.core.db 或相对导入两种方式加载
    from . import db
except ImportError:  # pragma: no cover - 兼容脚本直跑场景
    from src.core import db  # type: ignore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 三态常量与标签（冻结，ARCH §8.1/§8.2）
# ---------------------------------------------------------------------------
BUCKET_CHANGE = "change"              # 放款后变更 → 问题
BUCKET_BASELINE = "baseline"          # 放款前基线 → 非问题
BUCKET_UNDETERMINED = "undetermined"  # 无法判定 → 按现状披露

PHASE_LABELS = {"change": "放款后·变更", "baseline": "放款前·基线", "undetermined": "无法判定"}

# 详情/标签细分文案（ARCH §3.3 冻结）
PHASE_LABELS_DETAIL = {
    "change": "放款后·变更",
    "baseline": "放款前·基线",
    "undetermined": "基线缺失·无法对比",   # N3（无基线）
    "no_loan": "未设放款日·无法对比",       # N4（无放款日）
    "no_date": "事件日期未知·无法对比",     # 事件日期缺失
}

# ---------------------------------------------------------------------------
# 11 个对比维度（key -> 中文标签，ARCH §8.4/附录 B 冻结）
# ---------------------------------------------------------------------------
DIMENSIONS: Tuple[Tuple[str, str], ...] = (
    ("legal_person", "法定代表人"),
    ("reg_capital", "注册资本"),
    ("shareholders", "股东与股权结构"),
    ("business_scope", "经营范围"),
    ("business_status", "经营状态"),
    ("abnormal_flag", "经营异常"),
    ("penalty_count", "行政处罚"),
    ("lawsuit_count", "涉诉新增"),
    ("enforcement_status", "被执行失信新增"),
    ("tax_status", "税务状态"),
    ("risk_tags", "风险标签"),
)

# 维度 key -> 中文标签
DIM_LABELS: Dict[str, str] = dict(DIMENSIONS)

# 属性维（取字符串，与基线旧值 diff）
STATE_DIMS = {"legal_person", "reg_capital", "shareholders", "business_scope", "business_status"}
# 新增维（按较基线"新增/增量"识别，不看总量）
NEW_DIMS = {"abnormal_flag", "penalty_count", "lawsuit_count", "enforcement_status",
            "tax_status", "risk_tags"}

# 维度默认空值（用于合并时的"该维缺失"判定）
_DIM_DEFAULTS: Dict[str, object] = {
    "legal_person": "", "reg_capital": "", "shareholders": "", "business_scope": "",
    "business_status": "", "abnormal_flag": None, "penalty_count": None,
    "lawsuit_count": None, "enforcement_status": "", "tax_status": "", "risk_tags": "",
}

# ---------------------------------------------------------------------------
# 关键词表（变更识别规则，ARCH 附录 B）
# ---------------------------------------------------------------------------
_ABNORMAL_WORDS = ("经营异常", "异常名录", "列入经营异常")
_PENALTY_WORDS = ("行政处罚", "处罚", "罚款")
_TAX_WORDS = ("欠税", "非正常户", "重大税收违法", "税收违法", "税务")
_STATUS_WORDS = ("经营状态", "登记状态", "存续", "在业", "注销", "吊销", "迁出", "设立")
_SHAREHOLDER_WORDS = ("股东", "投资人", "股权", "持股")

# "X变更为Y" / "X变更到Y" → 取 Y（遇逗号/句号/换行或行尾截止；保留分号，兼容经营范围条目）
_AFTER_RE = re.compile(r"变更(?:到)?为\s*(.+?)(?=[，,。\n\r]|$)")
_STATUS_RE = re.compile(r"(?:登记状态|经营状态|状态)[:：]?\s*([^\s，,；;。]+)")


@dataclass
class DiffItem:
    """单个维度的对比差异项。"""

    dim: str        # 维度 key（11 维之一）
    label: str      # 中文标签
    old: str        # 基线值（旧）
    new: str        # 放款后值（新）

    def to_dict(self) -> Dict[str, str]:
        return {"dim": self.dim, "label": self.label, "old": self.old, "new": self.new}


@dataclass
class Verdict:
    """三态判定结果（读取层派生，不落库）。

    ``bucket`` 为三态之一；``subtype`` 为细分场景（``change``/``baseline``/
    ``no_loan``/``no_date``/``no_baseline``），供详情/标签文案细分（ARCH §3.3）。
    """

    bucket: str
    reason: str
    diff_items: List[DiffItem] = field(default_factory=list)
    subtype: str = ""

    def is_problem(self) -> bool:
        """仅 ``change``（放款后变更）视为问题。"""
        return self.bucket == BUCKET_CHANGE

    def phase_label(self) -> str:
        """放款时点标签（三态 + 细分文案，供 GUI 直接展示）。"""
        if self.bucket == BUCKET_CHANGE:
            return PHASE_LABELS_DETAIL["change"]
        if self.bucket == BUCKET_BASELINE:
            return PHASE_LABELS_DETAIL["baseline"]
        # undetermined：区分无放款日 / 无事件日期 / 无基线
        return PHASE_LABELS_DETAIL.get(self.subtype, PHASE_LABELS_DETAIL["undetermined"])

    def compare_text(self) -> str:
        """相对基线的对比结论（纯文本，供详情弹窗展示）。"""
        if self.bucket == BUCKET_CHANGE and self.diff_items:
            parts = [f"{d.label} {d.old or '无'}→{d.new or '无'}" for d in self.diff_items]
            return "较基线变更：" + "；".join(parts)
        if self.bucket == BUCKET_BASELINE:
            return "较基线无变化（仅记录）"
        if self.subtype == "no_baseline":
            return "无基线，无法对比"
        if self.subtype in ("no_loan",):
            return "未设放款日，无法对比"
        return "事件日期未知，无法对比"


# ---------------------------------------------------------------------------
# 日期工具（ARCH §8.5 冻结签名）
# ---------------------------------------------------------------------------
def _days_in_month(year: int, month: int) -> int:
    """返回指定年月的天数（含闰年 2 月 29 日）。"""
    if month == 2:
        leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def parse_date(value) -> str:
    """归一为 ``'YYYY-MM-DD'``；任何非法/空值 → ``''``。

    合法性校验分两步：① 形状 ``YYYY-MM-DD``（带时间戳会被截断到前 10 位、
    首尾空白会被去除）；② 月/日**语义范围**校验（月 1–12，日 1–当月天数，
    含闰年）——如 ``'2026-13-45'`` 视为非法返回 ``''``（PRD §3.5）。
    """
    s = str(value or "").strip()[:10]
    if not (len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit()):
        return ""
    year, month, day = int(s[0:4]), int(s[5:7]), int(s[8:10])
    if not (1 <= month <= 12):
        return ""
    if not (1 <= day <= _days_in_month(year, month)):
        return ""
    return s


def date_ge(a, b) -> bool:
    """``a >= b``（ISO 字符串比较）；任一为空/非法 → ``False``。"""
    da, dbb = parse_date(a), parse_date(b)
    if not da or not dbb:
        return False
    return da >= dbb


# ---------------------------------------------------------------------------
# 维度映射与取值
# ---------------------------------------------------------------------------
def _dim_of_text(text: str, category: str = "biz_change") -> str:
    """按 category + 关键词把文本映射到 11 维之一（含风险标签兜底）。"""
    text = text or ""
    if category == "lawsuit":
        return "lawsuit_count"
    if category == "enforcement":
        return "tax_status" if _is_tax(text) else "enforcement_status"
    # biz_change 及其它类别
    if "法定代表人" in text:
        return "legal_person"
    if "注册资本" in text:
        return "reg_capital"
    if any(w in text for w in _SHAREHOLDER_WORDS):
        return "shareholders"
    if "经营范围" in text:
        return "business_scope"
    if any(w in text for w in _ABNORMAL_WORDS):
        return "abnormal_flag"
    if any(w in text for w in _PENALTY_WORDS):
        return "penalty_count"
    if _is_tax(text):
        return "tax_status"
    if any(w in text for w in _STATUS_WORDS):
        return "business_status"
    # news / social / 未命中 → 风险标签兜底（全量类别统一适用，C2）
    return "risk_tags"


def dimension_of(event) -> str:
    """事件 → 11 维之一（category + 关键词映射，ARCH §3.1）。"""
    if event is None:
        return "risk_tags"
    text = f"{getattr(event, 'title', '')} {getattr(event, 'summary', '')}"
    return _dim_of_text(text, getattr(event, "category", "") or "biz_change")


def _is_tax(text: str) -> bool:
    return any(w in (text or "") for w in _TAX_WORDS)


def _is_abnormal(text: str) -> bool:
    return any(w in (text or "") for w in _ABNORMAL_WORDS)


def _is_penalty(text: str) -> bool:
    return any(w in (text or "") for w in _PENALTY_WORDS)


def _after_value(text: str) -> str:
    """从 "X变更为Y" 文本中提取"变更后"值 Y；无法解析返回 ``''``。"""
    m = _AFTER_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _enforcement_text(text: str) -> str:
    """归一被执行/失信/限高状态文本；取不到返回 ``''``。"""
    text = text or ""
    if "失信" in text:
        return "失信"
    if "限高" in text or "限制高消费" in text:
        return "限制高消费"
    if "被执行人" in text or "执行" in text:
        return "被执行人"
    return ""


def _tax_text(text: str) -> str:
    """归一税务状态文本；取不到返回 ``''``。"""
    text = text or ""
    if "非正常户" in text:
        return "非正常户"
    if "重大税收违法" in text:
        return "重大税收违法"
    if "欠税" in text:
        return "欠税"
    return ""


def _event_tags(event) -> set:
    """从事件派生风险标签集合（供风险标签维对比，ARCH §3.2 dim 11）。"""
    tags = set()
    if getattr(event, "sentiment", "") == "negative":
        tags.add("负面")
    sev = getattr(event, "severity", "")
    if sev == "high":
        tags.add("高风险")
    elif sev == "medium":
        tags.add("中风险")
    return tags


def _parse_tags(raw) -> set:
    """把 ``risk_tags`` 字段（JSON 数组 / 分隔串 / 空）解析为集合。"""
    if not raw:
        return set()
    s = str(raw).strip()
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return {str(x).strip() for x in val if str(x).strip()}
        if isinstance(val, str):
            return {val} if val.strip() else set()
    except (ValueError, TypeError):
        pass
    return {p.strip() for p in re.split(r"[,，;；|/]", s) if p.strip()}


def compare_dim(event, baseline, dim: str) -> List[DiffItem]:
    """对比事件的维度取值与基线旧值，返回差异项列表（空=无变化）。"""
    label = DIM_LABELS.get(dim, dim)
    text = f"{getattr(event, 'title', '')} {getattr(event, 'summary', '')}"

    if dim in STATE_DIMS:
        base = getattr(baseline, dim, "") or ""
        new = _after_value(text)
        if dim == "business_status" and not new:
            m = _STATUS_RE.search(text)
            new = m.group(1).strip() if m else ""
        if base and new and new != base:
            return [DiffItem(dim, label, str(base), str(new))]
        return []

    if dim == "abnormal_flag":
        base = getattr(baseline, "abnormal_flag", None)
        if _is_abnormal(text) and base != 1:
            return [DiffItem(dim, label, _flag_text(base), "已列入")]
        return []

    if dim == "penalty_count":
        base = getattr(baseline, "penalty_count", None)
        # 该维基线缺失（None）→ 无法判定增量，仅记录不判问题
        if _is_penalty(text) and base is not None:
            return [DiffItem(dim, label, str(base), str(int(base) + 1))]
        return []

    if dim == "lawsuit_count":
        base = getattr(baseline, "lawsuit_count", None)
        # 涉诉一律按"较基线新增"识别，不看存量总量；该维基线缺失 → 仅记录
        if getattr(event, "category", "") == "lawsuit" and base is not None:
            return [DiffItem(dim, label, str(base), str(int(base) + 1))]
        return []

    if dim == "enforcement_status":
        base = getattr(baseline, "enforcement_status", "") or ""
        new = _enforcement_text(text)
        if new and new != base:
            return [DiffItem(dim, label, base or "无", new)]
        return []

    if dim == "tax_status":
        base = getattr(baseline, "tax_status", "") or ""
        new = _tax_text(text)
        if new and new != base:
            return [DiffItem(dim, label, base or "无", new)]
        return []

    if dim == "risk_tags":
        base = _parse_tags(getattr(baseline, "risk_tags", ""))
        new_tags = _event_tags(event) - base
        if new_tags:
            return [DiffItem(dim, label,
                             ",".join(sorted(base)) or "无",
                             ",".join(sorted(new_tags)))]
        return []

    return []


def _flag_text(v) -> str:
    """经营异常标志 → 可读文本。"""
    if v == 1:
        return "已列入"
    if v == 0:
        return "未列入"
    return "未知"


# ---------------------------------------------------------------------------
# 判定（纯函数，ARCH §3.3 判定顺序）
# ---------------------------------------------------------------------------
def classify(event, ent, baseline) -> Verdict:
    """三态判定（纯函数，无 IO）：change / baseline / undetermined。

    判定顺序（PRD §3.5/§3.6、ARCH §3.3；FIX-3 口径澄清：放款前事件一律
    ``baseline``，无需基线参与）::

        1) 无放款日                    -> undetermined(no_loan)
        2) 事件日期为空/非法           -> undetermined(no_date)
        3) 事件日期 < 放款日           -> baseline（放款前·基线，非问题、完整留存）
        4) 事件日期 >= 放款日：
             无基线                   -> undetermined(no_baseline)
             diff 非空                -> change（放款后变更·问题）
             否则                     -> baseline（无变化，仅记录）

    说明：放款日前的日期本身即为充分证据——用户明确要求"放款前的信息一律
    记录但不视为问题"，故 ``d < loan`` 先于"无基线"判断（放款前事件在无基线
    快照时也判 ``baseline``，而非 ``undetermined``）。
    """
    loan = parse_date(getattr(ent, "loan_date", "") if ent is not None else "")
    if not loan:
        return Verdict(BUCKET_UNDETERMINED, "未设放款日，无法做放款前后对比",
                       [], subtype="no_loan")

    d = parse_date(getattr(event, "occurred_at", "") if event is not None else "")
    if not d:
        return Verdict(BUCKET_UNDETERMINED, "事件日期未知，无法判定放款前后",
                       [], subtype="no_date")

    if d < loan:  # 放款前 → 基线（非问题，完整留存；无需基线参与判定）
        return Verdict(BUCKET_BASELINE, "放款前·基线（非问题）",
                       [], subtype="baseline")

    if baseline is None:
        return Verdict(BUCKET_UNDETERMINED, "基线缺失，无法对比，本期按现状披露",
                       [], subtype="no_baseline")

    # 放款后（含同日）→ 逐维对比
    dim = dimension_of(event)
    diffs = compare_dim(event, baseline, dim)
    if diffs:
        return Verdict(BUCKET_CHANGE, "放款后变更/新增（问题）", diffs, subtype="change")
    return Verdict(BUCKET_BASELINE, "放款后事件，较基线无变化（仅记录）",
                   [], subtype="baseline")


# ---------------------------------------------------------------------------
# 基线构建（三源逐维降级，有 IO）
# ---------------------------------------------------------------------------
def _is_empty(v) -> bool:
    return v is None or v == ""


def _merge_dim(dims: Dict[str, object], dim_sources: Dict[str, str],
               dim: str, value, source: str) -> None:
    """仅当目标维度为空时写入（高优先级先写入，低优先级不覆盖）。"""
    if dim not in _DIM_DEFAULTS or _is_empty(value):
        return
    if _is_empty(dims.get(dim)):
        dims[dim] = value
        dim_sources[dim] = source


def _profile_to_dims(profile: Dict) -> Dict[str, object]:
    """把连接器/数据源的 ``profile.*`` 归一为 11 维取值。"""
    out: Dict[str, object] = {}
    if not isinstance(profile, dict):
        return out
    for key in ("legal_person", "reg_capital", "business_scope", "business_status", "tax_status"):
        if not _is_empty(profile.get(key)):
            out[key] = str(profile.get(key))
    for key in ("penalty_count", "lawsuit_count"):
        if profile.get(key) is not None:
            try:
                out[key] = int(profile.get(key))
            except (TypeError, ValueError):
                pass
    sh = profile.get("shareholders")
    if isinstance(sh, list) and sh:
        out["shareholders"] = json.dumps(sh, ensure_ascii=False)
    elif isinstance(sh, str) and sh.strip():
        out["shareholders"] = sh.strip()
    abnormal = profile.get("abnormal")
    if abnormal is not None:
        out["abnormal_flag"] = 1 if abnormal in (True, 1, "1", "是", "true", "True") else 0
    if profile.get("enforcement_status"):
        out["enforcement_status"] = str(profile["enforcement_status"])
    tags = profile.get("risk_tags")
    if isinstance(tags, list) and tags:
        out["risk_tags"] = json.dumps([str(t) for t in tags], ensure_ascii=False)
    elif isinstance(tags, str) and tags.strip():
        out["risk_tags"] = tags.strip()
    return out


def _apply_auto_source(spec: Dict, loan: str, dims: Dict, dim_sources: Dict) -> None:
    """① 数据源带日期的变更记录 / 档案（连接器或 gsxt/爱企查）→ 逐维写入。

    同一来源内的多条变更记录按 ``change_date`` **降序**（放款前最近一次在前）消费，
    即同一维取"放款前最近一次"变更后的值（而非最早一次）。跨来源优先级不变。
    """
    src_name = str(spec.get("source") or spec.get("connector") or "auto")
    for dim, value in _profile_to_dims(spec.get("profile") or {}).items():
        _merge_dim(dims, dim_sources, dim, value, src_name)
    # 仅消费放款前的变更记录，并按变更日期降序（最近一次在前）→ 同维取最近值
    pre_changes = []
    for ch in spec.get("changes") or []:
        cd = parse_date(ch.get("change_date"))
        if not cd or cd >= loan:
            continue
        pre_changes.append((cd, ch))
    pre_changes.sort(key=lambda pair: pair[0], reverse=True)
    for _cd, ch in pre_changes:
        item = str(ch.get("change_item") or "")
        after = ch.get("after")
        dim = _dim_of_text(f"{item} {after}", "biz_change")
        _merge_dim(dims, dim_sources, dim, after, src_name)


def _apply_manual_row(row: BaselineSnapshot, dims: Dict, dim_sources: Dict) -> None:
    """② 用户手动补录（``baseline_snapshot`` 行）→ 逐维写入。"""
    src_name = row.source or "manual"
    for dim in _DIM_DEFAULTS:
        _merge_dim(dims, dim_sources, dim, getattr(row, dim, None), src_name)


def _apply_history(events: Sequence[Event], loan: str, dims: Dict, dim_sources: Dict) -> None:
    """③ 放款前已采集的历史事件 → 逐维派生写入。

    状态维（``STATE_DIMS`` / 被执行 / 税务）取**放款前最近一次**变更后的取值
    （即放款时点的实际状态）：将放款前事件按 ``occurred_at`` **降序**（最新在前、
    同日按 id 降序）遍历，配合 ``_merge_dim`` 的"首个非空即留"语义 → 每一维命中的
    即"最近一次"有效变更值。跨来源优先级不受影响（①自动源/②手动已写入的维仍不会
    被历史来源覆盖，因 ``_merge_dim`` 仅在目标维为空时写入）。
    """
    pre = [e for e in events
           if parse_date(getattr(e, "occurred_at", "")) and parse_date(e.occurred_at) < loan]
    # 放款前事件按日期降序（最新在前）→ 同一维多次变更时保留"最近一次"后的状态
    pre.sort(key=lambda e: (parse_date(e.occurred_at), e.id or 0), reverse=True)
    lawsuit = penalty = 0
    abnormal = 0
    tags: set = set()
    for e in pre:
        text = f"{getattr(e, 'title', '')} {getattr(e, 'summary', '')}"
        dim = dimension_of(e)
        if dim == "lawsuit_count":
            lawsuit += 1
        elif dim == "penalty_count":
            penalty += 1
        elif dim == "abnormal_flag":
            abnormal = 1
        elif dim in STATE_DIMS:
            val = _after_value(text)
            if dim == "business_status" and not val:
                m = _STATUS_RE.search(text)
                val = m.group(1).strip() if m else ""
            _merge_dim(dims, dim_sources, dim, val, "history")
        elif dim == "enforcement_status":
            _merge_dim(dims, dim_sources, dim, _enforcement_text(text), "history")
        elif dim == "tax_status":
            _merge_dim(dims, dim_sources, dim, _tax_text(text), "history")
        elif dim == "risk_tags":
            tags |= _event_tags(e)
    if lawsuit:
        _merge_dim(dims, dim_sources, "lawsuit_count", lawsuit, "history")
    if penalty:
        _merge_dim(dims, dim_sources, "penalty_count", penalty, "history")
    if abnormal:
        _merge_dim(dims, dim_sources, "abnormal_flag", abnormal, "history")
    if tags:
        _merge_dim(dims, dim_sources, "risk_tags",
                   json.dumps(sorted(tags), ensure_ascii=False), "history")


def build_baseline(ent, sources: Optional[Sequence[Dict]] = None) -> Optional[BaselineSnapshot]:
    """按三源优先级逐维构建放款前基线快照；无放款日或三维皆无 → ``None``。

    :param ent: 企业（须含 ``id`` 与 ``loan_date``）
    :param sources: 可选，数据源带日期变更记录/档案列表，元素形如
        ``{"source": "tyc_mcp", "profile": {...}, "changes": [{...}]}``
    :returns: ``BaselineSnapshot`` 或 ``None``
    """
    if ent is None:
        return None
    loan = parse_date(getattr(ent, "loan_date", ""))
    if not loan:
        return None  # 无放款日无法建立基线（N4）

    dims: Dict[str, object] = dict(_DIM_DEFAULTS)
    dim_sources: Dict[str, str] = {}

    # ① 数据源带日期的变更记录 / 档案（含连接器代查）
    for spec in sources or []:
        try:
            _apply_auto_source(dict(spec), loan, dims, dim_sources)
        except Exception as exc:  # noqa: BLE001 - 单个来源异常不影响其它来源
            log.warning("build_baseline: 来源 %s 解析失败：%s", spec.get("source"), exc)

    # ② 用户手动补录的放款时点状态（baseline_snapshot 行）
    ent_id = getattr(ent, "id", None)
    if ent_id:
        try:
            row = db.get_baseline(ent_id)
            if row is not None:
                _apply_manual_row(row, dims, dim_sources)
        except Exception as exc:  # noqa: BLE001 - 取数失败降级到下一源
            log.warning("build_baseline: 读取手动基线失败（ent=%s）：%s", ent_id, exc)

    # ③ 放款前已采集的历史事件
    if ent_id:
        try:
            events = db.list_events(enterprise_id=ent_id, limit=2000)
            _apply_history(events, loan, dims, dim_sources)
        except Exception as exc:  # noqa: BLE001
            log.warning("build_baseline: 读取历史事件失败（ent=%s）：%s", ent_id, exc)

    if all(_is_empty(dims[d]) for d in _DIM_DEFAULTS):
        return None  # 三维皆无 → 基线缺失（N3）

    used = {s for s in dim_sources.values() if s}
    source = used.pop() if len(used) == 1 else ("mixed" if used else "")
    return BaselineSnapshot(
        enterprise_id=ent_id or 0,
        loan_date=loan,
        source=source,
        dim_sources=json.dumps(dim_sources, ensure_ascii=False),
        **{dim: dims[dim] for dim in _DIM_DEFAULTS},
    )


# ---------------------------------------------------------------------------
# 基线存储（薄封装 db，ARCH §3.1 BaselineStore）
# ---------------------------------------------------------------------------
class BaselineStore:
    """``baseline_snapshot`` 表的薄封装（委托 ``core.db``）。"""

    @staticmethod
    def get(ent_id: int) -> Optional[BaselineSnapshot]:
        """按企业 id 取基线快照。"""
        return db.get_baseline(ent_id)

    @staticmethod
    def save(snapshot: BaselineSnapshot) -> int:
        """按 ``enterprise_id`` 幂等写入基线快照，返回行 id。"""
        fields = {dim: getattr(snapshot, dim, None) for dim in _DIM_DEFAULTS}
        fields.update({
            "loan_date": getattr(snapshot, "loan_date", ""),
            "source": getattr(snapshot, "source", ""),
            "dim_sources": getattr(snapshot, "dim_sources", "{}") or "{}",
            "note": getattr(snapshot, "note", ""),
        })
        return db.upsert_baseline(snapshot.enterprise_id, **fields)

    @staticmethod
    def list_all() -> List[BaselineSnapshot]:
        """列出全部基线快照。"""
        return db.list_baselines()

    @staticmethod
    def delete(ent_id: int) -> int:
        """删除指定企业基线快照，返回受影响行数。"""
        return db.delete_baseline(ent_id)

    def build_and_save(self, ent, sources: Optional[Sequence[Dict]] = None
                       ) -> Optional[BaselineSnapshot]:
        """构建基线并落库（供刷新时自动建立/更新基线使用）。"""
        snapshot = build_baseline(ent, sources)
        if snapshot is not None:
            try:
                self.save(snapshot)
            except Exception as exc:  # noqa: BLE001 - 落库失败不影响判定
                log.warning("BaselineStore.build_and_save 落库失败（ent=%s）：%s",
                            getattr(ent, "id", None), exc)
        return snapshot
