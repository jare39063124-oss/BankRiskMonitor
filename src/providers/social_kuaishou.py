"""快手搜索适配器：解析 https://www.kuaishou.com/search/video?searchKey={企业名}。

快手反爬严格：风控/未登录下页面缺少 __APOLLO_STATE__ 数据块，抛异常走 fallback。
"""
try:
    from src.core.models import Enterprise
    from src.providers.social_base import (SocialProviderBase, anti_spider_hit,
                                           extract_json_block, ts_to_date, walk_dicts)
except ImportError:
    from ..core.models import Enterprise
    from .social_base import (SocialProviderBase, anti_spider_hit,
                              extract_json_block, ts_to_date, walk_dicts)

SEARCH_URL = "https://www.kuaishou.com/search/video"
_ANTI_MARKS = ("captcha", "验证码", "环境异常")


class KuaishouProvider(SocialProviderBase):
    name = "kuaishou"
    category = "social"

    def fetch(self, ent: Enterprise) -> list:
        resp = self.http_get(SEARCH_URL, params={"searchKey": ent.name})
        if resp.status_code != 200:
            raise RuntimeError(f"快手 HTTP {resp.status_code}")
        text = resp.text
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError("快手触发反爬验证")
        try:
            data = extract_json_block(text, "__APOLLO_STATE__")
        except RuntimeError as e:
            raise RuntimeError(f"快手页面解析失败: {e}")
        events = []
        for node in walk_dicts(data):
            caption = str(node.get("caption") or "").strip()
            if not caption:  # 仅取视频条目节点
                continue
            author = node.get("author")
            nick = author.get("nickname", "") if isinstance(author, dict) else ""
            events.append(self.make_event(
                ent, caption, summary=f"{nick}：{caption}" if nick else caption,
                url=str(node.get("photoUrl") or ""),
                occurred_at=ts_to_date(node.get("timestamp"))))
        return events
