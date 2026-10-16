"""Provider 抽象契约与 fallback 调度器（数据源声明式登记见 config.yaml sources 段）。"""
import logging
import time
from typing import List, Optional, Tuple

import requests

from ..core.models import Event, Enterprise
from ..core import settings

log = logging.getLogger(__name__)

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
    """按 config.yaml 中该类目的声明顺序实例化启用的 Provider。"""
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
            out.append(cls(spec.get("params", {})))
        except Exception as e:  # 单个源加载失败不影响其他源
            log.warning("provider %s load failed: %s", spec.get("name"), e)
    return out

def run_with_fallback(category: str, ent: Enterprise) -> Tuple[List[Event], str]:
    """依次尝试该类目下启用的 Provider，成功即返回（结构化+去重由调用方入库）。"""
    errs = []
    for p in load_providers(category):
        try:
            events = p.fetch(ent)
            if events:
                return events, p.name
            errs.append(f"{p.name}: empty")
        except Exception as e:
            log.warning("provider %s failed: %s", p.name, e)
            errs.append(f"{p.name}: {e}")
    return [], "; ".join(errs)
