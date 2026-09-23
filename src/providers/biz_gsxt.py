"""国家企业信用信息公示系统适配器（www.gsxt.gov.cn，可选源）。

该站点有强反爬（JS 挑战 + 滑动验证码），此处仅做最基本的公开查询尝试；
任何拦截/解析失败都抛异常，由 run_with_fallback 接管。
"""
from bs4 import BeautifulSoup

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

HOMEPAGE_URL = "https://www.gsxt.gov.cn/corp-query-homepage.html"
SEARCH_URL = "https://www.gsxt.gov.cn/corp-query-search-1.html"

_BAD_STATUS = ("注销", "吊销", "经营异常", "严重违法")


class GsxtProvider(ProviderBase):
    name = "gsxt"
    category = "biz_change"

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": "https://www.gsxt.gov.cn/"})
        return h

    def fetch(self, ent: Enterprise) -> list:
        # 先访问首页取基础 cookie，再发搜索请求
        home = self.http_get(HOMEPAGE_URL)
        if home.status_code != 200:
            raise RuntimeError(f"gsxt 首页 HTTP {home.status_code}")
        resp = self.http_get(SEARCH_URL, params={
            "tab": "", "queryword": ent.name, "searchword": ent.name})
        if resp.status_code != 200:
            raise RuntimeError(f"gsxt 搜索 HTTP {resp.status_code}")
        text = resp.text
        if "验证" in text or " lashen " in text or len(text) < 500:
            raise RuntimeError("gsxt 反爬拦截（需要验证码/JS 挑战）")
        return self._parse(text, ent)

    def _parse(self, html: str, ent: Enterprise) -> list:
        soup = BeautifulSoup(html, "html.parser")
        events = []
        for card in soup.select(".search_list .search_result, .search-result"):
            a = card.select_one("a[href]")
            title = a.get_text(" ", strip=True) if a else ""
            if not title:
                continue
            status = ""
            for kw in _BAD_STATUS:
                if kw in card.get_text(" ", strip=True):
                    status = kw
                    break
            if not status:
                continue  # 登记状态正常不产生事件
            events.append(Event(
                category="biz_change", title=f"{ent.name}：登记状态{status}",
                summary=title[:200], url=a.get("href") or "", source=self.name,
                severity="high", sentiment="negative", relevance="high"))
        return events
