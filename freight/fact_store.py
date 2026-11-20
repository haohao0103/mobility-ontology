"""外部业务事实演示库：TMS / 计费 / 结算系统的替身，不属于本体知识库。

本体只持久化 semantic_layer.json 中的类、关系、规则、动作与数据源映射；
本模块中的对象、关系、证据、事件、审计行是“外部系统里的事实”，
由 FactMCP 按工具调用读取，供诊断和展示临时组装使用。
"""
from __future__ import annotations

import json
import sqlite3
import threading

from freight import projection

# 并发模型（对标 Palantir 服务端多租户的降级实现，P0-3）：
# - 写路径：单一写连接 + api 层写锁串行（SQLite 单写者约束）
# - 读路径：WAL 模式下每线程独立只读连接，读写互不阻塞
# - busy_timeout：写冲突时等待而非报错

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
  object_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT,
  props TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_type ON objects(type);
CREATE INDEX IF NOT EXISTS idx_objects_status ON objects(type, status);

CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  object_type TEXT NOT NULL,
  source_system TEXT,
  valid_from TEXT,
  valid_to TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relations(subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_rel_object ON relations(object_id, predicate);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  object_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  source_system TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  version TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload TEXT NOT NULL,
  checksum TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_object ON evidence(object_id);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_object ON events(object_id, occurred_at);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  role TEXT NOT NULL,
  action_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  request TEXT NOT NULL,
  result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_object ON audit(object_id, occurred_at);
"""


class FactStore:
    def __init__(self, conn: sqlite3.Connection, db_path: str | None = None):
        self.conn = conn
        self.db_path = db_path
        # P0-3 并发修复：WAL + busy_timeout，读不再阻塞写、写不再阻塞读
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.OperationalError:
            pass
        conn.executescript(SCHEMA)
        # 投影表与源表同生命周期：建表放在同一处，避免「老库没有投影表」这类
        # 只在生产环境出现的分叉。
        conn.executescript(projection.PROJECTION_SCHEMA)
        # 老库首次升级 / 绕过写入口改库后的自愈：状态计数与实际不符就全量重建。
        # 不重建的后果不是报错而是静默返回空集，比报错危险得多。
        try:
            if not projection.is_fresh(conn):
                projection.sync_all(conn)
                conn.commit()
        except sqlite3.Error:
            pass
        self._read_local = threading.local()
        # 最近一次写操作的线程；只有它能借写连接读未提交数据
        self._write_thread: int | None = None

    def _read_conn(self) -> sqlite3.Connection:
        """线程本地只读连接；WAL 模式下读写不互斥。

        **只有写事务的发起线程**才回落到写连接：同一事务内的读写必须看到未提交
        数据（seed / 复合写流程依赖此语义）。其他线程即使在写事务进行中，也必须
        用独立只读连接——把写连接借给别的线程会直接撞
        "bad parameter or other API misuse"，这是 SQLite 连接不可跨线程共享的硬约束。
        """
        if self.conn.in_transaction and threading.get_ident() == self._write_thread:
            return self.conn
        c = getattr(self._read_local, "conn", None)
        if c is None:
            if not self.db_path:
                return self.conn  # 无法建独立连接时退回共享连接
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=5000")
            except sqlite3.OperationalError:
                pass
            self._read_local.conn = c
        return c

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        row = self._read_conn().execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._read_conn().execute(sql, args).fetchall()]

    def scalar(self, sql: str, args: tuple = ()):
        row = self._read_conn().execute(sql, args).fetchone()
        return row[0] if row else None

    def insert_object(self, object_id: str, type_id: str, name: str,
                      status: str | None, props: dict, at: str) -> None:
        self._write_thread = threading.get_ident()
        self.conn.execute(
            "INSERT OR REPLACE INTO objects(object_id,type,name,status,props,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (object_id, type_id, name, status, json.dumps(props, ensure_ascii=False), at, at),
        )
        projection.sync_object(self.conn, object_id)

    def update_object(self, object_id: str, props: dict, name: str | None,
                      status: str | None, at: str) -> None:
        self._write_thread = threading.get_ident()
        self.conn.execute(
            "UPDATE objects SET props=?, name=COALESCE(?,name), status=?, updated_at=? "
            "WHERE object_id=?",
            (json.dumps(props, ensure_ascii=False), name, status, at, object_id),
        )
        projection.sync_object(self.conn, object_id)

    def projection_is_fresh(self) -> bool:
        """投影新鲜度（用线程本地只读连接判定）。

        不能用 self.conn：那是单写连接，读线程直接用它会和在写的线程抢同一个
        sqlite3 连接，报 "bad parameter or other API misuse"。
        """
        return projection.is_fresh(self._read_conn())

    def get_row(self, object_id: str) -> dict | None:
        return self.one("SELECT * FROM objects WHERE object_id=?", (object_id,))

    def add_relation(self, subject_id: str, predicate: str, object_id: str,
                     subject_type: str, object_type: str,
                     at: str, source_system: str = "ontology") -> None:
        self._write_thread = threading.get_ident()
        self.conn.execute(
            """INSERT INTO relations
               (subject_id,predicate,object_id,subject_type,object_type,source_system,
                valid_from,valid_to,created_at)
               VALUES (?,?,?,?,?,?,?,NULL,?)""",
            (subject_id, predicate, object_id, subject_type, object_type,
             source_system, at, at),
        )

    def close_relation(self, subject_id: str, predicate: str, at: str,
                       keep_object_id: str | None = None) -> int:
        self._write_thread = threading.get_ident()
        sql = ("UPDATE relations SET valid_to=? WHERE subject_id=? AND predicate=? "
               "AND valid_to IS NULL")
        args: list = [at, subject_id, predicate]
        if keep_object_id:
            sql += " AND object_id<>?"
            args.append(keep_object_id)
        return self.conn.execute(sql, tuple(args)).rowcount

    def add_evidence(self, evidence_id: str, object_id: str, evidence_type: str,
                     source_system: str, occurred_at: str, version: str,
                     summary: str, payload: dict, checksum: str) -> None:
        self._write_thread = threading.get_ident()
        self.conn.execute(
            """INSERT OR REPLACE INTO evidence
               (evidence_id,object_id,evidence_type,source_system,occurred_at,version,summary,payload,checksum)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (evidence_id, object_id, evidence_type, source_system, occurred_at,
             version, summary, json.dumps(payload, ensure_ascii=False), checksum),
        )

    def add_event(self, object_id: str, event_type: str, occurred_at: str,
                  actor: str, payload: dict | None = None) -> None:
        self._write_thread = threading.get_ident()
        self.conn.execute(
            "INSERT INTO events(object_id,event_type,occurred_at,actor,payload) VALUES (?,?,?,?,?)",
            (object_id, event_type, occurred_at, actor,
             json.dumps(payload or {}, ensure_ascii=False)),
        )

    def add_audit(self, occurred_at: str, actor: str, role: str, action_id: str,
                  object_id: str, decision: str, idempotency_key: str | None,
                  request: dict, result: dict) -> int:
        self._write_thread = threading.get_ident()
        cur = self.conn.execute(
            """INSERT INTO audit
               (occurred_at,actor,role,action_id,object_id,decision,idempotency_key,request,result)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (occurred_at, actor, role, action_id, object_id, decision, idempotency_key,
             json.dumps(request, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def audit_by_key(self, key: str) -> dict | None:
        row = self.one("SELECT * FROM audit WHERE idempotency_key=?", (key,))
        if row:
            row["request"] = json.loads(row["request"])
            row["result"] = json.loads(row["result"])
        return row

    @staticmethod
    def decode(row: dict) -> dict:
        row = dict(row)
        row["props"] = json.loads(row["props"])
        return row