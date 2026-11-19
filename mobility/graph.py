"""知识图谱层：持久化时态三元组 + 事件时间线（非内存图，事实落 SQLite）。

本体负责定义「关系是否合法」，本层负责记录「一笔真实订单产生了哪些事实」。
时态关系（drives / uses_vehicle）以 valid_from / valid_to 表达，
避免把「司机驾驶车辆」写成车辆的固定字段。
"""
from __future__ import annotations
import json
import sqlite3

FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
  subject_type TEXT NOT NULL, object_type TEXT NOT NULL,
  valid_from TEXT, valid_to TEXT,
  occurred_at TEXT NOT NULL,
  order_id TEXT, seq INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'relation',
  payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_order ON facts(order_id, seq);
CREATE INDEX IF NOT EXISTS idx_facts_pred ON facts(predicate);
CREATE INDEX IF NOT EXISTS idx_facts_obj ON facts(object);
CREATE INDEX IF NOT EXISTS idx_facts_temporal ON facts(predicate, object, valid_from, valid_to);
"""


class TemporalGraph:
    def __init__(self, conn: sqlite3.Connection, semantic):
        self.conn = conn
        self.sem = semantic
        self.conn.executescript(FACTS_SCHEMA)

    def _next_seq(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM facts").fetchone()[0])

    def add_fact(
        self, subject: str, predicate: str, object: str,
        *, subject_type: str, object_type: str, occurred_at: str,
        order_id: str | None = None,
        valid_from: str | None = None, valid_to: str | None = None,
        kind: str = "relation", payload: dict | None = None,
    ) -> int:
        # 事件事实不走本体关系校验；关系事实必须合法
        if kind == "relation":
            self.sem.validate_relation(predicate, subject_type, object_type)
            if self.sem.is_temporal(predicate) and not valid_from:
                raise ValueError(f"时态关系 {predicate} 必须提供 valid_from")
        seq = self._next_seq()
        cur = self.conn.execute(
            """INSERT INTO facts
               (subject,predicate,object,subject_type,object_type,
                valid_from,valid_to,occurred_at,order_id,seq,kind,payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (subject, predicate, object, subject_type, object_type,
             valid_from, valid_to, occurred_at, order_id, seq, kind,
             json.dumps(payload or {}, ensure_ascii=False)),
        )
        return cur.lastrowid

    def event(self, order_id: str, name: str, occurred_at: str, payload: dict | None = None) -> int:
        """记录业务事件（时间线节点），用于客服还原完整过程。"""
        return self.add_fact(
            order_id, "event", name,
            subject_type="Order", object_type="Event",
            occurred_at=occurred_at, order_id=order_id,
            kind="event", payload=payload,
        )

    def close_interval(self, subject: str, predicate: str, object: str,
                       valid_to: str, order_id: str | None = None) -> int:
        """关闭仍生效的时态关系（取消 / 行程结束 / 支付完成时调用）。"""
        rows = self.conn.execute(
            """SELECT id FROM facts
               WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL
                 AND (? IS NULL OR order_id=?)""",
            (subject, predicate, object, order_id, order_id),
        ).fetchall()
        for (fid,) in rows:
            self.conn.execute("UPDATE facts SET valid_to=? WHERE id=?", (valid_to, fid))
        return len(rows)

    def active_intervals(self, predicate: str, object: str, at: str) -> list[dict]:
        """查询某时刻仍然生效的时态关系（例如：此刻谁在驾驶 V3）。"""
        return [dict(r) for r in self.conn.execute(
            """SELECT * FROM facts
               WHERE predicate=? AND object=?
                 AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)
               ORDER BY valid_from""",
            (predicate, object, at, at),
        ).fetchall()]

    def intervals(self, predicate: str, object: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM facts WHERE predicate=? AND object=? ORDER BY seq",
            (predicate, object),
        ).fetchall()]

    def facts_of(self, order_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE order_id=? ORDER BY seq", (order_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    def count_relation(self, predicate: str, object: str) -> int:
        """统计以该实体为宾语的关系数（如 Order 作为 initiates 的宾语）。"""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE predicate=? AND object=? AND kind='relation'",
            (predicate, object),
        ).fetchone()[0])

    def count_relation_subject(self, predicate: str, subject: str) -> int:
        """统计以该实体为主语的关系数（如 Trip 作为 executed_by 的主语）。"""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE predicate=? AND subject=? AND kind='relation'",
            (predicate, subject),
        ).fetchone()[0])

    def relation_targets(self, predicate: str, subject: str) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT object FROM facts WHERE predicate=? AND subject=? AND kind='relation'",
            (predicate, subject),
        ).fetchall()]

    def all_relations(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM facts WHERE kind='relation'"
        ).fetchall()]
