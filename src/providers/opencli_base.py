"""OpenCLI 后端：通过本地 `opencli` CLI 复用已登录 Chrome 做跨平台信息采集。

为什么引入
----------
微博/小红书/微信公众号等平台的搜索结果必须登录才返回，纯 HTTP 请求只能拿到
空壳页。OpenCLI（jackwener/opencli）把这些平台的抓取封装成了命令，并通过
Browser Bridge 扩展**复用用户已登录的 Chrome 会话**——正好契合「小号保留登录态、
按需访问」的场景：登录一次，之后由程序驱动同一个已登录浏览器去查。

与自研 CDP 方案的关系
---------------------
两者**并存互备**：本后端作为可选源注册进 ``config.yaml`` 的 ``sources.social``，
由既有的多源并发合并调度器统一编排。OpenCLI 不可用（未装 CLI / 未装扩展 /
浏览器未连接）时该源被跳过，其余源不受影响。

命令行契约
----------
``opencli <site> search <keyword> -f json`` 返回统一信封::

    {"ok": true, "data": {"rows": [...]}}      # 或 {"data": [...]}

退出码遵循 sysexits：0 成功、66 空结果、69 Browser Bridge 不可达、
75 超时、77 需要登录、78 配置错误。
"""
import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase, SocialAuthMissing
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase, SocialAuthMissing

# 平台 -> 中文名（用于事件 source 标注与展示）
PLATFORM_LABELS = {
    "weibo": "微博",
    "xiaohongshu": "小红书",
    "rednote": "小红书国际版",
    "weixin": "微信公众号",
    "douyin": "抖音",
    "zhihu": "知乎",
    "tieba": "贴吧",
    "hupu": "虎扑",
    "bilibili": "哔哩哔哩",
    "jike": "即刻",
    "smzdm": "什么值得买",
    "36kr": "36氪",
    "linux-do": "LinuxDo",
    "nowcoder": "牛客网",
    "sinablog": "新浪博客",
    "douban": "豆瓣",
    "dianping": "大众点评",
    "pixiv": "Pixiv",
    "mubu": "幕布",
    "weread": "微信读书",
    "hackernews": "HackerNews",
    "twitter": "Twitter",
    "reddit": "Reddit",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
    "gov-policy": "政府政策",
    "gov-law": "法律法规",
    "powerchina": "电建集团",
    "zlibrary": "Z-Library",
}

# 平台 -> (标题字段候选, 摘要字段候选, 作者字段候选, 时间字段候选, 链接字段候选)
# 字段名来自各适配器的 `--help -f json` 的 columns 定义。
FIELD_MAP: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "weibo": {"title": ("title",), "summary": ("title",), "author": ("author",),
              "time": ("time",), "url": ("url",)},
    "xiaohongshu": {"title": ("title",), "summary": ("title",), "author": ("author",),
                    "time": ("published_at",), "url": ("url",)},
    "rednote": {"title": ("title",), "summary": ("title",), "author": ("author",),
                "time": ("published_at",), "url": ("url",)},
    "weixin": {"title": ("title",), "summary": ("summary", "title"), "author": (),
               "time": ("publish_time",), "url": ("url",)},
    "douyin": {"title": ("desc",), "summary": ("desc",), "author": ("author",),
               "time": (), "url": ("url",)},
    "zhihu": {"title": ("title",), "summary": ("title",), "author": ("author",),
              "time": (), "url": ("url",)},
    "tieba": {"title": ("title",), "summary": ("title",), "author": ("author",),
              "time": ("time",), "url": ("url",)},
    "hupu": {"title": ("title",), "summary": ("title",), "author": ("author",),
             "time": (), "url": ("url",)},
    "bilibili": {"title": ("title", "desc"), "summary": ("desc", "title"),
                 "author": ("author",), "time": ("pubdate_ts",), "url": ("url",)},
    "jike": {"title": ("content",), "summary": ("content",), "author": ("author",),
             "time": ("time",), "url": ("url",)},
    "smzdm": {"title": ("title",), "summary": ("title",), "author": ("mall",),
              "time": ("updated_at",), "url": ("url",)},
    "36kr": {"title": ("title",), "summary": ("title",), "author": (),
             "time": ("date",), "url": ("url",)},
    "linux-do": {"title": ("title",), "summary": ("title",), "author": (),
                 "time": (), "url": ("url",)},
    "nowcoder": {"title": ("title",), "summary": ("title",), "author": ("author",),
                 "time": (), "url": ("url",)},
    "sinablog": {"title": ("title",), "summary": ("title",), "author": ("author",),
                 "time": ("time", "date"), "url": ("url",)},
    "douban": {"title": ("title",), "summary": ("title",), "author": (),
               "time": (), "url": ("url",)},
    "dianping": {"title": ("title",), "summary": ("title",), "author": (),
                  "time": (), "url": ("url",)},
    "pixiv": {"title": ("title",), "summary": ("title",), "author": ("author",),
              "time": (), "url": ("url",)},
    "mubu": {"title": ("title",), "summary": ("title",), "author": (),
             "time": (), "url": ("url",)},
    "weread": {"title": ("title",), "summary": ("title",), "author": (),
               "time": (), "url": ("url",)},
    "hackernews": {"title": ("title",), "summary": ("title",), "author": ("author",),
                   "time": (), "url": ("url",)},
    "twitter": {"title": ("text", "title"), "summary": ("text", "title"),
                "author": ("author", "username"), "time": ("time", "created_at"),
                "url": ("url",)},
    "reddit": {"title": ("title",), "summary": ("title", "selftext"),
               "author": ("author",), "time": ("created",), "url": ("url",)},
    "youtube": {"title": ("title",), "summary": ("title",), "author": ("author", "uploader"),
                "time": ("timestamp", "upload_date"), "url": ("url",)},
    "facebook": {"title": ("text", "title"), "summary": ("text", "title"),
                  "author": ("author",), "time": ("time",), "url": ("url",)},
    "instagram": {"title": ("title", "caption"), "summary": ("caption", "title"),
                  "author": ("author", "username"), "time": ("time", "taken_at"),
                  "url": ("url",)},
    "linkedin": {"title": ("text", "title"), "summary": ("text", "title"),
                 "author": ("author",), "time": ("time",), "url": ("url",)},
    "tiktok": {"title": ("desc", "title"), "summary": ("desc", "title"),
               "author": ("author",), "time": ("time", "create_time"), "url": ("url",)},
    "gov-policy": {"title": ("title",), "summary": ("description", "title"),
                   "author": (), "time": ("date",), "url": ("url",)},
    "gov-law": {"title": ("title",), "summary": ("title",), "author": ("department",),
                "time": ("publish_date",), "url": ("url",)},
    "powerchina": {"title": ("title",), "summary": ("title",), "author": (),
                   "time": (), "url": ("url",)},
    "zlibrary": {"title": ("title",), "summary": ("title",), "author": ("author",),
                 "time": ("year",), "url": ("url",)},
}

# 严重度关键词（与社媒基类一致口径）
_HIGH_WORDS = ("被执行", "失信", "查封", "破产", "冻结", "处罚", "欠税", "强制执行",
               "限制消费", "立案", "违法", "违约", "逾期", "欠款", "负面")
_MED_WORDS = ("诉讼", "起诉", "纠纷", "警示", "整改", "约谈", "风险", "争议",
              "下滑", "亏损", "裁员", "投诉")

# opencli 退出码（sysexits）
EXIT_EMPTY = 66
EXIT_BROWSER_DOWN = 69
EXIT_TIMEOUT = 75
EXIT_AUTH_REQUIRED = 77
EXIT_CONFIG_ERROR = 78


def classify_severity(title: str, summary: str = "") -> str:
    """按关键词判定严重度。"""
    text = f"{title} {summary}"
    if any(w in text for w in _HIGH_WORDS):
        return "high"
    if any(w in text for w in _MED_WORDS):
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Browser Bridge 扩展（Chrome 扩展，第三方二进制包，运行时按需下载）
# ---------------------------------------------------------------------------
EXTENSION_RELEASE_API = "https://api.github.com/repos/jackwener/opencli/releases/latest"
EXTENSION_HTML_HINT = (
    "在 Chrome 地址栏打开 chrome://extensions → 开启右上角「开发者模式」"
    "→ 点「加载已解压的扩展程序」→ 选择该目录。")


def extension_dir() -> str:
    """扩展解压目录（项目内 tools/opencli_ext）。"""
    try:
        from ..core.paths import app_root
    except ImportError:  # pragma: no cover - 顶层 providers 导入路径
        from src.core.paths import app_root
    return os.path.join(app_root(), "tools", "opencli_ext")


def extension_installed() -> bool:
    """扩展是否已解压到本地。"""
    return os.path.isfile(os.path.join(extension_dir(), "manifest.json"))


def download_extension(timeout: float = 120.0) -> tuple:
    """下载并解压 Browser Bridge 扩展，返回 ``(ok, message)``。

    扩展是第三方二进制包（不入库），首次使用时按需拉取。
    """
    import json as _json
    import urllib.request
    import zipfile

    target = extension_dir()
    try:
        req = urllib.request.Request(
            EXTENSION_RELEASE_API,
            headers={"User-Agent": "BankRiskMonitor"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            release = _json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return False, f"获取扩展版本失败：{e}"

    url = ""
    name = ""
    for asset in release.get("assets") or []:
        if "extension" in str(asset.get("name", "")).lower() \
                and str(asset.get("name", "")).endswith(".zip"):
            url = asset.get("browser_download_url") or ""
            name = asset.get("name") or ""
            break
    if not url:
        return False, "未在发布页找到扩展 zip 包"

    tmp = os.path.join(os.path.dirname(target) or ".", "_opencli_ext.zip")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "BankRiskMonitor"}),
                timeout=timeout) as resp, open(tmp, "wb") as fh:
            fh.write(resp.read())
        with zipfile.ZipFile(tmp) as zf:
            zf.extractall(target)
    except Exception as e:
        return False, f"下载或解压扩展失败：{e}"
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    if not extension_installed():
        return False, "扩展解压后未找到 manifest.json"
    return True, f"扩展已下载到：{target}\n\n{EXTENSION_HTML_HINT}（安装包 {name}）"


def _norm_date(value: Any) -> str:
    """把各平台的时间字段归一为 YYYY-MM-DD（尽力而为，失败返回空串）。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10 ** 12:
            ts //= 1000
        try:
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except (OSError, OverflowError, ValueError):
            return ""
    s = str(value).strip()
    if not s:
        return ""
    # 已含 YYYY-MM-DD
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # 纯数字时间戳字符串
    if s.isdigit():
        return _norm_date(int(s))
    # 相对时间「3小时前」「昨天」等：无法确定绝对日期则留空，交由上游判定
    return ""


class OpenCliProvider(ProviderBase):
    """OpenCLI 后端：一个 Provider 覆盖多个平台，按 ``params.platforms`` 分派。

    配置示例（config.yaml ``sources.social``）::

        - name: opencli
          enabled: false            # 默认关闭，需用户显式启用
          tier: 2
          class: providers.opencli_base.OpenCliProvider
          params:
            platforms: [weibo, xiaohongshu, weixin]
            limit: 10
            timeout_seconds: 90
    """

    name = "opencli"
    category = "social"

    def __init__(self, params: dict):
        super().__init__(params)
        self._cli = None
        self._checked = False

    # ---------- CLI 探测 ----------
    @property
    def cli_path(self) -> str:
        """opencli 可执行文件路径（优先 config 指定，其次 PATH）。"""
        custom = str(self.params.get("cli_path") or "").strip()
        if custom and os.path.exists(custom):
            return custom
        return shutil.which("opencli") or ""

    def cli_available(self) -> bool:
        """CLI 是否已安装。"""
        if not self._checked:
            self._checked = True
            self._cli = self.cli_path
        return bool(self._cli)

    def doctor(self) -> dict:
        """运行 ``opencli doctor``，返回结构化诊断结果。

        返回::

            {"ok": bool, "installed": bool, "extension_connected": bool,
             "issues": [str], "raw": dict}
        """
        out = {"ok": False, "installed": self.cli_available(),
               "extension_connected": False, "issues": [], "raw": {}}
        if not out["installed"]:
            out["issues"].append(
                "未安装 opencli CLI：npm install -g @jackwener/opencli")
            return out
        try:
            proc = subprocess.run(
                [self._cli, "doctor"], capture_output=True, text=True,
                timeout=int(self.params.get("doctor_timeout", 40)))
        except subprocess.TimeoutExpired:
            out["issues"].append("opencli doctor 执行超时")
            return out
        except OSError as e:
            out["issues"].append(f"opencli doctor 执行失败：{e}")
            return out
        try:
            payload = json.loads(proc.stdout or "{}")
        except ValueError:
            out["issues"].append("opencli doctor 输出无法解析")
            return out
        data = payload.get("data") or {}
        out["raw"] = data
        out["extension_connected"] = bool(data.get("extensionConnected"))
        issues = data.get("issues")
        if isinstance(issues, list):
            out["issues"] = [str(i) for i in issues]
        out["ok"] = out["extension_connected"] and not out["issues"]
        return out

    def ensure_ready(self):
        """CLI 缺失或浏览器桥未连通时抛异常（调度器记为该源失败/跳过）。"""
        if not self.cli_available():
            raise SocialAuthMissing(
                "未安装 opencli CLI（npm install -g @jackwener/opencli），"
                "该后端已跳过")
        diag = self.doctor()
        if not diag["extension_connected"]:
            raise SocialAuthMissing(
                "opencli 浏览器桥未连接：请在 Chrome 安装 Browser Bridge 扩展"
                "（项目内 tools/opencli_ext 可直接「加载已解压的扩展程序」），"
                "并用小号登录目标平台后重试")
        return diag

    # ---------- 采集 ----------
    def _platforms(self) -> List[str]:
        raw = self.params.get("platforms")
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.split(",") if x.strip()]
        if not raw:
            raw = ["weibo"]
        out = []
        for p in raw:
            p = str(p).strip()
            if p and p not in out:
                out.append(p)
        return out

    def _run_search(self, platform: str, keyword: str) -> List[dict]:
        """执行一次 ``opencli <platform> search``，返回行列表。"""
        limit = int(self.params.get("limit", 10) or 10)
        timeout = int(self.params.get("timeout_seconds", 90) or 90)
        cmd = [self._cli, platform, "search", keyword, "--limit", str(limit), "-f", "json"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"opencli {platform} search 超时（{timeout}s）")
        except OSError as e:
            raise RuntimeError(f"opencli {platform} search 执行失败：{e}")
        stdout = (proc.stdout or "").strip()
        if not stdout:
            raise RuntimeError(self._exit_hint(platform, proc.returncode,
                                               proc.stderr or ""))
        try:
            payload = json.loads(stdout)
        except ValueError:
            raise RuntimeError(f"opencli {platform} search 输出无法解析：{stdout[:200]}")
        if not payload.get("ok", False):
            err = payload.get("error") or {}
            code = err.get("code") or ""
            msg = err.get("message") or ""
            hint = err.get("hint") or ""
            raise RuntimeError(self._exit_hint(platform, code, msg, hint))
        data = payload.get("data")
        return _extract_rows(data)

    def _exit_hint(self, platform: str, code: Any, msg: str, hint: str = "") -> str:
        """把退出码/错误码翻译成可操作的中文提示。"""
        text = str(msg or "").strip()
        c = str(code or "")
        if c in ("BROWSER_CONNECT",) or code == EXIT_BROWSER_DOWN:
            text = ("opencli 浏览器桥未连接，请在 Chrome 安装 Browser Bridge 扩展"
                    "（tools/opencli_ext）")
        elif code == EXIT_AUTH_REQUIRED or c == "AUTH_REQUIRED":
            text = f"opencli {platform} 需要登录态，请用小号在 Chrome 登录后重试"
        elif code == EXIT_TIMEOUT or c == "TIMEOUT":
            text = f"opencli {platform} search 超时"
        elif code == EXIT_EMPTY or c == "EMPTY":
            text = f"opencli {platform} 无结果"
        elif code == EXIT_CONFIG_ERROR or c == "CONFIG_ERROR":
            text = f"opencli {platform} 配置错误：{text}"
        if hint and "opencli" not in text:
            text = f"{text}（{hint.splitlines()[0][:120]}）"
        return text or f"opencli {platform} 采集失败"

    def fetch(self, ent: Enterprise) -> List[Event]:
        """按配置的平台列表逐个 search，合并为 Event 列表。"""
        self.ensure_ready()
        events: List[Event] = []
        errors: List[str] = []
        for platform in self._platforms():
            try:
                rows = self._run_search(platform, ent.name)
            except Exception as e:  # 单平台失败不影响其余平台
                errors.append(f"{platform}: {e}")
                continue
            events.extend(self._to_events(platform, rows, ent))
        if not events and errors:
            raise RuntimeError("; ".join(errors))
        return events

    # ---------- 归一化 ----------
    def _to_events(self, platform: str, rows: List[dict], ent: Enterprise) -> List[Event]:
        fmap = FIELD_MAP.get(platform, {})
        out: List[Event] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = _pick_field(row, fmap.get("title", ()))
            if not title:
                continue
            summary = _pick_field(row, fmap.get("summary", ())) or title
            author = _pick_field(row, fmap.get("author", ()))
            occurred = _norm_date(_pick_field(row, fmap.get("time", ())))
            url = _pick_field(row, fmap.get("url", ()))
            if author:
                summary = f"{author}：{summary}" if not summary.startswith(author) else summary
            out.append(Event(
                category="social",
                title=title[:300],
                summary=summary[:300],
                url=url,
                source=f"opencli_{platform}",
                occurred_at=occurred,
                severity=classify_severity(title, summary),
                sentiment="neutral",
                relevance="high" if ent.name and ent.name in title else "medium"))
        return out


def _pick_field(row: dict, names) -> str:
    """按候选字段名依次取值（返回第一个非空字符串）。"""
    for name in names or ():
        val = row.get(name)
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            val = " ".join(str(x) for x in val)
        text = str(val).strip()
        if text:
            return text
    return ""


def _extract_rows(data: Any, _depth: int = 0) -> List[dict]:
    """从 opencli 的 data 字段里取出行列表（兼容多种外层包装）。

    常见形态：``{"rows": [...]}`` / ``{"items": [...]}`` / ``{"data": {"notes": [...]}}`` /
    ``[ {...} ]`` / 单行对象。
    """
    if data is None or _depth > 3:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("rows", "items", "results", "list", "notes", "posts", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                rows = _extract_rows(val, _depth + 1)
                if rows:
                    return rows
        # 单行对象
        if any(k in data for k in ("title", "desc", "content", "text")):
            return [data]
    return []
