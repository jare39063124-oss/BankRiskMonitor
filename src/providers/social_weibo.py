"""微博搜索适配器：解析 https://s.weibo.com/weibo?q={企业名} 结果页。

未登录可见性有限，正常解析可见条目；命中登录墙/验证码时抛异常走 fallback。
"""
import re
from datetime import datetime

from bs4 import BeautifulSoup

try:
    from src.core.models import Enterprise
    from src.providers.news_baidu import parse_time
    from src.providers.social_base import (SocialProviderBase, anti_spider_hit,
                                           login_required_hint)
except ImportError:
    from ..core.models import Enterprise
    from .news_baidu import parse_time
    from .social_base import (SocialProviderBase, anti_spider_hit,
                              login_required_hint)

SEARCH_URL = "https://s.weibo.com/weibo"
_ANTI_MARKS = ("passport.weibo.com", "visitor.weibo.com", "验证码", "微博安全中心")


def parse_weibo_time(s: str) -> str:
    """微博时间格式补充："09月20日"（默认当年），其余交给通用 parse_time。"""
    if not s:
        return ""
    m = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if m:
        return f"{datetime.now().year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return parse_time(s)


class WeiboProvider(SocialProviderBase):
    name = "weibo"
    category = "social"
    site = "weibo"
    referer = "https://s.weibo.com/"

    def fetch(self, ent: Enterprise) -> list:
        self.ensure_auth()
        text = self.fetch_html(SEARCH_URL, params={"q": ent.name})
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError(login_required_hint("微博触发登录墙/验证码"))
        return self._parse(text, ent)  # 无结果返回空列表，由 fallback 切换下一源

    def _parse(self, html: str, ent: Enterprise) -> list:
        soup = BeautifulSoup(html, "html.parser")
        events = []
        for card in soup.select("div.card-wrap[mid]"):
            node = card.select_one("p.txt")  # 只取博文卡片，跳过热榜/广告
            if node is None:
                continue
            content = node.get_text(" ", strip=True)
            if not content:
                continue
            time_a = card.select_one("div.from a")
            href = (time_a.get("href") if time_a else "") or ""
            url = href if href.startswith("http") else \
                ("https://s.weibo.com" + href if href else "")
            occurred = parse_weibo_time(time_a.get_text(strip=True) if time_a else "")
            nick_a = card.select_one("a.name")
            summary = f"{nick_a.get_text(strip=True)}：{content}" if nick_a else content
            events.append(self.make_event(ent, content, summary=summary,
                                          url=url, occurred_at=occurred))
        return events
