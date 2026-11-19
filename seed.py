"""批量真实订单：全部经业务链路（下单→派单→行程→计费/取消）产生，不是手工拼数字。"""
from __future__ import annotations
import random
from collections import defaultdict

from mobility.semantic import Clock


def _reach_wait(sys, order_id: str, target_minutes: float) -> float:
    """把逻辑时钟推进到「距下单恰好 target_minutes 分钟」，等待时长来自真实时间差。"""
    o = sys.store.one("SELECT created_at FROM orders WHERE order_id=?", (order_id,))
    cur = Clock.minutes_between(o["created_at"], sys.clock.now())
    sys.clock.advance(max(0, target_minutes - cur))
    return Clock.minutes_between(o["created_at"], sys.clock.now())


def seed_orders(sys, n: int = 200, seed: int = 42) -> dict:
    rng = random.Random(seed)
    service = [a for a in sys.sem.address_region if sys.sem.in_service_area(a)]
    passengers = [r[0] for r in sys.conn.execute(
        "SELECT passenger_id FROM passengers").fetchall()]
    combos = sys.store.all("SELECT DISTINCT vehicle_type, region FROM drivers")
    coupons = ["NONE", "C5", "C10"]
    # 固定轮转保证场景分布确定：60% 完整、40% 四种取消状态各 10%
    cycle = ["full"] * 6 + ["cancel_created", "cancel_matched",
                            "cancel_arrived", "cancel_intrip"]
    counts: dict[str, int] = defaultdict(int)
    errors: list[str] = []
    rows: list[dict] = []

    for i in range(n):
        scenario = cycle[i % len(cycle)]
        combo = combos[rng.randrange(len(combos))]
        vt, rg = combo["vehicle_type"], combo["region"]
        local = [a for a in service if sys.sem.region_of(a) == rg] or service
        origin, dest = rng.choice(local), rng.choice(service)
        passenger = passengers[i % len(passengers)]
        coupon = coupons[i % len(coupons)]
        wait_target = rng.choice([2, 5, 8, 12, 20, 40])

        try:
            oid = sys.create_order(passenger, origin, dest, vt, coupon_id=coupon)
            if scenario == "cancel_created":
                _reach_wait(sys, oid, wait_target)
                res = sys.cancel(oid, "PASSENGER_CHANGE")
            else:
                ds = sys.store.all(
                    "SELECT driver_id FROM drivers WHERE status='IDLE' AND vehicle_type=? "
                    "AND region=? ORDER BY driver_id", (vt, rg))
                vs = sys.store.all(
                    "SELECT vehicle_id FROM vehicles WHERE status='IDLE' AND vehicle_type=? "
                    "ORDER BY vehicle_id", (vt,))
                if not ds or not vs:
                    raise RuntimeError(f"无可用运力 combo={vt}/{rg}")
                did = ds[(i * 3) % len(ds)]["driver_id"]
                vid = vs[(i * 7) % len(vs)]["vehicle_id"]
                sys.dispatch(oid, driver_id=did, vehicle_id=vid)

                if scenario == "cancel_matched":
                    _reach_wait(sys, oid, wait_target)
                    res = sys.cancel(oid, "PASSENGER_CHANGE")
                elif scenario == "cancel_arrived":
                    sys.driver_arrived(oid)
                    reason = rng.choice(["PASSENGER_CHANGE", "DRIVER_LATE", "WEATHER"])
                    _reach_wait(sys, oid, wait_target)
                    res = sys.cancel(oid, reason)
                elif scenario == "cancel_intrip":
                    sys.driver_arrived(oid)
                    sys.passenger_onboard(oid)
                    # 模拟 GPS 累计上报：取消时按已行驶里程/时长计费
                    sys.store.update("trips", "order_id", oid, {
                        "distance_km": round(rng.uniform(1, 12), 1),
                        "duration_min": round(rng.uniform(3, 30), 1),
                        "route": f"{origin}→{dest}",
                    })
                    _reach_wait(sys, oid, wait_target)
                    res = sys.cancel(oid, "PASSENGER_CHANGE")
                else:
                    sys.driver_arrived(oid)
                    sys.passenger_onboard(oid)
                    dist = round(rng.uniform(2, 25), 1)
                    dur = round(rng.uniform(5, 60), 1)
                    off = sys.passenger_offboard(oid, dist, dur, route=f"{origin}→{dest}")
                    res = sys.pay(oid, amount=off["ctx"]["billing.total"])
                    res["_distance_km"] = dist
                    res["_duration_min"] = dur
            counts[scenario] += 1
            rows.append({
                "order_id": oid, "scenario": scenario,
                "rule_id": res.get("rule_id"), "decision": res.get("decision"),
                "fee": res.get("fee", 0.0),
                "wait_minutes": res.get("wait_minutes"),
            })
        except Exception as e:
            errors.append(f"#{i} {scenario} {vt}/{rg}: {type(e).__name__}: {e}")

    return {
        "n": n, "seed": seed,
        "scenarios": dict(counts),
        "errors": errors,
        "orders": rows,
        "stats": sys.stats(),
        "violations": sys.validate(),
    }
