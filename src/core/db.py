"""SQLite 存储层（契约）：建表与通用 CRUD。GUI/Provider/AI 层均通过本模块读写。"""
import json
import sqlite3
from datetime import datetime
from typing import List, Optional

from .models import Enterprise, Event, CreditMaterial, BaselineSnapshot
from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS enterprise (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    credit_code TEXT DEFAULT '',
    province TEXT DEFAULT '',
    city TEXT DEFAULT '',
    credit_note TEXT DEFAULT '',
    monitored INTEGER DEFAULT 1,
    is_related INTEGER DEFAULT 0,
    parent_id INTEGER,
    relation TEXT DEFAULT '',
    pen_level INTEGER DEFAULT 0,
    direction TEXT DEFAULT '',
    loan_date TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    url TEXT DEFAULT '',
    source TEXT DEFAULT '',
    occurred_at TEXT DEFAULT '',
    severity TEXT DEFAULT 'low',
    sentiment TEXT DEFAULT 'neutral',
    relevance TEXT DEFAULT 'low',
    ai_verdict TEXT DEFAULT '',
    engine TEXT DEFAULT '',
    pending_review INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(enterprise_id, category, title, source)
);
CREATE TABLE IF NOT EXISTS credit_material (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id INTEGER NOT NULL,
    filename TEXT DEFAULT '',
    path TEXT DEFAULT '',
    text TEXT DEFAULT '',
    uploaded_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS ai_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS baseline_snapshot (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id      INTEGER NOT NULL UNIQUE,
    loan_date          TEXT    DEFAULT '',
    legal_person       TEXT,                 -- 法定代表人
    reg_capital        TEXT,                 -- 注册资本（原文，含单位）
    shareholders       TEXT,                 -- 股东与股权结构（JSON 数组或分隔串）
    business_scope     TEXT,                 -- 经营范围摘要/指纹
    business_status    TEXT,                 -- 经营状态（存续/在业/注销/吊销…）
    abnormal_flag      INTEGER,              -- 1=基线时点已列入经营异常名录；NULL=该维缺失
    penalty_count      INTEGER,              -- 行政处罚基线条数
    lawsuit_count      INTEGER,              -- 涉诉基线条数
    enforcement_status TEXT,                 -- 被执行/失信基线状态
    tax_status         TEXT,                 -- 税务状态（欠税/非正常户/重大税收违法）
    risk_tags          TEXT,                 -- 风险标签集合（JSON 数组）
    source             TEXT    DEFAULT '',   -- auto|manual|history|mixed
    dim_sources        TEXT    DEFAULT '{}', -- JSON：{dim_key: 来源}，记录优先级降级链
    note               TEXT    DEFAULT '',
    built_at           TEXT    DEFAULT (datetime('now','localtime')),
    updated_at         TEXT    DEFAULT (datetime('now','localtime'))
);
"""

# baseline_snapshot 可写列（除自增 id 外的全部业务列，供 upsert 白名单校验）
_BASELINE_COLUMNS = (
    "enterprise_id", "loan_date", "legal_person", "reg_capital", "shareholders",
    "business_scope", "business_status", "abnormal_flag", "penalty_count",
    "lawsuit_count", "enforcement_status", "tax_status", "risk_tags",
    "source", "dim_sources", "note", "built_at", "updated_at",
)

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """查缺列后幂等 ``ALTER TABLE ... ADD COLUMN``；老库零破坏。返回是否新增。"""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        return True
    return False

def init_db():
    """建库/升级：新库直接建全（含 ``loan_date`` + ``baseline_snapshot``）；老库幂等补列。"""
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "enterprise", "loan_date", "loan_date TEXT DEFAULT ''")

# ---------- Enterprise ----------
def upsert_enterprise(name: str, credit_code: str = "", province: str = "", city: str = "",
                      credit_note: str = "", monitored: int = 1, loan_date: str = "") -> int:
    """按 (name, credit_code) 幂等插入/更新企业。

    ``loan_date`` 为 v2 新增可选关键字参数（默认 ``""``，向后兼容既有调用方）。
    更新既有记录时**仅当传入非空**才覆盖放款日，避免普通同步/导入把已设放款日清空。
    """
    with connect() as conn:
        cur = conn.execute(
            "SELECT id FROM enterprise WHERE name=? AND credit_code=?", (name, credit_code))
        row = cur.fetchone()
        if row:
            set_sql = "province=?, city=?, credit_note=?, monitored=?"
            args = [province, city, credit_note, monitored]
            if loan_date:
                set_sql += ", loan_date=?"
                args.append(loan_date)
            args.append(row["id"])
            conn.execute(f"UPDATE enterprise SET {set_sql} WHERE id=?", args)
            return row["id"]
        cur = conn.execute(
            "INSERT INTO enterprise(name,credit_code,province,city,credit_note,monitored,loan_date) "
            "VALUES(?,?,?,?,?,?,?)",
            (name, credit_code, province, city, credit_note, monitored, loan_date))
        return cur.lastrowid

def update_enterprise(ent_id: int, **fields):
    keys = ",".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE enterprise SET {keys} WHERE id=?", (*fields.values(), ent_id))

def delete_enterprise(ent_id: int):
    """软删除：停止监控，历史事件保留可查。"""
    with connect() as conn:
        conn.execute("UPDATE enterprise SET monitored=0 WHERE id=? OR parent_id=?", (ent_id, ent_id))

def list_enterprises(include_stopped: bool = False) -> List[Enterprise]:
    sql = "SELECT * FROM enterprise"
    if not include_stopped:
        sql += " WHERE monitored=1"
    sql += " ORDER BY pen_level, id"
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [Enterprise(**{k: r[k] for k in r.keys()}) for r in rows]

def get_enterprise(ent_id: int) -> Optional[Enterprise]:
    with connect() as conn:
        r = conn.execute("SELECT * FROM enterprise WHERE id=?", (ent_id,)).fetchone()
    return Enterprise(**{k: r[k] for k in r.keys()}) if r else None

# ---------- BaselineSnapshot（v2：放款前基线，一企业一行） ----------
def get_baseline(ent_id: int) -> Optional[BaselineSnapshot]:
    """按企业 id 取基线快照；不存在返回 ``None``。"""
    with connect() as conn:
        r = conn.execute("SELECT * FROM baseline_snapshot WHERE enterprise_id=?",
                         (ent_id,)).fetchone()
    return BaselineSnapshot(**{k: r[k] for k in r.keys()}) if r else None


def upsert_baseline(ent_id: int, **fields) -> int:
    """按 ``enterprise_id`` 幂等写入基线快照，返回该行 id。

    仅接受 ``_BASELINE_COLUMNS`` 白名单内字段（未知字段静默忽略）；
    ``built_at`` 首次写入即固化，重复写入仅刷新 ``updated_at``。
    """
    allowed = set(_BASELINE_COLUMNS)
    payload = {k: v for k, v in fields.items()
               if k in allowed and k not in ("enterprise_id", "updated_at")}
    payload["enterprise_id"] = ent_id
    cols = ["enterprise_id"] + [c for c in payload if c != "enterprise_id"]
    vals = [payload[c] for c in cols]
    placeholders = ",".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols
                        if c not in ("enterprise_id", "built_at"))
    updates += ", updated_at=datetime('now','localtime')"
    sql = (f"INSERT INTO baseline_snapshot({','.join(cols)}) VALUES({placeholders}) "
           f"ON CONFLICT(enterprise_id) DO UPDATE SET {updates}")
    with connect() as conn:
        conn.execute(sql, vals)
        row = conn.execute("SELECT id FROM baseline_snapshot WHERE enterprise_id=?",
                           (ent_id,)).fetchone()
    return row["id"] if row else 0


def list_baselines() -> List[BaselineSnapshot]:
    """列出全部基线快照（按企业 id 升序）。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM baseline_snapshot ORDER BY enterprise_id").fetchall()
    return [BaselineSnapshot(**{k: r[k] for k in r.keys()}) for r in rows]


def delete_baseline(ent_id: int) -> int:
    """删除指定企业的基线快照，返回受影响行数。"""
    with connect() as conn:
        cur = conn.execute("DELETE FROM baseline_snapshot WHERE enterprise_id=?", (ent_id,))
        return cur.rowcount

# ---------- Event ----------
def insert_event(ev: Event) -> bool:
    """去重插入：命中唯一约束则返回 False。"""
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO event(enterprise_id,category,title,summary,url,source,occurred_at,"
                "severity,sentiment,relevance,ai_verdict,engine,pending_review) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.enterprise_id, ev.category, ev.title, ev.summary, ev.url, ev.source,
                 ev.occurred_at, ev.severity, ev.sentiment, ev.relevance,
                 ev.ai_verdict, ev.engine, ev.pending_review))
        return True
    except sqlite3.IntegrityError:
        return False

def update_event_verdict(event_id: int, sentiment: str, severity: str, relevance: str,
                         verdict: str, engine: str, pending_review: int = 0):
    with connect() as conn:
        conn.execute(
            "UPDATE event SET sentiment=?,severity=?,relevance=?,ai_verdict=?,engine=?,pending_review=? WHERE id=?",
            (sentiment, severity, relevance, verdict, engine, pending_review, event_id))

def list_events(enterprise_id: Optional[int] = None, category: Optional[str] = None,
                source: Optional[str] = None, since: str = "", limit: int = 500,
                monitored_only: bool = False) -> List[Event]:
    sql, args = "SELECT e.* FROM event e", []
    joins = ""
    if monitored_only:
        joins = " JOIN enterprise ent ON ent.id=e.enterprise_id AND ent.monitored=1"
    conds = []
    if enterprise_id:
        conds.append("e.enterprise_id=?"); args.append(enterprise_id)
    if category:
        conds.append("e.category=?"); args.append(category)
    if source:
        conds.append("e.source=?"); args.append(source)
    if since:
        conds.append("e.occurred_at>=?"); args.append(since)
    sql += joins + (" WHERE " + " AND ".join(conds) if conds else "") + \
        " ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Event(**{k: r[k] for k in r.keys()}) for r in rows]

# ---------- CreditMaterial ----------
def add_material(ent_id: int, filename: str, path: str, text: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO credit_material(enterprise_id,filename,path,text) VALUES(?,?,?,?)",
            (ent_id, filename, path, text))
        return cur.lastrowid

def list_materials(ent_id: int) -> List[CreditMaterial]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM credit_material WHERE enterprise_id=?", (ent_id,)).fetchall()
    return [CreditMaterial(**{k: r[k] for k in r.keys()}) for r in rows]

# ---------- app_settings ----------
def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        r = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def set_setting(key: str, value: str):
    with connect() as conn:
        conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

# ---------- ai_queue ----------
def enqueue(task_type: str, payload: dict) -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO ai_queue(task_type,payload) VALUES(?,?)",
                           (task_type, json.dumps(payload, ensure_ascii=False)))
        return cur.lastrowid

def pop_pending_tasks(limit: int = 20):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM ai_queue WHERE status='pending' ORDER BY id LIMIT ?",
                            (limit,)).fetchall()
    return [(r["id"], r["task_type"], json.loads(r["payload"])) for r in rows]

def finish_task(task_id: int, ok: bool = True):
    with connect() as conn:
        conn.execute("UPDATE ai_queue SET status=? WHERE id=?", ("done" if ok else "failed", task_id))

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
