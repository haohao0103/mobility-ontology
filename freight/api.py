"""货拉拉订单本体 REST 接口：/freight/*"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .agent import LLMError
from .concurrency import (
    ConcurrencyLimit, SessionBusy, WRITE_LOCK, reasoning_slot,
)
from .engine import FreightSystem
from .semantic import BusinessReject, OntologyViolation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 外部业务事实的演示替身；本体知识在 freight/semantic_layer.json。
DEFAULT_FACT_DB = os.path.join(ROOT, "runs", "freight_facts.db")
WB_DEFAULT = "waybill:WB20261105001"  # 演示剧本运单，负向实验默认目标
# 写事务锁与 Agent 运行时共用同一把：普通 HTTP 写路径和 Agent 内部动作
# 不会各自为战，SQLite 单写连接始终只有一个事务在推进。
_LOCK = WRITE_LOCK
_SYSTEM: FreightSystem | None = None


def get_system() -> FreightSystem:
    global _SYSTEM
    if _SYSTEM is None:
        # 首次两个请求同时进来时不能各构造一个 FreightSystem（会重复执行剧本
        # 写库），初始化也放进同一把写锁里做双检。
        with _LOCK:
            if _SYSTEM is None:
                # with_scenario=True：服务启动即带上核心业务剧本（15 个迁移动作真实执行），
                # 于是运行中的服务与门禁第 26 项度量的是同一份数据。
                _SYSTEM = FreightSystem(DEFAULT_FACT_DB, fresh=False, seed=True,
                                        with_scenario=True)
    return _SYSTEM


class ActionIn(BaseModel):
    object_id: str
    actor: str
    role: str
    payload: dict = {}
    idempotency_key: Optional[str] = None


class MCPCallIn(BaseModel):
    tool: str
    arguments: dict = {}


class VersionIn(BaseModel):
    version: str


router = APIRouter(prefix="/freight", tags=["freight-ontology"])


def guard(fn):
    with _LOCK:
        return translate(fn)


def agent_guard(fn):
    # Agent 会多次调用大模型与 MCP，不能长时间占用全局锁，避免阻塞其他接口。
    # 写库动作在 FreightSystem.execute_action 内部借用写锁串行。
    return translate(fn)


def reasoning_guard(fn):
    # 给真正调用大模型的入口限流；名额拿不到立即 429，不把请求挂到超时。
    def run():
        with reasoning_slot():
            return fn()
    return translate(run)


def translate(fn):
    try:
        return fn()
    except SessionBusy as e:
        raise HTTPException(status_code=409, detail={
            "code": "SESSION_BUSY", "message": str(e),
        })
    except ConcurrencyLimit as e:
        raise HTTPException(status_code=429, detail={
            "code": "REASONING_BUSY", "message": str(e),
        })
    except LLMError as e:
        raise HTTPException(status_code=503, detail={
            "code": "LLM_UNAVAILABLE", "message": str(e),
        })
    except BusinessReject as e:
        status = 404 if e.code in ("OBJECT_NOT_FOUND", "VERSION_NOT_FOUND") else (
            403 if e.code == "FORBIDDEN_ROLE" else 409
        )
        raise HTTPException(status_code=status, detail={
            "code": e.code, "message": str(e), "details": e.details,
        })
    except OntologyViolation as e:
        raise HTTPException(status_code=422, detail={
            "code": "ONTOLOGY_VIOLATION", "message": str(e),
        })
    except ValueError as e:
        raise HTTPException(status_code=422, detail={
            "code": "VALIDATION_ERROR", "message": str(e),
        })


@router.get("/health")
def health():
    def fn():
        sys = get_system()
        return {
            "ok": True,
            "knowledge": {
                "source": "freight/semantic_layer.json",
                "version": sys.active_version,
                "classes": len(sys.sem.classes),
                "relations": len(sys.sem.relations),
                "rules": len(sys.sem.rules),
                "actions": len(sys.sem.actions),
            },
            "facts": {
                "transport": "MCP / API",
                "persisted_by_ontology": False,
                "demo_adapter_db": DEFAULT_FACT_DB,
                "stats": sys.stats(),
            },
        }
    return guard(fn)


@router.get("/storage")
def storage():
    return guard(lambda: get_system().storage_inventory())


@router.get("/meta")
def meta():
    def fn():
        sys = get_system()
        return {
            **sys.sem.dictionary(),
            "active_version": sys.active_version,
            "storage": {
                "knowledge": sys.sem.path,
                "facts": "MCP / API 按需查询，不写入本体库",
            },
            "knowledge_stats": {
                "classes": len(sys.sem.classes),
                "relations": len(sys.sem.relations),
                "rules": len(sys.sem.rules),
                "actions": len(sys.sem.actions),
            },
            "demo": {
                "shipper": "shipper:S001",
                "order": "order:HL20261105001",
                "waybill": "waybill:WB20261105001",
                "driver": "driver:D001",
                "vehicle": "vehicle:V001",
                "fee": "fee:F003",
                "settlement": "settlement:ST20261105001",
                "invoice": "invoice:INV20261105001",
                "exception": "exception:EX20261105001",
                "reassign_allowed": "waybill:WB20261106001",
                "reassign_blocked": "waybill:WB20261106002",
            },
        }
    return guard(fn)


@router.get("/ontology")
def ontology():
    return guard(lambda: get_system().ontology())


@router.get("/ontology/versions")
def ontology_versions():
    return guard(lambda: get_system().semantic_versions())


@router.get("/ontology/diff")
def ontology_diff(from_version: Optional[str] = Query(None),
                  to_version: Optional[str] = Query(None)):
    return guard(lambda: get_system().semantic_diff(from_version, to_version))


@router.get("/ontology/impact")
def ontology_impact(from_version: Optional[str] = Query(None),
                    to_version: Optional[str] = Query(None)):
    return guard(lambda: get_system().semantic_impact(from_version, to_version))


@router.get("/ontology/validate")
def ontology_validate(version: Optional[str] = Query(None)):
    return guard(lambda: get_system().semantic_validate(version))


@router.get("/ontology/guard-demo")
def ontology_guard_demo(case: str = Query("unknown_fee_type")):
    """B-05 扩展：多 case 的门禁负向演示（未签字 FAIL / 补签 PASS）。"""
    from freight.demo_labs import guard_demo
    return guard(lambda: guard_demo(get_system(), case))


# ---------------------------------------------------------- demo labs（负向实验，零写主库）
@router.get("/contracts/coverage")
def contracts_coverage(waybill_id: str = Query(WB_DEFAULT)):
    """B-01: 费用归属矩阵 / 解析状态 / 新鲜度 / 证据覆盖 / 缺口清单。"""
    from freight.demo_labs import contract_coverage
    return guard(lambda: contract_coverage(get_system(), waybill_id))


@router.post("/contracts/simulate")
def contracts_simulate(body: dict):
    """B-02: 删认领/证据要求 → before/after findings 翻转（persisted:false）。"""
    from freight.demo_labs import contract_simulate
    return guard(lambda: contract_simulate(get_system(), body.get("case", "drop_claim"),
                                           body.get("waybill_id", WB_DEFAULT)))


@router.get("/ontology/abox-coverage")
def ontology_abox_coverage():
    """B-03: ABox 深水区覆盖率与问题清单。"""
    from freight.demo_labs import abox_coverage
    return guard(lambda: abox_coverage(get_system()))


@router.post("/actions/lab")
def actions_lab(body: dict):
    """B-04: SAVEPOINT 内错角色/错状态/允许动作/同键重放，ROLLBACK 零污染。"""
    from freight.demo_labs import action_lab
    return guard(lambda: action_lab(get_system(), body.get("waybill_id", WB_DEFAULT)))


@router.post("/ontology/type-lab")
def ontology_type_lab(body: dict):
    """B-06: 改属性类型/函数签名副本，真实写入/调用路径的拒绝码。"""
    from freight.demo_labs import type_lab
    return guard(lambda: type_lab(get_system(), body.get("case", "waiting_minutes_boolean")))


@router.post("/agent/check-answerable")
def agent_check_answerable(body: dict):
    """问句级可答性检查：这个问句答得出来吗、缺什么（纯确定性，不调 LLM）。"""
    from freight.answerability import check_answerable
    question = (body.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": "缺少 question"}
    return guard(lambda: check_answerable(get_system(), question, body.get("terms")))


@router.post("/agent/declaration-check")
def agent_declaration_check(body: dict):
    """B-07: 确定性检查工具白名单/可圈定类型/证据 checksum，不调 LLM。"""
    from freight.demo_labs import declaration_check
    return guard(lambda: declaration_check(
        get_system(), body.get("omit_tool"), body.get("omit_type")))


@router.get("/ontology/property-graph-parity")
def ontology_property_graph_parity():
    """属性图 vs 可执行本体：同题同数据对照实验（六题 + 漏改实验 + 对齐估算）。

    核心实现在 freight/parity_lab.py，与 scripts/property_graph_parity.py 共用，
    单一实现、单一口径；零写主库。
    """
    try:
        from freight.parity_lab import build_report
    except ImportError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "PARITY_DEPENDENCY_MISSING",
            "message": f"缺少对照实验依赖（networkx）：{exc}",
        })
    return guard(lambda: build_report())


@router.post("/pricing/lab")
def pricing_lab_api(body: dict):
    """多维计价实验室：车型×时段×天气 决策表演示（只读求值，零写库）。

    body: {case: truck_rain|vehicle, vehicle_type, weather,
           waiting_minutes, time_at}
    """
    from freight.demo_labs import pricing_lab
    return guard(lambda: pricing_lab(
        get_system(),
        case=body.get("case") or "truck_rain",
        vehicle_type=body.get("vehicle_type") or "大货车",
        weather=body.get("weather") or "rain",
        waiting_minutes=float(body.get("waiting_minutes") or 53),
        time_at=body.get("time_at") or "",
    ))



@router.post("/ontology/activate")
def ontology_activate(body: VersionIn):
    return guard(lambda: get_system().activate_version(body.version))


@router.post("/ontology/rollback")
def ontology_rollback():
    return guard(lambda: get_system().rollback_version())


@router.get("/facts/overview")
def facts_overview():
    return guard(lambda: get_system().overview())


@router.get("/facts/query/{waybill_id}")
def fact_query(waybill_id: str, include_context: bool = False):
    return guard(lambda: get_system().query_waybill(waybill_id, include_context))


@router.get("/mcp/tools")
def mcp_tools(type: Optional[str] = Query(default=None)):
    return guard(lambda: get_system().mcp_tools(type))


@router.post("/mcp/call")
def mcp_call(body: MCPCallIn):
    return guard(lambda: get_system().mcp_call(body.tool, body.arguments))


@router.get("/objects")
def objects(type: Optional[str] = None, q: Optional[str] = None,
            limit: int = Query(100, ge=1, le=500)):
    return guard(lambda: {"objects": get_system().search(type, q, limit)})


@router.get("/objects/{object_id}")
def object_detail(object_id: str):
    return guard(lambda: get_system().get_object(object_id))


@router.get("/objects/{object_id}/graph")
def object_graph(object_id: str, depth: int = Query(2, ge=1, le=5)):
    return guard(lambda: get_system().graph(object_id, depth))


@router.get("/ontology/state-machines")
def state_machines():
    """状态机总览：全部已声明状态机的对象类型。"""
    return guard(lambda: get_system().state_machine())


@router.get("/ontology/state-machines/{object_type}")
def state_machine_of_type(object_type: str):
    """单个对象类型的状态机：状态集、起点/终态/外部状态、迁移边与可达集。"""
    return guard(lambda: get_system().state_machine(object_type))


@router.get("/objects/{object_id}/state-history")
def object_state_history(object_id: str):
    """对象状态轨迹：只含状态变更事件，附该类型的完整状态机。"""
    return guard(lambda: get_system().state_history(object_id))


@router.get("/objects/{object_id}/trace")
def object_trace(object_id: str):
    return guard(lambda: get_system().trace(object_id))


@router.get("/objects/{object_id}/impact")
def object_impact(object_id: str):
    return guard(lambda: get_system().impact(object_id))


@router.get("/waybills/{waybill_id}/fee-analysis")
def fee_analysis(waybill_id: str, version: Optional[str] = Query(None)):
    def fn():
        sys = get_system()
        sem = sys.semantic_for(version) if version else None
        return sys.fee_analysis(waybill_id, sem=sem)
    return guard(fn)


@router.get("/waybills/{waybill_id}/explain")
def waybill_explain(waybill_id: str,
                    audience: str = Query("cs", pattern="^(cs|finance|driver)$"),
                    version: Optional[str] = Query(None)):
    def fn():
        sys = get_system()
        sem = sys.semantic_for(version) if version else None
        return sys.explain(waybill_id, audience=audience, sem=sem)
    return guard(fn)


@router.get("/agent/preflight")
def agent_preflight():
    """演示前网关探测：永远返回 200，用 available 字段表达结果。"""
    return get_system().llm_preflight()


@router.post("/waybills/{waybill_id}/reasoning-chain/start")
def reasoning_chain_start(waybill_id: str,
                          max_steps: int = Query(6, ge=2, le=10),
                          question: str = Query("", max_length=300)):
    return agent_guard(lambda: get_system().agent_chain_start(
        waybill_id, max_steps=max_steps, question=question,
    ))


@router.post("/reasoning-chain/{session_id}/next")
def reasoning_chain_next(session_id: str):
    return reasoning_guard(lambda: get_system().agent_chain_next(session_id))


@router.post("/reasoning-chain/{session_id}/run")
def reasoning_chain_run(session_id: str,
                        max_wait: float = Query(240.0, ge=5.0, le=900.0)):
    """一次跑到结论（演示用）：连续推进推理链，返回最终答案与全程 stage。"""
    return reasoning_guard(lambda: get_system().agent_chain_run(
        session_id, max_wait=max_wait))


@router.get("/waybills/{waybill_id}/reasoning-chain")
def reasoning_chain(waybill_id: str,
                    max_steps: int = Query(6, ge=2, le=10),
                    question: str = Query("", max_length=300)):
    return reasoning_guard(lambda: get_system().reasoning_chain(
        waybill_id, max_steps=max_steps, question=question,
    ))


@router.get("/audit")
def audit(object_id: Optional[str] = None,
          limit: int = Query(100, ge=1, le=500)):
    return guard(lambda: {"audit": get_system().audit(object_id, limit)})


@router.post("/actions/{action_id}")
def run_action(action_id: str, body: ActionIn):
    return guard(lambda: get_system().execute_action(
        action_id=action_id,
        object_id=body.object_id,
        actor=body.actor,
        role=body.role,
        payload=body.payload,
        idempotency_key=body.idempotency_key,
    ))


# -------------------------------------------------- 组合式 Object Set 查询（E4）
# 对标 Palantir OSDK 组合语义：谓词/集合代数/followLink/聚合，安全联动（行级过滤影响
# total 与聚合，列级脱敏作用于输出 props；scope token 检查集合涉及的主类型）。

class ObjectSetQueryIn(BaseModel):
    set: dict
    where: Optional[dict] = None
    orderBy: Optional[list] = None
    aggregate: Optional[dict] = None
    page: Optional[dict] = None


def _object_query_engine():
    from enhancements.runtime import EnhancedSystem
    from freight.object_query import ObjectQueryEngine
    enh = EnhancedSystem(get_system())
    return ObjectQueryEngine(get_system().sem, enh.ext, enh.security), enh


@router.post("/object-set/query")
def object_set_query(body: ObjectSetQueryIn,
                     user: str = Query("anonymous"), roles: str = Query("")):
    from enhancements.security import SecurityContext, AccessDenied
    from freight.object_query import QueryError
    from freight.semantic import OntologyViolation

    engine, enh = _object_query_engine()
    role_set = {r.strip() for r in roles.split(",") if r.strip()}
    token = enh.ext.scope_tokens.get("default_token")
    ctx = SecurityContext(
        user=user, roles=role_set, markings=role_set,
        scope_token=token if user.startswith("agent-") else None,
    )
    # scope token：集合主类型必须在令牌可见范围内
    try:
        if ctx.scope_token:
            main_type = engine.validate_set(body.set)
            if main_type == "*":
                raise AccessDenied(
                    "scope token 不允许代数组合查询（无法静态判定类型）",
                    code="TOOL_NOT_IN_SCOPE")
            engine.security.check_scope(ctx, "class", main_type)
        query = body.model_dump(exclude_none=True)
        return engine.execute(query, get_system().facts.store, ctx)
    except QueryError as exc:
        raise HTTPException(400, f"{exc.code}: {exc}") from exc
    except OntologyViolation as exc:
        raise HTTPException(400, f"ONTOLOGY_VIOLATION: {exc}") from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc


# -------------------------------------------------- Ontology SQL 入口
# 对标 Palantir Ontology SQL（2026-11-12 GA）：SQL 只是前端方言，
# 编译到同一查询树 IR 后走同一执行引擎——TBox 校验/安全/聚合全部复用。

class ObjectSetSqlIn(BaseModel):
    sql: str
    params: Optional[dict] = None


@router.post("/object-set/sql")
def object_set_sql(body: ObjectSetSqlIn,
                   user: str = Query("anonymous"), roles: str = Query("")):
    from enhancements.security import SecurityContext, AccessDenied
    from freight.object_query import QueryError
    from freight.semantic import OntologyViolation
    from freight.sql_frontend import SqlCompileError, compile_sql, project

    try:
        compiled = compile_sql(body.sql, body.params)
    except SqlCompileError as exc:
        raise HTTPException(400, f"{exc.code}: {exc}") from exc
    projection = compiled.pop("_projection", [{"kind": "star"}])

    engine, enh = _object_query_engine()
    role_set = {r.strip() for r in roles.split(",") if r.strip()}
    token = enh.ext.scope_tokens.get("default_token")
    ctx = SecurityContext(
        user=user, roles=role_set, markings=role_set,
        scope_token=token if user.startswith("agent-") else None,
    )
    try:
        if ctx.scope_token:
            main_type = engine.validate_set(compiled["set"])
            if main_type == "*":
                raise AccessDenied(
                    "scope token 不允许代数组合查询（无法静态判定类型）",
                    code="TOOL_NOT_IN_SCOPE")
            engine.security.check_scope(ctx, "class", main_type)
        return project(engine.execute(compiled, get_system().facts.store, ctx),
                       projection)
    except QueryError as exc:
        raise HTTPException(400, f"{exc.code}: {exc}") from exc
    except OntologyViolation as exc:
        raise HTTPException(400, f"ONTOLOGY_VIOLATION: {exc}") from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc