"""启动自举单元测试：配置文件生成、.env 保护、_load 兜底与重建。

运行：python -m unittest tests.test_boot -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import settings  # noqa: E402


def _tmp_root() -> str:
    """创建临时目录，模拟打包后 exe 所在目录（app_root）。"""
    return tempfile.mkdtemp(prefix="brm_boot_test_")


class EnsureBootFilesTest(unittest.TestCase):
    """ensure_boot_files()：三文件缺失时全部生成；已存在不覆盖。"""

    def test_generate_all_when_missing(self):
        root = _tmp_root()
        created = settings.ensure_boot_files(target_dir=root)
        self.assertEqual(sorted(created), [".env", "config.yaml", "model_policy.yaml"])
        with open(os.path.join(root, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        with open(os.path.join(root, "model_policy.yaml"), encoding="utf-8") as f:
            pol = yaml.safe_load(f)
        with open(os.path.join(root, ".env"), encoding="utf-8") as f:
            env_text = f.read()
        # config.yaml 含默认数据源字段（sources 四类）
        for key in ("news", "biz_change", "lawsuit", "enforcement"):
            self.assertIn(key, cfg["sources"])
        # model_policy.yaml 含默认 fallback_models
        self.assertTrue(pol["fallback_models"])
        # .env 为空模板：仅键名占位，值非真实 key
        self.assertEqual(env_text, "OPENROUTER_API_KEY=\n")
        # 重复调用：文件已存在，不再生成任何文件
        self.assertEqual(settings.ensure_boot_files(target_dir=root), [])

    def test_env_not_overwritten(self):
        root = _tmp_root()
        marker = "OPENROUTER_API_KEY=\nUSER_MARKER=keep_me\n"
        with open(os.path.join(root, ".env"), "w", encoding="utf-8") as f:
            f.write(marker)
        created = settings.ensure_boot_files(target_dir=root)
        self.assertNotIn(".env", created)
        with open(os.path.join(root, ".env"), encoding="utf-8") as f:
            self.assertEqual(f.read(), marker)  # 内容一字不变（不覆盖、不追加）

    def test_write_failure_returns_partial(self):
        # 目标目录参数指向一个已存在的普通文件：写入必然失败，
        # 应捕获异常并返回空列表，绝不抛出（启动不崩溃）
        root = _tmp_root()
        blocker = os.path.join(root, "not_a_dir")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")
        self.assertEqual(settings.ensure_boot_files(target_dir=blocker), [])


class LoadFallbackTest(unittest.TestCase):
    """_load 兜底：文件缺失/损坏时回退内置默认内容，并尽力重建文件。"""

    def setUp(self):
        self.root = _tmp_root()
        settings.ensure_boot_files(target_dir=self.root)
        # 把 settings 使用的 app_root 指向临时目录，不污染真实项目根
        p = mock.patch.object(settings, "app_root", return_value=self.root)
        p.start()
        self.addCleanup(p.stop)
        settings.reload()

    def tearDown(self):
        settings.reload()  # 清缓存，避免污染其他用例

    def test_config_missing_fallback_and_regenerate(self):
        os.rename(os.path.join(self.root, "config.yaml"),
                  os.path.join(self.root, "config.yaml.bak"))
        cfg = settings.config()  # 不应抛 FileNotFoundError
        self.assertEqual(cfg, yaml.safe_load(settings.DEFAULT_CONFIG_YAML))
        self.assertIn("sources", cfg)
        # 缺失的 config.yaml 被兜底逻辑重新生成
        self.assertTrue(os.path.exists(os.path.join(self.root, "config.yaml")))

    def test_policy_missing_fallback(self):
        os.rename(os.path.join(self.root, "model_policy.yaml"),
                  os.path.join(self.root, "model_policy.yaml.bak"))
        pol = settings.model_policy()  # 不应抛 FileNotFoundError
        self.assertEqual(pol, yaml.safe_load(settings.DEFAULT_MODEL_POLICY_YAML))
        self.assertIn("fallback_models", pol)
        self.assertTrue(os.path.exists(os.path.join(self.root, "model_policy.yaml")))

    def test_config_corrupted_fallback(self):
        with open(os.path.join(self.root, "config.yaml"), "w", encoding="utf-8") as f:
            f.write("{invalid: [unclosed")  # 非法 YAML
        cfg = settings.config()  # 不应抛 yaml.YAMLError
        self.assertEqual(cfg, yaml.safe_load(settings.DEFAULT_CONFIG_YAML))


if __name__ == "__main__":
    unittest.main()
