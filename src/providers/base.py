"""Provider 抽象契约与多源并发调度器（数据源声明式登记见 config.yaml sources 段）。

调度策略（v3）：同一类目下**所有**启用的源并发采集并合并结果（不再「首个非空即返回」），
每个源的成败单独记账，供「源健康度看板」展示；任一源失败不影响其余源。
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..core.models import Event, Enterprise
from ..core import settings

log = logging.getLogger(__name__)


class SocialAuthMissing(RuntimeError):
    """该源需要登录态但未配置（调度器据此跳过该源，且不发起任何请求）。"""


class ProviderBase:
    """所有数据源适配器的基类。子类实现 fetch() 返回归一化 Event 列表。"""
    name: str = "base"
    category: str = "news"

    def __init__(self, params: dict):
        self.params = params or {}
        cfg = settings.config().get("network", {})
        self.timeout = cfg.get("timeout_seconds", 15)
        self.user_agent = cfg.get("user_agent", "Mozilla/5.0")
        self.max_rps = cfg.get("max_per_host_rps", 1)
        self._last_req = 0.0

    def headers(self) -> dict:
        return {"User-Agent": self.user_agent}

    def _throttle(self):
        interval = 1.0 / max(self.max_rps, 0.1)
        wait = self._last_req + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.time()

    def http_get(self, url: str, **kw) -> requests.Response:
        self._throttle()
        kw.setdefault("headers", self.headers())
        kw.setdefault("timeout", self.timeout)
        proxy = settings.config().get("app", {}).get("proxy", "")
        if proxy:
            kw.setdefault("proxies", {"http": proxy, "https": proxy})
        return requests.get(url, **kw)

    def _json_get(self, url: str, **kw) -> dict:
        """JSON 优先 GET（复用 ``http_get`` 的节流/代理/UA/超时）。

        非 200 或返回非 JSON → 抛 ``RuntimeError``（由 ``run_with_fallback`` 兜底）。
        """
        resp = self.http_get(url, **kw)
        if resp.status_code != 200:
            raise RuntimeError(f"{self.name} HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError(f"{self.name} 返回非 JSON")

    def http_post(self, url: str, **kw) -> requests.Response:
        """POST 请求（节流/代理/UA/超时口径与 ``http_get`` 一致）。"""
        self._throttle()
        kw.setdefault("headers", self.headers())
        kw.setdefault("timeout", self.timeout)
        proxy = settings.config().get("app", {}).get("proxy", "")
        if proxy:
            kw.setdefault("proxies", {"http": proxy, "https": proxy})
        return requests.post(url, **kw)

    def _json_post(self, url: str, **kw) -> dict:
        """JSON 优先 POST；非 200 或非 JSON 抛 ``RuntimeError``。"""
        resp = self.http_post(url, **kw)
        if resp.status_code != 200:
            raise RuntimeError(f"{self.name} HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError(f"{self.name} 返回非 JSON")

    def fetch(self, ent: Enterprise) -> List[Event]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 数据源可用性分级（① 确定可用 / ② 条件可用 / ③ 尽力而为，ARCH §1.6.1/§8.8）
# 纯函数，供状态栏/设置页复用（T04 GUI 消费），本层不依赖任何 GUI。
# ---------------------------------------------------------------------------
def resolve_tier(spec: dict) -> int:
    """解析数据源可用性等级（1/2/3）；字段缺失时向后兼容推断。

    推断规则（不破坏既有 config）：显式 ``tier`` 优先；未声明时——
    付费（``paid``）或需 Key（``requires_key``）→ 等级 2（条件可用），否则等级 1。
    """
    spec = spec or {}
    tier = spec.get("tier")
    try:
        t = int(tier)  # type: ignore[arg-type]
        if t in (1, 2, 3):
            return t
    except (TypeError, ValueError):
        pass
    if spec.get("paid") or spec.get("requires_key"):
        return 2
    params = spec.get("params") or {}
    if "api_key" in params and not params.get("api_key"):
        return 2
    return 1


def availability_text(spec: dict, failed: bool = False) -> str:
    """数据源可用性 → UI 文案（冻结，ARCH §8.8）。绝不静默失败。"""
    spec = spec or {}
    params = spec.get("params") or {}
    if not spec.get("enabled"):
        if spec.get("requires_key") and not params.get("api_key"):
            return "未配置 Key，已跳过"
        return "已停用"
    if failed:
        if spec.get("requires_key") and not params.get("api_key"):
            return "未配置 Key，已跳过"
        return "该源当前不可用（反爬/网络）"
    return "可用"


def summarize_sources(cfg: dict = None) -> str:
    """状态栏汇总文案：``数据源：{enabled}/{total} 启用``（ARCH §8.8）。"""
    cfg = cfg if cfg is not None else settings.config()
    total = enabled = 0
    for specs in (cfg.get("sources", {}) or {}).values():
        for spec in specs or []:
            total += 1
            if spec.get("enabled"):
                enabled += 1
    return f"数据源：{enabled}/{total} 启用"

def load_providers(category: str):
    """按 config.yaml 中该类目的声明顺序实例化启用的 Provider。

    spec 级开关（``requires_auth`` 等）会并入 params 传给适配器。
    """
    out = []
    for spec in settings.config().get("sources", {}).get(category, []):
        if not spec.get("enabled"):
            continue
        try:
            mod_path, cls_name = spec["class"].rsplit(".", 1)
            import importlib
            try:
                mod = importlib.import_module(mod_path)
            except ImportError:
                # 开发模式下顶层 providers 包不可见，回退 src.providers.*
                mod = importlib.import_module("src." + mod_path)
            cls = getattr(mod, cls_name)
            params = dict(spec.get("params") or {})
            # 凭据支持 ${VAR} 间接引用（真实密钥放 .env，config.yaml 不入库）
            params = {k: settings.expand_env(v) for k, v in params.items()}
            if spec.get("requires_auth"):
                params["requires_auth"] = True
            out.append(cls(params))
        except Exception as e:  # 单个源加载失败不影响其他源
            log.warning("provider %s load failed: %s", spec.get("name"), e)
    return out


# ---------------------------------------------------------------------------
# 每源运行统计（源健康度看板数据源）
# ---------------------------------------------------------------------------
_SOURCE_STATS: Dict[str, Dict[str, Any]] = {}
# 最近一次各类目的采集明细（供同步层/健康度看板读取每源结果）
_LAST_RUN: Dict[str, Dict[str, Any]] = {}


def _record_stat(name: str, status: str, count: int, error: str = "",
                 category: str = "", elapsed_ms: int = 0):
    """记账一次源调用结果（含耗时，供源健康度看板展示）。"""
    rec = _SOURCE_STATS.setdefault(
        name, {"ok": 0, "empty": 0, "failed": 0, "skipped": 0,
               "events": 0, "last_ok_at": "", "category": category,
               "last_status": "", "last_count": 0, "last_elapsed_ms": 0,
               "last_at": ""})
    if category:
        rec["category"] = category
    rec["last_status"] = status
    rec["last_count"] = count
    rec["last_elapsed_ms"] = int(elapsed_ms)
    rec["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if error:
        rec["last_error"] = error
    if status == "ok":
        rec["ok"] += 1
        rec["events"] += count
        rec["last_ok_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    elif status == "empty":
        rec["empty"] += 1
    elif status == "skipped":
        rec["skipped"] += 1
        if error:
            rec["last_error"] = error
    else:
        rec["failed"] += 1
        rec["last_error"] = error or "未知错误"


def source_stats(category: str = "") -> Dict[str, Dict[str, Any]]:
    """返回各源累计运行统计的副本；传 category 可只看该类目。"""
    out = {}
    for k, v in _SOURCE_STATS.items():
        if category and v.get("category") != category:
            continue
        out[k] = dict(v)
    return out


def reset_source_stats():
    """清空统计（供「立即自测」前重置）。"""
    _SOURCE_STATS.clear()
    _LAST_RUN.clear()


def last_run(category: str) -> Dict[str, Any]:
    """返回该类目最近一次采集明细（含每源 stats/errors）的副本。"""
    return dict(_LAST_RUN.get(category) or {})


def health_report() -> List[Dict[str, Any]]:
    """汇总**全部已登记源**的健康度（供 GUI 源健康度看板）。

    每个源一行：名称、所属信息域、是否启用、tier、最近一次结果、耗时、条数、
    失败原因。**未调用过的源**也会出现（状态「未调用」），这样用户能一眼看出
    「哪些源还没跑过」而不是只看到跑过的部分。
    """
    from ..core import settings as _settings
    from ..core.models import CATEGORIES

    cfg = _settings.config()
    stats = _SOURCE_STATS
    rows: List[Dict[str, Any]] = []
    for cat in CATEGORIES:
        for spec in (cfg.get("sources", {}) or {}).get(cat, []) or []:
            name = spec.get("name", "?")
            st = stats.get(name) or {}
            rows.append({
                "name": name,
                "category": cat,
                "enabled": bool(spec.get("enabled")),
                "tier": resolve_tier(spec),
                "status": st.get("last_status", ""),
                "count": st.get("last_count", 0),
                "elapsed_ms": st.get("last_elapsed_ms", 0),
                "error": st.get("last_error", ""),
                "last_at": st.get("last_at", ""),
                "ok_count": st.get("ok", 0),
                "failed_count": st.get("failed", 0),
            })
    return rows


def _safe_fetch(p, ent: Enterprise):
    """单源采集：返回 (status, events, error, elapsed_ms)；异常不外泄。"""
    t0 = time.time()

    def _done(status, events, error=""):
        return status, events, error, int((time.time() - t0) * 1000)

    try:
        if getattr(p, "auth_required", False) is True and not p.has_auth():
            return _done("skipped", [],
                         f"{p.name} 需登录才能访问，未配置登录态（已跳过）")
        events = p.fetch(ent)
        if events:
            return _done("ok", list(events), "")
        return _done("empty", [], f"{p.name}: 无结果")
    except SocialAuthMissing as e:
        return _done("skipped", [], str(e))
    except Exception as e:
        log.warning("provider %s failed: %s", p.name, e)
        return _done("failed", [], f"{p.name}: {e}")


def run_category(category: str, ent: Enterprise, workers: int = 4) -> Dict[str, Any]:
    """并发采集该类目下所有启用的源并合并结果。

    返回::

        {
          "category": "social",
          "events":   [Event, ...],          # 跨源去重后的合并结果
          "sources":  ["weibo", "wechat_sogou"],  # 有结果的源（按配置顺序）
          "errors":   {"weibo": "..."},      # 每个失败/跳过源的说明
          "stats":    {"weibo": {"status": "ok", "count": 10, "error": ""}},
        }

    任一源失败不影响其余源；标题相同的条目跨源去重（保留先出现者）。
    """
    providers = load_providers(category)
    out: Dict[str, Any] = {"category": category, "events": [], "sources": [],
                           "errors": {}, "stats": {}}
    if not providers:
        return out
    results: Dict[int, Any] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(providers)))) as ex:
        futures = {ex.submit(_safe_fetch, p, ent): i for i, p in enumerate(providers)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:  # 兜底：理论上 _safe_fetch 不外泄异常
                results[idx] = ("failed", [], f"{providers[idx].name}: {e}")
    merged: List[Event] = []
    seen_titles = set()
    for idx, p in enumerate(providers):
        status, events, error, elapsed = results.get(
            idx, ("failed", [], f"{p.name}: 未执行", 0))
        _record_stat(p.name, status, len(events), error, category=category,
                     elapsed_ms=elapsed)
        out["stats"][p.name] = {"status": status, "count": len(events),
                                "error": error, "elapsed_ms": elapsed}
        if status == "ok":
            out["sources"].append(p.name)
            for ev in events:
                key = (ev.title or "").strip()
                if key and key in seen_titles:
                    continue
                if key:
                    seen_titles.add(key)
                merged.append(ev)
        elif error:
            out["errors"][p.name] = error
    out["events"] = merged
    _LAST_RUN[category] = out
    return out


def run_with_fallback(category: str, ent: Enterprise) -> Tuple[List[Event], str]:
    """采集该类目下所有启用源并返回 ``(合并事件, 来源标签)``。

    向后兼容原契约：``来源标签`` 在有成功源时为成功源名（多源用逗号分隔），
    全部失败时为错误串（供上层展示失败原因）。
    """
    result = run_category(category, ent)
    events = result["events"]
    if events:
        return events, ",".join(result["sources"])
    errs = result.get("errors") or {}
    if errs:
        return [], "; ".join(f"{k}: {v}" for k, v in errs.items())
    return [], f"{category}: 无可用数据源或均无结果"
