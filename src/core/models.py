"""核心数据模型（契约）：所有层共用，字段一经定义不得随意增删。"""
from dataclasses import dataclass, field
from typing import Optional

CATEGORIES = ("news", "biz_change", "lawsuit", "enforcement", "social")
CATEGORY_LABELS = {"news": "舆情新闻", "biz_change": "工商变更", "lawsuit": "涉诉", "enforcement": "被执行/查封", "social": "社媒"}
SEVERITIES = ("high", "medium", "low")
SENTIMENTS = ("negative", "neutral", "positive")

@dataclass
class Enterprise:
    id: Optional[int] = None
    name: str = ""
    credit_code: str = ""
    province: str = ""          # 用于地图定位，如 "浙江省"
    city: str = ""
    credit_note: str = ""       # 授信情况备注
    monitored: int = 1          # 1=监控中 0=已停止（删除为软删除）
    is_related: int = 0         # 1=穿透产生的关联主体
    parent_id: Optional[int] = None
    relation: str = ""          # 如 "股东(60%)" / "对外投资(100%)"
    pen_level: int = 0          # 穿透层级 0=主企业 1-2=穿透层
    direction: str = ""         # up/down
    loan_date: str = ""         # 放款日 YYYY-MM-DD，空=未设（v2 新增）
    created_at: str = ""

@dataclass
class Event:
    id: Optional[int] = None
    enterprise_id: int = 0
    category: str = ""          # news / biz_change / lawsuit / enforcement
    title: str = ""
    summary: str = ""
    url: str = ""
    source: str = ""            # 数据源名称，如 baidu_news
    occurred_at: str = ""       # ISO 日期字符串
    severity: str = "low"       # high / medium / low
    sentiment: str = "neutral"  # negative / neutral / positive
    relevance: str = "low"      # high / medium / low（与授信相关性）
    ai_verdict: str = ""        # AI 研判文本
    engine: str = ""            # ai / rule
    pending_review: int = 0     # 1=规则结果待 AI 复核
    created_at: str = ""

@dataclass
class CreditMaterial:
    id: Optional[int] = None
    enterprise_id: int = 0
    filename: str = ""
    path: str = ""
    text: str = ""              # 提取后的全文
    uploaded_at: str = ""

@dataclass
class QueueTask:
    id: Optional[int] = None
    task_type: str = ""         # assess / credit_impact / report / penetration
    payload: str = ""           # JSON
    status: str = "pending"     # pending / done / failed
    created_at: str = ""


@dataclass
class BaselineSnapshot:
    """放款前基线快照（v2 增量）：一企业一行，结构化 11 维 + 来源/降级链。

    字段与 ``baseline_snapshot`` 表 DDL 一一对应（见 ``db.SCHEMA`` / ARCH §3.2）。
    属性维（``legal_person``/``reg_capital``/``shareholders``/``business_scope``/
    ``business_status``）取字符串；新增维（``abnormal_flag``/``penalty_count``/
    ``lawsuit_count``）取 ``Optional[int]``，``None`` 表示该维基线缺失。
    """

    id: Optional[int] = None
    enterprise_id: int = 0
    loan_date: str = ""
    legal_person: str = ""            # 法定代表人
    reg_capital: str = ""             # 注册资本（原文，含单位）
    shareholders: str = ""            # 股东与股权结构（JSON 数组或分隔串）
    business_scope: str = ""          # 经营范围摘要/指纹
    business_status: str = ""         # 经营状态（存续/在业/注销/吊销…）
    abnormal_flag: Optional[int] = None   # 1=基线时点已列入经营异常；None=该维缺失
    penalty_count: Optional[int] = None   # 行政处罚基线条数
    lawsuit_count: Optional[int] = None   # 涉诉基线条数
    enforcement_status: str = ""      # 被执行/失信基线状态
    tax_status: str = ""              # 税务状态
    risk_tags: str = ""               # 风险标签集合（JSON 数组）
    source: str = ""                  # auto|manual|history|mixed
    dim_sources: str = "{}"           # JSON：{dim_key: 来源} 优先级降级链
    note: str = ""
    built_at: str = ""
    updated_at: str = ""
