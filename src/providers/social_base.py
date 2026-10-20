"""社媒类 Provider 公共基类：登录态注入、浏览器渲染抓取、严重度关键词、反爬判定、JSON 数据块提取。"""
import json
from datetime import datetime

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase, SocialAuthMissing
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase, SocialAuthMissing

# 严重度关键词（初始判断，后续 AI/规则研判会覆盖）
_HIGH_WORDS = ("被执行", "失信", "查封", "破产")
_MED_WORDS = ("诉讼", "处罚", "质押")

__all__ = ["SocialProviderBase", "SocialAuthMissing", "classify_social",
           "anti_spider_hit", "extract_json_block", "extract_json_block_lenient",
           "walk_dicts", "ts_to_date", "login_required_hint"]

# 登录墙/反爬时的统一引导文案
LOGIN_HINT = "（请在「设置 → 社媒登录态」用小号登录该平台后重试）"


def login_required_hint(msg: str) -> str:
    """给反爬/登录墙类错误补上可操作的引导语。"""
    text = str(msg or "")
    if not text:
        return text
    if any(k in text for k in ("登录墙", "反爬", "验证", "风控", "captcha")):
        if "社媒登录态" not in text:
            text += LOGIN_HINT
    return text


def classify_social(title: str, summary: str = "") -> str:
    """按关键词简单判断严重度：高危词 high、中危词 medium、其余 low。"""
    text = f"{title} {summary}"
    if any(w in text for w in _HIGH_WORDS):
        return "high"
    if any(w in text for w in _MED_WORDS):
        return "medium"
    return "low"


def anti_spider_hit(text: str, marks) -> bool:
    """页面命中任一反爬特征（验证码/登录墙/空壳页）即判定被拦截。"""
    low = text.lower()
    return any(str(m).lower() in low for m in marks)


def extract_json_block(text: str, marker: str) -> dict:
    """定位 marker 之后第一个完整 JSON 对象（花括号配对，跳过字符串字面量）。"""
    i = text.find(marker)
    if i < 0:
        raise RuntimeError(f"页面缺少数据块 {marker}")
    start = text.find("{", i)
    if start < 0:
        raise RuntimeError("页面数据块格式异常")
    depth, in_str, esc = 0, False, False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:j + 1])
                except ValueError as e:
                    raise RuntimeError(f"页面数据块解析失败: {e}")
    raise RuntimeError("页面数据块未闭合")


def _sanitize_json_text(raw: str) -> str:
    """把 JS 字面量非法值（undefined/NaN/Infinity）替换为 JSON 合法值。"""
    out = raw.replace("undefined", "null")
    out = out.replace("NaN", "null").replace("Infinity", "null")
    return out


def extract_json_block_lenient(text: str, markers) -> dict:
    """容错提取页面数据块：依次尝试多个 marker，逐个起点做 raw_decode 试探。

    真实页面常出现「数据块存在但 JSON 尾部混入未转义字符 / undefined」的情况，
    严格解析会在中途抛错；这里对每个 ``{`` 起点做增量解码，命中即返回。

    另支持两种常见封装：
    - ``<script id="RENDER_DATA" type="application/json">%7B...``（URL 编码 JSON）
    - ``<script>window.__INITIAL_STATE__ = {...}</script>``
    """
    if isinstance(markers, str):
        markers = [markers]
    obj = _extract_encoded_json(text, markers)
    if obj is not None:
        return obj
    dec = json.JSONDecoder()
    last_err = ""
    for marker in markers:
        i = text.find(marker)
        if i < 0:
            continue
        pos = text.find("{", i)
        while pos >= 0:
            candidate = _sanitize_json_text(text[pos:pos + 4_000_000])
            try:
                obj, _ = dec.raw_decode(candidate)
                if isinstance(obj, dict):
                    return obj
            except ValueError as e:
                last_err = str(e)
            pos = text.find("{", pos + 1)
    if markers:
        raise RuntimeError(f"页面数据块解析失败（{markers[0]}）: {last_err or '未找到可解析的 JSON'}")
    raise RuntimeError("页面数据块解析失败: 未提供数据块标记")


def _extract_encoded_json(text: str, markers) -> dict:
    """提取 ``<script id="XXX" type="application/json">`` 里的（URL 编码的）JSON。

    抖音/快手等站点把初始数据放在这种 script 标签里，内容常是 percent-encoding。
    """
    if isinstance(markers, str):
        markers = [markers]
    for marker in markers:
        # 形如 <script id="RENDER_DATA" type="application/json">....</script>
        for attr in (f'id="{marker}"', f'id="{marker.lower()}"'):
            i = text.find(attr)
            if i < 0:
                continue
            gt = text.find(">", i)
            if gt < 0:
                continue
            end = text.find("</script>", gt)
            if end < 0:
                continue
            raw = text[gt + 1:end].strip()
            if not raw:
                continue
            for candidate in (raw, _url_decode(raw)):
                candidate = _sanitize_json_text(candidate)
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except ValueError:
                    continue
    return None


def _url_decode(raw: str) -> str:
    """percent-decode（失败返回原串）。"""
    try:
        from urllib.parse import unquote
        return unquote(raw)
    except Exception:  # pragma: no cover
        return raw


def walk_dicts(node):
    """深度优先遍历 JSON 树，产出所有 dict 节点。"""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk_dicts(v)


def ts_to_date(ts) -> str:
    """Unix 时间戳 -> ISO 日期（自动识别毫秒级），失败返回空串。"""
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts > 10 ** 12:  # 毫秒级时间戳
        ts //= 1000
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return ""


class SocialProviderBase(ProviderBase):
    """社媒 Provider 基类：category 固定 social，运行时注入登录态。

    登录态来源优先级：``params.cookie``（config.yaml 内配置）> 凭据文件
    ``social_auth.yaml``（设置中心「社媒登录态」页写入）。任一存在即注入 Cookie 头。
    """

    category = "social"
    site = ""          # 子类覆盖：weibo / douyin / kuaishou / xiaohongshu
    referer = ""       # 子类覆盖：搜索页 Referer

    def __init__(self, params: dict):
        super().__init__(params)
        self._auth_rec = None

    # ---------- 登录态 ----------
    def _auth(self) -> dict:
        if self._auth_rec is None:
            try:
                from ..core import social_auth
            except ImportError:  # pragma: no cover - 顶层 providers 导入路径
                from src.core import social_auth
            try:
                self._auth_rec = social_auth.get(self.site) if self.site else {}
            except Exception:
                self._auth_rec = {}
        return self._auth_rec or {}

    def auth_cookie(self) -> str:
        """当前生效的 Cookie（config.yaml 优先，其次凭据文件）。"""
        cookie = str(self.params.get("cookie") or "").strip()
        if cookie:
            return cookie
        return str(self._auth().get("cookie") or "").strip()

    def has_auth(self) -> bool:
        """是否已配置登录态。"""
        return bool(self.auth_cookie())

    @property
    def auth_required(self) -> bool:
        """该源是否被配置为「需登录才能访问」（params.requires_auth / spec.requires_auth）。"""
        return bool(self.params.get("requires_auth"))

    def ensure_auth(self):
        """需登录但未配置登录态时抛 ``SocialAuthMissing``（调度器据此跳过，不发请求）。"""
        if self.auth_required and not self.has_auth():
            raise SocialAuthMissing(
                f"{self.name} 需登录才能访问，"
                f"请在「设置 → 社媒登录态」用小号登录后点「读取登录态」")

    def headers(self) -> dict:
        h = super().headers()
        cookie = self.auth_cookie()
        if cookie:
            h["Cookie"] = cookie
        ua = str(self._auth().get("user_agent") or "").strip()
        if ua:
            h["User-Agent"] = ua
        h.setdefault("Accept-Language", "zh-CN,zh;q=0.9")
        if self.referer:
            h.setdefault("Referer", self.referer)
        return h

    # ---------- 抓取通路 ----------
    def _cdp_port(self) -> int:
        """CDP 调试端口（凭据文件里记录，缺省 9222）。"""
        try:
            from ..core import social_auth
        except ImportError:  # pragma: no cover - 顶层 providers 导入路径
            from src.core import social_auth
        try:
            return int(social_auth.get_cdp_port())
        except Exception:
            return 9222

    def browser_available(self) -> bool:
        """已登录的调试浏览器是否正在运行（params.browser=false 可强制关闭该通路）。"""
        if str(self.params.get("browser", "")).strip().lower() in ("0", "false", "no", "off"):
            return False
        try:
            from ..core import cdp
        except ImportError:  # pragma: no cover - 顶层 providers 导入路径
            from src.core import cdp
        try:
            return cdp.is_debug_port_open(self._cdp_port())
        except Exception:
            return False

    def fetch_html(self, url: str, params: dict = None) -> str:
        """取目标页面 HTML。

        优先用**已登录的真实浏览器**渲染抓取（能拿到 JS 渲染后的数据块，
        也是抖音/快手/小红书不被反爬拦截的关键）；浏览器未运行或抓取失败时
        回退到普通 HTTP 请求（注入登录态 Cookie）。

        两条通路都失败时抛异常，由调度器记为该源失败。
        """
        full_url = url
        if params:
            from urllib.parse import urlencode
            sep = "&" if "?" in full_url else "?"
            full_url = f"{full_url}{sep}{urlencode(params)}"

        if self.browser_available():
            try:
                from ..core import cdp
            except ImportError:  # pragma: no cover
                from src.core import cdp
            try:
                settle = int(self.params.get("settle_ms") or 2500)
                html = cdp.fetch_rendered(full_url, port=self._cdp_port(), settle_ms=settle)
                if html and len(html) > 500:
                    return html
            except Exception as e:
                # 浏览器抓取失败不终止，回退 HTTP（错误信息留待 HTTP 失败时合并）
                self._browser_error = str(e)

        resp = self.http_get(url, params=params)
        if resp.status_code != 200:
            detail = getattr(self, "_browser_error", "")
            if detail:
                raise RuntimeError(f"HTTP {resp.status_code}（浏览器抓取亦失败：{detail}）")
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.text

    def make_event(self, ent: Enterprise, title: str, summary: str = "",
                   url: str = "", occurred_at: str = "") -> Event:
        """条目 -> Event 归一化：severity 按关键词，sentiment 默认 neutral。"""
        title = (title or "").strip()
        summary = (summary or "").strip()
        relevance = "high" if ent.name and ent.name in title else "medium"
        return Event(
            category="social", title=title, summary=summary[:300], url=url,
            source=self.name, occurred_at=occurred_at,
            severity=classify_social(title, summary),
            sentiment="neutral", relevance=relevance)
