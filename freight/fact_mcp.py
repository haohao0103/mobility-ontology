"""外部业务事实的 MCP / API 适配层（演示实现）。

设计边界：
- 本体服务只持久化 semantic_layer.json 中的知识定义。
- 运单、费用、结算、证据等业务事实属于外部系统，由本模块按需调用工具读取。
- 每次调用都记录 trace，返回给前端展示“查了什么、从哪来、返回几行”。
- 查询结果只在内存中组装，不写回本体知识库。
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable

from .fact_store import FactStore
from .object_set import page_slice, snapshot_digest, summarize_links
from .semantic import BusinessReject, SemanticLayer


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json_schema_type(spec: str) -> dict:
    """把工具参数类型描述转成 JSON Schema 类型片段。"""
    parts = [p.strip() for p in str(spec or "string").split("|")]
    types: list[str] = []
    for part in parts:
        if part.endswith("[]"):
            mapped = "array"
        elif part == "object":
            mapped = "object"
        elif part == "number":
            mapped = "number"
        elif part == "integer":
            mapped = "integer"
        elif part == "boolean":
            mapped = "boolean"
        elif part == "null":
            mapped = "null"
        else:
            mapped = "string"
        if mapped not in types:
            types.append(mapped)
    return {"type": types[0]} if len(types) == 1 else {"type": types}


def _input_schema(arguments: dict, required: list[str] | None = None) -> dict:
    properties = {
        name: _json_schema_type(spec) for name, spec in (arguments or {}).items()
    }
    if required is None:
        required = [
            name for name, spec in (arguments or {}).items()
            if "null" not in str(spec)
        ]
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def build_tool_surface(sem: SemanticLayer) -> list[dict]:
    """从通用原语 + semantic_layer.json 生成模型可见工具面。"""
    surface = []
    for tool in sem.raw.get("primitives", []):
        item = dict(tool)
        item["kind"] = "primitive"
        item["input_schema"] = _input_schema(
            item.get("arguments") or {}, required=item.get("required")
        )
        surface.append(item)

    if sem.actions:
        action_ids = sorted(sem.actions)
        action_targets = sorted({
            action.get("target") for action in sem.actions.values()
            if action.get("target")
        })
        catalog = "；".join(
            f'{action_id}({sem.actions[action_id]["target"]}; '
            f'roles={",".join(sem.actions[action_id].get("roles") or [])})'
            for action_id in action_ids
        )
        plan_args = {"object_id": "string", "action_id": "string|null"}
        plan_tool = {
            "name": "action.plan",
            "kind": "action",
            "system": "本体动作",
            "targets": action_targets,
            "description": (
                "动作预检：返回目标对象的可用/被阻止动作、规则阻断原因和影响面。"
                f"已定义动作：{catalog}"
            ),
            "arguments": plan_args,
            "returns": "ActionPreflight",
            "input_schema": _input_schema(plan_args),
        }
        execute_args = {
            "action_id": "string", "object_id": "string",
            "actor": "string", "role": "string",
            "payload": "object|null", "idempotency_key": "string|null",
        }
        execute_tool = {
            "name": "action.execute",
            "kind": "action",
            "system": "本体动作",
            "system_only": True,
            "targets": action_targets,
            "description": (
                "执行本体动作；权限、状态和规则前置条件由本体校验，"
                "拒绝时返回 FORBIDDEN_ROLE / RULE_REJECTED。"
                f"已定义动作：{catalog}"
            ),
            "arguments": execute_args,
            "returns": "ActionResult",
            "input_schema": _input_schema(
                execute_args, required=["action_id", "object_id", "actor", "role"]
            ),
        }
        execute_tool["input_schema"]["properties"]["action_id"]["enum"] = action_ids
        surface.extend([plan_tool, execute_tool])

    for function in sem.raw.get("functions", []):
        params = function.get("params") or []
        arguments = {
            param["name"]: param.get("type", "string") for param in params
        }
        required = [
            param["name"] for param in params if param.get("required")
        ]
        surface.append({
            "name": f'function.{function["id"]}',
            "kind": "function",
            "system": "确定性函数",
            "target": function.get("target"),
            "description": function.get("description") or function.get("label", ""),
            "arguments": arguments,
            "returns": function.get("returns", "object"),
            "input_schema": _input_schema(arguments, required=required),
        })
    return surface


def filter_tool_surface(surface: list[dict], type_id: str) -> list[dict]:
    """按对象类型裁剪工具面：通用原语 + 该类型的函数和诊断可见动作。"""
    return [
        tool for tool in surface
        if not tool.get("system_only")
        and (
            tool.get("kind") == "primitive"
            or tool.get("target") == type_id
            or type_id in (tool.get("targets") or [])
        )
    ]


class FactMCP:
    """外部事实工具集；底层 SQLite 只是演示替身，不是本体知识库。"""

    def __init__(self, db_path: str, sem: SemanticLayer):
        import sqlite3

        self.sem = sem
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.store = FactStore(self.conn, db_path)
        # 调用轨迹按线程隔离：两个用户同时跑 Agent 时，如果共用一条 trace，
        # 各自的 since(start) 会读到对方的工具结果，观察记录直接串台。
        self._trace_local = threading.local()
        self.runtime: Any = None
        self.conn.commit()

    def bind_runtime(self, runtime: Any) -> None:
        """绑定领域运行时，用于确定性函数和动作执行。"""
        self.runtime = runtime

    def _runtime(self) -> Any:
        if self.runtime is None:
            raise BusinessReject(
                "确定性函数/动作需要绑定领域运行时", "MCP_TOOL_UNAVAILABLE"
            )
        return self.runtime

    # ------------------------------------------------------------ 低层兼容委托
    def one(self, *args, **kwargs):
        return self.store.one(*args, **kwargs)

    def all(self, *args, **kwargs):
        return self.store.all(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.store.scalar(*args, **kwargs)

    def projection_is_fresh(self) -> bool:
        return self.store.projection_is_fresh()

    def insert_object(self, *args, **kwargs):
        return self.store.insert_object(*args, **kwargs)

    def update_object(self, object_id, props, name=None, status=None, at=None):
        """写入收口：更新前按属性注册表校验类型。

        动作用例直接 UPDATE 对象属性（10+ 处调用点），如果只在校验创建路径，
        类型约束就只在「新建」生效，改一条就绕过去了 —— 这种校验是装饰。
        收口放在 FactMCP 这个唯一写入口，避免在每个动作里重复。
        """
        sem = getattr(self.runtime, "sem", None) if self.runtime is not None else None
        if sem is not None:
            row = self.store.get_row(object_id)
            if row is not None:
                payload = dict(props or {})
                if status:
                    payload["status"] = status
                sem.validate_object(row["type"], payload)
        return self.store.update_object(object_id, props, name, status, at)

    def get_row(self, *args, **kwargs):
        return self.store.get_row(*args, **kwargs)

    def add_relation(self, *args, **kwargs):
        return self.store.add_relation(*args, **kwargs)

    def close_relation(self, *args, **kwargs):
        return self.store.close_relation(*args, **kwargs)

    def add_evidence(self, *args, **kwargs):
        return self.store.add_evidence(*args, **kwargs)

    def add_event(self, *args, **kwargs):
        return self.store.add_event(*args, **kwargs)

    def add_audit(self, *args, **kwargs):
        return self.store.add_audit(*args, **kwargs)

    def audit_by_key(self, *args, **kwargs):
        return self.store.audit_by_key(*args, **kwargs)

    @staticmethod
    def decode(row: dict) -> dict:
        return FactStore.decode(row)

    # ------------------------------------------------------------ 调用轨迹
    def _trace(self) -> list[dict]:
        trace = getattr(self._trace_local, "items", None)
        if trace is None:
            trace = []
            self._trace_local.items = trace
        return trace

    def begin(self) -> int:
        return len(self._trace())

    def since(self, start: int) -> list[dict]:
        return list(self._trace()[start:])

    def recent_trace(self, limit: int = 50) -> list[dict]:
        trace = self._trace()
        return list(trace[-limit:])

    def _record(self, tool: str, args: dict, source: str, fn: Callable[[], Any]):
        started_at = now_iso()
        trace = self._trace()
        seq = len(trace) + 1
        t0 = time.perf_counter()
        result: Any = None
        ok = True
        error = None
        try:
            result = fn()
            return result
        except Exception as exc:  # 保留失败轨迹，便于排查
            ok = False
            error = str(exc)
            raise
        finally:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            trace.append({
                "seq": seq,
                "tool": tool,
                "source_system": source,
                "args": args,
                "started_at": started_at,
                "elapsed_ms": elapsed,
                "rows": self._count_rows(result) if ok else 0,
                "ok": ok,
                "error": error,
            })
            if len(trace) > 1000:
                del trace[:200]

    @staticmethod
    def _count_rows(result: Any) -> int:
        if result is None:
            return 1
        if isinstance(result, list):
            return len(result)
        if isinstance(result, dict):
            return 1
        return 1

    # ------------------------------------------------------------ 数据源映射
    def source_for_type(self, type_id: str | None) -> str:
        for item in self.sem.raw.get("data_sources", []):
            if item.get("class") == type_id:
                return item.get("system", "EXTERNAL")
        return "EXTERNAL"

    def tool_for_type(self, type_id: str | None) -> str | None:
        for item in self.sem.raw.get("data_sources", []):
            if item.get("class") == type_id:
                return item.get("tool")
        return None

    # ------------------------------------------------------------ 行 / 节点
    def _row(self, object_id: str) -> dict | None:
        return self.store.get_row(object_id)

    def _node(self, row: dict) -> dict:
        decoded = self.store.decode(row)
        cls = self.sem.classes.get(decoded["type"], {"label": decoded["type"]})
        return {
            "object_id": decoded["object_id"],
            "type": decoded["type"],
            "type_label": cls.get("label", decoded["type"]),
            "name": decoded["name"],
            "status": decoded["status"],
            "property_count": len(decoded["props"]),
            "properties": decoded["props"],
            "property_labels": self._property_labels(decoded["type"]),
            "created_at": decoded["created_at"],
            "updated_at": decoded["updated_at"],
        }

    def _property_labels(self, type_id: str) -> dict:
        cls = self.sem.classes.get(type_id, {})
        props = cls.get("properties", {})
        if isinstance(props, dict):
            return {name: spec.get("label", name) for name, spec in props.items()}
        return {name: name for name in props}

    def _typed(self, object_id: str, expected_type: str, tool: str,
               source: str) -> dict:
        def load():
            row = self._row(object_id)
            if not row:
                raise BusinessReject(f"外部系统未找到对象: {object_id}", "OBJECT_NOT_FOUND")
            if row["type"] != expected_type:
                raise BusinessReject(
                    f"对象类型不符: {object_id} 是 {row['type']}，期望 {expected_type}",
                    "TYPE_MISMATCH",
                )
            return self._node(row)

        return self._record(tool, {"object_id": object_id}, source, load)

    # ------------------------------------------------------------ 工具实现
    def get_waybill(self, waybill_id: str) -> dict:
        return self._typed(waybill_id, "Waybill", "tms.get_waybill", "TMS")

    def get_order(self, order_id: str) -> dict:
        return self._typed(order_id, "Order", "tms.get_order", "TMS")

    def get_driver(self, driver_id: str) -> dict:
        return self._typed(driver_id, "Driver", "tms.get_driver", "TMS")

    def get_vehicle(self, vehicle_id: str) -> dict:
        return self._typed(vehicle_id, "Vehicle", "tms.get_vehicle", "TMS")

    def get_shipper(self, shipper_id: str) -> dict:
        return self._typed(shipper_id, "Shipper", "crm.get_shipper", "CRM")

    def get_object(self, object_id: str, type_id: str | None = None) -> dict:
        source = self.source_for_type(type_id) if type_id else "EXTERNAL"
        tool = self.tool_for_type(type_id) if type_id else "tms.get_object"
        if type_id:
            return self._typed(object_id, type_id, tool or "tms.get_object", source)

        def load():
            row = self._row(object_id)
            if not row:
                raise BusinessReject(f"外部系统未找到对象: {object_id}", "OBJECT_NOT_FOUND")
            return self._node(row)

        return self._record("tms.get_object", {"object_id": object_id}, "EXTERNAL", load)

    def list_objects(self, type_id: str | None = None, q: str | None = None,
                     limit: int = 200) -> list[dict]:
        def load():
            sql = "SELECT * FROM objects WHERE 1=1"
            args: list = []
            if type_id:
                sql += " AND type=?"
                args.append(type_id)
            if q:
                sql += " AND (object_id LIKE ? OR name LIKE ? OR props LIKE ?)"
                args += [f"%{q}%", f"%{q}%", f"%{q}%"]
            sql += " ORDER BY type, object_id LIMIT ?"
            args.append(max(1, min(limit, 500)))
            return [self._node(row) for row in self.store.all(sql, tuple(args))]

        return self._record("tms.list_objects", {"type": type_id, "q": q},
                            "MULTI", load)

    # ------------------------------------------------------------ 通用图查询原语
    OBJECT_COLUMNS = {
        "object_id", "type", "name", "status", "created_at", "updated_at",
    }
    FILTER_OPS = {
        "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in",
        "contains", "starts_with", "is_null", "is_not_null",
    }
    AGG_OPS = {"count", "sum", "avg", "min", "max"}

    def _schema_error(self, message: str, **details) -> None:
        details.setdefault("allowed_types", sorted(self.sem.classes))
        raise BusinessReject(message, "SCHEMA_ERROR", details)

    def _validate_type(self, type_id: str) -> None:
        if type_id not in self.sem.classes:
            self._schema_error(
                f"未定义的本体类型: {type_id}",
                allowed_types=sorted(self.sem.classes),
            )

    def _class_properties(self, type_id: str) -> list[str]:
        self._validate_type(type_id)
        props = self.sem.classes[type_id].get("properties", [])
        return list(props) if not isinstance(props, dict) else list(props)

    def _validate_property(self, type_id: str, name: str) -> None:
        if name in self.OBJECT_COLUMNS:
            return
        props = self._class_properties(type_id)
        if name not in props:
            self._schema_error(
                f"{type_id} 不允许的属性: {name}",
                type=type_id,
                allowed_properties=props,
                allowed_object_columns=sorted(self.OBJECT_COLUMNS),
            )
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            self._schema_error(
                f"非法属性名: {name}", type=type_id, allowed_properties=props,
            )

    def _property_expr(self, type_id: str, name: str) -> str:
        self._validate_property(type_id, name)
        if name in self.OBJECT_COLUMNS:
            return f"o.{name}"
        return f"json_extract(o.props, '$.{name}')"

    def _compile_filters(self, type_id: str, where: Any,
                         args: list) -> list[str]:
        if where in (None, {}, []):
            return []
        if isinstance(where, dict):
            where = [
                {"property": key, "op": "eq", "value": value}
                for key, value in where.items()
            ]
        if not isinstance(where, list):
            self._schema_error(
                "where 必须是过滤对象或过滤数组", allowed_ops=sorted(self.FILTER_OPS),
            )
        clauses: list[str] = []
        for item in where:
            if not isinstance(item, dict):
                self._schema_error(
                    "where 的每一项必须是对象",
                    allowed_ops=sorted(self.FILTER_OPS),
                )
            prop = str(
                item.get("property") or item.get("field") or item.get("key") or ""
            )
            if not prop:
                self._schema_error(
                    "过滤条件缺少 property",
                    type=type_id,
                    allowed_properties=self._class_properties(type_id),
                )
            op = str(item.get("op") or item.get("operator") or "eq").lower()
            if op not in self.FILTER_OPS:
                self._schema_error(
                    f"不支持的过滤操作符: {op}",
                    allowed_ops=sorted(self.FILTER_OPS),
                )
            expr = self._property_expr(type_id, prop)
            value = item.get("value")
            if op == "is_null":
                clauses.append(f"{expr} IS NULL")
            elif op == "is_not_null":
                clauses.append(f"{expr} IS NOT NULL")
            elif op in ("in", "not_in"):
                if not isinstance(value, list) or not value:
                    self._schema_error(
                        f"{op} 的 value 必须是非空数组",
                        property=prop, allowed_ops=sorted(self.FILTER_OPS),
                    )
                placeholders = ",".join("?" for _ in value)
                keyword = "IN" if op == "in" else "NOT IN"
                clauses.append(f"{expr} {keyword} ({placeholders})")
                args.extend(value)
            elif op == "contains":
                clauses.append(f"{expr} LIKE ?")
                args.append(f"%{value}%")
            elif op == "starts_with":
                clauses.append(f"{expr} LIKE ?")
                args.append(f"{value}%")
            elif op in ("gt", "gte", "lt", "lte"):
                sql_op = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
                clauses.append(f"{expr} {sql_op} ?")
                args.append(value)
            elif op == "ne":
                clauses.append(f"{expr} <> ?")
                args.append(value)
            else:
                clauses.append(f"{expr} = ?")
                args.append(value)
        return clauses

    def _compile_order_by(self, type_id: str, order_by: Any) -> str:
        if not order_by:
            return ""
        if isinstance(order_by, str):
            order_by = [order_by]
        if not isinstance(order_by, list):
            self._schema_error(
                "order_by 必须是字符串或字符串数组",
                allowed_properties=self._class_properties(type_id),
            )
        parts = []
        for raw in order_by:
            text = str(raw).strip()
            if not text:
                continue
            direction = "ASC"
            if " " in text:
                text, raw_direction = text.rsplit(" ", 1)
                if raw_direction.lower() not in ("asc", "desc"):
                    self._schema_error(
                        f"非法排序方向: {raw_direction}",
                        allowed_directions=["asc", "desc"],
                    )
                direction = raw_direction.upper()
            expr = self._property_expr(type_id, text.strip())
            parts.append(f"{expr} {direction}")
        return ", ".join(parts)

    def schema(self, question: str | None = None,
               types: list[str] | None = None,
               top_k: int = 12, depth: int = 2,
               fee_top_k: int = 4) -> dict:
        def load():
            return self.sem.schema(
                question=question, types=types, top_k=top_k,
                depth=depth, fee_top_k=fee_top_k, closure="strong",
            )

        return self._record(
            "ontology.schema",
            {"question": question, "types": types, "top_k": top_k,
             "depth": depth},
            "本体",
            load,
        )

    def catalog(self, group_ids: list[str] | None = None,
                budget: int | None = None,
                aggregate_ids: list[str] | None = None,
                context_ids: list[str] | None = None) -> dict:
        return self._record(
            "ontology.catalog",
            {"group_ids": group_ids, "budget": budget,
             "aggregate_ids": aggregate_ids, "context_ids": context_ids},
            "本体",
            lambda: self.sem.catalog(
                group_ids=group_ids, budget=budget,
                aggregate_ids=aggregate_ids, context_ids=context_ids,
            ),
        )

    def check_answerable(self, question: str,
                         terms: list[str] | None = None) -> dict:
        """问句级可答性检查：这个问题在本体与事实里答得出来吗？缺什么？

        借鉴 selfgraph 的 check_answerable —— 回答之前先判断图里有没有答案，
        让「不知道」成为一等公民，而不是让模型编一个。纯确定性，不调用大模型。
        """
        from freight.answerability import check_answerable
        return self._record(
            "ontology.check_answerable",
            {"question": question, "terms": terms},
            "本体",
            lambda: check_answerable(self._runtime(), question, terms),
        )

    def object_type_search(self, question: str, top_k: int = 8,
                           group_ids: list[str] | None = None) -> dict:
        return self._record(
            "ontology.object_type_search",
            {"question": question, "top_k": top_k, "group_ids": group_ids},
            "本体",
            lambda: self.sem.object_type_search(
                question, top_k=top_k, group_ids=group_ids,
            ),
        )

    def state_machine(self, object_type: str | None = None,
                      object_id: str | None = None) -> dict:
        """状态机读取（本体原语 ontology.state_machine）。

        只读：返回状态集、起点/终态/外部状态、迁移边（带稳定 id）与可达集；
        给 object_id 时再附当前状态、可用迁移与已走过的状态轨迹。
        放在事实层实现（store + sem 都在这里），引擎与 REST 共用这一份。
        """
        return self._record(
            "ontology.state_machine",
            {"object_type": object_type, "object_id": object_id},
            "本体",
            lambda: self._state_machine_view(object_type, object_id),
        )

    def state_history(self, object_id: str) -> dict:
        """对象状态轨迹：只取「状态变更」事件（事件类型 ∈ 该类状态集）。

        events 表里混着证据事件、并发测试事件等；不按状态集过滤就会把不相干
        的事件当成状态变化 —— 轨迹必须是状态序列，不是事件流水。
        """
        row = self.store.get_row(object_id)
        if row is None:
            raise BusinessReject(f"对象不存在: {object_id}", "OBJECT_NOT_FOUND")
        row = self.decode(row)
        machine = self.sem.state_machine(row["type"])
        cls = self.sem.classes.get(row["type"], {})
        statuses = set((machine or {}).get("statuses") or cls.get("statuses") or [])
        rows = self.store.all(
            "SELECT * FROM events WHERE object_id=? ORDER BY occurred_at, id",
            (object_id,))
        steps = []
        for r in rows:
            if r["event_type"] not in statuses:
                continue
            steps.append({
                "at": r["occurred_at"],
                "status": r["event_type"],
                "actor": r["actor"],
                "payload": json.loads(r["payload"]),
            })
        return {
            "object_id": object_id,
            "type": row["type"],
            "current_status": row["status"],
            "statuses": sorted(statuses),
            "steps": steps,
            "step_count": len(steps),
            "machine": machine,
        }

    def _state_machine_view(self, object_type: str | None = None,
                            object_id: str | None = None) -> dict:
        if object_id:
            history = self.state_history(object_id)
            machine = history.get("machine")
            if machine is None:
                return {"object_id": object_id, "type": history["type"],
                        "machine": None,
                        "note": f"{history['type']} 未声明状态机"}
            available = [t for t in machine["transitions"]
                         if history["current_status"] in t["from"]]
            return {
                "object_id": object_id,
                "type": history["type"],
                "current_status": history["current_status"],
                "available_transitions": available,
                "history": history["steps"],
                "machine": machine,
            }
        if object_type:
            return {"object_type": object_type,
                    "machine": self.sem.state_machine(object_type)}
        return self.sem.state_machines_view()

    def schema_search_around(self, type_ids: list[str],
                             link_type_ids: list[str] | None = None,
                             direction: str = "both") -> dict:
        return self._record(
            "ontology.search_around",
            {"type_ids": type_ids, "link_type_ids": link_type_ids,
             "direction": direction, "scope": "schema"},
            "本体",
            lambda: self.sem.schema_search_around(
                type_ids, link_type_ids=link_type_ids, direction=direction,
            ),
        )

    def paths(self, from_type: str, to_type: str,
              max_depth: int = 4, limit: int = 10) -> list[dict]:
        def load():
            return self.sem.paths(
                from_type=from_type, to_type=to_type,
                max_depth=max_depth, limit=limit,
            )

        return self._record(
            "ontology.path",
            {"from_type": from_type, "to_type": to_type,
             "max_depth": max_depth, "limit": limit},
            "本体",
            load,
        )

    def _semantic_version(self) -> str:
        return str(self.sem.raw.get("meta", {}).get("version") or "")

    def _object_page(self, nodes: list[dict], query: dict, limit: int,
                     token: str | None) -> dict:
        # 保持查询层已经确定的顺序（含 order_by 与类型/ID 破平），
        # 分页切片必须与一次性结果同序。不能在这里重新排序。
        ordered = list(nodes)
        snapshot = snapshot_digest([
            [item["object_id"], item["updated_at"]] for item in ordered
        ])
        page = page_slice(
            [item["object_id"] for item in ordered], query,
            self._semantic_version(), snapshot, limit, token,
        )
        by_id = {item["object_id"]: item for item in ordered}
        page["objects"] = [by_id[object_id] for object_id in page["object_ids"]]
        return page

    def query_objects(self, type_id: str, where: Any = None,
                      select: list[str] | None = None,
                      order_by: Any = None,
                      limit: int = 100,
                      next_page_token: str | None = None,
                      query_tree: Any = None) -> dict:
        self._validate_type(type_id)
        if query_tree:
            # 组合式 Object Set 查询（E4）：query_tree 优先，type 仅做工具参数合法性校验
            return self._record(
                "ontology.query",
                {"type": type_id, "query_tree": query_tree},
                "组合式 Object Set 查询",
                lambda: self._query_tree(query_tree),
            )
        select_list = [str(item) for item in (select or [])]
        for name in select_list:
            self._validate_property(type_id, name)
        size = max(1, min(int(limit), 200))
        query = {
            "type": type_id,
            "where": where,
            "select": select_list or None,
            "order_by": order_by,
        }

        def load():
            args: list = [type_id]
            clauses = ["o.type=?"]
            clauses.extend(self._compile_filters(type_id, where, args))
            sql = "SELECT o.* FROM objects o WHERE " + " AND ".join(clauses)
            order = self._compile_order_by(type_id, order_by)
            if order:
                sql += f" ORDER BY {order}, o.type ASC, o.object_id ASC"
            else:
                sql += " ORDER BY o.type ASC, o.object_id ASC"
            sql += " LIMIT ?"
            args.append(1001)
            rows = self.store.all(sql, tuple(args))
            truncated = len(rows) > 1000
            objects = []
            for row in rows[:1000]:
                node = self._node(row)
                if select_list:
                    node["properties"] = {
                        key: value for key, value in node["properties"].items()
                        if key in select_list
                    }
                    node["selected"] = select_list
                objects.append(node)
            page = self._object_page(
                objects, query, size, next_page_token,
            )
            return {
                "type": type_id,
                "object_set_id": page["object_set_id"],
                "snapshot_id": page["snapshot_id"],
                "semantic_version": page["semantic_version"],
                "count": page["count"],
                "page_count": page["page_count"],
                "objects": page["objects"],
                "next_page_token": page["next_page_token"],
                "truncated": truncated,
                "query": {**query, "limit": size},
            }

        return self._record(
            "ontology.query",
            {"type": type_id, "where": where, "select": select_list,
             "order_by": order_by, "limit": size,
             "next_page_token": next_page_token},
            "本体图查询",
            load,
        )

    def _query_tree(self, query_tree: Any) -> dict:
        """组合式 Object Set 查询（E4）：校验+求值走 freight.object_query。"""
        from freight.object_query import ObjectQueryEngine
        ext = None
        try:  # 扩展注册表可选（值类型校验增强；缺失时 legacy 属性跳过类型校验）
            from enhancements.registry import ExtensionRegistry
            ext = ExtensionRegistry(base_classes=self.sem.classes)
        except ImportError:
            pass
        engine = ObjectQueryEngine(self.sem, ext)
        return engine.execute(dict(query_tree), self.store)

    def sql(self, sql: str, params: dict | None = None) -> dict:
        """Ontology SQL（对标 Palantir Ontology SQL）：SQL 编译到查询树 IR，
        与 ontology.query 走同一执行引擎（TBox/安全/聚合全部复用）。"""
        from freight.sql_frontend import compile_sql, project

        def load():
            compiled = compile_sql(sql, params)
            projection = compiled.pop("_projection", [{"kind": "star"}])
            return project(self._query_tree(compiled), projection)

        return self._record(
            "ontology.sql",
            {"sql": sql, "params": params},
            "本体 SQL 查询",
            load,
        )

    def _traverse(self, object_ids: list[str],
                  predicates: list[str] | None,
                  direction: str, depth: int,
                  max_objects: int = 2000) -> dict:
        rows = {object_id: self._row(object_id) for object_id in object_ids}
        missing = [object_id for object_id, row in rows.items() if not row]
        if missing:
            raise BusinessReject(
                f"外部系统未找到对象: {', '.join(missing)}", "OBJECT_NOT_FOUND"
            )
        objects: dict[str, dict] = {
            object_id: self._node(rows[object_id])
            for object_id in object_ids
        }
        relations: dict[int, dict] = {}
        edges: list[dict] = []
        visited = set(object_ids)
        frontier = list(object_ids)
        truncated = False
        for _ in range(depth):
            next_frontier: list[str] = []
            for current in frontier:
                for rel in self._relation_rows(current, direction, predicates):
                    relations[rel["id"]] = rel
                    if rel["subject_id"] == current:
                        other = rel["object_id"]
                        rel_direction = "out"
                        neighbor_type = rel["object_type"]
                    elif rel["object_id"] == current:
                        other = rel["subject_id"]
                        rel_direction = "in"
                        neighbor_type = rel["subject_type"]
                    else:
                        continue
                    edges.append({
                        "link_type_id": rel["predicate"],
                        "direction": rel_direction,
                        "neighbor_type": neighbor_type,
                        "relation_id": rel["id"],
                        "object_id": other,
                    })
                    if other not in objects:
                        if len(objects) >= max_objects:
                            truncated = True
                            continue
                        other_row = self._row(other)
                        if not other_row:
                            continue
                        objects[other] = self._node(other_row)
                    if other not in visited:
                        visited.add(other)
                        next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break
        ordered = sorted(
            objects.values(), key=lambda item: (item["type"], item["object_id"])
        )
        ordered_relations = sorted(
            relations.values(),
            key=lambda item: (
                item["subject_id"], item["predicate"], item["object_id"],
                item["valid_from"] or "",
            ),
        )
        return {
            "root": object_ids[0] if len(object_ids) == 1 else None,
            "roots": object_ids,
            "depth": depth,
            "link_type_ids": predicates or [],
            "objects": ordered,
            "relations": ordered_relations,
            "link_type_summary": summarize_links(edges),
            "counts": {
                "objects": len(ordered),
                "relations": len(ordered_relations),
            },
            "truncated": truncated,
        }

    def _paginate_subgraph(self, result: dict, query: dict,
                           limit: int, token: str | None) -> dict:
        objects = result["objects"]
        relations = result["relations"]
        snapshot = snapshot_digest(
            [[item["object_id"], item["updated_at"]] for item in objects]
            + [["relation", item["id"], item["valid_from"]]
               for item in relations]
        )
        page = page_slice(
            [item["object_id"] for item in objects], query,
            self._semantic_version(), snapshot, limit, token,
        )
        by_id = {item["object_id"]: item for item in objects}
        result["objects"] = [
            by_id[object_id] for object_id in page["object_ids"]
        ]
        result["object_set_id"] = page["object_set_id"]
        result["snapshot_id"] = page["snapshot_id"]
        result["semantic_version"] = page["semantic_version"]
        result["count"] = page["count"]
        result["page_count"] = page["page_count"]
        result["next_page_token"] = page["next_page_token"]
        result["counts"] = {
            "objects": page["count"],
            "relations": len(relations),
            "returned_objects": page["page_count"],
        }
        return result

    def traverse_objects(self, object_id: str, predicate: str | None = None,
                         direction: str = "out", depth: int = 1,
                         limit: int = 200,
                         next_page_token: str | None = None) -> dict:
        predicates = [predicate] if predicate else []
        unknown = [p for p in predicates if p not in self.sem.relations]
        if unknown:
            self._schema_error(
                f"未定义的关系: {', '.join(unknown)}",
                allowed_relations=sorted(self.sem.relations),
            )
        if direction not in ("out", "in", "both"):
            self._schema_error(
                f"非法的遍历方向: {direction}",
                allowed_directions=["out", "in", "both"],
            )
        depth = max(1, min(int(depth), 5))
        size = max(1, min(int(limit), 500))
        query = {
            "object_id": object_id,
            "predicate": predicate,
            "direction": direction,
            "depth": depth,
        }

        def load():
            result = self._traverse([object_id], predicates, direction, depth)
            return self._paginate_subgraph(
                result, query, size, next_page_token,
            )

        return self._record(
            "ontology.traverse",
            {"object_id": object_id, "predicate": predicate,
             "direction": direction, "depth": depth, "limit": size,
             "next_page_token": next_page_token},
            "本体图查询",
            load,
        )

    def search_around_objects(self, object_ids: list[str],
                              link_type_ids: list[str] | None = None,
                              direction: str = "both", depth: int = 1,
                              limit: int = 200,
                              next_page_token: str | None = None) -> dict:
        """从锚点对象集合沿 link type 做 search-around，不预计算全局闭包。"""
        roots = [str(item) for item in dict.fromkeys(object_ids or []) if item]
        if not roots:
            raise BusinessReject("search_around 至少需要一个锚点对象", "ARGUMENT_ERROR")
        if isinstance(link_type_ids, str):
            link_type_ids = [link_type_ids]
        predicates = [str(item) for item in (link_type_ids or []) if item]
        unknown = [p for p in predicates if p not in self.sem.relations]
        if unknown:
            self._schema_error(
                f"未定义的关系: {', '.join(unknown)}",
                allowed_relations=sorted(self.sem.relations),
            )
        if direction not in ("out", "in", "both"):
            self._schema_error(
                f"非法的遍历方向: {direction}",
                allowed_directions=["out", "in", "both"],
            )
        depth = max(1, min(int(depth), 5))
        size = max(1, min(int(limit), 500))
        query = {
            "object_ids": roots,
            "link_type_ids": predicates,
            "direction": direction,
            "depth": depth,
        }

        def load():
            result = self._traverse(roots, predicates, direction, depth)
            return self._paginate_subgraph(
                result, query, size, next_page_token,
            )

        return self._record(
            "ontology.search_around",
            {"object_ids": roots, "link_type_ids": predicates,
             "direction": direction, "depth": depth, "limit": size,
             "next_page_token": next_page_token},
            "本体图查询",
            load,
        )

    def aggregate(self, type_id: str, group_by: Any = None,
                  metrics: Any = None, where: Any = None,
                  limit: int = 100) -> dict:
        self._validate_type(type_id)
        group_list = [group_by] if isinstance(group_by, str) else list(group_by or [])
        group_list = [str(item) for item in group_list]
        for name in group_list:
            self._validate_property(type_id, name)

        if metrics is None:
            metrics = [{"op": "count", "as": "count"}]
        elif isinstance(metrics, dict):
            metrics = [
                {
                    "op": op,
                    "property": prop,
                    "as": f"{op}_{prop}",
                }
                for prop, op in metrics.items()
            ]
        if not isinstance(metrics, list):
            self._schema_error(
                "metrics 必须是数组或 {property: op} 对象",
                allowed_ops=sorted(self.AGG_OPS),
            )
        metric_specs = []
        for raw in metrics:
            if not isinstance(raw, dict):
                self._schema_error(
                    "metrics 的每一项必须是对象",
                    allowed_ops=sorted(self.AGG_OPS),
                )
            op = str(raw.get("op") or "count").lower()
            if op not in self.AGG_OPS:
                self._schema_error(
                    f"不支持的聚合操作: {op}",
                    allowed_ops=sorted(self.AGG_OPS),
                )
            prop = raw.get("property")
            alias = str(raw.get("as") or (f"{op}_{prop}" if prop else op))
            if op != "count" and not prop:
                self._schema_error(
                    f"{op} 必须指定 property",
                    allowed_ops=sorted(self.AGG_OPS),
                    allowed_properties=self._class_properties(type_id),
                )
            if prop:
                self._validate_property(type_id, str(prop))
            metric_specs.append({
                "op": op, "property": str(prop) if prop else None, "as": alias,
            })

        def load():
            args: list = [type_id]
            clauses = ["o.type=?"]
            clauses.extend(self._compile_filters(type_id, where, args))
            group_sql = []
            for index, name in enumerate(group_list):
                group_sql.append(
                    f'{self._property_expr(type_id, name)} AS g{index}'
                )
            metric_sql = []
            for index, spec in enumerate(metric_specs):
                if spec["op"] == "count":
                    if spec["property"]:
                        expr = self._property_expr(type_id, spec["property"])
                        metric_sql.append(f"COUNT({expr}) AS m{index}")
                    else:
                        metric_sql.append(f"COUNT(*) AS m{index}")
                else:
                    expr = self._property_expr(type_id, spec["property"])
                    metric_sql.append(
                        f'{spec["op"].upper()}({expr}) AS m{index}'
                    )
            sql = "SELECT " + ", ".join(group_sql + metric_sql)
            sql += " FROM objects o WHERE " + " AND ".join(clauses)
            if group_sql:
                sql += " GROUP BY " + ", ".join(
                    f"g{index}" for index in range(len(group_list))
                )
            sql += " LIMIT ?"
            args.append(max(1, min(int(limit), 500)))
            raw_rows = self.store.all(sql, tuple(args))
            rows = []
            for raw in raw_rows:
                item = {}
                for index, name in enumerate(group_list):
                    item[name] = raw.get(f"g{index}")
                for index, spec in enumerate(metric_specs):
                    item[spec["as"]] = raw.get(f"m{index}")
                rows.append(item)
            return {
                "type": type_id,
                "group_by": group_list,
                "metrics": metric_specs,
                "count": len(rows),
                "rows": rows,
                "query": {
                    "type": type_id, "where": where,
                    "group_by": group_list, "limit": args[-1],
                },
            }

        return self._record(
            "ontology.aggregate",
            {"type": type_id, "group_by": group_list,
             "metrics": metric_specs, "where": where, "limit": limit},
            "本体图查询",
            load,
        )

    def list_fee_items(self, waybill_id: str) -> list[dict]:
        def load():
            rows = self.store.all(
                """SELECT r.object_id FROM relations r
                   WHERE r.subject_id=? AND r.predicate='waybill_produces_fee'
                     AND r.valid_to IS NULL ORDER BY r.object_id""",
                (waybill_id,),
            )
            fees = []
            for rel in rows:
                row = self._row(rel["object_id"])
                if row and row["type"] == "FeeItem":
                    fees.append(self._node(row))
            return fees

        return self._record("billing.list_fee_items", {"waybill_id": waybill_id},
                            "计费中心", load)

    def get_fee_item(self, fee_id: str) -> dict:
        return self._typed(fee_id, "FeeItem", "billing.get_fee_item", "计费中心")

    def fee_source_contracts(self) -> list[dict]:
        """data_sources 中声明了 fee_types 的费用源连接器。

        计费中心只提供费用索引；真正的费用明细按来源系统（TMS/ETC/...）
        分别拉取。没有 fee_types 的映射是类级主数据源，不参与费用分流。
        """
        return [
            item for item in self.sem.data_sources
            if item.get("class") == "FeeItem" and item.get("fee_types")
        ]

    def list_fee_lines(self, waybill_id: str, system: str,
                       fee_types: list[str], tool: str | None = None) -> list[dict]:
        """按来源系统拉取自己负责的费用明细行。

        计费中心返回的是费用索引；真正的明细由各来源系统连接器负责。
        连接器不能越界返回别的系统的费用类型，否则口径归属会串。
        """
        allowed = set(fee_types)

        def load():
            rows = []
            for node in self.list_fee_items(waybill_id):
                props = node["properties"]
                if str(props.get("source_system") or "") != system:
                    continue
                if allowed and str(props.get("fee_type") or "") not in allowed:
                    continue
                rows.append(node)
            return rows

        return self._record(
            tool or f"{system.lower()}.list_fee_lines",
            {"waybill_id": waybill_id, "fee_types": fee_types},
            system or "EXTERNAL", load,
        )

    def collect_fee_sources(self, waybill_id: str) -> dict:
        """费用索引 + 多源明细连接器：返回逐源取数结果与未解析缺口。

        每个连接器只能返回自己声明负责的费用类型；返回不了的费用项不会被
        静默丢弃，而是进入 unresolved_ids，由引擎转成数据缺口 finding。
        """
        index_nodes = self.list_fee_items(waybill_id)
        index = [{
            "object_id": node["object_id"],
            "type": node["type"],
            "name": node["name"],
            "status": node["status"],
            "props": dict(node["properties"]),
        } for node in index_nodes]
        claimed: dict[str, str] = {}
        sources: list[dict] = []
        for contract in self.fee_source_contracts():
            system = str(contract.get("system") or "")
            tool = str(contract.get("tool") or f"{system.lower()}.list_fee_lines")
            fee_types = [str(item) for item in contract.get("fee_types") or []]
            rows = self.list_fee_lines(waybill_id, system, fee_types, tool)
            ids = [row["object_id"] for row in rows]
            for oid in ids:
                claimed[oid] = system
            sources.append({
                "system": system,
                "tool": tool,
                "fee_types": fee_types,
                "fee_ids": ids,
                "fee_count": len(ids),
                "amount": round(sum(
                    float(row["properties"].get("actual_amount") or 0) for row in rows
                ), 2),
            })
        for fee in index:
            fee["source_resolved"] = fee["object_id"] in claimed
            fee["source_contract"] = claimed.get(fee["object_id"])
        return {
            "fees": index,
            "fee_ids": [fee["object_id"] for fee in index],
            "sources": sources,
            "unresolved_ids": [
                fee["object_id"] for fee in index if not fee["source_resolved"]
            ],
        }

    def get_quote(self, quote_id: str) -> dict:
        return self._typed(quote_id, "Quote", "pricing.get_quote", "计价中心")

    def get_quote_for_order(self, order_id: str) -> dict | None:
        """按订单读取当前生效报价单（有 valid_until 时仍以最新版本为准）。"""
        def load():
            rel = self.store.one(
                "SELECT object_id FROM relations WHERE predicate='order_has_quote' "
                "AND subject_id=? AND valid_to IS NULL "
                "ORDER BY valid_from DESC LIMIT 1",
                (order_id,),
            )
            if not rel:
                return None
            row = self._row(rel["object_id"])
            if not row or row["type"] != "Quote":
                return None
            return self._node(row)

        return self._record("pricing.get_quote", {"order_id": order_id},
                            "计价中心", load)

    def list_quote_items(self, quote_id: str) -> list[dict]:
        """按报价单读取逐项明细；报价总额不再充当明细的替代品。"""
        def load():
            rows = self.store.all(
                "SELECT object_id FROM relations WHERE predicate='quote_contains_item' "
                "AND subject_id=? AND valid_to IS NULL ORDER BY object_id",
                (quote_id,),
            )
            items = []
            for rel in rows:
                row = self._row(rel["object_id"])
                if row and row["type"] == "QuoteItem":
                    items.append(self._node(row))
            return items

        return self._record("pricing.list_quote_items", {"quote_id": quote_id},
                            "计价中心", load)

    def _relation_rows(self, object_id: str, direction: str = "both",
                       predicate: str | list[str] | None = None) -> list[dict]:
        clauses = []
        args: list = []
        if direction == "out":
            clauses.append("subject_id=?")
            args.append(object_id)
        elif direction == "in":
            clauses.append("object_id=?")
            args.append(object_id)
        else:
            clauses.append("(subject_id=? OR object_id=?)")
            args += [object_id, object_id]
        predicates = (
            [predicate] if isinstance(predicate, str)
            else [str(item) for item in (predicate or [])]
        )
        if predicates:
            placeholders = ",".join("?" for _ in predicates)
            clauses.append(f"predicate IN ({placeholders})")
            args.extend(predicates)
        clauses.append("valid_to IS NULL")
        rows = self.store.all(
            f"SELECT * FROM relations WHERE {' AND '.join(clauses)} ORDER BY valid_from, id",
            tuple(args),
        )
        out = []
        for rel in rows:
            subject = self._row(rel["subject_id"])
            target = self._row(rel["object_id"])
            definition = self.sem.relations.get(rel["predicate"], {})
            out.append({
                "id": rel["id"],
                "predicate": rel["predicate"],
                "label": definition.get("label", rel["predicate"]),
                "domain": definition.get("domain"),
                "range": definition.get("range"),
                "subject_id": rel["subject_id"],
                "subject_name": subject["name"] if subject else rel["subject_id"],
                "subject_type": rel["subject_type"],
                "object_id": rel["object_id"],
                "object_name": target["name"] if target else rel["object_id"],
                "object_type": rel["object_type"],
                "source_system": rel["source_system"],
                "valid_from": rel["valid_from"],
                "valid_to": rel["valid_to"],
                "created_at": rel["created_at"],
                "active": rel["valid_to"] is None,
            })
        return out

    def list_relations(self, object_id: str, direction: str = "both",
                       predicate: str | None = None) -> list[dict]:
        return self._record(
            "graph.list_relations",
            {"object_id": object_id, "direction": direction, "predicate": predicate},
            "图查询 / MCP",
            lambda: self._relation_rows(object_id, direction, predicate),
        )

    def _evidence_rows(self, object_ids: list[str]) -> list[dict]:
        if not object_ids:
            return []
        placeholders = ",".join("?" for _ in object_ids)
        rows = self.store.all(
            f"SELECT * FROM evidence WHERE object_id IN ({placeholders}) "
            "ORDER BY occurred_at, evidence_id",
            tuple(object_ids),
        )
        for ev in rows:
            ev["payload"] = json.loads(ev["payload"])
            owner = self._row(ev["object_id"])
            ev["object_name"] = owner["name"] if owner else ev["object_id"]
            ev["object_type"] = owner["type"] if owner else None
        return rows

    def list_evidence(self, object_ids: list[str]) -> list[dict]:
        return self._record("evidence.list", {"object_ids": object_ids},
                            "证据中心", lambda: self._evidence_rows(object_ids))

    def _event_rows(self, object_ids: list[str]) -> list[dict]:
        if not object_ids:
            return []
        placeholders = ",".join("?" for _ in object_ids)
        rows = self.store.all(
            f"SELECT * FROM events WHERE object_id IN ({placeholders}) "
            "ORDER BY occurred_at, id",
            tuple(object_ids),
        )
        for event in rows:
            event["payload"] = json.loads(event["payload"])
            owner = self._row(event["object_id"])
            event["object_name"] = owner["name"] if owner else event["object_id"]
            event["object_type"] = owner["type"] if owner else None
        return rows

    def list_events(self, object_ids: list[str]) -> list[dict]:
        return self._record("tms.list_events", {"object_ids": object_ids},
                            "TMS", lambda: self._event_rows(object_ids))

    def _audit_rows(self, object_ids: list[str] | None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM audit"
        args: list = []
        if object_ids:
            placeholders = ",".join("?" for _ in object_ids)
            sql += f" WHERE object_id IN ({placeholders})"
            args += object_ids
        sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        rows = self.store.all(sql, tuple(args))
        for row in rows:
            row["request"] = json.loads(row["request"])
            row["result"] = json.loads(row["result"])
            action = self.sem.actions.get(row["action_id"], {})
            row["action_label"] = action.get("label", row["action_id"])
        return rows

    def list_audit(self, object_ids: list[str] | None = None,
                   limit: int = 100) -> list[dict]:
        return self._record("audit.list", {"object_ids": object_ids, "limit": limit},
                            "审计中心", lambda: self._audit_rows(object_ids, limit))

    def list_settlements(self, waybill_id: str) -> list[dict]:
        def load():
            fee_ids = [rel["object_id"] for rel in self.store.all(
                """SELECT object_id FROM relations
                   WHERE subject_id=? AND predicate='waybill_produces_fee'
                     AND valid_to IS NULL""",
                (waybill_id,),
            )]
            settlement_ids: set[str] = set()
            for fee_id in fee_ids:
                for rel in self.store.all(
                    """SELECT object_id FROM relations
                       WHERE subject_id=? AND predicate='fee_included_in_settlement'
                         AND valid_to IS NULL""",
                    (fee_id,),
                ):
                    settlement_ids.add(rel["object_id"])
            nodes = []
            for sid in sorted(settlement_ids):
                row = self._row(sid)
                if row and row["type"] == "Settlement":
                    nodes.append(self._node(row))
            return nodes

        return self._record("finance.get_settlement", {"waybill_id": waybill_id},
                            "结算系统", load)

    def list_invoices(self, settlement_id: str) -> list[dict]:
        def load():
            rows = self.store.all(
                """SELECT object_id FROM relations
                   WHERE subject_id=? AND predicate='settlement_generates_invoice'
                     AND valid_to IS NULL""",
                (settlement_id,),
            )
            nodes = []
            for rel in rows:
                row = self._row(rel["object_id"])
                if row and row["type"] == "Invoice":
                    nodes.append(self._node(row))
            return nodes

        return self._record("finance.get_invoice", {"settlement_id": settlement_id},
                            "发票系统", load)

    def list_exceptions(self, object_id: str) -> list[dict]:
        def load():
            rows = self.store.all(
                """SELECT object_id FROM relations
                   WHERE subject_id=? AND predicate='waybill_has_exception'
                     AND valid_to IS NULL""",
                (object_id,),
            )
            nodes = []
            for rel in rows:
                row = self._row(rel["object_id"])
                if row and row["type"] == "ExceptionTicket":
                    nodes.append(self._node(row))
            return nodes

        return self._record("cs.list_exceptions", {"object_id": object_id},
                            "客服工单", load)

    # ------------------------------------------------------------ 组合查询
    def query_waybill(self, waybill_id: str, include_context: bool = False) -> dict:
        """一次真实的“事实现查现用”：运单 → 费用项，结果只在内存中组装。"""
        start = self.begin()
        waybill = self.get_waybill(waybill_id)
        fees = self.list_fee_items(waybill_id)
        edges = self.list_relations(waybill_id, "out", "waybill_produces_fee")
        node_ids = [waybill["object_id"]] + [fee["object_id"] for fee in fees]
        evidence = self.list_evidence(node_ids)
        events = self.list_events(node_ids)
        audit = self.list_audit(node_ids, 50)

        nodes = [waybill] + fees
        if include_context:
            order_ids = [rel["object_id"] for rel in self.list_relations(
                waybill_id, "out", "order_generates_waybill")]
            for order_id in order_ids:
                order = self.get_order(order_id)
                nodes.append(order)
                context_edges = self.list_relations(order_id, "out", "order_generates_waybill")
                edges.extend(context_edges)

        trace = self.since(start)
        return {
            "generated_at": now_iso(),
            "waybill_id": waybill_id,
            "storage": "external-mcp",
            "source": "MCP / TMS · 计费中心 · 证据中心",
            "objects": nodes,
            "relations": edges,
            "evidence": evidence,
            "events": events,
            "audit": audit,
            "trace": trace,
            "stats": {
                "nodes": len(nodes),
                "edges": len(edges),
                "calls": len(trace),
                "evidence": len(evidence),
                "events": len(events),
                "audit": len(audit),
            },
        }

    def facts_overview(self) -> dict:
        """跨系统全量快照，仅供调试 / 总览；生产环境应改为逐类查询。"""
        start = self.begin()
        objects = self.list_objects()
        relations = self.list_relations_for_overview()
        evidence = self.list_evidence([obj["object_id"] for obj in objects])
        events = self.list_events([obj["object_id"] for obj in objects])
        audit = self.list_audit(None, 500)
        return {
            "objects": objects,
            "relations": relations,
            "evidence": evidence,
            "events": events,
            "audit": audit,
            "trace": self.since(start),
        }

    def list_relations_for_overview(self) -> list[dict]:
        def load():
            rows = self.store.all(
                "SELECT * FROM relations WHERE valid_to IS NULL ORDER BY valid_from, id"
            )
            out = []
            for rel in rows:
                subject = self._row(rel["subject_id"])
                target = self._row(rel["object_id"])
                definition = self.sem.relations.get(rel["predicate"], {})
                out.append({
                    "id": rel["id"],
                    "predicate": rel["predicate"],
                    "label": definition.get("label", rel["predicate"]),
                    "domain": definition.get("domain"),
                    "range": definition.get("range"),
                    "subject_id": rel["subject_id"],
                    "subject_name": subject["name"] if subject else rel["subject_id"],
                    "subject_type": rel["subject_type"],
                    "object_id": rel["object_id"],
                    "object_name": target["name"] if target else rel["object_id"],
                    "object_type": rel["object_type"],
                    "source_system": rel["source_system"],
                    "valid_from": rel["valid_from"],
                    "valid_to": rel["valid_to"],
                    "created_at": rel["created_at"],
                    "active": rel["valid_to"] is None,
                })
            return out

        return self._record("graph.list_relations", {"scope": "all"}, "图查询 / MCP", load)

    # ------------------------------------------------------------ MCP 入口
    def tools(self, type_id: str | None = None) -> dict:
        surface = build_tool_surface(self.sem)
        if type_id:
            surface = filter_tool_surface(surface, type_id)
        return {
            "transport": "MCP (demo adapter)",
            "note": (
                "模型工具面由通用图查询原语、semantic_layer.json 的动作和"
                "确定性函数生成；底层外部工具仅作内部实现，不直接暴露。"
                + (f" 已按对象类型 {type_id} 裁剪。" if type_id else "")
            ),
            "type": type_id,
            "tool_count": len(surface),
            "tools": surface,
        }

    def tools_for(self, type_id: str) -> dict:
        """对象级能力集：当前对象能用的通用原语、确定性函数和动作。"""
        if type_id not in self.sem.classes:
            raise BusinessReject(f"未定义的对象类型: {type_id}", "SCHEMA_ERROR")
        return self.tools(type_id)

    def call(self, tool: str, arguments: dict | None = None) -> dict:
        args = arguments or {}
        start = self.begin()
        handlers: dict[str, Callable[[], Any]] = {
            "ontology.catalog": lambda: self.catalog(
                args.get("group_ids"), args.get("budget"),
                args.get("aggregate_ids"), args.get("context_ids")),
            "ontology.check_answerable": lambda: self.check_answerable(
                args["question"], args.get("terms")),
            "ontology.object_type_search": lambda: self.object_type_search(
                args["question"], args.get("top_k", 8), args.get("group_ids")),
            "ontology.search_around": lambda: self.search_around_objects(
                args.get("object_ids") or ([args["object_id"]] if args.get("object_id") else []),
                args.get("link_type_ids") or args.get("predicate"),
                args.get("direction", "both"), args.get("depth", 1),
                args.get("limit", 200), args.get("next_page_token")),
            "ontology.schema": lambda: self.schema(
                args.get("question"), args.get("types"),
                args.get("top_k", 12), args.get("depth", 2)),
            "ontology.path": lambda: self.paths(
                args["from_type"], args["to_type"],
                args.get("max_depth", 4), args.get("limit", 10)),
            "ontology.sql": lambda: self.sql(
                args.get("sql", ""), args.get("params")),
            "ontology.state_machine": lambda: self.state_machine(
                args.get("object_type"), args.get("object_id")),
            "ontology.query": lambda: self.query_objects(
                args["type"], args.get("where"), args.get("select"),
                args.get("order_by"), args.get("limit", 100),
                args.get("next_page_token"), args.get("query_tree")),
            "ontology.traverse": lambda: self.traverse_objects(
                args["object_id"], args.get("predicate"),
                args.get("direction", "out"), args.get("depth", 1),
                args.get("limit", 200), args.get("next_page_token")),
            "ontology.aggregate": lambda: self.aggregate(
                args["type"], args.get("group_by"), args.get("metrics"),
                args.get("where"), args.get("limit", 100)),
            "tms.get_waybill": lambda: self.get_waybill(args["waybill_id"]),
            "tms.get_order": lambda: self.get_order(args["order_id"]),
            "tms.get_driver": lambda: self.get_driver(args["driver_id"]),
            "tms.get_vehicle": lambda: self.get_vehicle(args["vehicle_id"]),
            "crm.get_shipper": lambda: self.get_shipper(args["shipper_id"]),
            "billing.list_fee_items": lambda: self.list_fee_items(args["waybill_id"]),
            "billing.get_fee_item": lambda: self.get_fee_item(args["fee_id"]),
            "tms.list_charge_lines": lambda: self.list_fee_lines(
                args["waybill_id"], "TMS",
                args.get("fee_types") or [
                    "base", "mileage", "waiting", "loading", "insurance", "other"],
                "tms.list_charge_lines"),
            "etc.list_toll_transactions": lambda: self.list_fee_lines(
                args["waybill_id"], "ETC", ["highway"],
                "etc.list_toll_transactions"),
            "pricing.get_quote": lambda: self.get_quote(args["quote_id"]),
            "pricing.get_quote_for_order": lambda: self.get_quote_for_order(
                args["order_id"]),
            "pricing.list_quote_items": lambda: self.list_quote_items(
                args["quote_id"]),
            "graph.list_relations": lambda: self.list_relations(
                args["object_id"], args.get("direction", "both"), args.get("predicate")),
            "evidence.list": lambda: self.list_evidence(args.get("object_ids", [])),
            "tms.list_events": lambda: self.list_events(args.get("object_ids", [])),
            "audit.list": lambda: self.list_audit(args.get("object_ids"), args.get("limit", 100)),
            "finance.get_settlement": lambda: self.list_settlements(args["waybill_id"]),
            "finance.get_invoice": lambda: self.list_invoices(args["settlement_id"]),
            "cs.list_exceptions": lambda: self.list_exceptions(args["object_id"]),
            "tms.list_objects": lambda: self.list_objects(
                args.get("type"), args.get("q"), args.get("limit", 200)),
            "tms.get_object": lambda: self.get_object(args["object_id"]),
            "action.plan": lambda: self._record(
                "action.plan", args, "本体动作",
                lambda: self._runtime().action_preflight(
                    args["object_id"], args.get("action_id"))),
            "action.execute": lambda: self._record(
                "action.execute", args, "本体动作",
                lambda: self._runtime().execute_action(
                    args["action_id"], args["object_id"],
                    args.get("actor", "agent"), args["role"],
                    args.get("payload") or {}, args.get("idempotency_key"))),
        }
        for function in self.sem.raw.get("functions", []):
            def handler(fn=function):
                params = fn.get("params") or []
                missing = [
                    param["name"] for param in params
                    if param.get("required") and param["name"] not in args
                ]
                if missing:
                    raise BusinessReject(
                        f'函数 {fn["id"]} 缺少必填参数: {", ".join(missing)}',
                        "ARGUMENT_ERROR",
                    )
                kwargs = {
                    param["name"]: args.get(param["name"])
                    for param in params if param["name"] in args
                }
                from freight.tiers import tier_for, with_tiers
                tier = tier_for(self.sem, f'function.{fn["id"]}')

                def _call(fn=fn, kwargs=kwargs, tier=tier):
                    out = getattr(self._runtime(), fn["implementation"])(**kwargs)
                    return with_tiers(out, [tier])

                return self._record(
                    f'function.{fn["id"]}', args, "确定性函数", _call)
            handlers[f'function.{function["id"]}'] = handler
        handler = handlers.get(tool)
        if not handler:
            raise BusinessReject(f"未知 MCP 工具: {tool}", "MCP_TOOL_NOT_FOUND")
        result = handler()
        return {"tool": tool, "result": result, "trace": self.since(start)}