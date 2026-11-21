"""业务 what-if 仿真（P-12/Workshop 前置能力补齐）。

在 SQLite savepoint 内对真实引擎执行一组动作，逐对象算出前后差异，
随后 **整段回滚**——生产事实零改动。与 change_set（真实编辑+可回滚）不同，
仿真从一开始就不打算落库，用来回答"如果执行这几个动作会怎样"。

副作用派发在仿真期间临时摘除，避免 webhook/通知在回滚后残留投递凭据。
"""
from __future__ import annotations

import json
from typing import Any


class SimulationError(Exception):
    pass


class Simulator:
    def __init__(self, system: Any, ext: Any = None, security: Any = None):
        self.system = system
        self.ext = ext
        self.security = security

    def snapshot(self, object_ids: list[str] | None = None) -> dict:
        conn = self.system.conn
        if object_ids:
            ph = ",".join("?" for _ in object_ids)
            rows = conn.execute(
                f"SELECT object_id,type,status,props FROM objects WHERE object_id IN ({ph})",
                tuple(object_ids)).fetchall()
        else:
            rows = conn.execute(
                "SELECT object_id,type,status,props FROM objects").fetchall()
        return {r["object_id"]: {"type": r["type"], "status": r["status"],
                                 "props": json.loads(r["props"])} for r in rows}

    @staticmethod
    def diff(before: dict, after: dict) -> list[dict]:
        out: list[dict] = []
        for oid in sorted(set(before) | set(after)):
            b, a = before.get(oid), after.get(oid)
            if b is None:
                out.append({"object_id": oid, "change": "CREATED", "after": a})
                continue
            if a is None:
                out.append({"object_id": oid, "change": "DELETED", "before": b})
                continue
            props = {}
            for key in sorted(set(b["props"]) | set(a["props"])):
                if b["props"].get(key) != a["props"].get(key):
                    props[key] = {"before": b["props"].get(key),
                                  "after": a["props"].get(key)}
            if b["status"] != a["status"]:
                props["status"] = {"before": b["status"], "after": a["status"]}
            if props:
                out.append({"object_id": oid, "type": a["type"],
                            "change": "UPDATED", "properties": props})
        return out

    def run(self, actions: list[dict], watch: list[str] | None = None) -> dict:
        if not actions:
            raise SimulationError("仿真动作不能为空")
        system = self.system
        conn = system.conn
        targets = watch or [a.get("object_id") for a in actions if a.get("object_id")]
        hook = system.effect_hook
        system.effect_hook = None
        conn.execute("SAVEPOINT sim_sp")
        try:
            before = self.snapshot(targets)
            results = []
            for a in actions:
                results.append(system.execute_action(
                    a["action_id"], a["object_id"], a.get("actor", "simulator"),
                    a.get("role", "operator"), a.get("payload") or {},
                    None, defer_commit=True))
            after = self.snapshot(targets)
            changes = self.diff(before, after)
            verdict = {"actions": len(actions), "succeeded": len(results),
                       "objects_changed": len(changes), "changes": changes,
                       "results": results, "production_untouched": True}
            return verdict
        except Exception as exc:  # noqa: BLE001
            return {"actions": len(actions), "succeeded": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "production_untouched": True}
        finally:
            conn.execute("ROLLBACK TO sim_sp")
            conn.execute("RELEASE sim_sp")
            conn.rollback()
            system.effect_hook = hook

    def compare_query(self, actions: list[dict], query: dict) -> dict:
        """在同一个仿真事务内对比动作前后的 Object Set 查询结果。"""
        from freight.object_query import ObjectQueryEngine

        system, conn = self.system, self.system.conn
        eng = ObjectQueryEngine(system.sem, self.ext, self.security)
        hook = system.effect_hook
        system.effect_hook = None
        conn.execute("SAVEPOINT sim_query_sp")
        try:
            before = eng.execute(query, system.facts.store)
            results = []
            for a in actions:
                results.append(system.execute_action(
                    a["action_id"], a["object_id"], a.get("actor", "simulator"),
                    a.get("role", "operator"), a.get("payload") or {},
                    None, defer_commit=True))
            after = eng.execute(query, system.facts.store)
            b_ids = {o["object_id"] for o in before["objects"]}
            a_ids = {o["object_id"] for o in after["objects"]}
            return {"before_total": before["total"], "after_total": after["total"],
                    "added": sorted(a_ids - b_ids), "removed": sorted(b_ids - a_ids),
                    "succeeded": len(results), "production_untouched": True}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}",
                    "production_untouched": True}
        finally:
            conn.execute("ROLLBACK TO sim_query_sp")
            conn.execute("RELEASE sim_query_sp")
            conn.rollback()
            system.effect_hook = hook
