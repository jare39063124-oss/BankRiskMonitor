"""搜狗新闻搜索适配器：解析 https://news.sogou.com/news?query={企业名} 结果页。

结构与百度适配器一致，解析失败/被反爬拦截时抛异常走 fallback。
"""
from bs4 import BeautifulSoup

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
    from src.providers.news_baidu import classify, parse_time
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase
    from .news_baidu import classify, parse_time

SEARCH_URL = "https://news.sogou.com/news"


class SogouNewsProvider(ProviderBase):
    name = "sogou_news"
    category = "news"

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": "https://news.sogou.com/"})
        return h

    def fetch(self, ent: Enterprise) -> list:
        resp = self.http_get(SEARCH_URL, params={"query": ent.name})
        if resp.status_code != 200:
            raise RuntimeError(f"搜狗新闻 HTTP {resp.status_code}")
        text = resp.text
        if "antispider" in text.lower() or "验证码" in text:
            raise RuntimeError("搜狗新闻触发反爬验证")
        events = self._parse(text, ent)
        if not events:
            raise RuntimeError("搜狗新闻未解析到结果")
        return events

    def _parse(self, html: str, ent: Enterprise) -> list:
        soup = BeautifulSoup(html, "html.parser")
        events = []
        for card in soup.select("div.vrwrap, div.rb, div.news-box"):
            a = card.select_one("h3 a") or card.select_one("a[href]")
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            url = a.get("href", "") or ""
            if url.startswith("/"):  # 搜狗跳转链接为相对路径
                url = "https://news.sogou.com" + url
            txt = card.select_one(".news-txt, .space-txt, p")
            summary = txt.get_text(" ", strip=True) if txt else ""
            meta = card.select_one(".news-from, [class*=from], [class*=source]")
            meta_text = meta.get_text(" ", strip=True) if meta else ""
            occurred = parse_time(meta_text)
            sentiment, severity = classify(title, summary)
            relevance = "high" if ent.name and ent.name in title else "medium"
            events.append(Event(
                category="news", title=title, summary=summary[:300], url=url,
                source=self.name, occurred_at=occurred, severity=severity,
                sentiment=sentiment, relevance=relevance))
        return events
