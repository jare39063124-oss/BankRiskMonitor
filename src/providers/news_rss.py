"""RSS 通用舆情源适配器：用 feedparser 解析 config params["feeds"] 中的订阅源。

按企业名关键词过滤条目：标题或摘要含企业名才生成事件，否则返回空走 fallback。
"""
import time

import feedparser

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
    from src.providers.news_baidu import classify
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase
    from .news_baidu import classify


class RssProvider(ProviderBase):
    name = "rss"
    category = "news"

    def fetch(self, ent: Enterprise) -> list:
        feeds = self.params.get("feeds") or []
        if not feeds:
            raise RuntimeError("RSS feeds 未配置")
        events = []
        for url in feeds:
            try:
                parsed = feedparser.parse(url, request_headers={"User-Agent": self.user_agent})
            except Exception as e:  # 单个订阅源失败不影响其他源
                if not events:
                    raise RuntimeError(f"RSS 解析失败: {e}")
                continue
            for entry in parsed.entries or []:
                title = (entry.get("title") or "").strip()
                summary = (entry.get("summary") or "").strip()
                if not title:
                    continue
                if not ent.name or (ent.name not in title and ent.name not in summary):
                    continue  # 关键词过滤：只保留与企业名相关的条目
                pp = entry.get("published_parsed") or entry.get("updated_parsed")
                occurred = time.strftime("%Y-%m-%d", pp) if pp else ""
                sentiment, severity = classify(title, summary)
                events.append(Event(
                    category="news", title=title, summary=summary[:300],
                    url=entry.get("link") or "", source=self.name, occurred_at=occurred,
                    severity=severity, sentiment=sentiment, relevance="high"))
        return events
