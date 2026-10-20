"""OpenCLI 后端 Provider 测试（全部离线，mock 子进程调用）。"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Enterprise  # noqa: E402
from src.providers import opencli_base as ob  # noqa: E402
from src.providers.opencli_base import OpenCliProvider  # noqa: E402

ENT = Enterprise(id=1, name="辽宁沈抚示范区鑫泰房地产开发有限公司")


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["opencli"], returncode=returncode, stdout=stdout, stderr=stderr)


class RowExtractionTest(unittest.TestCase):
    """opencli 的 data 字段可能有多种外层包装。"""

    def test_rows_key(self):
        rows = ob._extract_rows({"rows": [{"title": "a"}]})
        self.assertEqual(rows, [{"title": "a"}])

    def test_items_key(self):
        self.assertEqual(len(ob._extract_rows({"items": [{"title": "a"}]})), 1)

    def test_nested_data_key(self):
        self.assertEqual(len(ob._extract_rows({"data": {"notes": [{"title": "a"}]}})), 1)

    def test_plain_list(self):
        self.assertEqual(len(ob._extract_rows([{"title": "a"}])), 1)

    def test_single_object(self):
        self.assertEqual(len(ob._extract_rows({"title": "a", "url": "u"})), 1)

    def test_empty(self):
        self.assertEqual(ob._extract_rows(None), [])
        self.assertEqual(ob._extract_rows({}), [])


class FieldPickTest(unittest.TestCase):
    def test_first_non_empty(self):
        self.assertEqual(ob._pick_field({"a": "", "b": "x"}, ("a", "b")), "x")

    def test_missing(self):
        self.assertEqual(ob._pick_field({}, ("a",)), "")

    def test_list_joined(self):
        self.assertEqual(ob._pick_field({"tags": ["a", "b"]}, ("tags",)), "a b")


class DateNormTest(unittest.TestCase):
    def test_iso_passthrough(self):
        self.assertEqual(ob._norm_date("2026-09-20 10:00:00"), "2026-09-20")

    def test_unix_seconds(self):
        self.assertEqual(len(ob._norm_date(1785000000)), 10)

    def test_unix_millis(self):
        self.assertEqual(len(ob._norm_date(1785000000000)), 10)

    def test_relative_returns_empty(self):
        self.assertEqual(ob._norm_date("3小时前"), "")

    def test_none(self):
        self.assertEqual(ob._norm_date(None), "")


class SeverityTest(unittest.TestCase):
    def test_high(self):
        self.assertEqual(ob.classify_severity("某公司被执行 100万"), "high")

    def test_medium(self):
        self.assertEqual(ob.classify_severity("某公司被起诉"), "medium")

    def test_low(self):
        self.assertEqual(ob.classify_severity("某公司发布年度报告"), "low")


class CliProbeTest(unittest.TestCase):
    def test_cli_found_on_path(self):
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value="/usr/bin/opencli"):
            self.assertTrue(p.cli_available())

    def test_cli_missing(self):
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value=None):
            self.assertFalse(p.cli_available())

    def test_custom_cli_path(self):
        with mock.patch.object(ob.os.path, "exists", return_value=True):
            p = OpenCliProvider({"cli_path": "C:/x/opencli.exe"})
            self.assertEqual(p.cli_path, "C:/x/opencli.exe")


class DoctorTest(unittest.TestCase):
    def test_doctor_reports_not_installed(self):
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value=None):
            d = p.doctor()
        self.assertFalse(d["installed"])
        self.assertFalse(d["ok"])
        self.assertTrue(any("npm install" in i for i in d["issues"]))

    def test_doctor_parses_payload(self):
        payload = {"ok": True, "data": {
            "extensionConnected": True, "issues": [], "cliVersion": "2.0.1"}}
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value="opencli"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            d = p.doctor()
        self.assertTrue(d["installed"])
        self.assertTrue(d["extension_connected"])
        self.assertTrue(d["ok"])

    def test_doctor_detects_disconnected(self):
        payload = {"ok": True, "data": {
            "extensionConnected": False, "issues": ["extension not connected"]}}
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value="opencli"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            d = p.doctor()
        self.assertFalse(d["extension_connected"])
        self.assertFalse(d["ok"])
        self.assertTrue(d["issues"])

    def test_doctor_handles_bad_json(self):
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value="opencli"), \
                mock.patch.object(subprocess, "run", return_value=_proc("not json")):
            d = p.doctor()
        self.assertFalse(d["ok"])
        self.assertTrue(d["issues"])


class ReadyGateTest(unittest.TestCase):
    def test_missing_cli_raises_auth_missing(self):
        from src.providers.base import SocialAuthMissing
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value=None):
            with self.assertRaises(SocialAuthMissing):
                p.ensure_ready()

    def test_disconnected_extension_raises(self):
        from src.providers.base import SocialAuthMissing
        p = OpenCliProvider({})
        with mock.patch.object(ob.shutil, "which", return_value="opencli"), \
                mock.patch.object(OpenCliProvider, "doctor",
                                  return_value={"ok": False, "installed": True,
                                                "extension_connected": False,
                                                "issues": [], "raw": {}}):
            with self.assertRaises(SocialAuthMissing) as ctx:
                p.ensure_ready()
        self.assertIn("Browser Bridge", str(ctx.exception))


class SearchRunTest(unittest.TestCase):
    def _provider(self, platforms=("weibo",), **kw):
        params = {"platforms": list(platforms)}
        params.update(kw)
        p = OpenCliProvider(params)
        p._cli = "opencli"
        p._checked = True
        return p

    def test_success_returns_events(self):
        payload = {"ok": True, "data": {"rows": [
            {"rank": 1, "id": "1", "title": "鑫泰房地产被执行", "author": "财经",
             "time": "2026-09-20", "url": "https://weibo.com/1"}]}}
        p = self._provider()
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.source, "opencli_weibo")
        self.assertEqual(ev.category, "social")
        self.assertEqual(ev.title, "鑫泰房地产被执行")
        self.assertEqual(ev.occurred_at, "2026-09-20")
        self.assertEqual(ev.severity, "high")
        self.assertIn("财经", ev.summary)
        self.assertEqual(ev.url, "https://weibo.com/1")

    def test_command_shape(self):
        payload = {"ok": True, "data": {"rows": []}}
        p = self._provider(platforms=("weibo",), limit=5, timeout_seconds=30)
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))) as run:
            p.fetch(ENT)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:4], ["opencli", "weibo", "search", ENT.name])
        self.assertIn("--limit", cmd)
        self.assertIn("-f", cmd)
        self.assertIn("json", cmd)

    def test_platform_failure_isolated(self):
        """一个平台失败不影响其它平台。"""
        good = {"ok": True, "data": {"rows": [
            {"title": "某平台的结果", "url": "u", "author": "a"}]}}

        def fake_run(cmd, **kw):
            if cmd[1] == "weibo":
                return _proc("", returncode=69, stderr="bridge down")
            return _proc(json.dumps(good))

        p = self._provider(platforms=("weibo", "zhihu"))
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run", side_effect=fake_run):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "opencli_zhihu")

    def test_all_platforms_fail_raises(self):
        p = self._provider(platforms=("weibo",))
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc("", returncode=69)):
            with self.assertRaises(RuntimeError):
                p.fetch(ENT)

    def test_error_envelope_becomes_exception(self):
        payload = {"ok": False, "error": {
            "code": "BROWSER_CONNECT", "message": "not connected",
            "hint": "install extension"}}
        p = self._provider()
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            with self.assertRaises(RuntimeError) as ctx:
                p.fetch(ENT)
        self.assertIn("Browser Bridge", str(ctx.exception))

    def test_empty_stdout_raises(self):
        p = self._provider()
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run", return_value=_proc("", returncode=1)):
            with self.assertRaises(RuntimeError):
                p.fetch(ENT)

    def test_timeout_raises(self):
        p = self._provider()
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("opencli", 90)):
            with self.assertRaises(RuntimeError) as ctx:
                p.fetch(ENT)
        self.assertIn("超时", str(ctx.exception))

    def test_xiaohongshu_field_mapping(self):
        payload = {"ok": True, "data": {"rows": [
            {"rank": 1, "title": "某楼盘笔记", "author": "小红薯",
             "likes": "12", "published_at": "2026-09-19", "url": "https://xhs/1"}]}}
        p = self._provider(platforms=("xiaohongshu",))
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            events = p.fetch(ENT)
        self.assertEqual(events[0].source, "opencli_xiaohongshu")
        self.assertEqual(events[0].occurred_at, "2026-09-19")

    def test_weixin_field_mapping(self):
        payload = {"ok": True, "data": {"rows": [
            {"rank": 1, "page": 1, "title": "公众号文章", "url": "https://mp/1",
             "summary": "摘要内容", "publish_time": "2026-09-18"}]}}
        p = self._provider(platforms=("weixin",))
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            events = p.fetch(ENT)
        self.assertEqual(events[0].source, "opencli_weixin")
        self.assertIn("摘要内容", events[0].summary)

    def test_douyin_desc_field(self):
        payload = {"ok": True, "data": {"rows": [
            {"rank": 1, "desc": "某企业宣传片", "author": "某公司",
             "url": "https://v.douyin/1", "plays": "1000"}]}}
        p = self._provider(platforms=("douyin",))
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            events = p.fetch(ENT)
        self.assertEqual(events[0].source, "opencli_douyin")
        self.assertEqual(events[0].title, "某企业宣传片")

    def test_row_without_title_skipped(self):
        payload = {"ok": True, "data": {"rows": [{"author": "x"}, {"title": "ok"}]}}
        p = self._provider()
        with mock.patch.object(p, "ensure_ready"), \
                mock.patch.object(subprocess, "run",
                                  return_value=_proc(json.dumps(payload))):
            events = p.fetch(ENT)
        self.assertEqual(len(events), 1)


class ConfigTest(unittest.TestCase):
    def test_config_declares_opencli_source(self):
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        specs = cfg.get("sources", {}).get("social", [])
        names = [s.get("name") for s in specs]
        self.assertIn("opencli", names)
        spec = next(s for s in specs if s.get("name") == "opencli")
        self.assertFalse(spec.get("enabled"), "opencli 后端默认应关闭（需显式启用）")
        self.assertIn("weibo", spec["params"]["platforms"])
        self.assertEqual(spec["class"], "providers.opencli_base.OpenCliProvider")

    def test_provider_loadable(self):
        from src.providers import base as pb
        from src.core import settings as st
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        orig = st._config
        try:
            # 用独立副本，避免污染其它测试的全局配置
            st._config = cfg
            # opencli 默认关闭 → 不在 load_providers 结果里
            names = [p.name for p in pb.load_providers("social")]
            self.assertNotIn("opencli", names)
            # 手动启用后应能加载
            for s in st._config["sources"]["social"]:
                if s.get("name") == "opencli":
                    s["enabled"] = True
            names = [p.name for p in pb.load_providers("social")]
            self.assertIn("opencli", names)
        finally:
            st._config = orig


class ExtensionTest(unittest.TestCase):
    """Browser Bridge 扩展：按需下载 + 解压。"""

    def test_extension_dir_under_tools(self):
        d = ob.extension_dir()
        self.assertTrue(d.replace("\\", "/").endswith("tools/opencli_ext"))

    def test_extension_installed_detects_manifest(self):
        with mock.patch.object(ob.os.path, "isfile", return_value=True):
            self.assertTrue(ob.extension_installed())
        with mock.patch.object(ob.os.path, "isfile", return_value=False):
            self.assertFalse(ob.extension_installed())

    def test_download_failure_returns_error(self):
        import urllib.request
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=OSError("network down")):
            ok, msg = ob.download_extension()
        self.assertFalse(ok)
        self.assertIn("失败", msg)

    def test_download_success_extracts_zip(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", '{"name": "OpenCLI"}')
        payload = buf.getvalue()
        release = {"assets": [{"name": "opencli-extension-v1.zip",
                               "browser_download_url": "https://x/y.zip"}]}

        class _Resp:
            def __init__(self, data):
                self._d = data

            def read(self):
                return self._d

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        tmpdir = os.path.join(os.environ.get("TEMP", "."), "_oclitest")
        os.makedirs(tmpdir, exist_ok=True)
        ext = os.path.join(tmpdir, "ext")
        with mock.patch.object(ob, "extension_dir", return_value=ext), \
                mock.patch("urllib.request.urlopen",
                           side_effect=[_Resp(json.dumps(release).encode()),
                                        _Resp(payload)]):
            ok, msg = ob.download_extension()
        self.assertTrue(ok, msg)
        self.assertIn("加载已解压的扩展程序", msg)
        # 扩展应已解压出 manifest.json
        self.assertTrue(os.path.isfile(os.path.join(ext, "manifest.json")))


class PlatformCoverageTest(unittest.TestCase):
    """字段映射表应覆盖配置里启用的平台。"""

    def test_default_platforms_have_field_map(self):
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        spec = next(s for s in cfg["sources"]["social"] if s["name"] == "opencli")
        for p in spec["params"]["platforms"]:
            self.assertIn(p, ob.FIELD_MAP, f"平台 {p} 缺少字段映射")

    def test_all_field_map_platforms_labeled(self):
        for p in ob.FIELD_MAP:
            self.assertIn(p, ob.PLATFORM_LABELS, f"平台 {p} 缺少中文名")


if __name__ == "__main__":
    unittest.main()
