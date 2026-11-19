"""应用层 REST 接口：乘客端 / 司机端 / 调度 / 客服查询共用同一套语义层。"""
from __future__ import annotations
import os
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mobility import MobilitySystem, BusinessReject, ConstraintViolation, OntologyViolation
from profile_ai.api import router as profile_router
from freight.api import router as freight_router

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(ROOT, "runs", "system.db")
# 所有接口共用同一条 SQLite 连接（check_same_thread=False），
# FastAPI 线程池并发访问时必须串行化，避免连接状态竞争。
DB_LOCK = threading.Lock()


class OrderIn(BaseModel):
    passenger_id: str
    origin: str
    destination: str
    vehicle_type: str = "经济型"
    coupon_id: str = "NONE"


class DispatchIn(BaseModel):
    driver_id: Optional[str] = None
    vehicle_id: Optional[str] = None


class OffboardIn(BaseModel):
    distance_km: float
    duration_min: float
    route: str = ""


class PayIn(BaseModel):
    amount: Optional[float] = None


class CancelIn(BaseModel):
    reason: str = "PASSENGER_CHANGE"
    wait_minutes: Optional[float] = None


class RefundIn(BaseModel):
    amount: float
    reason: str = "CANCELLATION"


class TicketIn(BaseModel):
    question: str


class RelationCheckIn(BaseModel):
    subject_type: str
    predicate: str
    object_type: str


def create_app(db_path: str = DEFAULT_DB, fresh: bool = False) -> FastAPI:
    sys = MobilitySystem(db_path, fresh=fresh)
    sys.bootstrap()
    app = FastAPI(
        title="共享出行互联网本体 · 最小闭环 v1",
        description="订单 / 派单 / 计费 / 取消 / 客服追溯，规则集中在语义层",
        version="1.0.0",
    )

    def guard(fn):
        with DB_LOCK:
            try:
                return fn()
            except (BusinessReject, ConstraintViolation, OntologyViolation) as e:
                raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

    @app.get("/health")
    def health():
        return guard(lambda: {"ok": True, "db": db_path, "stats": sys.stats()})

    @app.get("/semantic/dictionary")
    def dictionary():
        return guard(sys.sem.dictionary)

    @app.get("/constraints")
    def constraints():
        return guard(lambda: {"definitions": sys.sem.constraints, "violations": sys.validate()})

    @app.get("/stats")
    def stats():
        return guard(sys.stats)

    @app.get("/orders")
    def list_orders(limit: int = 50):
        return guard(lambda: {"orders": sys.store.all(
            "SELECT o.order_id, o.passenger_id, o.status, o.created_at, o.origin, "
            "o.destination, o.vehicle_type, o.driver_id, o.cancel_rule_id, "
            "(SELECT COALESCE(SUM(b.total),0) FROM billing_records b "
            " WHERE b.order_id=o.order_id) AS total "
            "FROM orders o ORDER BY o.order_id DESC LIMIT ?", (limit,))})

    @app.get("/orders/{order_id}/facts")
    def facts(order_id: str):
        return guard(lambda: {"order_id": order_id,
                              "facts": sys.graph.facts_of(order_id)})

    @app.post("/validate/relation")
    def validate_relation(body: RelationCheckIn):
        """本体关系校验（不落库）：用来演示定义域/值域约束。"""
        rel = sys.sem.relations.get(body.predicate)
        try:
            sys.sem.validate_relation(body.predicate, body.subject_type, body.object_type)
            return {"ok": True, "relation": rel,
                    "message": f"{body.subject_type} —{rel['label']}→ {body.object_type} 合法"}
        except OntologyViolation as e:
            return {"ok": False, "relation": rel, "message": str(e)}

    @app.post("/orders")
    def create_order(body: OrderIn):
        return guard(lambda: {"order_id": sys.create_order(
            body.passenger_id, body.origin, body.destination,
            body.vehicle_type, coupon_id=body.coupon_id)})

    @app.get("/orders/{order_id}/candidates")
    def candidates(order_id: str):
        return guard(lambda: {"order_id": order_id,
                              "candidates": sys.candidate_drivers(order_id)})

    @app.post("/orders/{order_id}/dispatch")
    def dispatch(order_id: str, body: DispatchIn):
        return guard(lambda: sys.dispatch(order_id, driver_id=body.driver_id,
                                          vehicle_id=body.vehicle_id))

    @app.post("/orders/{order_id}/arrived")
    def arrived(order_id: str):
        return guard(lambda: sys.driver_arrived(order_id))

    @app.post("/orders/{order_id}/onboard")
    def onboard(order_id: str):
        return guard(lambda: sys.passenger_onboard(order_id))

    @app.post("/orders/{order_id}/offboard")
    def offboard(order_id: str, body: OffboardIn):
        return guard(lambda: sys.passenger_offboard(
            order_id, body.distance_km, body.duration_min, route=body.route))

    @app.post("/orders/{order_id}/pay")
    def pay(order_id: str, body: PayIn):
        def _pay():
            amount = body.amount
            if amount is None:
                amount = sys.store.scalar(
                    "SELECT total FROM billing_records WHERE order_id=? "
                    "ORDER BY billing_id DESC LIMIT 1", (order_id,))
                if amount is None:
                    raise BusinessReject("没有可支付的账单")
            return sys.pay(order_id, amount=amount)
        return guard(_pay)

    @app.post("/orders/{order_id}/cancel")
    def cancel(order_id: str, body: CancelIn):
        return guard(lambda: sys.cancel(order_id, reason=body.reason,
                                        wait_minutes=body.wait_minutes))

    @app.post("/orders/{order_id}/refund")
    def refund(order_id: str, body: RefundIn):
        return guard(lambda: sys.refund(order_id, amount=body.amount, reason=body.reason))

    @app.post("/orders/{order_id}/ticket")
    def ticket(order_id: str, body: TicketIn):
        return guard(lambda: {"ticket_id": sys.support_ticket(order_id, body.question)})

    @app.get("/orders/{order_id}/trace")
    def trace(order_id: str):
        return guard(lambda: sys.trace(order_id))

    @app.get("/orders/{order_id}/events")
    def events(order_id: str):
        return guard(lambda: {
            "order_id": order_id,
            "events": [e for e in sys.graph.facts_of(order_id) if e["kind"] == "event"],
        })

    # 画像 AI 语义层：/profile/*（HugeGraph PD 多租户 + Agent 工具 + 写门禁）
    app.include_router(profile_router)
    # 货拉拉订单本体：/freight/*（对象图谱 + 费用诊断 + 规则动作 + 审计）
    app.include_router(freight_router)
    # Palantir 追齐层：/enh/*（属性注册表 + 接口 + 安全 + 函数注册表 + 沙箱 + 度量）
    from enhancements.api import router as enh_router
    app.include_router(enh_router)

    # 统一入口：用户只需要记住根地址，再选择订单本体或画像本体
    @app.get("/", include_in_schema=False)
    def ontology_portal():
        portal_path = os.path.join(ROOT, "web", "portal.html")
        if not os.path.exists(portal_path):
            raise HTTPException(status_code=404, detail="入口页面未部署")
        return FileResponse(
            portal_path,
            media_type="text/html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )

    # 可视化单页（放在所有 API 路由之后，避免覆盖接口）
    web_dir = os.path.join(ROOT, "web")
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app


app = create_app()