"""外部业务事实演示库：TMS / 计费 / 结算系统的替身，不属于本体知识库。

本体只持久化 semantic_layer.json 中的类、关系、规则、动作与数据源映射；
本模块中的对象、关系、证据、事件、审计行是“外部系统里的事实”，
由 FactMCP 按工具调用读取，供诊断和展示临时组装使用。
"""
from __future__ import annotations

import json
import sqlite3

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
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(SCHEMA)

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def scalar(self, sql: str, args: tuple = ()):
        row = self.conn.execute(sql, args).fetchone()
        return row[0] if row else None

    def insert_object(self, object_id: str, type_id: str, name: str,
                      status: str | None, props: dict, at: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO objects(object_id,type,name,status,props,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (object_id, type_id, name, status, json.dumps(props, ensure_ascii=False), at, at),
        )

    def update_object(self, object_id: str, props: dict, name: str | None,
                      status: str | None, at: str) -> None:
        self.conn.execute(
            "UPDATE objects SET props=?, name=COALESCE(?,name), status=?, updated_at=? "
            "WHERE object_id=?",
            (json.dumps(props, ensure_ascii=False), name, status, at, object_id),
        )

    def get_row(self, object_id: str) -> dict | None:
        return self.one("SELECT * FROM objects WHERE object_id=?", (object_id,))

    def add_relation(self, subject_id: str, predicate: str, object_id: str,
                     subject_type: str, object_type: str,
                     at: str, source_system: str = "ontology") -> None:
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
        self.conn.execute(
            """INSERT OR REPLACE INTO evidence
               (evidence_id,object_id,evidence_type,source_system,occurred_at,version,summary,payload,checksum)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (evidence_id, object_id, evidence_type, source_system, occurred_at,
             version, summary, json.dumps(payload, ensure_ascii=False), checksum),
        )

    def add_event(self, object_id: str, event_type: str, occurred_at: str,
                  actor: str, payload: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events(object_id,event_type,occurred_at,actor,payload) VALUES (?,?,?,?,?)",
            (object_id, event_type, occurred_at, actor,
             json.dumps(payload or {}, ensure_ascii=False)),
        )

    def add_audit(self, occurred_at: str, actor: str, role: str, action_id: str,
                  object_id: str, decision: str, idempotency_key: str | None,
                  request: dict, result: dict) -> int:
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