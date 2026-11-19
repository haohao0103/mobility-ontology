"""领域服务层：订单 → 派单 → 接单 → 到达 → 上车 → 下车 → 计费 → 支付 → 取消 → 追溯。

所有判断来自语义层规则，所有关系写入受本体合法性约束，
所有状态变更走生命周期状态机，所有业务事实同步落业务库与持久化图谱。
"""
from __future__ import annotations
import sqlite3
from collections import defaultdict

from .semantic import SemanticLayer, Clock, ConstraintViolation, BusinessReject, OntologyViolation
from .store import Store
from .graph import TemporalGraph
from .constraints import ConstraintValidator
from .rules import RuleEngine
from .trace import build_trace, format_trace


class MobilitySystem:
    def __init__(self, db_path: str, fresh: bool = True,
                 semantic_path: str | None = None,
                 clock_start: str = "2026-11-12 09:00:00"):
        import os
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        if fresh and os.path.exists(db_path):
            os.remove(db_path)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.sem = SemanticLayer(semantic_path) if semantic_path else SemanticLayer()
        self.store = Store(self.conn)
        self.graph = TemporalGraph(self.conn, self.sem)
        self.validator = ConstraintValidator(self.store, self.graph, self.sem)
        self.clock = Clock(clock_start)
        self._ids: dict[str, int] = defaultdict(int)
        self.actions = self._register_actions()
        self.rules = RuleEngine(self.sem, self.actions)
        if not fresh:
            self._restore_ids()

    def _restore_ids(self) -> None:
        """复用已有数据库时，从各表恢复 ID 计数器，避免主键冲突。"""
        for prefix, table, col in [
            ("O", "orders", "order_id"), ("T", "trips", "trip_id"),
            ("C", "billing_records", "billing_id"), ("P", "payments", "payment_id"),
            ("S", "dispatches", "dispatch_id"), ("K", "support_tickets", "ticket_id"),
            ("R", "refunds", "refund_id"),
        ]:
            mx = 0
            for row in self.store.all(f"SELECT {col} FROM {table}"):
                tail = "".join(ch for ch in (row[col] or "") if ch.isdigit())
                if tail:
                    mx = max(mx, int(tail))
            self._ids[prefix] = mx

    # ------------------------------------------------------------------ 基础
    def next_id(self, prefix: str) -> str:
        self._ids[prefix] += 1
        return f"{prefix}{self._ids[prefix]}"

    def _reserve(self, prefix: str, explicit_id: str) -> None:
        """显式指定 ID（用于完整示例）时同步登记计数器，避免后续生成撞主键。"""
        tail = "".join(ch for ch in explicit_id if ch.isdigit())
        if tail:
            self._ids[prefix] = max(self._ids[prefix], int(tail))

    def _ts(self, advance: float = 1) -> str:
        ts = self.clock.now()
        self.clock.advance(advance)
        return ts

    def bootstrap(self, n_passengers: int = 10, n_drivers: int = 20, n_vehicles: int = 20) -> dict:
        """建立本体实例（乘客/司机/车辆/优惠券）。司机 X、车辆 V3 用于完整示例。"""
        for i in range(n_passengers):
            pid = "A" if i == 0 else chr(ord("A") + i)
            if not self.store.one("SELECT 1 FROM passengers WHERE passenger_id=?", (pid,)):
                self.store.insert("passengers", {"passenger_id": pid, "name": f"乘客{pid}"})
        types = ["经济型", "舒适型", "商务型"]
        regions = ["海淀", "朝阳", "东城", "西城", "丰台", "昌平"]
        for i in range(n_drivers):
            did = ["X", "Y", "Z", "W"][i] if i < 4 else f"D{i+1:02d}"
            if not self.store.one("SELECT 1 FROM drivers WHERE driver_id=?", (did,)):
                self.store.insert("drivers", {
                    "driver_id": did, "name": f"司机{did}",
                    "vehicle_type": types[(i // 3) % 3], "region": regions[i % len(regions)],
                    "status": "IDLE",
                })
        for i in range(n_vehicles):
            vid = f"V{i+1}"
            if not self.store.one("SELECT 1 FROM vehicles WHERE vehicle_id=?", (vid,)):
                self.store.insert("vehicles", {
                    "vehicle_id": vid, "plate": f"京A{i+1:05d}",
                    "vehicle_type": types[(i // 3) % 3], "status": "IDLE",
                })
        for cid, amount in self.sem.pricing_cfg["coupon"].items():
            if not self.store.one("SELECT 1 FROM coupons WHERE coupon_id=?", (cid,)):
                self.store.insert("coupons", {"coupon_id": cid, "amount": amount})
        return {
            "passengers": self.store.scalar("SELECT COUNT(*) FROM passengers"),
            "drivers": self.store.scalar("SELECT COUNT(*) FROM drivers"),
            "vehicles": self.store.scalar("SELECT COUNT(*) FROM vehicles"),
        }

    # ------------------------------------------------------------------ 订单
    def create_order(self, passenger_id: str, origin: str, destination: str,
                     vehicle_type: str, coupon_id: str = "NONE",
                     order_id: str | None = None, advance: float = 1) -> str:
        if not self.store.one("SELECT 1 FROM passengers WHERE passenger_id=?", (passenger_id,)):
            raise ConstraintViolation(f"C1 订单必须关联有效乘客: {passenger_id}")
        oid = order_id or self.next_id("O")
        if order_id:
            self._reserve("O", order_id)
        ts = self._ts(advance)
        region = self.sem.region_of(origin) or "未知"
        self.store.insert("orders", {
            "order_id": oid, "passenger_id": passenger_id,
            "origin": origin, "destination": destination, "region": region,
            "vehicle_type": vehicle_type, "created_at": ts, "status": "CREATED",
            "driver_id": None, "coupon_id": coupon_id,
            "cancel_fee": 0.0, "cancel_reason": None,
            "cancel_rule_id": None, "cancel_decision": None, "ticket_id": None,
        })
        # 图谱：谁发起、起点终点、区域
        self.graph.add_fact(passenger_id, "initiates", oid,
                            subject_type="Passenger", object_type="Order",
                            occurred_at=ts, order_id=oid)
        self.graph.add_fact(oid, "has_origin", origin,
                            subject_type="Order", object_type="Address",
                            occurred_at=ts, order_id=oid)
        self.graph.add_fact(oid, "has_destination", destination,
                            subject_type="Order", object_type="Address",
                            occurred_at=ts, order_id=oid)
        for addr in (origin, destination):
            reg = self.sem.region_of(addr)
            if reg:
                self.graph.add_fact(addr, "located_in", reg,
                                    subject_type="Address", object_type="Region",
                                    occurred_at=ts, order_id=oid)
        # 优惠券与订单的关联属于事件载荷，不写成关系：
        # 本体中 applies_coupon 的定义域是 BillingRecord，Order 不是合法主语。
        self.graph.event(oid, "ORDER_CREATED", ts, {
            "passenger": passenger_id, "origin": origin, "destination": destination,
            "vehicle_type": vehicle_type, "coupon": coupon_id,
        })
        return oid

    def candidate_drivers(self, order_id: str) -> list[dict]:
        """能力问题①：乘客叫车后，哪些司机可以接单？"""
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        if not o:
            raise BusinessReject(f"订单不存在: {order_id}")
        return self.store.all(
            """SELECT d.driver_id, d.name, d.vehicle_type, d.region, d.status AS driver_status,
                      (SELECT vehicle_id FROM vehicles v
                       WHERE v.vehicle_type=d.vehicle_type AND v.status='IDLE'
                       ORDER BY vehicle_id LIMIT 1) AS suggested_vehicle
               FROM drivers d
               WHERE d.status='IDLE' AND d.vehicle_type=? AND d.region=?
               ORDER BY d.driver_id""",
            (o["vehicle_type"], o["region"]),
        )

    # ------------------------------------------------------------------ 派单
    def dispatch(self, order_id: str, driver_id: str | None = None,
                 vehicle_id: str | None = None, trip_id: str | None = None,
                 advance: float = 1) -> dict:
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        if not o:
            raise BusinessReject(f"订单不存在: {order_id}")
        ts = self._ts(advance)
        ctx = {
            "order_id": order_id, "ts": ts,
            "order.status": o["status"], "order.driver_id": o["driver_id"],
            "order.in_service_area": self.sem.in_service_area(o["origin"])
                                     and self.sem.in_service_area(o["destination"]),
            "order.vehicle_type": o["vehicle_type"], "order.region": o["region"],
            "dispatch.candidate_count": None,
            # 可选：显式指定运力（用于完整示例与受控回归）
            "forced_driver_id": driver_id, "forced_vehicle_id": vehicle_id,
            "forced_trip_id": trip_id,
        }
        res = self.rules.run_chain("dispatch", ctx)
        if res["decision"] == "DISPATCH_OK":
            acc = self.accept(order_id, ctx["driver_id"], ctx["vehicle_id"],
                              trip_id=ctx.get("forced_trip_id"))
            res["trip_id"] = acc["ctx"]["trip_id"]
            res["driver_id"] = ctx["driver_id"]
            res["vehicle_id"] = ctx["vehicle_id"]
        res["candidates"] = ctx.get("candidates", [])
        return res

    # ------------------------------------------------------------------ 接单
    def accept(self, order_id: str, driver_id: str, vehicle_id: str,
               trip_id: str | None = None, advance: float = 1) -> dict:
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        d = self.store.one("SELECT * FROM drivers WHERE driver_id=?", (driver_id,))
        v = self.store.one("SELECT * FROM vehicles WHERE vehicle_id=?", (vehicle_id,))
        if not (o and d and v):
            raise BusinessReject("订单/司机/车辆必须存在")
        # 约束前置拦截：C4 车辆未完单、C7 司机同时跑两单
        busy_v = self.store.scalar(
            "SELECT COUNT(*) FROM trips WHERE vehicle_id=? AND status IN ('CREATED','ARRIVED','ONBOARD')",
            (vehicle_id,),
        )
        if busy_v or v["status"] != "IDLE":
            raise ConstraintViolation(f"C4 车辆必须先完成上一单才能进入新订单: {vehicle_id}")
        busy_d = self.store.scalar(
            "SELECT COUNT(*) FROM trips WHERE driver_id=? AND status IN ('CREATED','ARRIVED','ONBOARD')",
            (driver_id,),
        )
        if busy_d or d["status"] != "IDLE":
            raise ConstraintViolation(f"C7 司机不得同时执行两笔进行中行程: {driver_id}")
        if d["vehicle_type"] != o["vehicle_type"] or v["vehicle_type"] != o["vehicle_type"]:
            raise BusinessReject(f"车型不匹配: 订单={o['vehicle_type']}")
        ts = self._ts(advance)
        tid = trip_id or self.next_id("T")
        if trip_id:
            self._reserve("T", trip_id)
        ctx = {
            "order_id": order_id, "ts": ts, "driver_id": driver_id, "vehicle_id": vehicle_id,
            "trip_id": tid,
            "order.status": o["status"], "driver.status": d["status"],
        }
        return self.rules.run_chain("accept", ctx)

    # ------------------------------------------------------------------ 行程
    def driver_arrived(self, order_id: str, advance: float = 1) -> dict:
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        t = self.store.one("SELECT * FROM trips WHERE order_id=?", (order_id,))
        if not (o and t):
            raise BusinessReject("订单或行程不存在")
        self.sem.assert_transition("order", o["status"], "DRIVER_ARRIVED")
        self.sem.assert_transition("driver", self._driver_status(t["driver_id"]), "ARRIVED")
        self.sem.assert_transition("trip", t["status"], "ARRIVED")
        ts = self._ts(advance)
        self.store.update("orders", "order_id", order_id,
                          {"status": "DRIVER_ARRIVED", "driver_id": t["driver_id"]})
        self.store.update("drivers", "driver_id", t["driver_id"], {"status": "ARRIVED"})
        self.store.update("trips", "trip_id", t["trip_id"],
                          {"status": "ARRIVED", "arrived_at": ts})
        self.graph.event(order_id, "DRIVER_ARRIVED", ts,
                         {"driver": t["driver_id"], "vehicle": t["vehicle_id"]})
        return {"order_id": order_id, "status": "DRIVER_ARRIVED", "at": ts}

    def passenger_onboard(self, order_id: str, advance: float = 1) -> dict:
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        t = self.store.one("SELECT * FROM trips WHERE order_id=?", (order_id,))
        if not (o and t):
            raise BusinessReject("订单或行程不存在")
        ts = self._ts(advance)
        ctx = {
            "order_id": order_id, "trip_id": t["trip_id"], "ts": ts,
            "driver_id": t["driver_id"], "vehicle_id": t["vehicle_id"],
            "event": "PASSENGER_ONBOARD",
            "order.status": o["status"], "driver.status": self._driver_status(t["driver_id"]),
        }
        return self.rules.run_chain("billing", ctx)

    def passenger_offboard(self, order_id: str, distance_km: float, duration_min: float,
                           route: str = "", advance: float = 1) -> dict:
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        t = self.store.one("SELECT * FROM trips WHERE order_id=?", (order_id,))
        if not (o and t):
            raise BusinessReject("订单或行程不存在")
        if not t["meter_start_at"]:
            raise BusinessReject("计费未开始，不能下车结算")
        ts = self._ts(advance)
        ctx = {
            "order_id": order_id, "trip_id": t["trip_id"], "ts": ts,
            "driver_id": t["driver_id"], "vehicle_id": t["vehicle_id"],
            "event": "PASSENGER_OFFBOARD",
            "distance_km": float(distance_km), "duration_min": float(duration_min),
            "vehicle_type": o["vehicle_type"], "region": o["region"],
            "coupon_id": o["coupon_id"], "route": route,
            "order.status": o["status"],
            "billing_id": None, "payment_id": None, "billing.total": None,
        }
        return self.rules.run_chain("billing", ctx)

    # ------------------------------------------------------------------ 支付 / 退款
    def pay(self, order_id: str, amount: float, advance: float = 1) -> dict:
        b = self.store.one(
            "SELECT * FROM billing_records WHERE order_id=? ORDER BY billing_id DESC LIMIT 1",
            (order_id,),
        )
        p = self.store.one("SELECT * FROM payments WHERE order_id=?", (order_id,))
        if not (b and p):
            raise BusinessReject("没有可支付的账单")
        ts = self._ts(advance)
        ctx = {
            "order_id": order_id, "ts": ts, "billing_id": b["billing_id"],
            "payment_id": p["payment_id"],
            "payment.amount": float(amount), "billing.total": float(b["total"]),
        }
        return self.rules.run_chain("payment", ctx)

    def refund(self, order_id: str, amount: float, reason: str = "CANCELLATION",
               advance: float = 1) -> dict:
        p = self.store.one(
            "SELECT * FROM payments WHERE order_id=? AND status='PAID'", (order_id,)
        )
        if not p:
            raise BusinessReject("没有已支付款项可退")
        ts = self._ts(advance)
        ctx = {
            "order_id": order_id, "ts": ts, "payment_id": p["payment_id"],
            "refund_id": self.next_id("R"), "reason": reason,
            "refund.amount": float(amount), "payment.amount": float(p["amount"]),
        }
        return self.rules.run_chain("refund", ctx)

    # ------------------------------------------------------------------ 取消
    def cancel(self, order_id: str, reason: str = "PASSENGER_CHANGE",
               wait_minutes: float | None = None, advance: float = 1) -> dict:
        o = self.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
        if not o:
            raise BusinessReject(f"订单不存在: {order_id}")
        t = self.store.one("SELECT * FROM trips WHERE order_id=?", (order_id,))
        ts = self._ts(advance)
        wait = (float(wait_minutes) if wait_minutes is not None
                else self.clock.minutes_between(o["created_at"], ts))
        ctx = {
            "order_id": order_id, "ts": ts,
            "order.status": o["status"], "order.driver_id": o["driver_id"],
            "wait_minutes": wait, "cancel_reason": reason,
            "trip_id": t["trip_id"] if t else None,
            "driver_id": t["driver_id"] if t else None,
            "vehicle_id": t["vehicle_id"] if t else None,
            "distance_km": t["distance_km"] if t else 0.0,
            "duration_min": t["duration_min"] if t else 0.0,
            "vehicle_type": o["vehicle_type"], "region": o["region"],
            "coupon_id": o["coupon_id"],
        }
        res = self.rules.run_chain("cancel", ctx)
        res["wait_minutes"] = wait
        res["driver_status"] = self._driver_status(ctx["driver_id"]) if ctx["driver_id"] else "IDLE"
        res["vehicle_status"] = self._vehicle_status(ctx["vehicle_id"]) if ctx["vehicle_id"] else "IDLE"
        return res

    # ------------------------------------------------------------------ 客服
    def support_ticket(self, order_id: str, question: str, advance: float = 1) -> str:
        tid = self.next_id("K")
        ts = self._ts(advance)
        self.store.insert("support_tickets", {
            "ticket_id": tid, "order_id": order_id, "question": question, "created_at": ts,
        })
        self.store.update("orders", "order_id", order_id, {"ticket_id": tid})
        self.graph.add_fact(order_id, "has_ticket", tid,
                            subject_type="Order", object_type="SupportTicket",
                            occurred_at=ts, order_id=order_id)
        self.graph.event(order_id, "SUPPORT_TICKET_OPENED", ts,
                         {"ticket": tid, "question": question})
        return tid

    def trace(self, order_id: str) -> dict:
        return build_trace(self, order_id)

    def trace_text(self, order_id: str) -> str:
        return format_trace(self.trace(order_id))

    def validate(self) -> list[dict]:
        return self.validator.validate()

    def stats(self) -> dict:
        return {
            "orders": self.store.scalar("SELECT COUNT(*) FROM orders"),
            "trips": self.store.scalar("SELECT COUNT(*) FROM trips"),
            "billing_records": self.store.scalar("SELECT COUNT(*) FROM billing_records"),
            "payments": self.store.scalar("SELECT COUNT(*) FROM payments"),
            "refunds": self.store.scalar("SELECT COUNT(*) FROM refunds"),
            "facts": self.store.scalar("SELECT COUNT(*) FROM facts"),
            "temporal_drives": self.store.scalar(
                "SELECT COUNT(*) FROM facts WHERE predicate='drives'"),
            "order_status": {r["status"]: r["n"] for r in self.store.all(
                "SELECT status, COUNT(*) n FROM orders GROUP BY status")},
            "cancel_by_rule": {r["cancel_rule_id"]: r["n"] for r in self.store.all(
                "SELECT cancel_rule_id, COUNT(*) n FROM orders "
                "WHERE cancel_rule_id IS NOT NULL GROUP BY cancel_rule_id")},
            "cancel_fee_sum": round(self.store.scalar(
                "SELECT COALESCE(SUM(cancel_fee),0) FROM orders") or 0, 2),
        }

    # ------------------------------------------------------------------ 内部 helper
    def _driver_status(self, driver_id: str) -> str:
        r = self.store.one("SELECT status FROM drivers WHERE driver_id=?", (driver_id,))
        return r["status"] if r else "OFFLINE"

    def _vehicle_status(self, vehicle_id: str) -> str:
        r = self.store.one("SELECT status FROM vehicles WHERE vehicle_id=?", (vehicle_id,))
        return r["status"] if r else "MAINTENANCE"

    def _trip_of(self, ctx: dict) -> dict | None:
        if ctx.get("trip_id"):
            return self.store.one("SELECT * FROM trips WHERE trip_id=?", (ctx["trip_id"],))
        if ctx.get("order_id"):
            return self.store.one("SELECT * FROM trips WHERE order_id=?", (ctx["order_id"],))
        return None

    # ------------------------------------------------------------------ 动作注册表
    def _register_actions(self) -> dict:
        sys = self

        def reject(env):
            raise BusinessReject(f"{env['rule']['id']} {env['explain']}")

        def emit_no_driver(env):
            ctx = env["ctx"]
            sys.graph.event(ctx["order_id"], "NO_AVAILABLE_DRIVER", ctx["ts"], {})
            return {}

        def find_candidates(env):
            ctx = env["ctx"]
            cands = sys.candidate_drivers(ctx["order_id"])
            ctx["candidates"] = cands
            ctx["dispatch.candidate_count"] = len(cands)
            sys.graph.event(ctx["order_id"], "SERVICE_AREA_CHECKED", ctx["ts"], {
                "origin": sys.store.one("SELECT origin FROM orders WHERE order_id=?",
                                        (ctx["order_id"],))["origin"],
                "result": "IN_SERVICE_AREA", "candidate_count": len(cands),
            })
            return {}

        def send_dispatch(env):
            ctx = env["ctx"]
            cands = ctx["candidates"]
            picked_id = ctx.get("forced_driver_id") or (cands[0]["driver_id"] if cands else None)
            vid = ctx.get("forced_vehicle_id") or sys.store.scalar(
                "SELECT vehicle_id FROM vehicles WHERE vehicle_type=? AND status='IDLE' "
                "ORDER BY vehicle_id LIMIT 1", (ctx["order.vehicle_type"],))
            if not picked_id or not vid:
                ctx["dispatch.candidate_count"] = 0
                return {}
            ctx["driver_id"], ctx["vehicle_id"] = picked_id, vid
            did = sys.next_id("S")
            sys.store.insert("dispatches", {
                "dispatch_id": did, "order_id": ctx["order_id"],
                "driver_id": picked_id, "sent_at": ctx["ts"], "result": "SENT",
            })
            sys.graph.add_fact(did, "sent_to", picked_id,
                               subject_type="Dispatch", object_type="Driver",
                               occurred_at=ctx["ts"], order_id=ctx["order_id"])
            return {}

        def emit_dispatch(env):
            ctx = env["ctx"]
            sys.graph.event(ctx["order_id"], "ORDER_DISPATCHED", ctx["ts"], {
                "driver": ctx.get("driver_id"), "vehicle": ctx.get("vehicle_id"),
                "candidate_count": ctx.get("dispatch.candidate_count"),
            })
            return {}

        def create_trip(env):
            ctx = env["ctx"]
            oid, ts = ctx["order_id"], ctx["ts"]
            tid = ctx["trip_id"]
            sys.sem.assert_transition("order", ctx["order.status"], "MATCHED")
            sys.sem.assert_transition("driver", ctx["driver.status"], "ASSIGNED")
            sys.store.insert("trips", {
                "trip_id": tid, "order_id": oid,
                "driver_id": ctx["driver_id"], "vehicle_id": ctx["vehicle_id"],
                "route": "", "status": "CREATED", "started_at": ts,
            })
            sys.store.update("orders", "order_id", oid,
                             {"status": "MATCHED", "driver_id": ctx["driver_id"]})
            sys.store.update("drivers", "driver_id", ctx["driver_id"], {"status": "ASSIGNED"})
            sys.store.update("vehicles", "vehicle_id", ctx["vehicle_id"], {"status": "IN_USE"})
            sys.graph.add_fact(oid, "has_trip", tid,
                               subject_type="Order", object_type="Trip",
                               occurred_at=ts, order_id=oid)
            sys.graph.add_fact(tid, "executed_by", ctx["driver_id"],
                               subject_type="Trip", object_type="Driver",
                               occurred_at=ts, order_id=oid)
            sys.graph.add_fact(tid, "uses_vehicle", ctx["vehicle_id"],
                               subject_type="Trip", object_type="Vehicle",
                               occurred_at=ts, order_id=oid,
                               valid_from=ts)
            sys.graph.event(oid, "TRIP_CREATED", ts, {
                "trip": tid, "driver": ctx["driver_id"], "vehicle": ctx["vehicle_id"],
            })
            return {"order.status": "MATCHED", "driver.status": "ASSIGNED",
                    "vehicle.status": "IN_USE"}

        def open_drives_interval(env):
            ctx = env["ctx"]
            # 时态关系：司机驾驶车辆必须绑定具体时间段，而不是车辆上的固定字段
            sys.graph.add_fact(ctx["driver_id"], "drives", ctx["vehicle_id"],
                               subject_type="Driver", object_type="Vehicle",
                               occurred_at=ctx["ts"], order_id=ctx["order_id"],
                               valid_from=ctx["ts"])
            return {}

        def emit_accept(env):
            ctx = env["ctx"]
            sys.graph.event(ctx["order_id"], "DRIVER_ACCEPTED", ctx["ts"], {
                "driver": ctx["driver_id"], "trip": ctx["trip_id"],
            })
            return {}

        def start_meter(env):
            ctx = env["ctx"]
            oid, tid, ts = ctx["order_id"], ctx["trip_id"], ctx["ts"]
            o = sys.store.one("SELECT status FROM orders WHERE order_id=?", (oid,))
            t = sys.store.one("SELECT * FROM trips WHERE trip_id=?", (tid,))
            ds = sys._driver_status(ctx["driver_id"])
            sys.sem.assert_transition("order", o["status"], "IN_TRIP")
            sys.sem.assert_transition("driver", ds, "ON_TRIP")
            sys.sem.assert_transition("trip", t["status"], "ONBOARD")
            sys.store.update("orders", "order_id", oid, {"status": "IN_TRIP"})
            sys.store.update("drivers", "driver_id", ctx["driver_id"], {"status": "ON_TRIP"})
            sys.store.update("trips", "trip_id", tid,
                             {"status": "ONBOARD", "onboard_at": ts, "meter_start_at": ts})
            sys.graph.event(oid, "PASSENGER_ONBOARD", ts, {"trip": tid})
            sys.graph.event(oid, "METER_STARTED", ts, {
                "trip": tid, "meter_start_at": ts,
                "note": "计费起点是乘客上车时刻，不是接单或司机到达时刻",
            })
            return {"order.status": "IN_TRIP", "driver.status": "ON_TRIP"}

        def emit_meter_start(env):
            return {}

        def stop_meter(env):
            ctx = env["ctx"]
            oid, tid, ts = ctx["order_id"], ctx["trip_id"], ctx["ts"]
            o = sys.store.one("SELECT status FROM orders WHERE order_id=?", (oid,))
            t = sys.store.one("SELECT status FROM trips WHERE trip_id=?", (tid,))
            sys.sem.assert_transition("order", o["status"], "BILLED")
            sys.sem.assert_transition("trip", t["status"], "ENDED")
            sys.store.update("trips", "trip_id", tid, {
                "status": "ENDED", "ended_at": ts, "meter_end_at": ts,
                "distance_km": ctx["distance_km"], "duration_min": ctx["duration_min"],
                "route": ctx.get("route", ""),
            })
            sys.store.update("orders", "order_id", oid, {"status": "BILLED"})
            sys.graph.add_fact(tid, "passes", ctx.get("route") or "默认路线",
                               subject_type="Trip", object_type="RoadSegment",
                               occurred_at=ts, order_id=oid)
            sys.graph.event(oid, "PASSENGER_OFFBOARD", ts, {"trip": tid})
            sys.graph.event(oid, "METER_STOPPED", ts, {
                "trip": tid, "meter_end_at": ts,
                "distance_km": ctx["distance_km"], "duration_min": ctx["duration_min"],
            })
            return {"order.status": "BILLED"}

        def compute_bill(env):
            ctx = env["ctx"]
            oid, tid, ts = ctx["order_id"], ctx["trip_id"], ctx["ts"]
            p = sys.sem.pricing(ctx["vehicle_type"])
            dist_fee = round(ctx["distance_km"] * p["per_km"], 2)
            time_fee = round(ctx["duration_min"] * p["per_min"], 2)
            surcharge = sys.sem.surcharge(ctx["region"])
            coupon = sys.sem.coupon_amount(ctx["coupon_id"])
            # 合计复用规则引擎的同一计费策略，避免口径分叉
            total = sys.rules.fee("actual_usage", ctx)
            bid = sys.next_id("C")
            sys.store.insert("billing_records", {
                "billing_id": bid, "trip_id": tid, "order_id": oid,
                "base_fare": p["base_fare"], "distance_fee": dist_fee,
                "time_fee": time_fee, "region_surcharge": surcharge,
                "coupon_deduction": coupon, "total": total,
                "source": "TRIP", "created_at": ts,
            })
            sys.graph.add_fact(tid, "produces", bid,
                               subject_type="Trip", object_type="BillingRecord",
                               occurred_at=ts, order_id=oid)
            if coupon > 0:
                sys.graph.add_fact(bid, "applies_coupon", ctx["coupon_id"],
                                   subject_type="BillingRecord", object_type="Coupon",
                                   occurred_at=ts, order_id=oid)
            sys.graph.event(oid, "BILLING_COMPUTED", ts, {
                "billing": bid, "base_fare": p["base_fare"], "distance_fee": dist_fee,
                "time_fee": time_fee, "region_surcharge": surcharge,
                "coupon_deduction": coupon, "total": total,
            })
            return {"billing_id": bid, "billing.total": total}

        def create_payment(env):
            ctx = env["ctx"]
            oid, tid, ts = ctx["order_id"], ctx["trip_id"], ctx["ts"]
            pid = sys.next_id("P")
            total = ctx["billing.total"]
            sys.store.insert("payments", {
                "payment_id": pid, "billing_id": ctx["billing_id"], "order_id": oid,
                "amount": total, "status": "PENDING", "paid_at": None,
            })
            sys.graph.add_fact(tid, "has_payment", pid,
                               subject_type="Trip", object_type="Payment",
                               occurred_at=ts, order_id=oid)
            sys.graph.event(oid, "BILL_GENERATED", ts, {
                "payment": pid, "billing": ctx["billing_id"], "amount": total,
            })
            return {"payment_id": pid, "payment.amount": total}

        def emit_billing(env):
            return {}

        def release_capacity(env):
            ctx = env["ctx"]
            t = sys._trip_of(ctx)
            if not t:
                return {}
            did, vid = t["driver_id"], t["vehicle_id"]
            ds, vs = sys._driver_status(did), sys._vehicle_status(vid)
            if ds in ("ASSIGNED", "ARRIVED", "ON_TRIP"):
                sys.sem.assert_transition("driver", ds, "IDLE")
                sys.store.update("drivers", "driver_id", did, {"status": "IDLE"})
            if vs == "IN_USE":
                sys.sem.assert_transition("vehicle", vs, "IDLE")
                sys.store.update("vehicles", "vehicle_id", vid, {"status": "IDLE"})
            sys.graph.event(ctx["order_id"], "CAPACITY_RELEASED", ctx["ts"], {
                "driver": did, "driver_status": "IDLE",
                "vehicle": vid, "vehicle_status": "IDLE",
            })
            return {"driver.status": "IDLE", "vehicle.status": "IDLE"}

        def close_drives_interval(env):
            ctx = env["ctx"]
            t = sys._trip_of(ctx)
            if not t:
                return {}
            n1 = sys.graph.close_interval(t["driver_id"], "drives", t["vehicle_id"],
                                          ctx["ts"], ctx["order_id"])
            n2 = sys.graph.close_interval(t["trip_id"], "uses_vehicle", t["vehicle_id"],
                                          ctx["ts"], ctx["order_id"])
            return {"closed_drives": n1, "closed_vehicle_use": n2}

        def mark_cancelled(env):
            ctx = env["ctx"]
            oid, ts = ctx["order_id"], ctx["ts"]
            o = sys.store.one("SELECT status FROM orders WHERE order_id=?", (oid,))
            sys.sem.assert_transition("order", o["status"], "CANCELLED")
            sys.store.update("orders", "order_id", oid, {
                "status": "CANCELLED", "cancel_fee": env["fee"],
                "cancel_reason": ctx["cancel_reason"],
                "cancel_rule_id": env["rule"]["id"],
                "cancel_decision": env["decision"],
            })
            t = sys._trip_of(ctx)
            if t and t["status"] in ("CREATED", "ARRIVED", "ONBOARD"):
                sys.sem.assert_transition("trip", t["status"], "ENDED")
                sys.store.update("trips", "trip_id", t["trip_id"], {
                    "status": "ENDED", "ended_at": ts, "meter_end_at": ts,
                })
            sys.graph.event(oid, "ORDER_CANCELLED", ts, {
                "rule": env["rule"]["id"], "rule_name": env["rule"]["name"],
                "decision": env["decision"], "fee": env["fee"],
                "reason": ctx["cancel_reason"], "wait_minutes": ctx["wait_minutes"],
                "explain": env["explain"],
            })
            return {"order.status": "CANCELLED"}

        def emit_cancel(env):
            ctx, fee = env["ctx"], env["fee"]
            oid, ts = ctx["order_id"], ctx["ts"]
            t = sys._trip_of(ctx)
            if fee > 0:
                bid = sys.next_id("C")
                sys.store.insert("billing_records", {
                    "billing_id": bid, "trip_id": t["trip_id"] if t else None,
                    "order_id": oid, "base_fare": fee,
                    "distance_fee": 0.0, "time_fee": 0.0, "region_surcharge": 0.0,
                    "coupon_deduction": 0.0, "total": fee,
                    "source": "CANCELLATION", "created_at": ts,
                })
                if t:
                    sys.graph.add_fact(t["trip_id"], "produces", bid,
                                       subject_type="Trip", object_type="BillingRecord",
                                       occurred_at=ts, order_id=oid)
                sys.graph.event(oid, "CANCELLATION_CHARGED", ts, {
                    "billing": bid, "fee": fee, "rule": env["rule"]["id"],
                    "strategy": env["judge"].get("fee_strategy"),
                    "wait_minutes": ctx["wait_minutes"], "reason": ctx["cancel_reason"],
                })
            else:
                sys.graph.event(oid, "CANCELLATION_FREE", ts, {
                    "rule": env["rule"]["id"], "fee": 0,
                })
            return {}

        def settle(env):
            ctx = env["ctx"]
            oid, ts = ctx["order_id"], ctx["ts"]
            sys.store.update("payments", "payment_id", ctx["payment_id"],
                             {"status": "PAID", "paid_at": ts})
            sys.store.update("orders", "order_id", oid, {"status": "COMPLETED"})
            t = sys._trip_of(ctx)
            if t and t["status"] == "ENDED":
                sys.sem.assert_transition("trip", t["status"], "SETTLED")
                sys.store.update("trips", "trip_id", t["trip_id"], {"status": "SETTLED"})
            sys.graph.event(oid, "PAYMENT_SETTLED", ts, {
                "payment": ctx["payment_id"], "amount": ctx["payment.amount"],
            })
            return {}

        def emit_payment(env):
            return {}

        def create_refund(env):
            ctx = env["ctx"]
            oid, ts = ctx["order_id"], ctx["ts"]
            sys.store.insert("refunds", {
                "refund_id": ctx["refund_id"], "payment_id": ctx["payment_id"],
                "order_id": oid, "amount": ctx["refund.amount"],
                "created_at": ts, "reason": ctx.get("reason", ""),
            })
            sys.graph.add_fact(ctx["payment_id"], "refunds", ctx["refund_id"],
                               subject_type="Payment", object_type="Refund",
                               occurred_at=ts, order_id=oid)
            sys.graph.event(oid, "REFUND_CREATED", ts, {
                "refund": ctx["refund_id"], "amount": ctx["refund.amount"],
            })
            return {}

        def emit_refund(env):
            return {}

        return {
            "reject": reject, "emit_no_driver": emit_no_driver,
            "find_candidates": find_candidates, "send_dispatch": send_dispatch,
            "emit_dispatch": emit_dispatch, "create_trip": create_trip,
            "open_drives_interval": open_drives_interval, "emit_accept": emit_accept,
            "start_meter": start_meter, "emit_meter_start": emit_meter_start,
            "stop_meter": stop_meter, "compute_bill": compute_bill,
            "create_payment": create_payment, "emit_billing": emit_billing,
            "release_capacity": release_capacity,
            "close_drives_interval": close_drives_interval,
            "mark_cancelled": mark_cancelled, "emit_cancel": emit_cancel,
            "settle": settle, "emit_payment": emit_payment,
            "create_refund": create_refund, "emit_refund": emit_refund,
        }

    # ------------------------------------------------------------------ 完整示例
    def run_showcase(self) -> dict:
        """严格复现需求中的完整实例：A / O1 / X / V3 / T1 / C1 / P1。"""
        oid = self.create_order("A", "中关村", "清河站", "经济型",
                                coupon_id="C5", order_id="O1")
        cands = self.candidate_drivers(oid)
        disp = self.dispatch(oid, driver_id="X", vehicle_id="V3", trip_id="T1")
        self.driver_arrived(oid)
        self.passenger_onboard(oid)
        off = self.passenger_offboard(oid, distance_km=8.6, duration_min=22.0,
                                      route="中关村→上地→清河站")
        total = off["ctx"]["billing.total"]
        self.pay(oid, amount=total)
        ticket = self.support_ticket(oid, "为什么扣款")
        return {
            "order_id": oid, "passenger": "A", "driver": "X", "vehicle": "V3",
            "trip_id": disp["trip_id"], "candidate_count": len(cands),
            "billing_id": off["ctx"]["billing_id"],
            "payment_id": off["ctx"]["payment_id"],
            "total": total, "ticket_id": ticket,
        }
