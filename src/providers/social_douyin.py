"""抖音搜索适配器：解析 https://www.douyin.com/search/{企业名} 内嵌 JSON 数据块。

抖音反爬严格：页面为 JS 空壳或验证码时拿不到 __ROUTER_DATA__，抛异常走 fallback。
"""
from urllib.parse import quote

try:
    from src.core.models import Enterprise
    from src.providers.social_base import (SocialProviderBase, anti_spider_hit,
                                           extract_json_block, ts_to_date, walk_dicts)
except ImportError:
    from ..core.models import Enterprise
    from .social_base import (SocialProviderBase, anti_spider_hit,
                              extract_json_block, ts_to_date, walk_dicts)

SEARCH_URL = "https://www.douyin.com/search"
_ANTI_MARKS = ("captcha", "安全验证", "滑动验证")


class DouyinProvider(SocialProviderBase):
    name = "douyin"
    category = "social"

    def fetch(self, ent: Enterprise) -> list:
        resp = self.http_get(f"{SEARCH_URL}/{quote(ent.name)}")
        if resp.status_code != 200:
            raise RuntimeError(f"抖音 HTTP {resp.status_code}")
        text = resp.text
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError("抖音触发反爬验证")
        try:
            data = extract_json_block(text, "__ROUTER_DATA__")
        except RuntimeError as e:
            raise RuntimeError(f"抖音页面解析失败: {e}")
        events = []
        for node in walk_dicts(data):
            desc = str(node.get("desc") or node.get("title") or "").strip()
            if not desc or "create_time" not in node:  # 仅取视频条目节点
                continue
            author = node.get("author")
            nick = author.get("nickname", "") if isinstance(author, dict) else ""
            events.append(self.make_event(
                ent, desc, summary=f"{nick}：{desc}" if nick else desc,
                url=str(node.get("share_url") or ""),
                occurred_at=ts_to_date(node.get("create_time"))))
        return events
