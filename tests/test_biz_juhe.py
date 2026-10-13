"""聚合数据工商信息适配器单测：返回归一化解析（纯函数，不联网）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.biz_juhe import parse_events  # noqa: E402

SAMPLE = {
    "Name": "测试集团有限公司",
    "Status": "存续",
    "OperName": "张三",
    "RegCapital": "10000万元人民币",
    "CreditCode": "91110000XXXXXXXXXX",
    "StartDate": "2018-01-29",
    "Address": "北京市海淀区",
    "Scope": "一般项目：技术服务",
    "BelongOrg": "北京市海淀区市场监督管理局",
    "Partner": [
        {"StockName": "甲公司", "StockPercent": "60%"},
        {"StockName": "乙公司", "StockPercent": "40%"},
    ],
}


def test_parse_normal():
    events = parse_events(SAMPLE, "测试集团有限公司")
    assert len(events) == 1
    ev = events[0]
    assert ev.category == "biz_change"
    assert ev.source == "juhe_ent"
    assert ev.severity == "low"
    assert ev.sentiment == "neutral"
    assert "张三" in ev.summary and "甲公司" in ev.summary
    assert ev.relevance == "high"


def test_parse_bad_status():
    s = dict(SAMPLE, Status="注销")
    events = parse_events(s, "测试集团有限公司")
    assert events[0].severity == "high"
    assert events[0].sentiment == "negative"
    assert "注销" in events[0].title


def test_parse_empty():
    assert parse_events({}, "某企业") == []


def test_parse_snake_case_alias():
    s = {"name": "某企业", "status": "存续", "operName": "李四",
         "regCapital": "500万", "creditCode": "91XXX"}
    events = parse_events(s, "某企业")
    assert len(events) == 1
    assert "李四" in events[0].summary
    assert "91XXX" in events[0].summary
