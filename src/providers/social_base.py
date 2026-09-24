"""社媒类 Provider 公共基类：Cookie 注入、严重度关键词、反爬判定、JSON 数据块提取。"""
import json
from datetime import datetime

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

# 严重度关键词（初始判断，后续 AI/规则研判会覆盖）
_HIGH_WORDS = ("被执行", "失信", "查封", "破产")
_MED_WORDS = ("诉讼", "处罚", "质押")


def classify_social(title: str, summary: str = "") -> str:
    """按关键词简单判断严重度：高危词 high、中危词 medium、其余 low。"""
    text = f"{title} {summary}"
    if any(w in text for w in _HIGH_WORDS):
        return "high"
    if any(w in text for w in _MED_WORDS):
        return "medium"
    return "low"


def anti_spider_hit(text: str, marks) -> bool:
    """页面命中任一反爬特征（验证码/登录墙/空壳页）即判定被拦截。"""
    low = text.lower()
    return any(str(m).lower() in low for m in marks)


def extract_json_block(text: str, marker: str) -> dict:
    """定位 marker 之后第一个完整 JSON 对象（花括号配对，跳过字符串字面量）。"""
    i = text.find(marker)
    if i < 0:
        raise RuntimeError(f"页面缺少数据块 {marker}")
    start = text.find("{", i)
    if start < 0:
        raise RuntimeError("页面数据块格式异常")
    depth, in_str, esc = 0, False, False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:j + 1])
                except ValueError as e:
                    raise RuntimeError(f"页面数据块解析失败: {e}")
    raise RuntimeError("页面数据块未闭合")


def walk_dicts(node):
    """深度优先遍历 JSON 树，产出所有 dict 节点。"""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_dicts(v)


def ts_to_date(ts) -> str:
    """Unix 时间戳 -> ISO 日期（自动识别毫秒级），失败返回空串。"""
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts > 10 ** 12:  # 毫秒级时间戳
        ts //= 1000
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


class SocialProviderBase(ProviderBase):
    """社媒 Provider 基类：category 固定 social，cookie 非空时注入 Cookie 头。"""
    category = "social"

    def headers(self) -> dict:
        h = super().headers()
        cookie = str(self.params.get("cookie") or "").strip()
        if cookie:
            h["Cookie"] = cookie
        return h

    def make_event(self, ent: Enterprise, title: str, summary: str = "",
                   url: str = "", occurred_at: str = "") -> Event:
        """条目 -> Event 归一化：severity 按关键词，sentiment 默认 neutral。"""
        title = (title or "").strip()
        summary = (summary or "").strip()
        relevance = "high" if ent.name and ent.name in title else "medium"
        return Event(
            category="social", title=title, summary=summary[:300], url=url,
            source=self.name, occurred_at=occurred_at,
            severity=classify_social(title, summary),
            sentiment="neutral", relevance=relevance)
