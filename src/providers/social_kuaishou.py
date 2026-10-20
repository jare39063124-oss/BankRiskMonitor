"""快手搜索适配器：解析 https://www.kuaishou.com/search/video?searchKey={企业名}。

快手反爬严格：风控/未登录下页面缺少数据块；注入登录态后服务端会返回含
__APOLLO_STATE__ 的页面。本适配器对多个候选数据块标记做容错试探。
"""
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

SEARCH_URL = "https://www.kuaishou.com/search/video"
_ANTI_MARKS = ("captcha", "验证码", "环境异常")
# 候选数据块标记：快手改版频繁，逐个试探
_MARKERS = ("__APOLLO_STATE__", "__INITIAL_STATE__", "window.__INITIAL_STATE__",
             "__NUXT__", "RENDER_DATA")


class KuaishouProvider(SocialProviderBase):
    name = "kuaishou"
    category = "social"
    site = "kuaishou"
    referer = "https://www.kuaishou.com/"

    def fetch(self, ent: Enterprise) -> list:
        self.ensure_auth()
        text = self.fetch_html(SEARCH_URL, params={"searchKey": ent.name})
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError(login_required_hint("快手触发反爬验证"))
        try:
            data = extract_json_block_lenient(text, _MARKERS)
        except RuntimeError as e:
            raise RuntimeError(login_required_hint(f"快手页面解析失败: {e}"))
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
