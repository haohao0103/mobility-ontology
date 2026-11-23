"""批量 Action 应用与跨对象 edits（P-07 补齐）。

单笔 execute_action 已有角色/状态/规则/参数校验 + 审计 + 幂等；
本模块在其上加**原子批量**：所有动作在同一 SQLite savepoint 内执行，
任一失败整批回滚（含审计），不留半截状态。

跨对象 edits 是同一事务内对多个对象属性的直接写入，走 FactStore 唯一写入口，
因此投影与 OSv2 物化层同步维护。
"""
from __future__ import annotations

import json
import time
from typing import Any

from .concurrency import WRITE_LOCK

SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_audits (
  batch_key TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  count INTEGER NOT NULL,
  status TEXT NOT NULL,
  detail TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class BatchError(Exception):
    pass


class BatchExecutor:
    def __init__(self, system: Any):
        self.system = system
        self.effects = None  # EnhancedSystem 挂载；提交后统一派发副作用
        system.conn.executescript(SCHEMA)

    def plan(self, actions: list[dict]) -> dict:
        """只读预检：逐笔调用 action_preflight，返回可否整批提交。"""
        items = []
        for i, a in enumerate(actions):
            try:
                view = self.system.action_preflight(a["object_id"], a["action_id"])
                items.append({"index": i, "ok": bool(view.get("allowed", True)),
                              "action_id": a["action_id"], "object_id": a["object_id"],
                              "blockers": view.get("blockers", [])})
            except Exception as exc:  # noqa: BLE001
                items.append({"index": i, "ok": False,
                              "action_id": a.get("action_id"),
                              "object_id": a.get("object_id"),
                              "error": f"{type(exc).__name__}: {exc}"})
        return {"count": len(actions), "ready": all(x["ok"] for x in items),
                "items": items}

    def apply(self, actions: list[dict], batch_key: str | None = None) -> dict:
        with WRITE_LOCK:
            return self._apply_locked(actions, batch_key)

    def _apply_locked(self, actions: list[dict], batch_key: str | None = None) -> dict:
        if not actions:
            raise BatchError("批量动作不能为空")
        conn = self.system.conn
        if batch_key:
            old = conn.execute("SELECT * FROM batch_audits WHERE batch_key=?",
                               (batch_key,)).fetchone()
            if old is not None:
                return dict(old)
        sp = "batch_sp"
        conn.execute(f"SAVEPOINT {sp}")
        try:
            results = []
            for a in actions:
                result = self.system.execute_action(
                    a["action_id"], a["object_id"], a.get("actor", "system"),
                    a.get("role", "operator"), a.get("payload") or {},
                    a.get("idempotency_key"), defer_commit=True)
                results.append(result)
            detail = json.dumps({"results": results}, ensure_ascii=False, default=str)
            if batch_key:
                conn.execute(
                    "INSERT INTO batch_audits(batch_key,created_at,actor,count,status,detail) "
                    "VALUES (?,?,?,?,?,?)",
                    (batch_key, _now(), actions[0].get("actor", "system"),
                     len(actions), "APPLIED", detail))
            conn.execute(f"RELEASE {sp}")
            conn.commit()
            if self.effects is not None:
                for a, r in zip(actions, results):
                    try:
                        r["effects"] = self.effects.dispatch(
                            a["action_id"], a["object_id"], r,
                            a.get("actor", "system"), a.get("role", "operator"))
                    except Exception:  # noqa: BLE001
                        r["effects"] = [{"ok": False, "error": "effect_dispatch_failed"}]
            return {"status": "APPLIED", "count": len(actions), "results": results,
                    "batch_key": batch_key}
        except Exception as exc:  # noqa: BLE001
            conn.execute(f"ROLLBACK TO {sp}")
            conn.execute(f"RELEASE {sp}")
            conn.rollback()
            if batch_key:
                conn.execute(
                    "INSERT OR REPLACE INTO batch_audits"
                    "(batch_key,created_at,actor,count,status,detail) VALUES (?,?,?,?,?,?)",
                    (batch_key, _now(), actions[0].get("actor", "system"),
                     len(actions), "FAILED",
                     json.dumps({"error": f"{type(exc).__name__}: {exc}"},
                                ensure_ascii=False)))
                conn.commit()
            raise BatchError(f"批量执行失败已整批回滚: {exc}") from exc

    def edits(self, edits: list[dict], actor: str = "system") -> dict:
        """跨对象 edits：同一事务内修改多个对象的属性（含投影同步）。"""
        with WRITE_LOCK:
            return self._edits_locked(edits, actor)

    def _edits_locked(self, edits: list[dict], actor: str = "system") -> dict:
        if not edits:
            raise BatchError("edits 不能为空")
        conn = self.system.conn
        conn.execute("SAVEPOINT edits_sp")
        try:
            applied = []
            for e in edits:
                oid = e["object_id"]
                row = conn.execute("SELECT * FROM objects WHERE object_id=?",
                                   (oid,)).fetchone()
                if row is None:
                    raise BatchError(f"对象不存在: {oid}")
                props = json.loads(row["props"])
                for prop, value in (e.get("set") or {}).items():
                    props[prop] = value
                for prop in e.get("unset") or []:
                    props.pop(prop, None)
                self.system.facts.update_object(
                    oid, props, row["name"], e.get("status"), _now())
                applied.append(oid)
            conn.execute("RELEASE edits_sp")
            conn.commit()
            return {"status": "APPLIED", "objects": applied}
        except Exception:
            conn.execute("ROLLBACK TO edits_sp")
            conn.execute("RELEASE edits_sp")
            conn.rollback()
            raise

    def history(self) -> list[dict]:
        return [dict(r) for r in self.system.conn.execute(
            "SELECT batch_key,created_at,actor,count,status FROM batch_audits "
            "ORDER BY created_at DESC LIMIT 100")]