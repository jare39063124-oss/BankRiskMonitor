"""社媒登录态、多源合并、容错解析相关测试。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import social_auth  # noqa: E402
from src.core.models import Enterprise  # noqa: E402
from src.providers import base as pbase  # noqa: E402
from src.providers.base import SocialAuthMissing  # noqa: E402
from src.providers.social_base import (SocialProviderBase,  # noqa: E402
                                       extract_json_block_lenient)


class _TempAuthFile(unittest.TestCase):
    """把凭据文件重定向到临时目录，避免污染项目根。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "social_auth.yaml")
        patcher = mock.patch.object(social_auth, "auth_path", lambda: self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


class ParseCookieTest(unittest.TestCase):
    """用户粘贴格式的归一化。"""

    def test_header_line(self):
        self.assertEqual(social_auth.parse_cookie("Cookie: a=1; b=2"), "a=1; b=2")

    def test_plain_pairs(self):
        self.assertEqual(social_auth.parse_cookie("a=1; b=2"), "a=1; b=2")

    def test_json_array(self):
        raw = '[{"name":"a","value":"1"},{"name":"b","value":"2"}]'
        self.assertEqual(social_auth.parse_cookie(raw), "a=1; b=2")

    def test_json_object(self):
        self.assertEqual(social_auth.parse_cookie('{"a":"1","b":"2"}'), "a=1; b=2")

    def test_multiline_merged(self):
        self.assertEqual(social_auth.parse_cookie("a=1;\nb=2"), "a=1; b=2")

    def test_empty(self):
        self.assertEqual(social_auth.parse_cookie(""), "")


class MaskCookieTest(unittest.TestCase):
    def test_masks_values(self):
        masked = social_auth.mask_cookie("SUB=abcdefgh; SUBP=xyz")
        self.assertNotIn("abcdefgh", masked)
        self.assertIn("SUB=", masked)

    def test_long_cookie_count_suffix(self):
        masked = social_auth.mask_cookie("a=1; b=2; c=3; d=4")
        self.assertIn("共 4 项", masked)


class CredentialStoreTest(_TempAuthFile):
    """凭据读写 / 清除 / 状态。"""

    def test_set_get_clear(self):
        social_auth.set_cookie("weibo", "Cookie: SUB=abc; SUBP=def")
        self.assertEqual(social_auth.get_cookie("weibo"), "SUB=abc; SUBP=def")
        st = social_auth.status("weibo")
        self.assertTrue(st["configured"])
        self.assertEqual(st["count"], 2)
        social_auth.clear("weibo")
        self.assertEqual(social_auth.get_cookie("weibo"), "")

    def test_missing_file_returns_empty(self):
        self.assertEqual(social_auth.load_all(), {})

    def test_all_status_covers_five_sites(self):
        sites = {s["site"] for s in social_auth.all_status()}
        self.assertTrue({"weibo", "douyin", "kuaishou",
                         "xiaohongshu", "wechat_sogou"} <= sites)


class _FakeProvider(SocialProviderBase):
    name = "fake"
    category = "social"
    site = "weibo"

    def __init__(self, params, events=None, exc=None):
        super().__init__(params)
        self._events = events or []
        self._exc = exc

    def fetch(self, ent):
        if self._exc:
            raise self._exc
        return list(self._events)


class AuthInjectionTest(_TempAuthFile):
    """登录态注入优先级与「未配置即跳过」。"""

    def test_param_cookie_wins(self):
        social_auth.set_cookie("weibo", "SUB=file")
        p = _FakeProvider({"cookie": "SUB=param"})
        self.assertEqual(p.auth_cookie(), "SUB=param")

    def test_fallback_to_credential_file(self):
        social_auth.set_cookie("weibo", "SUB=file")
        p = _FakeProvider({})
        self.assertEqual(p.auth_cookie(), "SUB=file")
        self.assertTrue(p.has_auth())

    def test_headers_inject_cookie(self):
        social_auth.set_cookie("weibo", "SUB=file")
        p = _FakeProvider({})
        p.referer = "https://s.weibo.com/"
        h = p.headers()
        self.assertEqual(h.get("Cookie"), "SUB=file")
        self.assertEqual(h.get("Referer"), "https://s.weibo.com/")

    def test_ensure_auth_raises_when_required_and_missing(self):
        p = _FakeProvider({"requires_auth": True})
        self.assertTrue(p.auth_required)
        with self.assertRaises(SocialAuthMissing):
            p.ensure_auth()

    def test_ensure_auth_passes_when_configured(self):
        social_auth.set_cookie("weibo", "SUB=file")
        p = _FakeProvider({"requires_auth": True})
        p.ensure_auth()  # 不抛异常

    def test_social_auth_missing_is_single_class(self):
        """social_base 与 base 必须共用同一个异常类，否则调度器捕获不到。"""
        from src.providers import social_base
        self.assertIs(social_base.SocialAuthMissing, SocialAuthMissing)


class RunCategoryTest(unittest.TestCase):
    """多源并发合并：跨源去重 + 每源状态记账。"""

    def setUp(self):
        pbase.reset_source_stats()

    def _ent(self):
        return Enterprise(name="测试企业")

    def test_merges_and_dedups(self):
        ev1 = mock.Mock(title="同一条")
        ev2 = mock.Mock(title="另一条")
        p1 = mock.Mock(name="p1")
        p1.name = "p1"
        p1.auth_required = False
        p1.fetch.return_value = [ev1, ev2]
        p2 = mock.Mock(name="p2")
        p2.name = "p2"
        p2.auth_required = False
        p2.fetch.return_value = [ev1]
        with mock.patch.object(pbase, "load_providers", return_value=[p1, p2]):
            res = pbase.run_category("social", self._ent())
        self.assertEqual(len(res["events"]), 2)
        self.assertEqual(res["sources"], ["p1", "p2"])
        self.assertEqual(res["stats"]["p1"]["status"], "ok")

    def test_failed_source_does_not_break_others(self):
        good = mock.Mock()
        good.name = "good"
        good.auth_required = False
        good.fetch.return_value = [mock.Mock(title="A")]
        bad = mock.Mock()
        bad.name = "bad"
        bad.auth_required = False
        bad.fetch.side_effect = RuntimeError("网络错误")
        with mock.patch.object(pbase, "load_providers", return_value=[good, bad]):
            res = pbase.run_category("social", self._ent())
        self.assertEqual(len(res["events"]), 1)
        self.assertIn("bad", res["errors"])

    def test_auth_required_source_is_skipped_without_request(self):
        p = mock.Mock()
        p.name = "need_auth"
        p.auth_required = True
        p.has_auth.return_value = False
        with mock.patch.object(pbase, "load_providers", return_value=[p]):
            res = pbase.run_category("social", self._ent())
        p.fetch.assert_not_called()
        self.assertEqual(res["stats"]["need_auth"]["status"], "skipped")

    def test_run_with_fallback_label_lists_all_ok_sources(self):
        p1 = mock.Mock()
        p1.name = "a"
        p1.auth_required = False
        p1.fetch.return_value = [mock.Mock(title="A")]
        p2 = mock.Mock()
        p2.name = "b"
        p2.auth_required = False
        p2.fetch.return_value = [mock.Mock(title="B")]
        with mock.patch.object(pbase, "load_providers", return_value=[p1, p2]):
            events, label = pbase.run_with_fallback("social", self._ent())
        self.assertEqual(len(events), 2)
        self.assertEqual(label, "a,b")

    def test_stats_accumulate_per_source(self):
        p = mock.Mock()
        p.name = "acc"
        p.auth_required = False
        p.fetch.return_value = [mock.Mock(title="A")]
        with mock.patch.object(pbase, "load_providers", return_value=[p]):
            pbase.run_category("news", self._ent())
        st = pbase.source_stats("news")
        self.assertEqual(st["acc"]["ok"], 1)
        self.assertEqual(st["acc"]["category"], "news")


class LenientJsonTest(unittest.TestCase):
    """数据块容错抽取。"""

    def test_plain_block(self):
        text = 'x=1; window.__INITIAL_STATE__={"a":{"b":1}}'
        data = extract_json_block_lenient(text, ["__INITIAL_STATE__"])
        self.assertEqual(data["a"]["b"], 1)

    def test_uses_second_marker_when_first_absent(self):
        text = 'window.__INITIAL_STATE__={"a":1}'
        data = extract_json_block_lenient(text, ["__ROUTER_DATA__", "__INITIAL_STATE__"])
        self.assertEqual(data["a"], 1)

    def test_undefined_is_sanitized(self):
        text = 'window.__INITIAL_STATE__={"a":undefined,"b":2}'
        data = extract_json_block_lenient(text, ["__INITIAL_STATE__"])
        self.assertIsNone(data["a"])
        self.assertEqual(data["b"], 2)

    def test_trailing_garbage_tolerated(self):
        text = 'window.__INITIAL_STATE__={"a":{"b":1}}, extra(junk);'
        data = extract_json_block_lenient(text, ["__INITIAL_STATE__"])
        self.assertEqual(data["a"]["b"], 1)

    def test_missing_marker_raises(self):
        with self.assertRaises(RuntimeError):
            extract_json_block_lenient("nothing here", ["__INITIAL_STATE__"])


class CdpCookieFilterTest(unittest.TestCase):
    """CDP 读到的 Cookie 按域名筛选。"""

    def test_filter_by_domain_suffix(self):
        from src.core import cdp
        cookies = [
            {"name": "a", "value": "1", "domain": ".weibo.com"},
            {"name": "b", "value": "2", "domain": "www.douyin.com"},
            {"name": "c", "value": "3", "domain": ".example.org"},
        ]
        header = cdp.cookie_header_for(cookies, ["weibo.com"])
        self.assertEqual(header, "a=1")


if __name__ == "__main__":
    unittest.main()
