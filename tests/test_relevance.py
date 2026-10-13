"""core.relevance 相关性过滤单测。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Event  # noqa: E402
from src.core.relevance import core_name, filter_relevant, relevance_of  # noqa: E402

ENT = "辽宁省沈抚示范区鑫泰房地产开发有限公司"


def test_core_name():
    assert core_name(ENT) == "鑫泰房地产开发"
    assert core_name("抚顺同盛资产经营有限公司") == "同盛资产经营"
    assert core_name("浙江省杭州市某科技有限公司") == "某科技"
    assert core_name("华为技术有限公司") == "华为技术"


def test_filter_keeps_full_name_hit():
    evs = [Event(category="news", title="辽宁省沈抚示范区鑫泰房地产开发有限公司新增开工许可")]
    out = filter_relevant(evs, ENT, "strict")
    assert len(out) == 1
    assert relevance_of(evs[0].title, "", ENT) == "high"


def test_filter_keeps_core_hit():
    evs = [Event(category="news", title="鑫泰房地产中标某项目")]
    out = filter_relevant(evs, ENT, "strict")
    assert len(out) == 1
    assert relevance_of(evs[0].title, "", ENT) == "medium"


def test_filter_drops_unrelated():
    evs = [Event(category="news", title="今日全国猪肉价格上涨"),
           Event(category="news", title="某小区鑫泰嘉园开盘"),  # 部分词不应命中核心词
           Event(category="news", title="泰房地产公司新闻")]     # 非完整核心词
    out = filter_relevant(evs, ENT, "strict")
    assert len(out) == 0


def test_filter_keeps_empty_title_and_off_mode():
    assert len(filter_relevant([Event(category="news")], ENT, "strict")) == 1  # 无标题保留
    evs = [Event(category="news", title="完全无关的新闻")]
    assert len(filter_relevant(evs, ENT, "off")) == 1  # off 不过滤
