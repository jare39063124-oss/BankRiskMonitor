"""百度新闻搜索适配器：抓取百度新闻公开搜索结果页，解析标题/摘要/时间/链接。

反爬策略：命中安全验证页或无结果时抛异常，由 run_with_fallback 切换下一个源。
"""
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

try:  # 兼容两种导入方式：src.providers.*（项目根在 sys.path）与 providers.*（src 在 sys.path）
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

SEARCH_URL = "https://www.baidu.com/s"

_NEG_WORDS = ("处罚", "罚款", "失信", "被执行", "诉讼", "起诉", "官司", "败诉", "立案", "违规",
              "违法", "拖欠", "欠款", "债务", "违约", "破产", "清算", "冻结", "查封", "欠税", "停产")
_POS_WORDS = ("中标", "签约", "合作", "增长", "盈利", "获批", "融资", "上市", "获奖", "扶持", "补贴")
_HIGH_WORDS = ("破产", "清算", "失信", "被执行", "查封", "冻结", "立案", "拘留", "退市", "吊销")


def classify(title: str, summary: str = "") -> tuple:
    """基于关键词的简易情感/严重度分类，返回 (sentiment, severity)。"""
    text = f"{title} {summary}"
    sentiment = "neutral"
    if any(w in text for w in _NEG_WORDS):
        sentiment = "negative"
    elif any(w in text for w in _POS_WORDS):
        sentiment = "positive"
    if any(w in text for w in _HIGH_WORDS):
        severity = "high"
    else:
        severity = "medium" if sentiment == "negative" else "low"
    return sentiment, severity


def parse_time(s: str) -> str:
    """把"3小时前/昨天/2026-09-20"等尽量转成 ISO 日期，失败返回空串。"""
    if not s:
        return ""
    s = s.strip()
    now = datetime.now()
    m = re.search(r"(\d+)\s*分钟前", s)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    if "昨天" in s:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if "今天" in s or "刚刚" in s:
        return now.strftime("%Y-%m-%d")
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


class BaiduNewsProvider(ProviderBase):
    name = "baidu_news"
    category = "news"

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": "https://www.baidu.com/", "Accept": "text/html,application/xhtml+xml"})
        return h

    def fetch(self, ent: Enterprise) -> list:
        resp = self.http_get(SEARCH_URL, params={
            "wd": ent.name, "tn": "news", "rtt": 4, "bsst": 1, "cl": 2, "rn": 10})
        if resp.status_code != 200:
            raise RuntimeError(f"百度新闻 HTTP {resp.status_code}")
        text = resp.text
        if "百度安全验证" in text or "wappass" in text:
            raise RuntimeError("百度新闻触发反爬验证")
        events = self._parse(text, ent)
        if not events:
            raise RuntimeError("百度新闻未解析到结果（可能被反爬或无结果）")
        return events

    def _parse(self, html: str, ent: Enterprise) -> list:
        soup = BeautifulSoup(html, "html.parser")
        events = []
        for card in soup.select("div.result-op, div.result"):
            a = card.select_one("h3 a") or card.select_one("a[href]")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 6:  # 过滤"..."等噪声锚文本
                continue
            url = a.get("href", "") or ""
            abs_node = card.select_one(".c-abstract, [class*=abstract], .content-right")
            summary = abs_node.get_text(" ", strip=True) if abs_node else ""
            meta = card.select_one(".c-color-gray, [class*=source]")
            meta_text = meta.get_text(" ", strip=True) if meta else ""
            occurred = parse_time(meta_text)
            sentiment, severity = classify(title, summary)
            relevance = "high" if ent.name and ent.name in title else "medium"
            events.append(Event(
                category="news", title=title, summary=summary[:300], url=url,
                source=self.name, occurred_at=occurred, severity=severity,
                sentiment=sentiment, relevance=relevance))
        return events
