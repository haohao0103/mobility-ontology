"""增强层 REST 路由（挂 /enh/*）。

演示级安全上下文传递：?user=alice&roles=freight-core,PII
（生产部署应换为认证中间件注入，见 README 纪律节）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, HTTPException, Query  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from enhancements.runtime import EnhancedSystem  # noqa: E402
from enhancements.security import SecurityContext, AccessDenied  # noqa: E402
from enhancements.function_registry import FunctionRegistryError  # noqa: E402
from enhancements.property_registry import RegistryViolation  # noqa: E402
from enhancements.interfaces import InterfaceViolation  # noqa: E402
from enhancements import sandbox  # noqa: E402

router = APIRouter(prefix="/enh", tags=["enhancements"])

_SYSTEM: EnhancedSystem | None = None


def get_enh() -> EnhancedSystem:
    global _SYSTEM  # noqa: PLW0603
    if _SYSTEM is None:
        from freight.api import get_system
        _SYSTEM = EnhancedSystem(get_system())
    return _SYSTEM


def ctx_from(user: str, roles: str) -> SecurityContext:
    role_set = {r.strip() for r in roles.split(",") if r.strip()} if roles else set()
    token = get_enh().ext.scope_tokens.get("default_token")
    return SecurityContext(user=user, roles=role_set, markings=role_set,
                           scope_token=token if user.startswith("agent-") else None)


class SimulateIn(BaseModel):
    user: str = "anonymous"
    roles: list[str] = []
    class_id: str = "FeeItem"


class FunctionCallIn(BaseModel):
    args: dict = {}


@router.get("/health")
def health():
    e = get_enh()
    issues = e.ext.validate()
    return {
        "ok": not issues,
        "extension_version": e.ext.meta.get("version"),
        "property_types": len(e.ext.property_types),
        "interfaces": len(e.ext.interfaces),
        "markings": len(e.ext.markings),
        "object_security_policies": len(e.ext.object_security_policies),
        "property_security_policies": len(e.ext.property_security_policies),
        "functions_registered": len(e.ext.function_registry),
        "cross_reference_issues": issues,
    }


@router.get("/property-registry/{class_id}")
def property_registry(class_id: str):
    e = get_enh()
    try:
        props = e.properties.properties_of(class_id)
    except RegistryViolation as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "class": class_id,
        "policy": e.ext.policy,
        "properties": [
            {
                "name": p.name, "label": p.label, "value_type": p.value_type,
                "unit": p.unit, "marking": p.marking, "timeseries": p.timeseries,
                "primary_key": p.primary_key, "status": p.status,
                "registered": p.registered, "issues": p.issues,
            }
            for p in props
        ],
    }


@router.get("/interfaces")
def interfaces():
    e = get_enh()
    return {
        "interfaces": [
            {
                "id": i["id"], "label": i.get("label"),
                "match": i.get("match"),
                "implementations": e.interfaces.implementations(i["id"]),
            }
            for i in e.ext.interfaces.values()
        ]
    }


@router.get("/objects")
def objects(user: str = Query("anonymous"), roles: str = Query(""),
            type: str | None = None, q: str | None = None,
            limit: int = Query(20, le=100)):
    e = get_enh()
    ctx = ctx_from(user, roles)
    result = e.search(q=q, types=[type] if type else None, limit=limit)
    # 安全过滤 + 脱敏
    import json as _json
    rows = e.store.all(
        "SELECT * FROM objects WHERE object_id IN (%s)" % ",".join("?" * len(result["objects"])),
        tuple(o["object_id"] for o in result["objects"]),
    ) if result["objects"] else []
    out = []
    for r in rows:
        props = _json.loads(r["props"])
        if not e.security.visible_object(r["type"], props, ctx):
            continue
        masked = e.security.mask_properties(r["type"], props, ctx)
        out.append({
            "object_id": r["object_id"], "type": r["type"],
            "name": r["name"], "status": r["status"], "props": masked,
        })
    return {"visible": len(out), "user": user, "objects": out}


@router.get("/objects/{object_id}")
def secure_object(object_id: str, user: str = Query("anonymous"),
                  roles: str = Query("")):
    e = get_enh()
    ctx = ctx_from(user, roles)
    try:
        return e.secure_get_object(object_id, ctx)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc


@router.post("/security/simulate")
def security_simulate(body: SimulateIn):
    e = get_enh()
    import json as _json
    ctx = SecurityContext.with_roles(body.user, set(body.roles))
    rows = e.store.all("SELECT * FROM objects WHERE type=?", (body.class_id,))
    parsed = [
        {
            "object_id": r["object_id"], "status": r["status"],
            "props": _json.loads(r["props"]),
        }
        for r in rows
    ]
    return e.security.simulate(body.class_id, parsed, ctx)


@router.get("/functions")
def functions():
    return {"functions": get_enh().functions.list_functions()}


@router.post("/functions/{function_id}/call")
def function_call(function_id: str, body: FunctionCallIn,
                  user: str = Query("agent-readonly"), roles: str = Query("freight-core")):
    e = get_enh()
    ctx = ctx_from(user, roles)
    try:
        return e.call_function(function_id, body.args, ctx)
    except FunctionRegistryError as exc:
        code = exc.code
        status = 404 if code == "FUNCTION_UNKNOWN" else 400
        raise HTTPException(status, f"{code}: {exc}") from exc
    except InterfaceViolation as exc:
        raise HTTPException(400, f"INTERFACE_VIOLATION: {exc}") from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc


@router.post("/functions/{function_id}/sandbox")
def function_sandbox(function_id: str, body: FunctionCallIn):
    e = get_enh()
    fn = e.ext.function_registry.get(function_id)
    if fn is None:
        raise HTTPException(404, f"FUNCTION_UNKNOWN: {function_id}")
    cfg = fn.get("sandbox", {"timeout_ms": 5000, "memory_mb": 256})
    try:
        result = sandbox.run_in_sandbox(
            function_id, body.args, e.ext,
            timeout_ms=cfg.get("timeout_ms", 5000),
            memory_mb=cfg.get("memory_mb", 256),
        )
    except sandbox.SandboxTimeout as exc:
        raise HTTPException(504, f"SANDBOX_TIMEOUT: {exc}") from exc
    except sandbox.SandboxError as exc:
        raise HTTPException(500, f"SANDBOX_ERROR: {exc}") from exc
    return result


@router.get("/usage/summary")
def usage_summary():
    return get_enh().usage.usage_summary()


@router.get("/usage/{class_id}/dependents")
def usage_dependents(class_id: str):
    e = get_enh()
    deps = e.usage.dependents(class_id)
    return {
        "class": class_id,
        "dependents": deps,
        "deletable": not deps,
    }


@router.get("/osdk")
def osdk(target: str = Query("typescript")):
    """OSDK-lite 在线生成：本体 JSON -> TS / Python / OpenAPI 3.1。

    本体演进后重新请求即可同步客户端类型（演示页面据此断言 schema 漂移）。
    """
    import tools.gen_osdk as gen
    e = get_enh()
    sem, ext = e.system.sem, e.ext
    openapi = gen.generate_openapi(sem, ext)
    ts = gen.generate_typescript(sem, ext)
    py = gen.generate_python(sem, ext)
    files = {"typescript": ts, "python": py,
             "openapi": json.dumps(openapi, ensure_ascii=False, indent=1)}
    if target not in files:
        raise HTTPException(400, f"OSDK_TARGET_UNKNOWN: {target}")
    derived = sem.raw.get("derived_types", [])
    return {
        "target": target,
        "code": files[target],
        "files": files,
        "stats": {
            "classes": len(sem.classes),
            "derived_types": len(derived),
            "interfaces": len(ext.interfaces),
            "functions": len(ext.function_registry),
            "typescript_lines": ts.count("\n") + 1,
            "python_lines": py.count("\n") + 1,
            "openapi_schemas": len(openapi["components"]["schemas"]),
            "derived_in_openapi": [
                d["id"] for d in derived
                if openapi["components"]["schemas"].get(d["id"], {}).get("x-derived")
            ],
        },
    }