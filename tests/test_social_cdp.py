"""社媒「真实接入」链路测试：CDP 渲染抓取、登录态读取、GUI 登录态面板。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import cdp, social_auth  # noqa: E402
from src.core.models import Enterprise  # noqa: E402
from src.providers.base import SocialAuthMissing  # noqa: E402
from src.providers.social_base import SocialProviderBase  # noqa: E402


class _TempAuthFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._tmp.name, "social_auth.yaml")
        p = mock.patch.object(social_auth, "auth_path", lambda: self._path)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)


class CdpPortTest(_TempAuthFile):
    """CDP 端口持久化。"""

    def test_default_port(self):
        self.assertEqual(social_auth.get_cdp_port(), 9222)

    def test_set_and_get_port(self):
        social_auth.set_cdp_port(9333)
        self.assertEqual(social_auth.get_cdp_port(), 9333)

    def test_port_survives_reload(self):
        social_auth.set_cdp_port(9444)
        self.assertEqual(social_auth.get_cdp_port(), 9444)


class ImportAllTest(_TempAuthFile):
    """一次性读取全部站点登录态。"""

    def test_partial_failure_isolated(self):
        def fake_import(site, port=0):
            if site == "douyin":
                raise RuntimeError("未登录")
            return "a=1"
        with mock.patch.object(social_auth, "import_from_chrome", fake_import):
            result = social_auth.import_all_from_chrome()
        self.assertTrue(result["weibo"][0])
        self.assertFalse(result["douyin"][0])
        self.assertIn("未登录", result["douyin"][1])

    def test_auth_summary_counts(self):
        social_auth.set_cookie("weibo", "a=1")
        social_auth.set_cookie("douyin", "b=2")
        summary = social_auth.auth_summary()
        self.assertEqual(summary["configured"], 2)
        self.assertEqual(summary["total"], 5)


class BrowserLaunchTest(_TempAuthFile):
    """启动登录浏览器。"""

    def test_launch_records_port(self):
        with mock.patch.object(cdp, "is_debug_port_open", return_value=False), \
                mock.patch.object(cdp, "launch_browser", return_value=(True, "ok")) as lb:
            ok, msg = social_auth.launch_login_browser(9222)
        self.assertTrue(ok)
        lb.assert_called_once()
        self.assertEqual(social_auth.get_cdp_port(), 9222)

    def test_already_running_short_circuits(self):
        with mock.patch.object(cdp, "is_debug_port_open", return_value=True), \
                mock.patch.object(cdp, "launch_browser") as lb:
            ok, msg = social_auth.launch_login_browser(9222)
        self.assertTrue(ok)
        lb.assert_not_called()


class _HtmlProvider(SocialProviderBase):
    """用于验证 fetch_html 走浏览器抓取通路的假源。"""
    name = "htmlprobe"
    category = "social"
    site = "weibo"

    def fetch(self, ent):
        text = self.fetch_html("https://example.invalid/search")
        return [self.make_event(ent, text[:40], url="https://example.invalid/x")]


class FetchHtmlRoutingTest(_TempAuthFile):
    """fetch_html：优先浏览器渲染抓取，失败回退 HTTP。"""

    def setUp(self):
        super().setUp()
        self.ent = Enterprise(name="测试企业")

    def test_browser_path_used_when_available(self):
        p = _HtmlProvider({})
        with mock.patch.object(p, "browser_available", return_value=True), \
                mock.patch.object(cdp, "fetch_rendered",
                                  return_value="<html>" + "x" * 900 + "</html>") as fr:
            events = p.fetch(self.ent)
        fr.assert_called_once()
        self.assertEqual(len(events), 1)

    def test_falls_back_to_http(self):
        p = _HtmlProvider({})
        resp = mock.Mock(status_code=200, text="<html>plain</html>")
        with mock.patch.object(p, "browser_available", return_value=False), \
                mock.patch.object(p, "http_get", return_value=resp):
            events = p.fetch(self.ent)
        self.assertEqual(len(events), 1)

    def test_http_error_mentions_browser_failure(self):
        p = _HtmlProvider({})
        resp = mock.Mock(status_code=403, text="")
        with mock.patch.object(p, "browser_available", return_value=True), \
                mock.patch.object(cdp, "fetch_rendered", side_effect=RuntimeError("浏览器挂了")), \
                mock.patch.object(p, "http_get", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                p.fetch(self.ent)
        self.assertIn("浏览器挂了", str(ctx.exception))

    def test_browser_error_does_not_break_http_success(self):
        p = _HtmlProvider({})
        resp = mock.Mock(status_code=200, text="<html>ok</html>")
        with mock.patch.object(p, "browser_available", return_value=True), \
                mock.patch.object(cdp, "fetch_rendered", side_effect=RuntimeError("超时")), \
                mock.patch.object(p, "http_get", return_value=resp):
            events = p.fetch(self.ent)
        self.assertEqual(len(events), 1)


class ProviderUsesFetchHtmlTest(unittest.TestCase):
    """四个社媒源都必须走 fetch_html（浏览器渲染通路）。"""

    def test_all_social_sources_use_fetch_html(self):
        from src.providers import (social_douyin, social_kuaishou,
                                   social_weibo, social_xiaohongshu)
        for mod in (social_weibo, social_douyin, social_kuaishou, social_xiaohongshu):
            src = open(mod.__file__, "r", encoding="utf-8").read()
            self.assertIn("self.fetch_html(", src,
                          f"{mod.__name__} 应使用 fetch_html 以走浏览器渲染抓取")
            self.assertNotIn("self.http_get(", src,
                             f"{mod.__name__} 不应再直接发 HTTP 请求")


class RequiresAuthSkipTest(unittest.TestCase):
    """未配置登录态时源被跳过（不发请求）。"""

    def test_source_skipped_without_cookie(self):
        from src.providers.social_weibo import WeiboProvider
        p = WeiboProvider({"requires_auth": True})
        with self.assertRaises(SocialAuthMissing):
            p.fetch(Enterprise(name="测试企业"))


class SocialAuthGuiTest(unittest.TestCase):
    """登录态面板基本结构（离屏）。"""

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(social_auth, "auth_path",
                              lambda: os.path.join(self._tmp.name, "social_auth.yaml"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_widget_lists_five_sites(self):
        from src.gui.social_auth_page import SocialAuthWidget
        w = SocialAuthWidget()
        self.assertEqual(w.tree.topLevelItemCount(), 5)
        w.deleteLater()

    def test_widget_shows_configured_state(self):
        from src.gui.social_auth_page import SocialAuthWidget
        social_auth.set_cookie("weibo", "SUB=abcdef; SUBP=xyz")
        w = SocialAuthWidget()
        item = w.tree.topLevelItem(0)
        self.assertEqual(item.text(1), "已配置")
        self.assertNotIn("abcdef", item.text(2))  # 掩码显示
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()
