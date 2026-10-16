"""QA 独立验收测试的共享夹具（独立于 tests/，不污染产品既有用例）。

- 统一把项目根与 src 加入 sys.path；
- 统一强制 ``QT_QPA_PLATFORM=offscreen``（所有 GUI 用例无头）；
- 统一把 SQLite 重定向到临时文件，绝不写项目 data/。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
