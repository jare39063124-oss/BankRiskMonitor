"""社媒登录态（Cookie）凭据管理：读写、掩码、Chrome CDP 读取、按需注入。

凭据文件：``<app_root>/social_auth.yaml``（**必须加入 .gitignore，真实 Cookie 绝不入库**）。

结构::

    weibo:
      cookie: "SUB=xxx; SUBP=yyy"
      user_agent: "Mozilla/5.0 ..."
      updated_at: "2026-10-20 12:00:00"

站点键与 config.yaml 中 social 类目的源名一致（weibo/douyin/kuaishou/xiaohongshu）。
本模块不依赖 GUI，可被 CLI/测试直接调用。
"""
import json
import os
from datetime import datetime

import yaml

from .paths import app_root

# 站点键 -> 采集时使用的域名（CDP 读取登录态时按域名筛选）
SITE_DOMAINS = {
    "weibo": ["weibo.com"],
    "douyin": ["douyin.com"],
    "kuaishou": ["kuaishou.com"],
    "xiaohongshu": ["xiaohongshu.com"],
    "wechat_sogou": ["sogou.com"],
}

# 站点键 -> 展示名
SITE_LABELS = {
    "weibo": "微博",
    "douyin": "抖音",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
    "wechat_sogou": "搜狗微信",
}

_FILE_NAME = "social_auth.yaml"

# 调试浏览器默认端口（与 cdp.DEFAULT_PORT 一致）
DEFAULT_CDP_PORT = 9222


def auth_path() -> str:
    """凭据文件绝对路径（位于 exe 同目录 / 项目根）。"""
    return os.path.join(app_root(), _FILE_NAME)


def browser_profile_dir() -> str:
    """专用浏览器配置目录：登录态长期保留在此，程序重启后仍可复用。"""
    return os.path.join(app_root(), "data", "browser_profile")


def load_all() -> dict:
    """读取全部凭据；文件缺失或损坏时返回空字典（绝不抛异常）。"""
    path = auth_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def save_all(data: dict) -> None:
    """写回全部凭据（原子替换，避免半截文件）。"""
    path = auth_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data or {}, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(f"保存登录态失败：{e}")


def get(site: str) -> dict:
    """取某站点的凭据记录（不存在返回空字典）。"""
    rec = load_all().get(site)
    return rec if isinstance(rec, dict) else {}


def get_cookie(site: str) -> str:
    """取某站点的 Cookie 字符串（未配置返回空串）。"""
    return str(get(site).get("cookie") or "").strip()


def get_user_agent(site: str) -> str:
    """取某站点绑定的 UA（未配置返回空串）。"""
    return str(get(site).get("user_agent") or "").strip()


def set_cookie(site: str, cookie: str, user_agent: str = "") -> str:
    """保存某站点的登录态（cookie 会被规范化为标准头格式），返回规范化后的串。"""
    normalized = parse_cookie(cookie)
    data = load_all()
    rec = data.get(site) if isinstance(data.get(site), dict) else {}
    rec = dict(rec or {})
    rec["cookie"] = normalized
    if user_agent:
        rec["user_agent"] = user_agent.strip()
    rec["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data[site] = rec
    save_all(data)
    return normalized


def set_cdp_port(port: int) -> None:
    """记录本次使用的 CDP 调试端口（社媒抓取与读取登录态都按此端口走）。"""
    data = load_all()
    data["_cdp"] = {"port": int(port),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    save_all(data)


def get_cdp_port() -> int:
    """当前记录的 CDP 端口（未记录返回默认 9222）。"""
    rec = load_all().get("_cdp")
    if isinstance(rec, dict):
        try:
            return int(rec.get("port") or DEFAULT_CDP_PORT)
        except (TypeError, ValueError):
            return DEFAULT_CDP_PORT
    return DEFAULT_CDP_PORT


def launch_login_browser(port: int = DEFAULT_CDP_PORT) -> tuple:
    """启动专用调试浏览器（用于手动登录小号），返回 ``(ok, message)``。

    浏览器使用项目内独立的配置目录，登录态长期保留；用户在该窗口登录
    抖音/快手/小红书/微博后，回到程序点「读取登录态」即可。
    """
    from . import cdp
    if cdp.is_debug_port_open(port):
        return True, f"调试浏览器已在运行（端口 {port}），请在浏览器窗口登录后点「读取登录态」"
    ok, msg = cdp.launch_browser(port=port, profile_dir=browser_profile_dir())
    if ok:
        set_cdp_port(port)
    return ok, msg


def browser_running(port: int = DEFAULT_CDP_PORT) -> bool:
    """调试浏览器是否正在运行。"""
    from . import cdp
    try:
        return cdp.is_debug_port_open(port)
    except Exception:
        return False


def clear(site: str) -> None:
    """清除某站点的登录态。"""
    data = load_all()
    if site in data:
        data.pop(site, None)
        save_all(data)


def parse_cookie(raw: str) -> str:
    """把用户粘贴的多种格式统一为标准 Cookie 头 ``a=1; b=2``。

    支持：
    - ``Cookie: a=1; b=2``（浏览器请求头整行复制）
    - ``a=1; b=2``（DevTools 复制值）
    - ``[{"name":"a","value":"1"}]``（Chrome 导出 JSON 数组）
    - ``{"a":"1","b":"2"}``（JSON 对象）
    - 多行粘贴（自动折行合并）
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("["):
        try:
            data = json.loads(s)
        except ValueError:
            data = None
        if isinstance(data, list):
            parts = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("Name")
                if not name:
                    continue
                parts.append(f"{name}={item.get('value') or item.get('Value') or ''}")
            if parts:
                return "; ".join(parts)
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return "; ".join(f"{k}={v}" for k, v in data.items())
    if s.lower().startswith("cookie:"):
        s = s[len("cookie:"):].strip()
    s = " ".join(x.strip() for x in s.replace("\r\n", "\n").split("\n") if x.strip())
    return s.strip().rstrip(";").strip()


def mask_cookie(cookie: str) -> str:
    """掩码显示 Cookie（只露前 3 项的键名与值前 2 位）。"""
    items = [x.strip() for x in (cookie or "").split(";") if x.strip()]
    if not items:
        return ""
    out = []
    for it in items[:3]:
        name, sep, val = it.partition("=")
        if not sep:
            out.append(it)
            continue
        out.append(f"{name}={val[:2]}***" if len(val) > 2 else f"{name}=***")
    if len(items) > 3:
        out.append(f"…共 {len(items)} 项")
    return "; ".join(out)


def status(site: str) -> dict:
    """登录态状态（供 GUI 展示）：是否已配置、掩码串、更新时间。"""
    rec = get(site)
    cookie = str(rec.get("cookie") or "")
    return {
        "site": site,
        "label": SITE_LABELS.get(site, site),
        "configured": bool(cookie),
        "masked": mask_cookie(cookie),
        "count": len([x for x in cookie.split(";") if x.strip()]),
        "updated_at": str(rec.get("updated_at") or ""),
    }


def all_status() -> list:
    """全部社媒站点的登录态状态列表。"""
    return [status(s) for s in SITE_DOMAINS]


def import_from_chrome(site: str, port: int = 0) -> str:
    """从本机调试浏览器读取该站点的 Cookie 并保存，返回读到的 Cookie 串。

    失败抛 ``RuntimeError``（含可读原因），由调用方（GUI）展示。
    """
    from . import cdp
    port = port or get_cdp_port()
    domains = SITE_DOMAINS.get(site, [site])
    cookies = cdp.read_cookies(port=port)
    header = cdp.cookie_header_for(cookies, domains)
    if not header:
        raise RuntimeError(
            f"未在调试浏览器中找到 {SITE_LABELS.get(site, site)} 的 Cookie，"
            f"请先在该浏览器窗口登录后重试")
    set_cookie(site, header)
    return header


def import_all_from_chrome(port: int = 0) -> dict:
    """一次性读取全部站点的登录态，返回 ``{site: (ok, message)}``。

    浏览器未启动或某个站点未登录时，仅该站点失败，其余照常。
    """
    port = port or get_cdp_port()
    out = {}
    for site in SITE_DOMAINS:
        try:
            import_from_chrome(site, port=port)
            out[site] = (True, "已读取")
        except RuntimeError as e:
            out[site] = (False, str(e))
        except Exception as e:
            out[site] = (False, str(e))
    return out


def auth_summary() -> dict:
    """社媒登录态总览（供主窗口状态栏展示）。

    ``configured``  为已配置登录态的站点数
    ``total``       站点总数
    ``browser``     调试浏览器是否在运行
    """
    statuses = all_status()
    configured = [s for s in statuses if s["configured"]]
    return {
        "configured": len(configured),
        "total": len(statuses),
        "sites": configured,
        "browser": browser_running(),
        "port": get_cdp_port(),
    }
