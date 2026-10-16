"""QA 独立回归 —— 真实库 data/monitor.db 端到端 classify 双路径复核。

对已设放款日的 3 家企业（鑫泰 id19 / 民泰 id16 / 开投 id15）分别：
  路径 A：baseline = db.get_baseline(id) or build_baseline(ent)
  路径 B：baseline = None（故意）
逐个事件跑 classify()，比较两路径的 bucket 分布并核对预期。

只读，不写库。
"""
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.core import db
from src.core import baseline_compare as bc

TARGETS = [(19, "鑫泰", "2026-09-29"),
           (16, "民泰", "2026-09-29"),
           (15, "开投", "2026-10-10")]


def main():
    print(f"db_path = {db.db_path()}")
    for eid, label, exp_loan in TARGETS:
        ent = db.get_enterprise(eid)
        if ent is None:
            print(f"[{label} id={eid}] 企业不存在！")
            continue
        stored = db.get_baseline(eid)
        built = bc.build_baseline(ent)
        baseA = stored or built
        events = db.list_events(enterprise_id=eid, limit=5000) or []

        distA, distB = Counter(), Counter()
        mismatches = []
        for e in events:
            vA = bc.classify(e, ent, baseA)
            vB = bc.classify(e, ent, None)
            distA[(vA.bucket, vA.subtype)] += 1
            distB[(vB.bucket, vB.subtype)] += 1
            if vA.bucket != vB.bucket:
                mismatches.append((e.id, e.occurred_at, e.title[:30],
                                   vA.bucket, vB.bucket))

        print("=" * 78)
        print(f"[{label} id={eid}] loan_date(期望 {exp_loan}) = {ent.loan_date!r}")
        print(f"  stored baseline 存在 = {stored is not None} | build_baseline = {built is not None}"
              f" | 路径A 基线来源 = {getattr(baseA, 'source', None)}")
        print(f"  事件总数 = {len(events)}")
        print(f"  路径A (get_baseline or build) bucket分布: {dict(distA)}")
        print(f"  路径B (None 基线)           bucket分布: {dict(distB)}")
        print(f"  两路径 bucket 分布一致 = {distA == distB}")
        if mismatches:
            print(f"  差异明细（A bucket != B bucket）共 {len(mismatches)} 条：")
            for mid, occ, title, a, b in mismatches[:20]:
                print(f"    - ev#{mid} {occ!r} {title!r}: A={a} B={b}")
        # 逐条事件明细（便于人工核对是否符合预期）
        print("  -- 事件逐条 --")
        for e in events:
            vA = bc.classify(e, ent, baseA)
            vB = bc.classify(e, ent, None)
            print(f"    ev#{e.id} {str(e.occurred_at)[:10]:<10} [{e.category:<11}] "
                  f"A={vA.bucket:<12} B={vB.bucket:<12} | {e.title[:34]}")


if __name__ == "__main__":
    main()
