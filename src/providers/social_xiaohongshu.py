"""小红书搜索适配器：解析 https://www.xiaohongshu.com/search_result?keyword={企业名}。

小红书页面里 __INITIAL_STATE__ 数据块存在但常混入未转义字符 / undefined，
严格 JSON 解析会在中途失败；这里用容错抽取（多标记 + 增量 raw_decode）。
风控下页面完全无数据块，抛异常走 fallback。
"""
try:
    from src.core.models import Enterprise
    from src.providers.social_base import (SocialProviderBase, anti_spider_hit,
                                           extract_json_block_lenient,
                                           login_required_hint, walk_dicts)
except ImportError:
    from ..core.models import Enterprise
    from .social_base import (SocialProviderBase, anti_spider_hit,
                              extract_json_block_lenient, login_required_hint,
                              walk_dicts)

SEARCH_URL = "https://www.xiaohongshu.com/search_result"
_ANTI_MARKS = ("captcha", "验证码", "环境异常", "安全验证")
# 候选数据块标记：小红书改版频繁，逐个试探
_MARKERS = ("__INITIAL_STATE__", "window.__INITIAL_STATE__", "RENDER_DATA")


class XiaohongshuProvider(SocialProviderBase):
    name = "xiaohongshu"
    category = "social"
    site = "xiaohongshu"
    referer = "https://www.xiaohongshu.com/"

    _NOTE_MARKS = {"display_title", "noteCard", "xsec_token"}  # 笔记节点特征

    def fetch(self, ent: Enterprise) -> list:
        self.ensure_auth()
        text = self.fetch_html(SEARCH_URL, params={"keyword": ent.name})
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError(login_required_hint("小红书触发反爬验证"))
        try:
            data = extract_json_block_lenient(text, _MARKERS)
        except RuntimeError as e:
            raise RuntimeError(login_required_hint(f"小红书页面解析失败: {e}"))
        events = []
        for node in walk_dicts(data):
            if "id" not in node or not (self._NOTE_MARKS & set(node)):
                continue  # 仅取笔记条目节点
            card = node.get("noteCard")
            card = card if isinstance(card, dict) else {}
            title = str(node.get("display_title") or card.get("display_title")
                        or card.get("title") or "").strip()
            if not title:
                continue
            note_id = str(node.get("id") or "")
            url = str(node.get("share_link") or "") or \
                (f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "")
            user = node.get("user")
            nick = user.get("nickname", "") if isinstance(user, dict) else ""
            events.append(self.make_event(
                ent, title, summary=f"{nick}：{title}" if nick else title, url=url))
        return events
