"""百度爱企查适配器（参考开源 ENScan_GO 思路用 Python 实现）。

流程：搜索页拿 pid -> 详情 AJAX 拿变更记录/股东/对外投资。
公开接口可能要求 cookie（params.cookie 可注入），失败一律抛异常走 fallback。
同时提供 fetch_shareholders / fetch_investments 供股权穿透引擎复用。
"""
import re

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

SEARCH_URL = "https://aiqicha.baidu.com/s"
CHANGE_URL = "https://aiqicha.baidu.com/detail/newChangeListAjax"
HOLDER_URL = "https://aiqicha.baidu.com/detail/newHolderAjax"
INVEST_URL = "https://aiqicha.baidu.com/detail/newInvestmentAjax"

# 变更事项 -> Event 标题模板（仅提取任务要求的四类）
_CHANGE_TEMPLATES = (
    ("股东", "股东由{b}变更为{a}"),
    ("法定代表人", "法定代表人由{b}变更为{a}"),
    ("注册资本", "注册资本由{b}变更为{a}"),
    ("地址", "注册地址由{b}变更为{a}"),
)


def _to_ratio(v):
    """把接口返回的比例统一成百分数（float），无法解析返回 None。"""
    if v in (None, "", "-"):
        return None
    if isinstance(v, (int, float)):
        return round(v * 100, 2) if v <= 1 else round(float(v), 2)
    s = str(v).replace("%", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _companies(data):
    """从 AJAX JSON 中提取记录列表。"""
    if isinstance(data, dict):
        for key in ("list", "result", "data", "rows"):
            v = data.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                sub = _companies(v)
                if sub:
                    return sub
    return []


class AiqichaProvider(ProviderBase):
    name = "aiqicha"
    category = "biz_change"

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": "https://aiqicha.baidu.com/",
                  "Accept": "application/json, text/html"})
        cookie = self.params.get("cookie") or ""
        if cookie:
            h["Cookie"] = cookie  # 需登录态时由配置注入（latin-1 字符）
        return h

    # ---------- 基础请求 ----------
    def _search_pid(self, name: str) -> str:
        """搜索页解析企业 pid。"""
        resp = self.http_get(SEARCH_URL, params={"q": name})
        if resp.status_code != 200:
            raise RuntimeError(f"爱企查搜索 HTTP {resp.status_code}")
        text = resp.text
        if "安全验证" in text or len(text) < 500:
            raise RuntimeError("爱企查搜索被反爬拦截")
        m = re.search(r"company_detail_(\d+)", text) or re.search(r'"pid"\s*:\s*"?(\d+)', text)
        if not m:
            raise RuntimeError("爱企查未解析到企业 pid")
        return m.group(1)

    def _get_json(self, url: str, params: dict) -> dict:
        resp = self.http_get(url, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"爱企查接口 HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError("爱企查接口返回非 JSON")

    @staticmethod
    def _first_pid_from_html(html: str) -> str:
        m = re.search(r"company_detail_(\d+)", html)
        return m.group(1) if m else ""

    # ---------- 变更记录（category=biz_change 事件） ----------
    def fetch(self, ent: Enterprise) -> list:
        pid = self._search_pid(ent.name)
        data = self._get_json(CHANGE_URL, {"pid": pid, "p": 1, "size": 20})
        rows = _companies(data)
        events = []
        for it in rows:
            item = str(it.get("changeItem") or it.get("item") or "")
            before = str(it.get("beforeContent") or "").strip() or "未知"
            after = str(it.get("afterContent") or "").strip() or "未知"
            for key, tpl in _CHANGE_TEMPLATES:
                if key in item:
                    title = f"{ent.name}：{tpl.format(b=before[:40], a=after[:40])}"
                    severity = "medium" if key in ("股东", "法定代表人") else "low"
                    events.append(Event(
                        category="biz_change", title=title,
                        summary=f"{item}：{before} -> {after}",
                        url=f"https://aiqicha.baidu.com/company_detail_{pid}",
                        source=self.name, occurred_at=str(it.get("changeDate") or "")[:10],
                        severity=severity, sentiment="neutral", relevance="high"))
                    break
        return events  # 无变更记录返回空，fallback 依次尝试下一个源

    # ---------- 股东 / 对外投资（供穿透引擎复用） ----------
    def _holders(self, name: str) -> list:
        pid = self._search_pid(name)
        data = self._get_json(HOLDER_URL, {"pid": pid, "p": 1, "size": 50})
        out = []
        for r in _companies(data):
            nm = str(r.get("subName") or r.get("name") or "").strip()
            if nm:
                out.append({"name": nm, "ratio": _to_ratio(r.get("ratio") or r.get("subRatio"))})
        return out

    def _investments(self, name: str) -> list:
        pid = self._search_pid(name)
        data = self._get_json(INVEST_URL, {"pid": pid, "p": 1, "size": 50})
        out = []
        for r in _companies(data):
            nm = str(r.get("subName") or r.get("name") or r.get("entName") or "").strip()
            if nm:
                out.append({"name": nm, "ratio": _to_ratio(r.get("ratio") or r.get("subRatio"))})
        return out


def fetch_shareholders(ent_name: str) -> list:
    """查询企业股东：[{'name': 股东名, 'ratio': 持股百分数}]。失败抛异常。"""
    return AiqichaProvider({})._holders(ent_name)


def fetch_investments(ent_name: str) -> list:
    """查询企业对外投资：[{'name': 被投企业, 'ratio': 持股百分数}]。失败抛异常。"""
    return AiqichaProvider({})._investments(ent_name)
