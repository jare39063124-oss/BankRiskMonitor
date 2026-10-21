"""OpenRouter LLM 客户端：OpenAI 兼容 /chat/completions + fallback 模型链 + 全局降级熔断。

- 按 model_policy().fallback_models 顺序尝试，命中 banned_models（支持通配符）的模型警告并跳过；
- 代理 / 超时 / 重试指数退避 / 失败阈值 来自 config.yaml 与 model_policy.yaml；
- 连续失败达到 failure_threshold 后进入全局降级（线程安全，模块级单例），调用方收到
  AIUnavailableError；降级不自愈，仅 health_check() 成功一次后复位标志与失败计数。
"""
import fnmatch
import logging
import os
import threading
import time
from typing import List, Optional, Tuple

import requests

from ..core import settings
from ..core.paths import app_root

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class AIUnavailableError(Exception):
    """AI 通道整体不可用（未配置 Key / 已降级 / 全部模型请求失败）。"""


class _ModelRequestError(RuntimeError):
    """单模型请求失败；``permanent`` 表示换模型也没用（应立即切换）。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        # 404 模型下线 / 403 无权限 / 400 请求非法 / 422 载荷不合法 → 永久性
        self.permanent = status_code in (400, 403, 404, 422)


def _load_env(path: str = ""):
    """极简 .env 解析（项目未安装 python-dotenv），仅 setdefault，不覆盖已有环境变量。"""
    p = path or os.path.join(app_root(), ".env")
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))
    except OSError:
        pass


_load_env()


def get_api_key() -> str:
    """从环境变量读取 API Key（.env 已在导入时注入），不落日志、不硬编码。"""
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


class _Degradation:
    """线程安全的全局降级状态（模块级单例）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._fails = 0
        self._degraded = False

    def record_failure(self, threshold: int):
        with self._lock:
            self._fails += 1
            if self._fails >= threshold and not self._degraded:
                self._degraded = True
                log.warning("AI 连续失败 %d 次，进入全局降级模式（等待健康检查恢复）", self._fails)

    def record_success(self):
        with self._lock:
            self._fails = 0
            self._degraded = False

    def snapshot(self) -> Tuple[bool, int]:
        with self._lock:
            return self._degraded, self._fails


_state = _Degradation()


def is_degraded() -> bool:
    return _state.snapshot()[0]


def is_available() -> bool:
    """AI 是否可用：未降级且已配置 API Key。"""
    return (not is_degraded()) and bool(get_api_key())


def _is_banned(model_id: str, banned: List[str]) -> bool:
    return any(fnmatch.fnmatchcase(model_id.lower(), str(b).lower()) for b in banned)


class LLMClient:
    """OpenRouter 客户端（requests 直连，OpenAI 兼容协议）。"""

    def __init__(self):
        pol = settings.model_policy() or {}
        req = pol.get("request", {}) or {}
        self.timeout = int(req.get("timeout_seconds", 60))
        self.max_retries = int(req.get("max_retries", 2))
        self.backoff_base = float(req.get("backoff_base_seconds", 2))
        self.failure_threshold = int(req.get("failure_threshold", 3))
        banned = pol.get("banned_models", []) or []
        self.models: List[str] = []
        for m in pol.get("fallback_models", []) or []:
            mid = (m.get("id") if isinstance(m, dict) else str(m)).strip()
            if not mid:
                continue
            if _is_banned(mid, banned):
                log.warning("模型 %s 命中禁用名单，已跳过", mid)
                continue
            self.models.append(mid)
        proxy = (settings.config().get("app", {}) or {}).get("proxy", "") or ""
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    def _headers(self) -> dict:
        key = get_api_key()
        if not key:
            raise AIUnavailableError("未配置 OPENROUTER_API_KEY")
        try:
            key.encode("latin-1")  # HTTP 头仅允许 latin-1 字符
        except UnicodeEncodeError:
            raise AIUnavailableError("OPENROUTER_API_KEY 含非 latin-1 字符，无法用于请求头")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/bankriskmonitor",
            "X-Title": "BankRiskMonitor",
        }

    def _post_chat(self, model_id: str, messages: list, temperature: float) -> str:
        """单模型单次请求，返回回复文本；失败抛异常由上层重试/换模型。"""
        body = {"model": model_id, "messages": messages, "temperature": temperature}
        resp = requests.post(API_URL, json=body, headers=self._headers(),
                             timeout=self.timeout, proxies=self.proxies)
        if resp.status_code != 200:
            raise _ModelRequestError(resp.status_code,
                                     f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        if not content:
            raise RuntimeError("模型返回空内容")
        return content

    def chat(self, messages: list, temperature: float = 0.2) -> Tuple[str, str]:
        """按 fallback 链请求，返回 (文本, 模型id)；全部失败抛 AIUnavailableError。"""
        if is_degraded():
            raise AIUnavailableError("AI 已全局降级，等待健康检查恢复")
        if not get_api_key():
            raise AIUnavailableError("未配置 OPENROUTER_API_KEY")
        if not self.models:
            raise AIUnavailableError("无可用模型（全部被禁用或未配置）")
        last_err: Optional[Exception] = None
        for mid in self.models:
            for attempt in range(self.max_retries + 1):
                try:
                    text = self._post_chat(mid, messages, temperature)
                    _state.record_success()
                    return text, mid
                except AIUnavailableError:
                    raise  # Key/配置问题：不重试、不计入失败阈值
                except _ModelRequestError as e:
                    last_err = e
                    if e.permanent:
                        # 模型级永久错误（404 模型已下线 / 403 无权限 / 400 请求非法）：
                        # 重试没有意义，**立即换下一个模型**，避免白等退避并
                        # 浪费免费模型的每日额度。
                        log.warning("模型 %s 不可用（%s），直接切换下一模型", mid, e)
                        break
                    log.warning("模型 %s 第 %d/%d 次请求失败: %s",
                                mid, attempt + 1, self.max_retries + 1, e)
                    if attempt < self.max_retries:
                        time.sleep(self.backoff_base * (2 ** attempt))  # 指数退避
                except Exception as e:
                    last_err = e
                    log.warning("模型 %s 第 %d/%d 次请求失败: %s",
                                mid, attempt + 1, self.max_retries + 1, e)
                    if attempt < self.max_retries:
                        time.sleep(self.backoff_base * (2 ** attempt))  # 指数退避
        _state.record_failure(self.failure_threshold)
        raise AIUnavailableError(f"全部模型请求失败: {last_err}")


_client: Optional[LLMClient] = None
_client_lock = threading.Lock()


def get_client() -> LLMClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = LLMClient()
        return _client


def health_check() -> bool:
    """AI 通道健康检查：任一模型请求成功即复位降级标志与失败计数。探测失败不计入失败阈值。"""
    if not get_api_key():
        return False
    client = get_client()
    if not client.models:
        return False
    for mid in client.models:
        try:
            client._post_chat(mid, [{"role": "user", "content": "ping"}], 0.0)
            _state.record_success()
            log.info("AI 健康检查通过（%s），降级状态已复位", mid)
            return True
        except Exception as e:
            log.warning("AI 健康检查失败（%s）: %s", mid, e)
    return False
