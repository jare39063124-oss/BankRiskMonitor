"""股权穿透引擎：向上查股东、向下查对外投资，逐层递归发现关联主体。

结果只返回未入库的新主体（Enterprise 草稿，is_related=1），
由调用方勾选后调用 save_related 入库；parent_id 统一指向被穿透的主企业，
与 db.delete_enterprise 的"停主停关联"语义保持一致。
"""
from . import db
from .models import Enterprise

try:
    from ..providers.biz_aiqicha import fetch_shareholders, fetch_investments
except ImportError:
    from src.providers.biz_aiqicha import fetch_shareholders, fetch_investments


def _fmt_ratio(v) -> str:
    """把比例值格式化为"60%"样式的字符串，空值返回空串。"""
    if v in (None, "", "-"):
        return ""
    if isinstance(v, (int, float)):
        return f"{round(float(v), 2):g}%"
    s = str(v).strip()
    return s if s.endswith("%") else f"{s}%"


def _exists(name: str, credit_code: str = "") -> bool:
    with db.connect() as conn:
        r = conn.execute("SELECT id FROM enterprise WHERE name=? AND credit_code=?",
                         (name, credit_code)).fetchone()
    return r is not None


def _safe_fetch(fn, name: str) -> list:
    try:
        return fn(name) or []
    except Exception:  # 单节点查询失败（网络/反爬）不阻断整体穿透
        return []


def penetrate(ent: Enterprise, up_levels: int = 2, down_levels: int = 2) -> list:
    """按层级穿透：返回未入库的新关联主体列表（按 name+credit_code 去重）。"""
    if not ent or not ent.name:
        return []
    results = []
    seen = {(ent.name, ent.credit_code)}
    for direction, max_levels in (("up", up_levels), ("down", down_levels)):
        fn = fetch_shareholders if direction == "up" else fetch_investments
        label = "股东" if direction == "up" else "对外投资"
        frontier = [ent.name]
        level = 0
        while frontier and level < max_levels:
            next_frontier = []
            for parent_name in frontier:
                for item in _safe_fetch(fn, parent_name):
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    code = str(item.get("credit_code") or "").strip()
                    key = (name, code)
                    if key in seen or _exists(name, code):
                        continue  # 同一主体去重 + 跳过已入库主体
                    seen.add(key)
                    results.append(Enterprise(
                        name=name, credit_code=code, is_related=1,
                        parent_id=ent.id,
                        relation=f"{label}({_fmt_ratio(item.get('ratio'))})",
                        pen_level=level + 1, direction=direction))
                    next_frontier.append(name)
            frontier = next_frontier
            level += 1
    return results


def save_related(base_ent_id: int, related_list: list, selected_names) -> list:
    """把勾选的关联主体入库（upsert + 更新穿透字段），返回入库后的企业 id 列表。"""
    selected = set(selected_names)
    saved = []
    for e in related_list:
        if e.name not in selected:
            continue
        ent_id = db.upsert_enterprise(e.name, e.credit_code)
        db.update_enterprise(ent_id, is_related=e.is_related, parent_id=base_ent_id,
                             relation=e.relation, pen_level=e.pen_level,
                             direction=e.direction)
        saved.append(ent_id)
    return saved
