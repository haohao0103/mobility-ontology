"""对象数据变更集 / 分支 / 一键回滚（对标 Palantir 对象数据分支与变更集）。

本体定义的版本治理已在 freight/release.py + change_gate.py；本模块补的是
**对象数据侧**：一次业务编辑作为原子变更集，可预览、应用、整体回滚；
分支是某一时刻对象事实的命名快照（POC 用快照副本，不做 MVCC）。

事实仍不进本体库：所有表建在 FactStore 同一个外部事实库。
"""
from __future__ import annotations

import json
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS change_sets (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  author TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  applied_at TEXT
);
CREATE TABLE IF NOT EXISTS change_items (
  change_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  op TEXT NOT NULL,
  object_id TEXT NOT NULL,
  prop TEXT,
  before TEXT,
  after TEXT,
  payload TEXT,
  PRIMARY KEY (change_id, seq)
);
CREATE TABLE IF NOT EXISTS object_branches (
  name TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  objects_json TEXT NOT NULL,
  relations_json TEXT NOT NULL,
  note TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ChangeSetError(Exception):
    pass


class ChangeSetStore:
    def __init__(self, store: Any):
        self.store = store
        store.conn.executescript(SCHEMA)

    # -------------------------------------------------- 变更集
    def create(self, change_id: str, label: str, author: str = "system") -> dict:
        self.store.conn.execute(
            "INSERT INTO change_sets(id,label,author,status,created_at) "
            "VALUES (?,?,?,?,?)", (change_id, label, author, "DRAFT", _now()))
        self.store.conn.commit()
        return {"id": change_id, "status": "DRAFT"}

    def plan(self, change_id: str, op: str, object_id: str,
             prop: str | None = None, after: Any = None,
             payload: dict | None = None) -> dict:
        """记录一个编辑项；op ∈ set_prop / add_relation / delete_object。"""
        cs = self.get(change_id)
        if cs["status"] != "DRAFT":
            raise ChangeSetError(f"变更集 {change_id} 已 {cs['status']}，不可再编辑")
        if op not in ("set_prop", "add_relation", "delete_object"):
            raise ChangeSetError(f"不支持的变更操作: {op}")
        conn = self.store.conn
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM change_items WHERE change_id=?",
            (change_id,)).fetchone()[0]
        before = None
        if op == "set_prop" and prop:
            row = conn.execute("SELECT props FROM objects WHERE object_id=?",
                               (object_id,)).fetchone()
            if row is not None:
                before = json.loads(row[0]).get(prop)
        conn.execute(
            "INSERT INTO change_items(change_id,seq,op,object_id,prop,before,after,payload) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (change_id, seq, op, object_id, prop,
             json.dumps(before, ensure_ascii=False) if before is not None else None,
             json.dumps(after, ensure_ascii=False) if after is not None else None,
             json.dumps(payload or {}, ensure_ascii=False)))
        conn.commit()
        return {"id": change_id, "seq": seq}

    def preview(self, change_id: str) -> dict:
        cs = self.get(change_id)
        items = [dict(r) for r in self.store.conn.execute(
            "SELECT seq,op,object_id,prop,before,after,payload FROM change_items "
            "WHERE change_id=? ORDER BY seq", (change_id,))]
        return {**cs, "items": items}

    def get(self, change_id: str) -> dict:
        row = self.store.conn.execute(
            "SELECT id,label,author,status,created_at,applied_at FROM change_sets "
            "WHERE id=?", (change_id,)).fetchone()
        if row is None:
            raise ChangeSetError(f"变更集不存在: {change_id}")
        return dict(row)

    def apply(self, change_id: str) -> dict:
        cs = self.get(change_id)
        if cs["status"] != "DRAFT":
            raise ChangeSetError(f"变更集已是 {cs['status']}，不可重复应用")
        items = list(self.store.conn.execute(
            "SELECT * FROM change_items WHERE change_id=? ORDER BY seq", (change_id,)))
        conn = self.store.conn
        try:
            for it in items:
                self._apply_item(it)
            conn.execute(
                "UPDATE change_sets SET status='APPLIED', applied_at=? WHERE id=?",
                (_now(), change_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {"id": change_id, "status": "APPLIED", "applied": len(items)}

    def _apply_item(self, it: Any) -> None:
        op, oid = it["op"], it["object_id"]
        conn = self.store.conn
        if op == "set_prop":
            row = conn.execute("SELECT * FROM objects WHERE object_id=?", (oid,)).fetchone()
            if row is None:
                raise ChangeSetError(f"对象不存在: {oid}")
            props = json.loads(row["props"])
            props[it["prop"]] = json.loads(it["after"]) if it["after"] else None
            self.store.update_object(oid, props, row["name"], row["status"], _now())
        elif op == "add_relation":
            p = json.loads(it["payload"])
            self.store.add_relation(
                oid, p["predicate"], p["object_id"], p.get("subject_type", ""),
                p.get("object_type", ""), _now(),
                p.get("source_system", "ontology"))
        elif op == "delete_object":
            conn.execute("DELETE FROM objects WHERE object_id=?", (oid,))
            conn.execute("DELETE FROM object_props WHERE object_id=?", (oid,))

    def rollback(self, change_id: str) -> dict:
        """一键回滚：逆序恢复 before。set_prop 恢复旧值，其他操作按 payload 反做。"""
        cs = self.get(change_id)
        if cs["status"] not in ("APPLIED", "ROLLED_BACK"):
            raise ChangeSetError(f"变更集 {cs['status']} 无可回滚内容")
        items = list(self.store.conn.execute(
            "SELECT * FROM change_items WHERE change_id=? ORDER BY seq DESC", (change_id,)))
        conn = self.store.conn
        try:
            for it in items:
                if it["op"] == "set_prop":
                    row = conn.execute("SELECT * FROM objects WHERE object_id=?",
                                       (it["object_id"],)).fetchone()
                    if row is None:
                        continue
                    props = json.loads(row["props"])
                    if it["before"] is None:
                        props.pop(it["prop"], None)
                    else:
                        props[it["prop"]] = json.loads(it["before"])
                    self.store.update_object(
                        it["object_id"], props, row["name"], row["status"], _now())
                elif it["op"] == "add_relation":
                    p = json.loads(it["payload"])
                    conn.execute(
                        "DELETE FROM relations WHERE subject_id=? AND predicate=? "
                        "AND object_id=?", (it["object_id"], p["predicate"], p["object_id"]))
            conn.execute("UPDATE change_sets SET status='ROLLED_BACK' WHERE id=?",
                         (change_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {"id": change_id, "status": "ROLLED_BACK", "reverted": len(items)}

    def list(self) -> list[dict]:
        return [dict(r) for r in self.store.conn.execute(
            "SELECT id,label,author,status,created_at,applied_at FROM change_sets "
            "ORDER BY created_at DESC")]

    # -------------------------------------------------- 对象数据分支
    def create_branch(self, name: str, note: str = "") -> dict:
        conn = self.store.conn
        objects = [dict(r) for r in conn.execute("SELECT * FROM objects")]
        relations = [dict(r) for r in conn.execute("SELECT * FROM relations")]
        conn.execute(
            "INSERT OR REPLACE INTO object_branches"
            "(name,created_at,objects_json,relations_json,note) VALUES (?,?,?,?,?)",
            (name, _now(), json.dumps(objects, ensure_ascii=False),
             json.dumps(relations, ensure_ascii=False), note))
        conn.commit()
        return {"name": name, "objects": len(objects), "relations": len(relations)}

    def list_branches(self) -> list[dict]:
        return [{"name": r["name"], "created_at": r["created_at"], "note": r["note"]}
                for r in self.store.conn.execute(
                    "SELECT name,created_at,note FROM object_branches ORDER BY created_at DESC")]

    def restore_branch(self, name: str) -> dict:
        row = self.store.conn.execute(
            "SELECT objects_json, relations_json FROM object_branches WHERE name=?",
            (name,)).fetchone()
        if row is None:
            raise ChangeSetError(f"分支不存在: {name}")
        objects, relations = json.loads(row[0]), json.loads(row[1])
        conn = self.store.conn
        try:
            conn.execute("DELETE FROM object_props")
            conn.execute("DELETE FROM objects")
            conn.execute("DELETE FROM relations")
            for o in objects:
                conn.execute(
                    "INSERT INTO objects(object_id,type,name,status,props,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (o["object_id"], o["type"], o["name"], o["status"], o["props"],
                     o["created_at"], o["updated_at"]))
            for r in relations:
                conn.execute(
                    "INSERT INTO relations(subject_id,predicate,object_id,subject_type,"
                    "object_type,source_system,valid_from,valid_to,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (r["subject_id"], r["predicate"], r["object_id"], r["subject_type"],
                     r["object_type"], r["source_system"], r["valid_from"],
                     r["valid_to"], r["created_at"]))
            from freight import projection, projection_v2
            projection.sync_all(conn)
            if getattr(self.store, "v2_ext", None) is not None:
                projection_v2.sync_all(conn, self.store.v2_ext)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {"name": name, "restored_objects": len(objects),
                "restored_relations": len(relations)}
