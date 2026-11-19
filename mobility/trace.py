"""客服可追溯接口：沿关系还原「谁发起、谁执行、何时发生、产生什么结果」。"""
from __future__ import annotations

from .semantic import BusinessReject


def build_trace(sys, order_id: str) -> dict:
    o = sys.store.one("SELECT * FROM orders WHERE order_id=?", (order_id,))
    if not o:
        raise BusinessReject(f"订单不存在: {order_id}")
    t = sys.store.one("SELECT * FROM trips WHERE order_id=?", (order_id,))
    labels = {rid: r["label"] for rid, r in sys.sem.relations.items()}

    # ---------- 时间线：事件 + 关系事实 ----------
    timeline = []
    for f in sys.graph.facts_of(order_id):
        if f["kind"] == "event":
            fact = f"事件 · {f['object']}"
        else:
            fact = f"{f['subject']} —{labels.get(f['predicate'], f['predicate'])}→ {f['object']}"
            if f["valid_from"]:
                fact += f" 〔有效 {f['valid_from']} ~ {f['valid_to'] or '至今'}〕"
        timeline.append({
            "seq": f["seq"], "time": f["occurred_at"],
            "kind": f["kind"], "predicate": f["predicate"],
            "fact": fact, "payload": f["payload"],
        })

    # ---------- 关系链 ----------
    passengers = [r[0] for r in sys.conn.execute(
        "SELECT subject FROM facts WHERE predicate='initiates' AND object=? AND kind='relation'",
        (order_id,)).fetchall()]
    trips = [r[0] for r in sys.conn.execute(
        "SELECT object FROM facts WHERE predicate='has_trip' AND subject=? AND kind='relation'",
        (order_id,)).fetchall()]
    drivers = [r[0] for r in sys.conn.execute(
        "SELECT object FROM facts WHERE predicate='executed_by' AND subject LIKE 'T%' AND kind='relation' "
        "AND subject IN (SELECT trip_id FROM trips WHERE order_id=?)", (order_id,)).fetchall()]
    vehicles = [r[0] for r in sys.conn.execute(
        "SELECT object FROM facts WHERE predicate='uses_vehicle' AND kind='relation' "
        "AND subject IN (SELECT trip_id FROM trips WHERE order_id=?)", (order_id,)).fetchall()]
    billings = sys.store.all(
        "SELECT * FROM billing_records WHERE order_id=? ORDER BY billing_id", (order_id,))
    payments = sys.store.all("SELECT * FROM payments WHERE order_id=?", (order_id,))
    refunds = sys.store.all("SELECT * FROM refunds WHERE order_id=?", (order_id,))
    tickets = sys.store.all("SELECT * FROM support_tickets WHERE order_id=?", (order_id,))

    # ---------- 时态驾驶关系 ----------
    drives = []
    for row in sys.conn.execute(
        "SELECT * FROM facts WHERE predicate='drives' AND order_id=? ORDER BY seq", (order_id,)
    ).fetchall():
        d = dict(row)
        drives.append({
            "driver": d["subject"], "vehicle": d["object"],
            "valid_from": d["valid_from"], "valid_to": d["valid_to"],
        })

    # ---------- 「为什么扣款」解释 ----------
    explanation = _explain(sys, o, t, billings, payments, refunds)

    return {
        "order_id": order_id,
        "order": dict(o),
        "trip": dict(t) if t else None,
        "relations": {
            "发起乘客": passengers, "对应行程": trips,
            "执行司机": drivers, "使用车辆": vehicles,
            "计费记录": [b["billing_id"] for b in billings],
            "支付": [p["payment_id"] for p in payments],
            "退款": [r["refund_id"] for r in refunds],
            "客服工单": [k["ticket_id"] for k in tickets],
        },
        "temporal_drives": drives,
        "timeline": timeline,
        "timeline_length": len(timeline),
        "billing": [dict(b) for b in billings],
        "payments": [dict(p) for p in payments],
        "refunds": [dict(r) for r in refunds],
        "explanation": explanation,
        "can_reconstruct": bool(passengers and timeline),
    }


def _explain(sys, o, t, billings, payments, refunds) -> str:
    lines = [f"订单 {o['order_id']} 当前状态 {o['status']}。"]
    lines.append(
        f"由乘客 {o['passenger_id']} 于 {o['created_at']} 发起，"
        f"起点 {o['origin']}、终点 {o['destination']}、车型 {o['vehicle_type']}。"
    )
    if t:
        lines.append(
            f"行程 {t['trip_id']} 由司机 {t['driver_id']} 驾驶车辆 {t['vehicle_id']} 执行；"
            f"行程生成 {t['started_at']}，计费开始 {t['meter_start_at'] or '未开始'}，"
            f"计费结束 {t['meter_end_at'] or '未结束'}。"
        )
    for b in billings:
        if b["source"] == "TRIP":
            lines.append(
                f"扣款合计 ¥{b['total']:.2f} = 起步价 ¥{b['base_fare']:.2f} "
                f"+ 里程费 ¥{b['distance_fee']:.2f} + 时长费 ¥{b['time_fee']:.2f} "
                f"+ 区域附加 ¥{b['region_surcharge']:.2f} "
                f"− 优惠券抵扣 ¥{b['coupon_deduction']:.2f}（计费记录 {b['billing_id']}）。"
            )
        else:
            cfg = sys.sem.pricing_cfg["cancellation"]
            lines.append(
                f"本单为取消收费 ¥{b['total']:.2f}（计费记录 {b['billing_id']}），"
                f"命中规则 {o['cancel_rule_id']}，判定 {o['cancel_decision']}；"
                f"取消原因 {o['cancel_reason']}，免费等待 {cfg['free_wait_minutes']} 分钟，"
                f"超出部分按 ¥{cfg['per_wait_minute']}/分钟、上限 ¥{cfg['wait_fee_cap']} 计算。"
            )
    for p in payments:
        lines.append(f"支付 {p['payment_id']} 金额 ¥{p['amount']:.2f}，状态 {p['status']}。")
    for r in refunds:
        lines.append(f"退款 {r['refund_id']} 金额 ¥{r['amount']:.2f}。")
    if o["status"] == "CANCELLED" and not billings:
        lines.append(f"本单取消免费，命中规则 {o['cancel_rule_id']}（{o['cancel_decision']}）。")
    if not billings:
        lines.append("本单未产生任何费用。")
    return " ".join(lines)


def format_trace(tr: dict) -> str:
    out = [
        "=" * 78,
        f"客服追溯 · 订单 {tr['order_id']}",
        "=" * 78,
        f"关系链: 发起={tr['relations']['发起乘客']}  行程={tr['relations']['对应行程']}  "
        f"司机={tr['relations']['执行司机']}  车辆={tr['relations']['使用车辆']}  "
        f"计费={tr['relations']['计费记录']}  支付={tr['relations']['支付']}",
    ]
    for d in tr["temporal_drives"]:
        out.append(f"时态关系: 司机 {d['driver']} 驾驶 {d['vehicle']} "
                   f"[{d['valid_from']} ~ {d['valid_to'] or '至今'}]")
    out.append("-" * 78)
    out.append(f"完整时间线（{tr['timeline_length']} 条事实）:")
    for e in tr["timeline"]:
        out.append(f"  {e['time']}  {e['fact']}")
    out.append("-" * 78)
    out.append("为什么扣款:")
    out.append("  " + tr["explanation"])
    out.append("=" * 78)
    return "\n".join(out)
