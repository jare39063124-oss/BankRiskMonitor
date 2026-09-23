"""核心数据模型（契约）：所有层共用，字段一经定义不得随意增删。"""
from dataclasses import dataclass, field
from typing import Optional

CATEGORIES = ("news", "biz_change", "lawsuit", "enforcement")
CATEGORY_LABELS = {"news": "舆情新闻", "biz_change": "工商变更", "lawsuit": "涉诉", "enforcement": "被执行/查封"}
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
