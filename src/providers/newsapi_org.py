"""NewsAPI.org 适配器：/v2/everything?q={企业名}，api_key 为空则抛异常跳过。"""
from datetime import date

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
    from src.providers.news_baidu import classify
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase
    from .news_baidu import classify

API_URL = "https://newsapi.org/v2/everything"


class NewsApiProvider(ProviderBase):
    name = "newsapi"
    category = "news"

    def fetch(self, ent: Enterprise) -> list:
        api_key = self.params.get("api_key") or ""
        if not api_key:
            raise RuntimeError("NewsAPI api_key 未配置，跳过该源")
        headers = self.headers()
        headers["X-Api-Key"] = api_key
        resp = self.http_get(API_URL, params={
            "q": ent.name, "language": "zh", "sortBy": "publishedAt", "pageSize": 20},
            headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"NewsAPI HTTP {resp.status_code}")
        data = resp.json()
        if data.get("status") != "ok":
            raise RuntimeError(f"NewsAPI 错误: {data.get('message')}")
        events = []
        for art in data.get("articles") or []:
            title = (art.get("title") or "").strip()
            if not title:
                continue
            summary = (art.get("description") or art.get("content") or "").strip()
            src = (art.get("source") or {}).get("name") or ""
            occurred = (art.get("publishedAt") or "")[:10] or date.today().strftime("%Y-%m-%d")
            sentiment, severity = classify(title, summary)
            relevance = "high" if ent.name and ent.name in title else "medium"
            events.append(Event(
                category="news", title=title, summary=summary[:300],
                url=art.get("url") or "", source=self.name, occurred_at=occurred,
                severity=severity, sentiment=sentiment, relevance=relevance))
        return events
