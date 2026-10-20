"""社媒五源离线测试：HTML/JSON 解析、反爬 fallback、Cookie 注入、事件去重。

全部 mock 掉网络（http_get / requests.get），不发起真实请求。
运行：python -m unittest tests.test_social -v
"""
import gc
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):  # 兼容 src.* 与顶层 providers.* 两种导入
    if p not in sys.path:
        sys.path.insert(0, p)

from src.core import db, settings, sync as sync_mod  # noqa: E402
from src.core.models import Enterprise, Event  # noqa: E402
from src.providers.social_base import ts_to_date  # noqa: E402

# 把真实配置预热进缓存（绕开 paths.py 开发模式路径问题，同 test_providers 做法）
with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}

ENT = Enterprise(id=1, name="测试公司")


class FakeResp:
    """模拟 requests.Response。"""

    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


# ---------- 离线页面样本 ----------
WEIBO_HTML = """<html><body>
<div class="card-wrap" mid="1">
  <div class="content"><p class="txt">测试公司 中标某重点项目，发展势头良好</p></div>
  <div class="from"><a href="/weibo/111">3分钟前</a></div>
  <a class="name" href="/n/测试官微">测试官微</a>
</div>
<div class="card-wrap" mid="2">
  <div class="content"><p class="txt">测试公司 新增被执行记录，金额较大</p></div>
  <div class="from"><a href="/weibo/222">2026-09-20</a></div>
  <a class="name" href="/n/财经爆料">财经爆料</a>
</div>
</body></html>"""

WECHAT_HTML = """<html><body>
<ul class="news-list">
  <li><div class="txt-box">
    <h3><a href="/link?url=abc123">测试公司 发布年度经营报告</a></h3>
    <p class="txt-info">测试公司披露上半年营收情况</p>
    <div class="s-p"><a href="/gohome/x"><span class="account">企业观察</span></a>
      <span class="s2" t="1785000000">2026-09-23</span></div>
  </div></li>
  <li><div class="txt-box">
    <h3><a href="https://weixin.sogou.com/link?url=xyz">测试公司被处罚？官方回应来了</a></h3>
    <p class="txt-info">监管部门对测试公司开出罚单</p>
    <div class="s-p"><a href="/gohome/y"><span class="account">财经深度</span></a>
      <span class="s2">昨天 18:30</span></div>
  </div></li>
</ul>
</body></html>"""

DOUYIN_HTML = """<html><head><script>
self.__ROUTER_DATA__ = {"loaderData": {"search": {"data": {"items": [
  {"desc": "测试公司发布新款产品", "create_time": 1785000000,
   "author": {"nickname": "科技君"}, "share_url": "https://v.douyin.com/abc"},
  {"desc": "无关视频", "create_time": 1785000001,
   "author": {"nickname": "路人"}, "share_url": "https://v.douyin.com/def"}
]}}}};
</script></head><body>页面壳</body></html>"""

DOUYIN_SHELL_HTML = "<html><body><div id='root'></div></body></html>"

KUAISHOU_HTML = """<html><head><script>
window.__APOLLO_STATE__ = {"defaultClient": {
  "Video:1": {"caption": "测试公司年会精彩回顾", "timestamp": 1785000000000,
              "photoUrl": "https://www.kuaishou.com/short-video/1",
              "author": {"nickname": "小快"}},
  "Video:2": {"caption": "测试公司新工厂开工", "timestamp": 1785000001000}
}};
</script></head><body></body></html>"""

XHS_HTML = """<html><head><script>
window.__INITIAL_STATE__ = {"search": {"notes": [
  {"id": "abc123", "xsec_token": "t1", "display_title": "测试公司工厂探秘",
   "user": {"nickname": "小红薯"}},
  {"id": "def456", "xsec_token": "t2", "display_title": "测试公司新品测评"}
]}};
</script></head><body></body></html>"""

WEIBO_ANTI_HTML = "<html><body>访问异常，跳转 passport.weibo.com 登录</body></html>"


class DbTestBase(unittest.TestCase):
    """把 SQLite 重定向到临时文件，避免污染项目 data 目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        patcher = mock.patch.object(
            db, "db_path", return_value=os.path.join(self._tmp.name, "test.db"))
        patcher.start()
        self._db_patcher = patcher
        db.init_db()

    def tearDown(self):
        self._db_patcher.stop()
        gc.collect()
        self._tmp.cleanup()


def no_browser(p):
    """关闭浏览器渲染通路（单测只验证 HTTP 解析，不碰真实浏览器）。"""
    return mock.patch.object(p, "browser_available", return_value=False)


# ---------- 各源解析 ----------
class WeiboTest(unittest.TestCase):
    def test_parse(self):
        from src.providers import social_weibo as m
        p = m.WeiboProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(WEIBO_HTML)):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.category == "social" and e.source == "weibo" for e in events))
        self.assertTrue(all(e.sentiment == "neutral" for e in events))
        self.assertEqual(events[0].url, "https://s.weibo.com/weibo/111")
        self.assertEqual(events[0].occurred_at, time.strftime("%Y-%m-%d"))
        self.assertEqual(events[0].severity, "low")
        self.assertIn("测试官微", events[0].summary)  # 昵称进 summary
        self.assertEqual(events[1].severity, "high")  # 含"被执行"
        self.assertEqual(events[1].occurred_at, "2026-09-20")

    def test_anti_spider_raises(self):
        from src.providers import social_weibo as m
        p = m.WeiboProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(WEIBO_ANTI_HTML)):
            with self.assertRaises(RuntimeError):
                p.fetch(ENT)


class WechatTest(unittest.TestCase):
    def test_parse(self):
        from src.providers import social_wechat as m
        p = m.SogouWeixinProvider({})
        with mock.patch.object(p, "http_get", return_value=FakeResp(WECHAT_HTML)):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.category == "social" and e.source == "wechat_sogou"
                            for e in events))
        self.assertEqual(events[0].url, "https://weixin.sogou.com/link?url=abc123")
        self.assertEqual(events[0].occurred_at, ts_to_date(1785000000))
        self.assertIn("企业观察", events[0].summary)  # 公众号名进 summary
        self.assertEqual(events[1].severity, "medium")  # 含"处罚"
        self.assertEqual(events[1].occurred_at,
                         (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))

    def test_antispider_raises(self):
        from src.providers import social_wechat as m
        p = m.SogouWeixinProvider({})
        with mock.patch.object(p, "http_get",
                               return_value=FakeResp("<html>_antispider 拦截</html>")):
            with self.assertRaises(RuntimeError):
                p.fetch(ENT)


class DouyinTest(unittest.TestCase):
    def test_parse(self):
        from src.providers import social_douyin as m
        p = m.DouyinProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(DOUYIN_HTML)):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.category == "social" and e.source == "douyin"
                            for e in events))
        self.assertEqual(events[0].url, "https://v.douyin.com/abc")
        self.assertEqual(events[0].occurred_at, ts_to_date(1785000000))
        self.assertIn("科技君", events[0].summary)

    def test_shell_page_raises(self):
        from src.providers import social_douyin as m
        p = m.DouyinProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(DOUYIN_SHELL_HTML)):
            with self.assertRaises(RuntimeError):
                p.fetch(ENT)


class KuaishouTest(unittest.TestCase):
    def test_parse(self):
        from src.providers import social_kuaishou as m
        p = m.KuaishouProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(KUAISHOU_HTML)):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.category == "social" and e.source == "kuaishou"
                            for e in events))
        self.assertEqual(events[0].url, "https://www.kuaishou.com/short-video/1")
        self.assertEqual(events[0].occurred_at, ts_to_date(1785000000))  # 毫秒级时间戳
        self.assertIn("小快", events[0].summary)


class XiaohongshuTest(unittest.TestCase):
    def test_parse(self):
        from src.providers import social_xiaohongshu as m
        p = m.XiaohongshuProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(XHS_HTML)):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.category == "social" and e.source == "xiaohongshu"
                            for e in events))
        self.assertEqual(events[0].url, "https://www.xiaohongshu.com/explore/abc123")
        self.assertIn("小红薯", events[0].summary)

    def test_shell_page_raises(self):
        from src.providers import social_xiaohongshu as m
        p = m.XiaohongshuProvider({})
        with no_browser(p), \
                mock.patch.object(p, "http_get", return_value=FakeResp(DOUYIN_SHELL_HTML)):
            with self.assertRaises(RuntimeError):
                p.fetch(ENT)


# ---------- Cookie 注入 ----------
class CookieTest(unittest.TestCase):
    def test_cookie_injected_and_keyword(self):
        from src.providers import social_weibo as m
        p = m.WeiboProvider({"cookie": "SUB=abc; SUBP=def"})
        with no_browser(p), \
                mock.patch("requests.get", return_value=FakeResp(WEIBO_HTML)) as g:
            p.fetch(ENT)
        kwargs = g.call_args.kwargs
        self.assertEqual(kwargs["headers"].get("Cookie"), "SUB=abc; SUBP=def")
        self.assertEqual(kwargs["params"].get("q"), ENT.name)  # 搜索词=企业名

    def test_cookie_empty_skipped(self):
        from src.providers import social_weibo as m
        self.assertNotIn("Cookie", m.WeiboProvider({}).headers())
        self.assertNotIn("Cookie", m.WeiboProvider({"cookie": "   "}).headers())


# ---------- fallback 与配置声明 ----------
class FallbackTest(unittest.TestCase):
    def test_fallback_switches_on_anti_spider(self):
        from src.providers import base as pbase
        from src.providers import social_wechat as wx, social_weibo as wb
        bad = wb.WeiboProvider({})
        good = wx.SogouWeixinProvider({})
        with no_browser(bad), no_browser(good), \
             mock.patch.object(bad, "http_get", return_value=FakeResp(WEIBO_ANTI_HTML)), \
             mock.patch.object(good, "http_get", return_value=FakeResp(WECHAT_HTML)), \
             mock.patch.object(pbase, "load_providers", return_value=[bad, good]):
            events, source = pbase.run_with_fallback("social", ENT)
        self.assertIn("wechat_sogou", source)  # 微博反爬自动切搜狗微信
        self.assertTrue(events)
        self.assertEqual(events[0].category, "social")

    def test_config_declares_five_social_sources(self):
        """config.yaml social 段：五个原有社媒源 + 可选的 opencli 后端。"""
        from src.providers import base as pbase
        ps = pbase.load_providers("social")
        names = [p.name for p in ps]
        for expected in ("weibo", "wechat_sogou", "douyin", "kuaishou", "xiaohongshu"):
            self.assertIn(expected, names)
        # opencli 后端默认关闭，不应出现在已启用源里
        self.assertNotIn("opencli", names)
        self.assertTrue(all(p.category == "social" for p in ps))


# ---------- 入库去重与 run_sync ----------
class SocialDbTest(DbTestBase):
    def test_insert_event_dedup(self):
        ev = Event(enterprise_id=1, category="social", title="同一条微博", source="weibo")
        self.assertTrue(db.insert_event(ev))
        self.assertFalse(db.insert_event(ev))  # (enterprise_id, category, title, source) 去重
        ev2 = Event(enterprise_id=1, category="social", title="同一条微博",
                    source="wechat_sogou")
        self.assertTrue(db.insert_event(ev2))  # 不同 source 不算重复
        self.assertEqual(len(db.list_events(enterprise_id=1)), 2)

    def test_run_sync_includes_social(self):
        def fake_rwf(cat, ent):
            if cat == "social":
                return [Event(category="social", title="测试公司微博爆料",
                              source="weibo")], "weibo"
            return [], f"{cat}: empty"

        with mock.patch.object(sync_mod, "run_with_fallback", side_effect=fake_rwf):
            res = sync_mod.run_sync(Enterprise(id=1, name="测试公司"))
        self.assertIn("social", res)
        self.assertEqual(res["social"]["inserted"], 1)
        self.assertEqual(res["social"]["source"], "weibo")
        # 第二次同步去重
        with mock.patch.object(sync_mod, "run_with_fallback", side_effect=fake_rwf):
            res2 = sync_mod.run_sync(Enterprise(id=1, name="测试公司"))
        self.assertEqual(res2["social"]["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
