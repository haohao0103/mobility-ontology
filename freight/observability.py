"""Agent 工具调用的观测与评测集（P-12 补齐）。

- 调用日志：每次 MCP 工具调用记录 tool / 参数摘要 / 耗时 / 状态 / 本体版本；
- 评测集：预置对象定位与检索完整性样本，可回归运行，输出准确率与证据覆盖率。

日志表建在事实库，不进本体库。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_invocations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  tool TEXT NOT NULL,
  args_digest TEXT NOT NULL,
  actor TEXT NOT NULL,
  status TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  ontology_version TEXT,
  error TEXT,
  result_digest TEXT
);
CREATE INDEX IF NOT EXISTS idx_invocations_tool ON tool_invocations(tool, occurred_at);
"""


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ToolObservability:
    def __init__(self, conn: Any, sem: Any = None):
        self.conn = conn
        self.sem = sem
        conn.executescript(SCHEMA)

    def record(self, tool: str, args: dict, status: str, duration_ms: int,
               actor: str = "anonymous", error: str | None = None,
               result: Any = None) -> None:
        version = self.sem.raw.get("meta", {}).get("version") if self.sem else None
        self.conn.execute(
            "INSERT INTO tool_invocations(occurred_at,tool,args_digest,actor,status,"
            "duration_ms,ontology_version,error,result_digest) VALUES (?,?,?,?,?,?,?,?,?)",
            (_now(), tool, _digest(args), actor, status, int(duration_ms), version,
             error, _digest(result) if result is not None else None))

    def timed(self, tool: str, args: dict, actor: str, fn: Callable[[], Any]) -> Any:
        start = time.perf_counter()
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - 失败也是要记录的观测数据
            self.record(tool, args, "ERROR", int((time.perf_counter() - start) * 1000),
                        actor, f"{type(exc).__name__}: {exc}")
            raise
        self.record(tool, args, "OK", int((time.perf_counter() - start) * 1000),
                    actor, result=result)
        return result

    def stats(self) -> dict:
        rows = list(self.conn.execute(
            "SELECT tool, status, COUNT(*) n, AVG(duration_ms) avg_ms, "
            "MAX(duration_ms) max_ms FROM tool_invocations GROUP BY tool, status"))
        by_tool: dict[str, dict] = {}
        for r in rows:
            entry = by_tool.setdefault(r["tool"], {"calls": 0, "ok": 0, "error": 0})
            entry["calls"] += r["n"]
            entry["ok" if r["status"] == "OK" else "error"] += r["n"]
            entry[f"{r['status'].lower()}_avg_ms"] = round(r["avg_ms"] or 0, 2)
            entry["max_ms"] = max(entry.get("max_ms", 0), r["max_ms"] or 0)
        return {"total": sum(v["calls"] for v in by_tool.values()), "by_tool": by_tool}

    def recent(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT occurred_at,tool,args_digest,actor,status,duration_ms,error "
            "FROM tool_invocations ORDER BY id DESC LIMIT ?", (limit,))]


# 评测集：只依赖本体声明的种子对象，不写业务事实。
EVAL_SET = [
    {"id": "loc-01", "kind": "object_location", "tool": "tms.get_waybill",
     "args": {"waybill_id": "WB20261105001"}, "expect": {"object_id": "waybill:WB20261105001"}},
    {"id": "loc-02", "kind": "object_location", "tool": "crm.get_shipper",
     "args": {"shipper_id": "S001"}, "expect": {"object_id": "shipper:S001"}},
    {"id": "loc-03", "kind": "object_location", "tool": "tms.get_driver",
     "args": {"driver_id": "D001"}, "expect": {"object_id": "driver:D001"}},
    {"id": "ret-01", "kind": "retrieval", "tool": "billing.list_fee_items",
     "args": {"waybill_id": "WB20261105001"}, "expect": {"min_results": 1}},
    {"id": "ret-02", "kind": "retrieval", "tool": "graph.list_relations",
     "args": {"object_id": "waybill:WB20261105001"}, "expect": {"min_results": 1}},
    {"id": "ret-03", "kind": "retrieval", "tool": "evidence.list",
     "args": {"object_ids": ["waybill:WB20261105001"]}, "expect": {"min_results": 0}},
    {"id": "sch-01", "kind": "schema", "tool": "ontology.schema",
     "args": {"question": "运单费用差异"}, "expect": {"min_results": 1}},
    {"id": "sch-02", "kind": "schema", "tool": "ontology.catalog",
     "args": {}, "expect": {"min_results": 1}},
]


def list_evalset() -> list[dict]:
    return EVAL_SET


def run_eval(call: Callable[[str, dict], Any], samples: list[dict] | None = None) -> dict:
    """对样本跑一遍工具调用，按 expect 判定。call 由调用方注入（FactMCP.call）。"""
    cases = samples or EVAL_SET
    results = []
    for s in cases:
        try:
            out = call(s["tool"], s["args"])
            ok = _matches(out, s["expect"])
            results.append({"id": s["id"], "kind": s["kind"], "ok": ok})
        except Exception as exc:  # noqa: BLE001 - 评测失败要计数，不中断整轮
            results.append({"id": s["id"], "kind": s["kind"], "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"})
    by_kind: dict[str, dict] = {}
    for s, r in zip(cases, results):
        agg = by_kind.setdefault(s["kind"], {"total": 0, "passed": 0})
        agg["total"] += 1
        agg["passed"] += int(r["ok"])
    for agg in by_kind.values():
        agg["accuracy"] = round(agg["passed"] / agg["total"], 4)
    return {"samples": len(cases), "passed": sum(r["ok"] for r in results),
            "by_kind": by_kind, "cases": results}


def _matches(out: Any, expect: dict) -> bool:
    def text(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    blob = text(out)
    for k, v in expect.items():
        if k == "min_results":
            n = len(out.get("objects") or out.get("items") or out.get("results")
                    or out.get("relations") or out.get("evidence") or out.get("tools")
                    or out.get("classes") or out.get("types") or [])
            if n < v:
                return False
        elif text(v) not in blob:
            return False
    return True
