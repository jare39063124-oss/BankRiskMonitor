"""Tavily 搜索适配器单测（纯函数 + 假 POST，不联网）。

覆盖：
  - render_query 占位符替换 / 无占位符追加
  - classify 严重度与情感判定
  - parse_results 字段归一化与去噪
  - fetch 请求体/鉴权头构造、未配 Key 时抛错、event_category 归域
  - settings.expand_env 的 ${VAR} 间接引用
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.tavily_search import (  # noqa: E402
    API_URL, TavilyProvider, classify, parse_results, render_query)


class _FakeTavily(TavilyProvider):
    """用假 POST 替身捕获请求，避免联网。"""

    def __init__(self, params, payload):
        super().__init__(params)
        self._payload = payload
        self.captured = {}

    def _json_post(self, url, **kw):
        self.captured["url"] = url
        self.captured["json"] = kw.get("json") or {}
        self.captured["headers"] = kw.get("headers") or {}
        return self._payload


def test_render_query_with_placeholder():
    assert render_query('"{name}" 被执行', "武汉鑫泰商业管理有限公司") == \
        '"武汉鑫泰商业管理有限公司" 被执行'


def test_render_query_without_placeholder_appends_name():
    assert render_query("涉诉", "甲公司") == "涉诉 甲公司"
    assert render_query("", "甲公司") == '"甲公司"'


def test_classify_severity():
    assert classify("该公司被列为失信被执行人")[0] == "high"
    assert classify("股权转让纠纷开庭公告")[0] == "medium"
    assert classify("公司举办年度技术交流会") == ("low", "neutral")


def test_parse_results_maps_fields_and_drops_empty():
    payload = {"results": [
        {"title": "武汉鑫泰商业管理有限公司执行裁定", "url": "https://x/1",
         "content": "被执行标的 100 万", "score": 0.9},
        {"title": "", "content": "", "url": "https://x/2"},
        "not-a-dict",
    ]}
    events = parse_results(payload, "武汉鑫泰商业管理有限公司", "lawsuit")
    assert len(events) == 1
    ev = events[0]
    assert ev.category == "lawsuit"
    assert ev.source == "tavily"
    assert ev.severity == "high"
    assert ev.sentiment == "negative"
    assert ev.relevance == "high"  # 标题含企业全名
    assert ev.url == "https://x/1"


def test_parse_results_falls_back_to_content_as_title():
    payload = {"results": [{"content": "某公司被执行信息摘要", "url": "https://x"}]}
    events = parse_results(payload, "某公司", "enforcement")
    assert events[0].title == "某公司被执行信息摘要"
    assert events[0].severity == "high"


def test_fetch_requires_api_key():
    p = _FakeTavily({"api_key": ""}, {})
    try:
        p.fetch(type("E", (), {"name": "某企业"})())
    except RuntimeError as e:
        assert "api_key" in str(e)
    else:  # pragma: no cover
        raise AssertionError("缺少 api_key 时应抛 RuntimeError")


def test_fetch_builds_request_and_returns_events():
    payload = {"results": [{"title": "甲公司 被执行", "content": "详情",
                            "url": "https://y"}]}
    p = _FakeTavily({
        "api_key": "tvly-test",
        "event_category": "enforcement",
        "query": '"{name}" 被执行',
        "search_depth": "advanced",
        "max_results": 5,
        "include_domains": ["zxgk.court.gov.cn"],
    }, payload)
    ent = type("E", (), {"name": "甲公司"})()
    events = p.fetch(ent)
    body = p.captured["json"]
    assert p.captured["url"] == API_URL
    assert body["query"] == '"甲公司" 被执行'
    assert body["search_depth"] == "advanced"
    assert body["max_results"] == 5
    assert body["include_domains"] == ["zxgk.court.gov.cn"]
    # 鉴权双通道：header Bearer + body api_key（兼容新旧接口）
    assert p.captured["headers"]["Authorization"] == "Bearer tvly-test"
    assert body["api_key"] == "tvly-test"
    assert len(events) == 1
    assert events[0].category == "enforcement"


def test_fetch_defaults_to_provider_category():
    payload = {"results": [{"title": "甲公司信息", "content": "x", "url": "u"}]}
    p = _FakeTavily({"api_key": "k", "query": "涉诉"}, payload)
    events = p.fetch(type("E", (), {"name": "甲公司"})())
    assert events[0].category == "lawsuit"  # 未给 event_category 时用类默认
    assert p.captured["json"]["search_depth"] == "advanced"  # 默认深度


def test_expand_env_indirection(monkeypatch):
    from src.core import settings
    monkeypatch.setenv("BRM_TEST_TOKEN", "secret-value")
    assert settings.expand_env("${BRM_TEST_TOKEN}") == "secret-value"
    assert settings.expand_env("${BRM_TEST_TOKEN_MISSING}") == ""
    assert settings.expand_env("plain-value") == "plain-value"
    assert settings.expand_env(["a"]) == ["a"]  # 非字符串原样返回
