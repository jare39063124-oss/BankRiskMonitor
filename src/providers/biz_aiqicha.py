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

try:
    from src.core import settings as _settings
except ImportError:
    from ..core import settings as _settings

SEARCH_URL = "https://aiqicha.baidu.com/s"
FILTER_URL = "https://aiqicha.baidu.com/s/advanceFilterAjax"   # JSON 搜索接口
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
            h["Cookie"] = cookie  # 登录态 Cookie（config 注入），未登录每日配额有限
        return h

    # ---------- 基础请求 ----------
    def _session_get(self, url: str, params: dict = None):
        """（保留给 _pid_via_json 之外的调用方）带会话预热的主页流 GET。"""
        import requests
        if not hasattr(self, "_session"):
            self._session = requests.Session()
            try:
                self._session.get("https://aiqicha.baidu.com/",
                                  headers=self.headers(), timeout=self.timeout)
            except Exception:  # noqa: BLE001  预热失败不阻断
                pass
        self._throttle()
        kw = {"headers": self.headers(), "timeout": self.timeout}
        proxy = _settings.config().get("app", {}).get("proxy", "")
        if proxy:
            kw["proxies"] = {"http": proxy, "https": proxy}
        return self._session.get(url, params=params, **kw)

    def _search_pid(self, name: str) -> str:
        """搜索解析企业 pid：HTML 搜索页（编码修复版）为主，JSON 接口反爬重试。"""
        resp = self.http_get(SEARCH_URL, params={"q": name})
        if resp.status_code != 200:
            raise RuntimeError(f"爱企查搜索 HTTP {resp.status_code}")
        resp.encoding = "utf-8"  # 修复：默认编码猜测会把"安全验证"变乱码导致漏检
        text = resp.text
        if "安全验证" in text or "wappass" in text or len(text) < 500:
            # HTML 被拦时尝试 JSON 搜索接口（会话流，成功率更高）
            pid = self._pid_via_json(name)
            if pid:
                return pid
            raise RuntimeError("爱企查搜索被反爬拦截（可在 config aiqicha.params.cookie 填登录 Cookie）")
        m = re.search(r"company_detail_(\d+)", text) or re.search(r'"pid"\s*:\s*"?(\d+)', text)
        if not m:
            raise RuntimeError("爱企查未解析到企业 pid")
        return m.group(1)

    def _pid_via_json(self, name: str) -> str:
        """会话流 JSON 搜索接口：预热主页拿 Cookie 后调用 advanceFilterAjax。"""
        try:
            import requests
            if not hasattr(self, "_session"):
                self._session = requests.Session()
                self._session.get("https://aiqicha.baidu.com/",
                                  headers=self.headers(), timeout=self.timeout)
            self._throttle()
            resp = self._session.get(
                FILTER_URL, params={"q": name, "p": 1, "s": 10, "t": 0},
                headers=self.headers(), timeout=self.timeout)
            resp.encoding = "utf-8"
            data = resp.json()
            payload = (data.get("data") or {}) if isinstance(data, dict) else {}
            lst = payload.get("list") or []
            if lst:
                pid = str(lst[0].get("pid") or "")
                if pid:
                    return pid
            limit = payload.get("limitForward") or {}
            if str(limit.get("userType") or "") == "nologin":
                raise RuntimeError(
                    "爱企查未登录配额为 0：请在 config.yaml aiqicha.params.cookie "
                    "填入浏览器登录 Cookie（F12 复制），或等待次日配额")
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001
            return ""

    def _get_json(self, url: str, params: dict) -> dict:
        resp = self.http_get(url, params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"爱企查接口 HTTP {resp.status_code}")
        resp.encoding = "utf-8"
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
