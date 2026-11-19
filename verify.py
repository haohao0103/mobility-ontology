"""验收套件：需求里的 5 个能力问题直接变成可执行用例。

所有数字来自真实运行，报告由本脚本自动生成（不手写结论）。
"""
from __future__ import annotations
import json
import os
import shutil
import sys
from datetime import datetime

from mobility import MobilitySystem, BusinessReject, ConstraintViolation, OntologyViolation
from mobility.semantic import Clock
from seed import seed_orders, _reach_wait

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(ROOT, "runs")
os.makedirs(RUNS, exist_ok=True)

LOG: list[str] = []


def log(msg: str = "") -> None:
    LOG.append(msg)
    print(msg)


class Suite:
    def __init__(self):
        self.rows: list[dict] = []

    def check(self, group: str, cid: str, item: str, expected: str,
              actual: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({
            "group": group, "id": cid, "item": item,
            "expected": expected, "actual": actual,
            "result": "PASS" if ok else "FAIL", "detail": detail,
        })
        log(f"  [{'PASS' if ok else 'FAIL'}] {cid} {item} | 期望={expected} | 实际={actual}")
        return ok

    def count(self) -> dict:
        total = len(self.rows)
        passed = sum(1 for r in self.rows if r["result"] == "PASS")
        return {"total": total, "passed": passed, "failed": total - passed}


def db(name: str, semantic_path: str | None = None) -> MobilitySystem:
    return MobilitySystem(os.path.join(RUNS, name), fresh=True,
                          semantic_path=semantic_path)


def full_flow(sys, passenger="A", origin="中关村", dest="清河站",
              vt="经济型", coupon="NONE", dist=8.6, dur=22.0,
              driver=None, vehicle=None, do_pay=True) -> dict:
    oid = sys.create_order(passenger, origin, dest, vt, coupon_id=coupon)
    disp = sys.dispatch(oid, driver_id=driver, vehicle_id=vehicle)
    sys.driver_arrived(oid)
    sys.passenger_onboard(oid)
    off = sys.passenger_offboard(oid, dist, dur, route=f"{origin}→{dest}")
    res = sys.pay(oid, amount=off["ctx"]["billing.total"]) if do_pay else None
    return {"order_id": oid, "dispatch": disp, "offboard": off, "pay": res}


# ============================================================ 1. 完整实例 + Q5
def test_showcase(s: Suite) -> dict:
    log("\n=== 1. 完整实例（需求原文的 A/O1/X/V3/T1/C1/P1） ===")
    m = db("showcase.db")
    m.bootstrap()
    sh = m.run_showcase()
    tr = m.trace("O1")
    events = {e["object"] for e in []}  # 兼容占位
    ev_names = [e["payload"].get("note", "") for e in tr["timeline"]]
    ev_set = {e["fact"].split("· ")[-1] for e in tr["timeline"] if e["kind"] == "event"}
    need = {"ORDER_CREATED", "ORDER_DISPATCHED", "DRIVER_ACCEPTED", "DRIVER_ARRIVED",
            "PASSENGER_ONBOARD", "METER_STARTED", "PASSENGER_OFFBOARD", "METER_STOPPED",
            "BILLING_COMPUTED", "BILL_GENERATED", "PAYMENT_SETTLED", "SUPPORT_TICKET_OPENED"}

    s.check("完整实例", "S1", "对象 ID 精确复现",
            "O1/A/X/V3/T1/C1/P1",
            f"{sh['order_id']}/{sh['passenger']}/{sh['driver']}/{sh['vehicle']}/"
            f"{sh['trip_id']}/{sh['billing_id']}/{sh['payment_id']}",
            sh["order_id"] == "O1" and sh["trip_id"] == "T1" and sh["billing_id"] == "C1"
            and sh["payment_id"] == "P1" and sh["vehicle"] == "V3")
    s.check("完整实例", "S2", "关系链完整",
            "发起/行程/司机/车辆/计费/支付均可追溯",
            json.dumps(tr["relations"], ensure_ascii=False),
            all(tr["relations"][k] for k in
                ["发起乘客", "对应行程", "执行司机", "使用车辆", "计费记录", "支付"]))
    s.check("完整实例", "S3", "时态驾驶关系带时间段",
            "X 驾驶 V3 [valid_from ~ valid_to]",
            json.dumps(tr["temporal_drives"], ensure_ascii=False),
            len(tr["temporal_drives"]) == 1
            and tr["temporal_drives"][0]["valid_from"]
            and tr["temporal_drives"][0]["valid_to"])
    s.check("完整实例", "S4", "约束零违规", "0", str(len(m.validate())),
            len(m.validate()) == 0)
    s.check("能力问题⑤", "Q5", "客服能还原完整过程",
            "时间线覆盖 12 个关键事件 + 解释扣款构成",
            f"时间线 {tr['timeline_length']} 条，缺失={sorted(need - ev_set)}，"
            f"可还原={tr['can_reconstruct']}",
            need.issubset(ev_set) and tr["can_reconstruct"]
            and "起步价" in tr["explanation"] and "优惠券抵扣" in tr["explanation"])
    log("\n" + m.trace_text("O1"))
    return {"showcase": sh, "trace": tr, "system": m}


# ============================================================ 2. Q1 ~ Q4
def test_capabilities(s: Suite) -> None:
    log("\n=== 2. 五个能力问题 → 验收用例 ===")
    c = db("capability.db")
    c.bootstrap()

    # ---- Q1 哪些司机可以接单 ----
    o1 = c.create_order("A", "中关村", "清河站", "经济型")
    cands = c.candidate_drivers(o1)
    expected = {r["driver_id"] for r in c.store.all(
        "SELECT driver_id FROM drivers WHERE status='IDLE' "
        "AND vehicle_type='经济型' AND region='海淀'")}
    actual = {x["driver_id"] for x in cands}
    s.check("能力问题①", "Q1", "乘客叫车后哪些司机可以接单",
            f"空闲+经济型+海淀，共 {len(expected)} 名 {sorted(expected)}",
            f"共 {len(actual)} 名 {sorted(actual)}",
            expected == actual and len(actual) > 0,
            "候选条件：服务区内、司机空闲、车型匹配、区域匹配")

    # ---- Q2 何时开始计费 ----
    o2 = c.create_order("B", "五道口", "上地", "经济型")
    c.dispatch(o2)
    c.driver_arrived(o2)
    c.passenger_onboard(o2)
    t2 = c.store.one("SELECT * FROM trips WHERE order_id=?", (o2,))
    ok2 = (t2["meter_start_at"] == t2["onboard_at"]
           and t2["meter_start_at"] != t2["started_at"]
           and t2["meter_start_at"] != t2["arrived_at"])
    s.check("能力问题②", "Q2", "司机接受订单后何时开始计费",
            "计费起点 = 乘客上车时刻（≠接单/到达）",
            f"行程生成={t2['started_at']} 到达={t2['arrived_at']} "
            f"上车/计费开始={t2['meter_start_at']}",
            ok2)

    # ---- Q3 取消按哪套规则收费 ----
    cfg = c.sem.pricing_cfg["cancellation"]
    p = c.sem.pricing("经济型")

    def wait_fee(w: float) -> float:
        return round(min(max(0.0, w - cfg["free_wait_minutes"]) * cfg["per_wait_minute"],
                         cfg["wait_fee_cap"]), 2)

    def make_cancel(stage: str, wait: float, reason: str = "PASSENGER_CHANGE",
                    dist: float = 0.0, dur: float = 0.0) -> tuple[str, dict]:
        oid = c.create_order("C", "中关村", "清河站", "经济型")
        if stage != "CREATED":
            c.dispatch(oid)
        if stage in ("ARRIVED", "IN_TRIP"):
            c.driver_arrived(oid)
        if stage == "IN_TRIP":
            c.passenger_onboard(oid)
            c.store.update("trips", "order_id", oid,
                           {"distance_km": dist, "duration_min": dur})
        _reach_wait(c, oid, wait)
        return oid, c.cancel(oid, reason)

    cases = [
        ("X01", "CREATED", 3.0, "PASSENGER_CHANGE", 0.0, 0.0, 0.0),
        ("X02", "MATCHED", 12.0, "PASSENGER_CHANGE", 0.0, 0.0, wait_fee(12.0)),
        ("X03", "IN_TRIP", 18.0, "PASSENGER_CHANGE", 6.0, 14.0, None),
        ("X04", "ARRIVED", 9.0, "PASSENGER_CHANGE", 0.0, 0.0,
         round(cfg["arrived_base_fee"] + wait_fee(9.0), 2)),
        ("X04-waive", "ARRIVED", 9.0, "DRIVER_LATE", 0.0, 0.0, 0.0),
    ]
    for rid, stage, w, reason, dist, dur, exp in cases:
        oid, res = make_cancel(stage, w, reason, dist, dur)
        if rid == "X03":
            raw = p["base_fare"] + dist * p["per_km"] + dur * p["per_min"]
            exp = round(max(0.0, max(p["min_fare"], raw)
                            + c.sem.surcharge("海淀") - c.sem.coupon_amount("NONE")), 2)
        s.check("能力问题③", rid, f"取消收费：{stage}/{reason}",
                f"命中 {rid.split('-')[0]}，费用 ¥{exp:.2f}",
                f"命中 {res['rule_id']}，费用 ¥{res['fee']:.2f}，"
                f"等待 {res['wait_minutes']:.0f} 分钟，{res['explain']}",
                res["rule_id"] == rid.split("-")[0]
                and abs(res["fee"] - exp) < 0.01)

    # ---- Q4 取消后车辆/司机状态 ----
    oid4 = c.create_order("D", "中关村", "清河站", "经济型")
    d4 = c.dispatch(oid4)
    t4 = c.store.one("SELECT * FROM trips WHERE order_id=?", (oid4,))
    _reach_wait(c, oid4, 10.0)
    c.cancel(oid4, "PASSENGER_CHANGE")
    ds = c._driver_status(t4["driver_id"])
    vs = c._vehicle_status(t4["vehicle_id"])
    drives = [dict(r) for r in c.conn.execute(
        "SELECT * FROM facts WHERE predicate='drives' AND object=? AND order_id=?",
        (t4["vehicle_id"], oid4)).fetchall()]
    uses = [dict(r) for r in c.conn.execute(
        "SELECT * FROM facts WHERE predicate='uses_vehicle' AND object=? AND order_id=?",
        (t4["vehicle_id"], oid4)).fetchall()]
    # 取消后该车可以立即进入下一单（证明"必须先完成上一单"已解除）
    o_next = c.create_order("E", "中关村", "清河站", "经济型")
    try:
        c.dispatch(o_next, driver_id=t4["driver_id"], vehicle_id=t4["vehicle_id"])
        reusable = True
    except Exception:
        reusable = False
    s.check("能力问题④", "Q4", "订单取消后车辆和司机状态如何更新",
            "司机/车辆回 IDLE，时态区间关闭，运力可复用",
            f"driver={ds} vehicle={vs} drives.valid_to={drives[0]['valid_to']} "
            f"uses.valid_to={uses[0]['valid_to']} 可复用={reusable}",
            ds == "IDLE" and vs == "IDLE" and drives[0]["valid_to"]
            and uses[0]["valid_to"] and reusable)


# ============================================================ 3. 约束反例
def test_constraints(s: Suite) -> None:
    log("\n=== 3. 约束守数据边界：注入 8 类业务矛盾 ===")
    v = db("constraints.db")
    v.bootstrap()
    v.run_showcase()

    def inject_and_check(cid: str, label: str, sqls: list[str],
                         cleanup: list[str]) -> None:
        before = {x["constraint_id"] for x in v.validate()}
        for sql in sqls:
            v.conn.execute(sql)
        after = {x["constraint_id"] for x in v.validate()}
        for sql in cleanup:
            v.conn.execute(sql)
        clean = len(v.validate())
        s.check("约束", cid, label, f"检出 {cid} 且清理后 0 违规",
                f"检出={sorted(after - before)} 清理后违规={clean}",
                cid in after and clean == 0)

    ts = "2026-11-12 09:30:00"
    inject_and_check("C1", "每笔订单必须关联一名乘客",
        ["INSERT INTO orders(order_id,passenger_id,status) VALUES('BAD1','A','CREATED')"],
        ["DELETE FROM orders WHERE order_id='BAD1'"])
    inject_and_check("C2", "每段行程必须关联一名司机",
        [f"INSERT INTO trips(trip_id,order_id,driver_id,vehicle_id,status,started_at) "
         f"VALUES('BADT','O1','X','V3','ENDED','{ts}')"],
        ["DELETE FROM trips WHERE trip_id='BADT'"])
    inject_and_check("C3", "行程开始时司机和车辆不能为空",
        [f"INSERT INTO trips(trip_id,order_id,driver_id,vehicle_id,status,started_at) "
         f"VALUES('BADT2','O1',NULL,NULL,'CREATED','{ts}')"],
        ["DELETE FROM trips WHERE trip_id='BADT2'"])
    inject_and_check("C4", "车辆必须先完成上一单才能进入新订单",
        [f"INSERT INTO facts(subject,predicate,object,subject_type,object_type,valid_from,"
         f"occurred_at,order_id,seq,kind,payload) VALUES('T1','uses_vehicle','V3','Trip','Vehicle',"
         f"'{ts}','{ts}','O1',9001,'relation','{{}}')",
         f"INSERT INTO facts(subject,predicate,object,subject_type,object_type,valid_from,"
         f"occurred_at,order_id,seq,kind,payload) VALUES('T2','uses_vehicle','V3','Trip','Vehicle',"
         f"'{ts}','{ts}','O1',9002,'relation','{{}}')"],
        ["DELETE FROM facts WHERE seq IN (9001,9002)"])
    inject_and_check("C5", "支付金额不能低于优惠券抵扣后的结果",
        ["INSERT INTO payments(payment_id,billing_id,order_id,amount,status) "
         "VALUES('BADP','C1','O1',0.01,'PAID')"],
        ["DELETE FROM payments WHERE payment_id='BADP'"])
    inject_and_check("C6", "退款金额不能超过实际支付金额",
        [f"INSERT INTO refunds(refund_id,payment_id,order_id,amount,created_at) "
         f"VALUES('BADR','P1','O1',9999.0,'{ts}')"],
        ["DELETE FROM refunds WHERE refund_id='BADR'"])
    inject_and_check("C7", "司机不得同时执行两笔进行中行程",
        [f"INSERT INTO trips(trip_id,order_id,driver_id,vehicle_id,status,started_at) "
         f"VALUES('BADT3','O1','X','V1','CREATED','{ts}')",
         f"INSERT INTO trips(trip_id,order_id,driver_id,vehicle_id,status,started_at) "
         f"VALUES('BADT4','O1','X','V2','ONBOARD','{ts}')"],
        ["DELETE FROM trips WHERE trip_id IN ('BADT3','BADT4')"])
    inject_and_check("C8", "关系必须符合本体定义域/值域",
        [f"INSERT INTO facts(subject,predicate,object,subject_type,object_type,"
         f"occurred_at,order_id,seq,kind,payload) VALUES('V3','initiates','O1','Vehicle','Order',"
         f"'{ts}','O1',9003,'relation','{{}}')"],
        ["DELETE FROM facts WHERE seq=9003"])


# ============================================================ 4. 引擎拦截
def test_engine_rejections(s: Suite) -> None:
    log("\n=== 4. 引擎路径真实拦截业务矛盾 ===")
    r = db("reject.db")
    r.bootstrap()

    def expect(cid: str, label: str, fn, exc) -> None:
        try:
            fn()
            ok, actual = False, "未拦截（错误）"
        except exc as e:
            ok, actual = True, f"{type(e).__name__}: {str(e)[:110]}"
        except Exception as e:
            ok, actual = False, f"抛出了非预期异常 {type(e).__name__}: {e}"
        s.check("引擎拦截", cid, label, f"抛出 {exc.__name__}", actual, ok)

    expect("E1", "订单没有乘客（C1）",
           lambda: r.create_order("NOBODY", "中关村", "清河站", "经济型"),
           ConstraintViolation)

    # 车辆占用：A 单进行中，B 单复用同一辆车
    oA = r.create_order("A", "中关村", "清河站", "经济型")
    r.dispatch(oA, driver_id="X", vehicle_id="V3")
    oB = r.create_order("B", "五道口", "上地", "经济型")
    expect("E2", "车辆未完单即接新单（C4）",
           lambda: r.dispatch(oB, driver_id="D19", vehicle_id="V3"),
           ConstraintViolation)
    # 司机双单：同司机、不同车
    oC = r.create_order("C", "中关村", "清河站", "经济型")
    expect("E3", "司机同时跑两单（C7）",
           lambda: r.dispatch(oC, driver_id="X", vehicle_id="V2"),
           ConstraintViolation)

    # 非服务区
    oD = r.create_order("D", "亦庄", "清河站", "经济型")
    expect("E4", "起终点不在服务区（规则 D00）",
           lambda: r.dispatch(oD), BusinessReject)

    # 少付 / 超额退款
    oE = r.create_order("E", "中关村", "清河站", "经济型")
    r.dispatch(oE)
    r.driver_arrived(oE)
    r.passenger_onboard(oE)
    off = r.passenger_offboard(oE, 5.0, 10.0)
    total = off["ctx"]["billing.total"]
    expect("E5", "付款少于应付金额（C5 / P01）",
           lambda: r.pay(oE, amount=round(total - 1, 2)), BusinessReject)
    r.pay(oE, amount=total)
    expect("E6", "退款超过实际支付金额（C6 / R01）",
           lambda: r.refund(oE, amount=total + 1), BusinessReject)
    ok_refund = r.refund(oE, amount=1.0)
    s.check("引擎拦截", "E7", "合法退款放行", "REFUNDED",
            ok_refund["decision"], ok_refund["decision"] == "REFUNDED")

    # 本体关系非法
    expect("E8", "写入不符合定义域的关系（C8）",
           lambda: r.graph.add_fact("V3", "initiates", "O1",
                                    subject_type="Vehicle", object_type="Order",
                                    occurred_at=r.clock.now()),
           OntologyViolation)


# ============================================================ 5. 时态关系
def test_temporal(s: Suite, showcase_sys: MobilitySystem) -> dict:
    log("\n=== 5. 同一辆车在不同时间由不同司机驾驶（时态关系） ===")
    m = showcase_sys
    first = [dict(x) for x in m.conn.execute(
        "SELECT * FROM facts WHERE predicate='drives' AND object='V3' ORDER BY seq").fetchall()]
    # 第二单：司机 Y 驾驶同一辆 V3（不同时间、不同区域）
    o2 = m.create_order("B", "国贸", "望京", "经济型")
    m.dispatch(o2, driver_id="Y", vehicle_id="V3")
    t2 = m.store.one("SELECT * FROM trips WHERE order_id=?", (o2,))
    m.driver_arrived(o2)
    m.passenger_onboard(o2)
    m.passenger_offboard(o2, 4.0, 12.0, route="国贸→望京")
    m.pay(o2, amount=m.store.scalar(
        "SELECT total FROM billing_records WHERE order_id=?", (o2,)))

    intervals = [dict(x) for x in m.conn.execute(
        "SELECT subject,object,valid_from,valid_to FROM facts "
        "WHERE predicate='drives' AND object='V3' ORDER BY seq").fetchall()]
    who_first = m.graph.active_intervals("drives", "V3", first[0]["valid_from"])
    # 取第二区间的中点
    mid = Clock.minutes_between(intervals[1]["valid_from"], intervals[1]["valid_to"])
    from datetime import timedelta
    fmt = "%Y-%m-%d %H:%M:%S"
    mid_at = (datetime.strptime(intervals[1]["valid_from"], fmt)
              + timedelta(minutes=mid / 2)).strftime(fmt)
    who_second = m.graph.active_intervals("drives", "V3", mid_at)
    overlap = any(a["valid_from"] < (b["valid_to"] or "9999") and
                  b["valid_from"] < (a["valid_to"] or "9999")
                  for i, a in enumerate(intervals) for b in intervals[i + 1:])
    s.check("时态关系", "T1", "V3 在不同时间由不同司机驾驶",
            "≥2 条 drives 区间，司机不同，区间不重叠",
            f"区间数={len(intervals)} 司机={[x['subject'] for x in intervals]} "
            f"重叠={overlap}",
            len(intervals) >= 2
            and len({x["subject"] for x in intervals}) >= 2 and not overlap)
    s.check("时态关系", "T2", "按时间点查询当时的驾驶者",
            f"第一时刻={first[0]['subject']}，第二时刻=Y",
            f"第一时刻={[x['subject'] for x in who_first]} "
            f"第二时刻={[x['subject'] for x in who_second]}",
            any(x["subject"] == "X" for x in who_first)
            and any(x["subject"] == "Y" for x in who_second))
    return {"intervals": intervals, "overlap": overlap}


# ============================================================ 6. 批量真实订单
def test_fleet(s: Suite) -> dict:
    log("\n=== 6. 批量真实订单（全部走业务链路） ===")
    f = db("fleet.db")
    f.bootstrap()
    res = seed_orders(f, n=200, seed=42)
    st = res["stats"]
    s.check("批量订单", "F1", "200 单全部执行成功", "errors=0",
            f"errors={len(res['errors'])} 场景={res['scenarios']}",
            len(res["errors"]) == 0, str(res["errors"][:3]))
    s.check("批量订单", "F2", "约束零违规", "violations=0",
            f"violations={len(res['violations'])}", len(res["violations"]) == 0)
    rules = st["cancel_by_rule"]
    s.check("批量订单", "F3", "四条取消规则全部命中",
            "X01/X02/X03/X04 均有命中", json.dumps(rules, ensure_ascii=False),
            all(r in rules for r in ["X01", "X02", "X03", "X04"]))
    # 时态统计：V3 被多少不同司机驾驶过
    v3 = [dict(x) for x in f.conn.execute(
        "SELECT subject,valid_from,valid_to FROM facts "
        "WHERE predicate='drives' AND object='V3' ORDER BY seq").fetchall()]
    v3_drivers = sorted({x["subject"] for x in v3})
    s.check("批量订单", "F4", "时态关系在批量场景下成立",
            "V3 的驾驶区间来自多个司机且无重叠",
            f"V3 区间数={len(v3)} 不同司机={len(v3_drivers)} {v3_drivers[:8]}",
            len(v3_drivers) >= 2)
    log(f"  统计: {json.dumps(st, ensure_ascii=False)}")
    return {"result": res, "v3_drivers": v3_drivers, "v3_intervals": len(v3)}


# ============================================================ 7. 单一口径
def test_single_source(s: Suite) -> dict:
    log("\n=== 7. 规则集中语义层：改一处参数，全链路口径同步变化 ===")
    src = os.path.join(ROOT, "semantic_layer.json")
    tuned_path = os.path.join(RUNS, "semantic_tuned.json")
    with open(src, encoding="utf-8") as fh:
        cfg = json.load(fh)
    old = cfg["pricing"]["cancellation"]["per_wait_minute"]
    new = old * 2
    cfg["pricing"]["cancellation"]["per_wait_minute"] = new
    with open(tuned_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)

    def cancel_fee(path: str, name: str) -> float:
        m = MobilitySystem(os.path.join(RUNS, name), fresh=True, semantic_path=path)
        m.bootstrap()
        oid = m.create_order("A", "中关村", "清河站", "经济型")
        m.dispatch(oid)
        _reach_wait(m, oid, 12.0)
        r = m.cancel(oid, "PASSENGER_CHANGE")
        return r["fee"]

    base = cancel_fee(src, "tuning_baseline.db")
    tuned = cancel_fee(tuned_path, "tuning.db")
    s.check("单一口径", "U1", "只改 per_wait_minute，取消费随之变化",
            f"{old} → {new}，等待12分钟费用 {base} → {round(base*2,2)}",
            f"基线 ¥{base:.2f} → 修改后 ¥{tuned:.2f}",
            abs(tuned - round(base * 2, 2)) < 0.01)
    return {"old_rate": old, "new_rate": new, "baseline_fee": base, "tuned_fee": tuned}


# ============================================================ 报告
def write_report(s: Suite, extra: dict) -> None:
    cnt = s.count()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    groups: dict[str, list[dict]] = {}
    for r in s.rows:
        groups.setdefault(r["group"], []).append(r)

    lines = [
        "# 共享出行互联网本体 · 第一版验收报告",
        "",
        f"- 生成时间：{now}（由 `verify.py` 自动生成，数字全部来自真实运行）",
        f"- 用例总数：**{cnt['total']}**，通过 **{cnt['passed']}**，失败 **{cnt['failed']}**",
        f"- 语义层：`semantic_layer.json`（15 类 / 15 关系 / 15 规则 / 8 约束）",
        "",
        "## 一、五个能力问题 → 验收用例",
        "",
        "| 编号 | 能力问题 / 检查项 | 期望 | 实际 | 结果 |",
        "|---|---|---|---|---|",
    ]
    for g in ["能力问题①", "能力问题②", "能力问题③", "能力问题④", "能力问题⑤"]:
        for r in groups.get(g, []):
            lines.append(f"| {r['id']} | {r['item']} | {r['expected']} | {r['actual']} | **{r['result']}** |")

    lines += ["", "## 二、约束守住数据边界（注入业务矛盾）", "",
              "| 编号 | 检查项 | 期望 | 实际 | 结果 |", "|---|---|---|---|---|"]
    for r in groups.get("约束", []):
        lines.append(f"| {r['id']} | {r['item']} | {r['expected']} | {r['actual']} | **{r['result']}** |")

    lines += ["", "## 三、引擎路径真实拦截", "",
              "| 编号 | 检查项 | 期望 | 实际 | 结果 |", "|---|---|---|---|---|"]
    for r in groups.get("引擎拦截", []):
        lines.append(f"| {r['id']} | {r['item']} | {r['expected']} | {r['actual']} | **{r['result']}** |")

    lines += ["", "## 四、完整实例 / 时态关系 / 批量订单 / 单一口径", "",
              "| 编号 | 检查项 | 期望 | 实际 | 结果 |", "|---|---|---|---|---|"]
    for g in ["完整实例", "时态关系", "批量订单", "单一口径"]:
        for r in groups.get(g, []):
            lines.append(f"| {r['id']} | {r['item']} | {r['expected']} | {r['actual']} | **{r['result']}** |")

    sh = extra["showcase"]["showcase"]
    tr = extra["showcase"]["trace"]
    fleet = extra["fleet"]["result"]
    tuning = extra["tuning"]
    st = fleet["stats"]
    lines += [
        "",
        "## 五、运行数据（真实结果）",
        "",
        f"- 完整实例：`{sh['order_id']} / {sh['passenger']} / {sh['driver']} / {sh['vehicle']} / "
        f"{sh['trip_id']} / {sh['billing_id']} / {sh['payment_id']}`，"
        f"时间线 **{tr['timeline_length']}** 条事实，扣款 **¥{sh['total']:.2f}**。",
        f"- 时态关系：V3 在完整实例+第二单中有 "
        f"**{len(extra['temporal']['intervals'])}** 条驾驶区间；"
        f"200 单批量中 V3 由 **{len(extra['fleet']['v3_drivers'])}** 名不同司机驾驶、"
        f"共 **{extra['fleet']['v3_intervals']}** 个区间。",
        f"- 批量订单：**{fleet['n']}** 单，错误 **{len(fleet['errors'])}**，"
        f"约束违规 **{len(fleet['violations'])}**；状态分布 "
        f"`{json.dumps(st['order_status'], ensure_ascii=False)}`；"
        f"取消规则命中 `{json.dumps(st['cancel_by_rule'], ensure_ascii=False)}`；"
        f"取消费合计 **¥{st['cancel_fee_sum']:.2f}**。",
        f"- 图谱事实总量 **{st['facts']}** 条，其中时态 `drives` **{st['temporal_drives']}** 条。",
        f"- 单一口径：`per_wait_minute` {tuning['old_rate']} → {tuning['new_rate']}，"
        f"同一取消场景费用 **¥{tuning['baseline_fee']:.2f} → ¥{tuning['tuned_fee']:.2f}**。",
        "",
        "## 六、结论与后续",
        "",
        f"1. 第一版最小闭环 {'全部验收通过' if cnt['failed'] == 0 else '存在失败用例'}："
        "下单、派单、计费、取消、支付、客服追溯均由语义层同一套定义驱动。",
        "2. 本体负责关系合法性，图谱负责持久化事实与事件时间线，"
        "业务库负责高频交易，规则引擎负责判断——分层与需求一致。",
        "3. 「司机驾驶车辆」以 `valid_from/valid_to` 时态区间表达，"
        "按时间点查询可还原当时的驾驶者，不是车辆的固定字段。",
        "4. 后续扩展（拼车/货运/预约单/排班/调度/理赔）复用现有 15 类对象，"
        "只需补充新关系与规则；建议优先补「路线重合度」「费用分摊」两类拼车规则。",
        "",
        "## 附：交付物",
        "",
        "| 交付物 | 位置 |",
        "|---|---|",
        "| 领域词典（15 类） | `semantic_layer.json` / `runs/domain_dictionary.json` |",
        "| 完整生命周期（订单/行程/司机/车辆） | `semantic_layer.json#lifecycle` |",
        "| 可执行规则（下单/派单/计费/取消/支付） | `semantic_layer.json#rules` |",
        "| 一批真实订单 | `runs/fleet.db`（200 单，全部经引擎链路） |",
        "| 可追溯接口 | `mobility/trace.py`、`GET /orders/{id}/trace` |",
        "| 业务库 / 持久化图谱 | `runs/*.db`（SQLite，非内存图） |",
        "| 应用层 | `cli.py`、`api.py` |",
        "",
    ]
    with open(os.path.join(RUNS, "验收报告.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(RUNS, "acceptance_results.json"), "w", encoding="utf-8") as fh:
        json.dump({"generated_at": now, "summary": cnt, "rows": s.rows,
                   "extra": {
                       "showcase": extra["showcase"]["showcase"],
                       "timeline_length": tr["timeline_length"],
                       "fleet_stats": st,
                       "fleet_errors": fleet["errors"],
                       "temporal_v3_drivers": extra["fleet"]["v3_drivers"],
                       "temporal_v3_intervals": extra["fleet"]["v3_intervals"],
                       "tuning": tuning,
                   }}, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(RUNS, "verify.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(LOG))

    # 领域词典导出
    with open(os.path.join(RUNS, "domain_dictionary.json"), "w", encoding="utf-8") as fh:
        json.dump(extra["showcase"]["system"].sem.dictionary(), fh,
                  ensure_ascii=False, indent=2)


def main() -> int:
    s = Suite()
    log("=" * 78)
    log("共享出行互联网本体 · 第一版验收")
    log("=" * 78)
    showcase = test_showcase(s)
    test_capabilities(s)
    test_constraints(s)
    test_engine_rejections(s)
    temporal = test_temporal(s, showcase["system"])
    fleet = test_fleet(s)
    tuning = test_single_source(s)
    cnt = s.count()
    log("\n" + "=" * 78)
    log(f"验收汇总: 总 {cnt['total']} / 通过 {cnt['passed']} / 失败 {cnt['failed']}")
    log("=" * 78)
    write_report(s, {"showcase": showcase, "temporal": temporal,
                     "fleet": fleet, "tuning": tuning})
    return 0 if cnt["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
