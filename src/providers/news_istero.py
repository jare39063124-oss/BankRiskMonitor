"""起零数据免费热榜接口适配器（澎湃新闻热榜等，source=istero_pengpai）。

该源为通用热榜：对每个企业都返回近期热榜条目（中性、low），
标题含企业名时 relevance 提高为 high。token 为空则抛异常跳过该源。
"""
try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase


def _extract_items(data):
    """从接口返回 JSON 中提取条目列表（兼容常见的多层包装结构）。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "result", "list", "items", "newslist", "rows"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                sub = _extract_items(v)
                if sub:
                    return sub
    return []


class IsteroNewsProvider(ProviderBase):
    name = "istero_pengpai"
    category = "news"

    def fetch(self, ent: Enterprise) -> list:
        base = self.params.get("base_url") or ""
        token = self.params.get("token") or ""
        if not base:
            raise RuntimeError("istero base_url 未配置")
        if not token:
            raise RuntimeError("istero token 未配置，跳过该源")
        resp = self.http_get(base, params={"token": token})  # GET 返回 JSON，如接口为 POST 再按文档调整
        if resp.status_code != 200:
            raise RuntimeError(f"istero HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("istero 返回非 JSON")
        items = _extract_items(data)
        if not items:
            raise RuntimeError("istero 未解析到热榜条目")
        events = []
        for it in items[:30]:
            title = (it.get("title") or it.get("name") or it.get("word") or "").strip()
            if not title:
                continue
            hit = bool(ent.name) and ent.name in title
            url = it.get("url") or it.get("link") or ""
            occurred = (it.get("time") or it.get("pub_time") or "")[:10]
            events.append(Event(
                category="news", title=title, summary="", url=url, source=self.name,
                occurred_at=occurred, severity="medium" if hit else "low",
                sentiment="neutral", relevance="high" if hit else "low"))
        if not events:
            raise RuntimeError("istero 热榜条目均为空标题")
        return events
