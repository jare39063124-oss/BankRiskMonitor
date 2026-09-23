"""SQLite 存储层（契约）：建表与通用 CRUD。GUI/Provider/AI 层均通过本模块读写。"""
import json
import sqlite3
from datetime import datetime
from typing import List, Optional

from .models import Enterprise, Event, CreditMaterial
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
"""

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)

# ---------- Enterprise ----------
def upsert_enterprise(name: str, credit_code: str = "", province: str = "", city: str = "",
                      credit_note: str = "", monitored: int = 1) -> int:
    with connect() as conn:
        cur = conn.execute(
            "SELECT id FROM enterprise WHERE name=? AND credit_code=?", (name, credit_code))
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE enterprise SET province=?, city=?, credit_note=?, monitored=? WHERE id=?",
                (province, city, credit_note, monitored, row["id"]))
            return row["id"]
        cur = conn.execute(
            "INSERT INTO enterprise(name,credit_code,province,city,credit_note,monitored) VALUES(?,?,?,?,?,?)",
            (name, credit_code, province, city, credit_note, monitored))
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
