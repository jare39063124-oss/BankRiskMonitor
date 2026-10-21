"""AI 通道单测：模型策略校验 + 永久性错误立即换模型（Task 7.3 相关）。

背景
----
2026-10 实测发现：model_policy.yaml 里原有的 5 个免费模型**全部 404**
（已下线 / 仅付费版），导致 AI 通道永久走降级。已整体替换为实测可用的免费模型。

同时暴露一个效率问题：404（模型没了）被当成可重试错误，每个模型白等 3 次
退避（≈6 秒），既慢又浪费免费模型的每日额度。现改为**永久性错误立即换模型**。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ai import llm_client  # noqa: E402


def test_model_policy_only_free_models():
    """硬约束：模型价格必须为 0（免费），且不得含元路由。"""
    from src.core import settings
    pol = settings.model_policy()
    models = pol.get("fallback_models") or []
    assert models, "模型策略不能为空"
    for m in models:
        assert float(m.get("cost", 1)) == 0, f"{m.get('id')} 不是免费模型"
        mid = str(m.get("id") or "")
        assert not mid.startswith("openrouter/auto"), "禁止元路由 openrouter/auto"
        assert mid not in ("openrouter/free",), "禁止元路由 openrouter/free"
        assert ":free" in mid, f"{mid} 应为免费模型（:free 后缀）"


def test_model_policy_models_are_distinct_and_nonempty():
    from src.core import settings
    ids = [str(m.get("id") or "") for m in
           (settings.model_policy().get("fallback_models") or [])]
    assert all(ids)
    assert len(ids) == len(set(ids)), "模型列表不应重复"
    assert len(ids) >= 2, "至少保留 2 个模型以便互备"


def test_permanent_error_switches_model_immediately():
    """404（模型下线）应立即切换下一模型，不做退避重试。"""
    from unittest import mock

    client = llm_client.get_client()
    client.models = ["dead-model", "good-model"]
    calls = []

    def fake_post(model_id, messages, temperature):
        calls.append(model_id)
        if model_id == "dead-model":
            raise llm_client._ModelRequestError(404, "HTTP 404: model not found")
        return "ok"

    with mock.patch.object(client, "_post_chat", side_effect=fake_post), \
         mock.patch.object(client, "_headers", return_value={}), \
         mock.patch.object(llm_client.time, "sleep") as slept:
        text, mid = client.chat([{"role": "user", "content": "hi"}], 0.2)
    assert mid == "good-model"
    assert text == "ok"
    assert calls == ["dead-model", "good-model"]
    assert slept.call_count == 0, "永久性错误不应触发退避等待"


def test_transient_error_retries_then_switches():
    """429/5xx 属于可重试错误：先退避重试，再换下一模型。"""
    from unittest import mock

    client = llm_client.get_client()
    client.models = ["flaky", "good-model"]
    calls = []

    def fake_post(model_id, messages, temperature):
        calls.append(model_id)
        if model_id == "flaky":
            raise llm_client._ModelRequestError(429, "HTTP 429: rate limited")
        return "ok"

    original_max = client.max_retries
    client.max_retries = 1
    try:
        with mock.patch.object(client, "_post_chat", side_effect=fake_post), \
             mock.patch.object(client, "_headers", return_value={}), \
             mock.patch.object(llm_client.time, "sleep") as slept:
            text, mid = client.chat([{"role": "user", "content": "hi"}], 0.2)
    finally:
        client.max_retries = original_max
    assert mid == "good-model"
    assert calls.count("flaky") == 2, "429 应重试一次再换模型"
    assert slept.call_count == 1, "429 应触发一次退避"


def test_permanent_status_classification():
    """永久性状态码判定：模型级错误不重试。"""
    for code in (400, 403, 404, 422):
        assert llm_client._ModelRequestError(code, "x").permanent is True
    for code in (429, 500, 502, 503, 504):
        assert llm_client._ModelRequestError(code, "x").permanent is False
