"""搜狗微信搜索适配器：解析 https://weixin.sogou.com/weixin?type=2&query={企业名}。

注意搜狗 antispider 检测（页面含 _antispider 或验证码），命中即抛异常走 fallback。
"""
from bs4 import BeautifulSoup

try:
    from src.core.models import Enterprise
    from src.providers.news_baidu import parse_time
    from src.providers.social_base import SocialProviderBase, anti_spider_hit, ts_to_date
except ImportError:
    from ..core.models import Enterprise
    from .news_baidu import parse_time
    from .social_base import SocialProviderBase, anti_spider_hit, ts_to_date

SEARCH_URL = "https://weixin.sogou.com/weixin"
_ANTI_MARKS = ("antispider", "验证码", "系统检测到")


class SogouWeixinProvider(SocialProviderBase):
    name = "wechat_sogou"
    category = "social"

    def fetch(self, ent: Enterprise) -> list:
        resp = self.http_get(SEARCH_URL, params={"type": 2, "query": ent.name})
        if resp.status_code != 200:
            raise RuntimeError(f"搜狗微信 HTTP {resp.status_code}")
        text = resp.text
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError("搜狗微信触发 antispider 反爬")
        return self._parse(text, ent)

    def _parse(self, html: str, ent: Enterprise) -> list:
        soup = BeautifulSoup(html, "html.parser")
        events = []
        for li in soup.select("ul.news-list li"):
            box = li.select_one("div.txt-box")
            if box is None:
                continue
            a = box.select_one("h3 a")
            if a is None:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            href = a.get("href", "") or ""
            url = href if href.startswith("http") else \
                ("https://weixin.sogou.com" + href if href else "")
            info = box.select_one("p.txt-info")
            summary = info.get_text(" ", strip=True) if info is not None else ""
            account = box.select_one(".account")  # 公众号名
            if account is not None:
                summary = f"{account.get_text(strip=True)}：{summary}"
            time_node = box.select_one("span.s2")  # 优先取 unix 时间戳属性，退回文本
            occurred = ""
            if time_node is not None:
                ts = str(time_node.get("t") or time_node.get("lastmodified") or "")
                occurred = ts_to_date(ts) if ts.isdigit() \
                    else parse_time(time_node.get_text(strip=True))
            events.append(self.make_event(ent, title, summary=summary,
                                          url=url, occurred_at=occurred))
        return events
