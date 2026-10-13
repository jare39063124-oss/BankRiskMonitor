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

    def fetch(self, ent: Enterprise) -> List[Event]:
        raise NotImplementedError

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
