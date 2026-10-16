"""必备信息域覆盖度检查单测（src/core/coverage.py）。

目的：保证「扫描时明确提示缺失数据源」这一诉求可验证——
  - 只有反爬源（tier 3）→ weak
  - 一个都没启用 → missing
  - 有 tier 1 / 已配置凭据的 tier 2 → ok
  - 真实 config.yaml 必须声明 required_coverage 且能产出提示正文
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import coverage as cov  # noqa: E402


def _cfg(specs):
    return {"sources": specs}


def test_ok_when_tier1_enabled():
    cfg = _cfg({"lawsuit": [{"name": "a", "enabled": True, "tier": 1}]})
    c = cov.category_coverage(cfg, "lawsuit")
    assert c["level"] == cov.LEVEL_OK
    assert c["reliable"] == ["a"]


def test_ok_when_tier2_with_credentials():
    cfg = _cfg({"lawsuit": [{"name": "a", "enabled": True, "tier": 2,
                             "requires_key": True, "params": {"api_key": "k"}}]})
    assert cov.category_coverage(cfg, "lawsuit")["level"] == cov.LEVEL_OK


def test_weak_when_tier2_without_credentials_only():
    cfg = _cfg({"lawsuit": [{"name": "a", "enabled": True, "tier": 2,
                             "requires_key": True, "params": {"api_key": ""}}]})
    c = cov.category_coverage(cfg, "lawsuit")
    assert c["level"] == cov.LEVEL_WEAK
    assert c["best_effort"] == ["a"]


def test_weak_when_only_tier3_enabled():
    cfg = _cfg({"lawsuit": [{"name": "wenshu", "enabled": True, "tier": 3}]})
    c = cov.category_coverage(cfg, "lawsuit")
    assert c["level"] == cov.LEVEL_WEAK
    assert c["best_effort"] == ["wenshu"]
    assert c["reliable"] == []


def test_missing_when_nothing_enabled():
    cfg = _cfg({"lawsuit": [{"name": "wenshu", "enabled": False, "tier": 3}]})
    c = cov.category_coverage(cfg, "lawsuit")
    assert c["level"] == cov.LEVEL_MISSING
    assert c["disabled"] == ["wenshu"]


def test_tier_inferred_from_paid_flag():
    """未声明 tier 时向后兼容推断：paid → tier 2 → 无凭据 → weak。"""
    cfg = _cfg({"lawsuit": [{"name": "p", "enabled": True, "paid": True,
                             "params": {"api_key": ""}}]})
    assert cov.category_coverage(cfg, "lawsuit")["level"] == cov.LEVEL_WEAK


def test_required_coverage_default_fallback():
    items = cov.required_coverage({})
    cats = {i["category"] for i in items}
    assert {"lawsuit", "enforcement"} <= cats


def test_check_returns_problems_and_message():
    cfg = _cfg({
        "lawsuit": [{"name": "wenshu", "enabled": True, "tier": 3}],
        "enforcement": [{"name": "zxgk", "enabled": True, "tier": 1}],
    })
    cfg["required_coverage"] = [
        {"category": "lawsuit", "label": "涉诉", "hint": "用连接器代查"},
        {"category": "enforcement", "label": "被执行", "hint": "用连接器代查"},
    ]
    probs = cov.check_required_coverage(cfg)
    # 涉诉（仅反爬源）需提示；被执行（tier 1）无需提示
    assert len(probs) == 1
    assert probs[0]["category"] == "lawsuit"
    msg = cov.coverage_message(probs)
    assert "涉诉" in msg and "连接器" in msg
    # 无问题时正文为空
    assert cov.coverage_message([]) == ""


def test_real_config_declares_required_coverage_and_reports_gap():
    """真实配置必须声明必备域，且当前状态下涉诉/被执行应被判为需要提示。"""
    from src.core import settings
    cfg = settings.config()
    assert isinstance(cfg.get("required_coverage"), list) and cfg["required_coverage"]
    cats = {i["category"] for i in cov.required_coverage(cfg)}
    assert {"lawsuit", "enforcement"} <= cats
    probs = cov.check_required_coverage(cfg)
    # 当前只有反爬源 → 两个域都应被提示
    assert {p["category"] for p in probs} == {"lawsuit", "enforcement"}


def test_default_config_yaml_contains_required_coverage():
    """默认配置（打包后自举用）也必须带 required_coverage。"""
    from src.core import settings
    text = getattr(settings, "DEFAULT_CONFIG_YAML", "")
    assert isinstance(text, str) and "required_coverage" in text
