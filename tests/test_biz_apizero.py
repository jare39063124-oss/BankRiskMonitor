"""ApiZero 工商信息适配器单测：返回归一化解析（纯函数，不联网）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.biz_apizero import parse_events, pick_record  # noqa: E402

SAMPLE = {
    "name": "测试集团有限公司",
    "legal_person": "张三",
    "establish_time": "2018-01-29",
    "reg_capital": "10000万元人民币",
    "reg_status": "存续",
    "credit_code": "91110000XXXXXXXXXX",
    "business_scope": "一般项目：技术服务",
    "reg_location": "北京市海淀区",
    "history_names": ["测试有限 old1", "测试有限 old2"],
    "company_org_type": "有限责任公司",
    "city": "北京",
    "district": "海淀区",
}


def test_parse_normal():
    events = parse_events(SAMPLE, "测试集团有限公司")
    assert len(events) == 1
    ev = events[0]
    assert ev.category == "biz_change"
    assert ev.source == "apizero"
    assert ev.severity == "low"
    assert ev.sentiment == "neutral"
    assert ev.relevance == "high"
    assert ev.title == "测试集团有限公司：工商登记状态【存续】"
    assert "张三" in ev.summary
    assert "10000万元人民币" in ev.summary
    assert "2018-01-29" in ev.summary
    assert "91110000XXXXXXXXXX" in ev.summary
    assert "曾用名" in ev.summary


def test_parse_bad_status():
    s = dict(SAMPLE, reg_status="注销")
    events = parse_events(s, "测试集团有限公司")
    assert events[0].severity == "high"
    assert events[0].sentiment == "negative"
    assert "注销" in events[0].title


def test_parse_empty():
    assert parse_events({}, "某企业") == []


def test_parse_missing_fields():
    """字段缺失容错：仅 name + status 也能产出事件，空值段落跳过。"""
    s = {"name": "某企业", "reg_status": "存续"}
    events = parse_events(s, "某企业")
    assert len(events) == 1
    ev = events[0]
    assert ev.severity == "low"
    assert "法定代表人" not in ev.summary
    assert "注册资本" not in ev.summary
    assert "曾用名" not in ev.summary


def test_parse_history_names_alias_forms():
    """曾用名兼容 dict 列表 / 纯字符串列表 / 单字符串三种形态。"""
    for history in ([{"name": "旧名A"}], ["旧名A"], "旧名A"):
        s = dict(SAMPLE, history_names=history)
        events = parse_events(s, "测试集团有限公司")
        assert "旧名A" in events[0].summary


def test_pick_record_exact_wins():
    """全名精确命中优先于曾用名与核心词匹配。"""
    items = [
        {"name": "武汉鑫泰商业管理有限公司", "reg_status": "存续"},
        {"name": "其他公司", "history_names": ["武汉鑫泰商业管理有限公司"]},
    ]
    hit, how = pick_record(items, "武汉鑫泰商业管理有限公司")
    assert how == "exact"
    assert hit["reg_status"] == "存续"


def test_pick_record_matches_former_name():
    """名称已变更：记录名与监控企业名不同，但曾用名命中 -> 属名称变更信号。"""
    items = [{"name": "武汉鑫泰实业有限公司",
              "history_names": ["武汉鑫泰商业管理有限公司"],
              "reg_status": "存续"}]
    hit, how = pick_record(items, "武汉鑫泰商业管理有限公司")
    assert how == "history"
    assert hit["name"] == "武汉鑫泰实业有限公司"
    events = parse_events(hit, "武汉鑫泰商业管理有限公司", how)
    assert events[0].relevance == "high"
    assert "曾用名匹配" in events[0].summary


def test_pick_record_core_name_fallback():
    """名称写法有前缀差异时回落到核心词匹配，并降级为 medium + 人工核对提示。"""
    items = [{"name": "辽宁省鑫泰房地产开发有限公司", "reg_status": "存续"}]
    hit, how = pick_record(items, "鑫泰房地产开发有限公司")
    assert how == "core"
    events = parse_events(hit, "鑫泰房地产开发有限公司", how)
    assert events[0].relevance == "medium"
    assert "人工核对" in events[0].summary


def test_pick_record_short_core_name_not_matched():
    """核心词过短（<4 字）不做包含匹配，避免泛词误命中。"""
    items = [{"name": "北京鑫泰商业管理有限公司", "reg_status": "存续"}]
    hit, how = pick_record(items, "鑫泰有限公司")
    assert hit is None and how == ""


def test_pick_record_skips_non_dict_and_no_match():
    assert pick_record(["not-a-dict"], "某企业") == (None, "")
    assert pick_record([{"name": "完全无关企业", "reg_status": "存续"}],
                       "武汉鑫泰商业管理有限公司") == (None, "")
