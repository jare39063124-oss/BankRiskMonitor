"""抖音搜索适配器：解析 https://www.douyin.com/search/{企业名} 内嵌 JSON 数据块。

抖音反爬严格：未登录时页面为 JS 空壳或验证码，拿不到数据块。
登录态（Cookie）注入后服务端会返回含 __ROUTER_DATA__ 的页面。
本适配器对多个候选数据块标记做容错试探，任一命中即可解析。
"""
from urllib.parse import quote

try:
    from src.core.models import Enterprise
    from src.providers.social_base import (SocialProviderBase, anti_spider_hit,
                                           extract_json_block_lenient,
                                           login_required_hint, ts_to_date,
                                           walk_dicts)
except ImportError:
    from ..core.models import Enterprise
    from .social_base import (SocialProviderBase, anti_spider_hit,
                              extract_json_block_lenient, login_required_hint,
                              ts_to_date, walk_dicts)

SEARCH_URL = "https://www.douyin.com/search"
_ANTI_MARKS = ("captcha", "安全验证", "滑动验证")
# 候选数据块标记：抖音改版频繁，逐个试探
_MARKERS = ("__ROUTER_DATA__", "RENDER_DATA", "_ROUTER_DATA", "__INITIAL_STATE__",
             "window.__INITIAL_STATE__")


class DouyinProvider(SocialProviderBase):
    name = "douyin"
    category = "social"
    site = "douyin"
    referer = "https://www.douyin.com/"

    def fetch(self, ent: Enterprise) -> list:
        self.ensure_auth()
        text = self.fetch_html(f"{SEARCH_URL}/{quote(ent.name)}")
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError(login_required_hint("抖音触发反爬验证"))
        try:
            data = extract_json_block_lenient(text, _MARKERS)
        except RuntimeError as e:
            raise RuntimeError(login_required_hint(f"抖音页面解析失败: {e}"))
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
