"""小红书搜索适配器：解析 https://www.xiaohongshu.com/search_result?keyword={企业名}。

小红书反爬严格：风控下页面缺少 __INITIAL_STATE__ 数据块，抛异常走 fallback。
"""
try:
    from src.core.models import Enterprise
    from src.providers.social_base import (SocialProviderBase, anti_spider_hit,
                                           extract_json_block, walk_dicts)
except ImportError:
    from ..core.models import Enterprise
    from .social_base import (SocialProviderBase, anti_spider_hit,
                              extract_json_block, walk_dicts)

SEARCH_URL = "https://www.xiaohongshu.com/search_result"
_ANTI_MARKS = ("captcha", "验证码", "环境异常", "安全验证")


class XiaohongshuProvider(SocialProviderBase):
    name = "xiaohongshu"
    category = "social"

    _NOTE_MARKS = {"display_title", "noteCard", "xsec_token"}  # 笔记节点特征

    def fetch(self, ent: Enterprise) -> list:
        resp = self.http_get(SEARCH_URL, params={"keyword": ent.name})
        if resp.status_code != 200:
            raise RuntimeError(f"小红书 HTTP {resp.status_code}")
        text = resp.text
        if anti_spider_hit(text, _ANTI_MARKS):
            raise RuntimeError("小红书触发反爬验证")
        try:
            data = extract_json_block(text, "__INITIAL_STATE__")
        except RuntimeError as e:
            raise RuntimeError(f"小红书页面解析失败: {e}")
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
