"""连接器代查补充通路（ARCH-v2-increment §1.6.3，冻结契约）—— 命令行入口。

主 Agent 经 MCP 工商连接器（tyc-mcp / qcc-company / qixinhuiyan-mcp / shuidi-credit）
代查企业档案 → 落成批次 JSON → 本脚本归一为 ``Event`` 幂等入库（``monitor.db``）。

核心逻辑已抽取到 ``src/core/connector_import.py``（GUI「代查结果导入」与本 CLI
共用同一套解析、schema 校验与入库规则），本文件只保留命令行薄封装，
并保留旧函数名（``load`` / ``build_events`` / ``run`` / ``_norm_date`` 等）以兼容
既有测试与外部调用。

用法::

    python tools/connector_ingest.py --file batch.json [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.core.connector_import import (  # noqa: E402,F401
    SCHEMA_VERSION,
    UNKNOWN_CONNECTOR,
    VALID_CONNECTORS,
    CompanyProfile,
    ConnectorProfile,
    build_events,
    load,
    map_connector,
    match_enterprise,
    norm_date,
    occurred_at_of,
    run,
    write_log,
)

# 旧私有别名（兼容既有测试）
_norm_date = norm_date
_occurred_at = occurred_at_of
_map_connector = map_connector
_match_enterprise = match_enterprise
_write_log = write_log


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="连接器代查档案 → Event 幂等入库")
    parser.add_argument("--file", required=True, help="批次 JSON 路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落库")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"[FAIL] 文件不存在：{args.file}")
        return 2
    result = run(args.file, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
