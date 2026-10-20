"""人民法院公告网适配器 + 浏览器后端层测试。

覆盖两类关键契约：
1. 公告行的解析/当事人匹配/类型分流（纯函数，不触网）；
2. ``fetch()`` 必须走「渲染后端 + 页面内搜索」（URL 参数 searchContent 无效），
   且同一企业短时间内重复调用只开一次页面（涉诉/执行两类共同复用）。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import cdp  # noqa: E402
from src.core.models import Enterprise  # noqa: E402
from src.providers import court_rmfygg as rm  # noqa: E402

FIXTURE_HTML = """
<html><body>
<table id="noticeListTable">
  <thead><tr role="row"><th>公告人</th><th>当事人</th><th>公告类型</th>
  <th>发布时间</th><th>下载打印</th></tr></thead>
  <tbody class="rmfy-table-tbody" id="gg-list">
    <tr role="row" class="oddd">
      <td><a href="/d1">武汉市江夏区人民法院</a></td>
      <td><a href="/d2">江伟、杜翠红、武汉鑫泰商业管理有限公司</a></td>
      <td>起诉状副本及开庭传票</td><td>2026-10-19</td><td></td>
    </tr>
    <tr role="row" class="evenn">
      <td>鄂尔多斯市东胜区人民法院</td>
      <td>鄂尔多斯市宝泰鑫置业有限责任公司</td>
      <td>执行文书、拍卖</td><td>2026-10-16</td><td></td>
    </tr>
  </tbody>
</table>
</body></html>
"""


class _FakeSession:
    """最小 PageSession 替身：记录被调用的 JS，返回预置 HTML。"""

    def __init__(self, html: str, verdict: str = "CLICKED"):
        self._html = html
        self._verdict = verdict
        self.opened = []
        self.waits = []
        self.evaluated = []
        self.closed = False

    def open(self, url, settle_ms=0):
        self.opened.append(url)
        return ""

    def wait_until(self, expression, timeout=0, interval=0.3):
        self.waits.append(expression)
        return True

    def evaluate(self, expression):
        self.evaluated.append(expression)
        return self._verdict

    def html(self):
        return self._html

    def close(self):
        self.closed = True


class ParseNoticeRowsTest(unittest.TestCase):
    def test_parses_all_data_rows(self):
        rows = rm.parse_notice_rows(FIXTURE_HTML)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["court"], "武汉市江夏区人民法院")
        self.assertIn("武汉鑫泰商业管理有限公司", rows[0]["parties"])
        self.assertEqual(rows[0]["notice_type"], "起诉状副本及开庭传票")
        self.assertEqual(rows[0]["publish_date"], "2026-10-19")
        self.assertEqual(rows[1]["notice_type"], "执行文书、拍卖")

    def test_header_row_excluded(self):
        rows = rm.parse_notice_rows(FIXTURE_HTML)
        self.assertNotIn("公告人", [r["court"] for r in rows])

    def test_empty_page_returns_empty(self):
        self.assertEqual(rm.parse_notice_rows("<html></html>"), [])


class MatchPartyTest(unittest.TestCase):
    def test_full_name_hit(self):
        self.assertTrue(rm.match_party("江伟、杜翠红、武汉鑫泰商业管理有限公司",
                                       "武汉鑫泰商业管理有限公司"))

    def test_unrelated_party_miss(self):
        self.assertFalse(rm.match_party("青岛城建门窗有限公司;董海涛",
                                        "武汉鑫泰商业管理有限公司"))

    def test_core_name_strips_region_and_suffix(self):
        self.assertEqual(rm._core_name("辽宁省鑫泰重工有限公司"), "鑫泰重工")

    def test_core_name_match_without_prefix(self):
        self.assertTrue(rm.match_party("济宁鑫泰重工工程有限公司",
                                       "辽宁省鑫泰重工工程有限公司"))

    def test_short_core_name_not_matched(self):
        # 核心词过短（<4）时不做包含匹配，避免误伤同名短词
        self.assertFalse(rm.match_party("北京鑫泰科技有限公司", "辽宁省鑫泰有限公司"))


class ClassifyNoticeTest(unittest.TestCase):
    def test_enforcement_is_high(self):
        self.assertEqual(rm.classify_notice("执行文书、拍卖"), ("enforcement", "high"))

    def test_lawsuit_is_medium(self):
        self.assertEqual(rm.classify_notice("起诉状副本及开庭传票"), ("lawsuit", "medium"))
        self.assertEqual(rm.classify_notice("裁判文书"), ("lawsuit", "medium"))

    def test_unknown_type_is_low_lawsuit(self):
        self.assertEqual(rm.classify_notice("其他（非法院单位）"), ("lawsuit", "low"))


class FetchFlowTest(unittest.TestCase):
    """fetch 必须走页面内搜索，而不是直接带 searchContent 开 URL。"""

    def setUp(self):
        rm._MEMO.clear()
        self.addCleanup(rm._MEMO.clear)

    def test_fetch_uses_in_page_search(self):
        session = _FakeSession(FIXTURE_HTML)
        with mock.patch.object(cdp, "public_session", lambda timeout=25.0: session):
            events = rm.RmfyggProvider({}).fetch(
                Enterprise(name="武汉鑫泰商业管理有限公司"))
        # 打开的是 https 列表页（Obscura 不跟随 http→https 跳转）
        self.assertEqual(session.opened, [rm.LIST_URL])
        self.assertTrue(rm.LIST_URL.startswith("https://"))
        # 执行了搜索脚本，并带上企业名
        joined = " ".join(session.evaluated)
        self.assertIn("searchButton", joined)
        self.assertIn("武汉鑫泰商业管理有限公司", joined)
        self.assertTrue(session.closed)
        # 只保留命中本企业的公告，且按类型分流到对应信息域
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].category, "lawsuit")
        self.assertIn("武汉市江夏区人民法院", events[0].title)
        self.assertEqual(events[0].source, "rmfygg")

    def test_page_structure_change_raises(self):
        session = _FakeSession(FIXTURE_HTML, verdict="NO_BUTTON")
        with mock.patch.object(cdp, "public_session", lambda timeout=25.0: session):
            with self.assertRaises(RuntimeError) as ctx:
                rm.RmfyggProvider({}).fetch(Enterprise(name="武汉鑫泰商业管理有限公司"))
        self.assertIn("页面结构变化", str(ctx.exception))

    def test_repeat_call_reuses_memo(self):
        session = _FakeSession(FIXTURE_HTML)
        calls = []

        def fake_session(timeout=25.0):
            calls.append(1)
            return session

        provider = rm.RmfyggProvider({})
        ent = Enterprise(name="武汉鑫泰商业管理有限公司")
        with mock.patch.object(cdp, "public_session", fake_session):
            first = provider.fetch(ent)
            second = provider.fetch(ent)
        self.assertEqual(len(calls), 1, "同类目重复调用不应重复开页面")
        self.assertEqual(len(first), len(second))

    def test_no_match_returns_empty(self):
        session = _FakeSession(FIXTURE_HTML)
        with mock.patch.object(cdp, "public_session", lambda timeout=25.0: session):
            events = rm.RmfyggProvider({}).fetch(Enterprise(name="北京某某科技有限公司"))
        self.assertEqual(events, [])


class BackendConfigTest(unittest.TestCase):
    """浏览器后端选择：默认 auto，配置非法值回退 auto。"""

    def test_default_is_auto(self):
        with mock.patch.object(cdp, "browser_conf", lambda: {}):
            self.assertEqual(cdp.configured_backend(), cdp.BACKEND_AUTO)

    def test_illegal_value_falls_back(self):
        with mock.patch.object(cdp, "browser_conf", lambda: {"backend": "firefox"}):
            self.assertEqual(cdp.configured_backend(), cdp.BACKEND_AUTO)

    def test_obscura_path_found_in_project(self):
        path = cdp.obscura_path()
        self.assertTrue(path, "项目应自带 tools/obscura/obscura.exe")
        self.assertTrue(os.path.exists(path))

    def test_launch_obscura_missing_binary(self):
        with mock.patch.object(cdp, "obscura_path", lambda: ""):
            ok, msg = cdp.launch_obscura(9223)
        self.assertFalse(ok)
        self.assertIn("obscura", msg)

    def test_ensure_backend_chrome_only_raises_when_closed(self):
        conf = {"backend": "chrome", "chrome_port": 9222}
        with mock.patch.object(cdp, "browser_conf", lambda: conf), \
                mock.patch.object(cdp, "is_debug_port_open", lambda *a, **k: False):
            with self.assertRaises(RuntimeError) as ctx:
                cdp.ensure_backend()
        self.assertIn("Chrome", str(ctx.exception))

    def test_ensure_backend_falls_back_to_running_chrome(self):
        conf = {"backend": "auto", "obscura_port": 9223, "chrome_port": 9222}
        with mock.patch.object(cdp, "browser_conf", lambda: conf), \
                mock.patch.object(cdp, "obscura_path", lambda: ""), \
                mock.patch.object(cdp, "is_debug_port_open",
                                  lambda port, timeout=1.0: port == 9222):
            self.assertEqual(cdp.ensure_backend(), 9222)

    def test_backend_status_shape(self):
        st = cdp.backend_status()
        for key in ("backend", "backend_label", "obscura_available",
                    "obscura_port", "chrome_port"):
            self.assertIn(key, st)

    def test_download_obscura_short_circuits_when_present(self):
        with mock.patch.object(cdp, "obscura_path", lambda: r"C:\x\obscura.exe"):
            ok, msg = cdp.download_obscura()
        self.assertTrue(ok)
        self.assertIn("已就绪", msg)

    def test_download_obscura_respects_opt_out(self):
        with mock.patch.object(cdp, "obscura_path", lambda: ""), \
                mock.patch.dict(os.environ, {"OBSCURA_DOWNLOAD_DISABLED": "1"}):
            ok, msg = cdp.download_obscura()
        self.assertFalse(ok)
        self.assertIn("禁用", msg)

    def test_ensure_obscura_installed_passthrough(self):
        with mock.patch.object(cdp, "obscura_path", lambda: r"C:\x\obscura.exe"):
            ok, msg = cdp.ensure_obscura_installed()
        self.assertTrue(ok)
        self.assertEqual(msg, r"C:\x\obscura.exe")


class BackendGuiTest(unittest.TestCase):
    """设置中心的「公开站点渲染后端」配置区。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_widget_exposes_backend_selector(self):
        from src.gui.social_auth_page import SocialAuthWidget
        w = SocialAuthWidget()
        try:
            self.assertEqual(w.backend_combo.count(), 3)
            self.assertEqual(w.backend_combo.currentData(), cdp.configured_backend())
            self.assertIn("Obscura", w.backend_status.text())
        finally:
            w.deleteLater()

    def test_save_backend_writes_into_config(self):
        from PySide6.QtWidgets import QMessageBox
        from src.core import settings
        from src.gui import social_auth_page as sap

        settings.config()  # 先加载真实配置，避免 patch.dict 让 _config 非空而跳过加载
        with mock.patch.object(settings, "save_config", lambda: None), \
                mock.patch.dict(settings._config, {"browser": {}}), \
                mock.patch.object(QMessageBox, "information", lambda *a, **k: None):
            w = sap.SocialAuthWidget()
            try:
                w.backend_combo.setCurrentIndex(
                    w.backend_combo.findData(cdp.BACKEND_CHROME))
                w._backend_save()
                self.assertEqual(settings._config["browser"]["backend"],
                                 cdp.BACKEND_CHROME)
            finally:
                w.deleteLater()


if __name__ == "__main__":
    unittest.main()
