"""SHACL 风格业务约束校验：拒绝的不只是格式错误，还包括业务矛盾。"""
from __future__ import annotations


class ConstraintValidator:
    def __init__(self, store, graph, semantic):
        self.store, self.graph, self.sem = store, graph, semantic

    def validate(self) -> list[dict]:
        """全量扫描，返回所有违规（空列表 = 数据边界干净）。"""
        out: list[dict] = []
        for c in self.sem.constraints:
            t = c["type"]
            handler = getattr(self, f"_check_{t}", None)
            if handler is None:
                raise ValueError(f"未实现的约束类型: {t}")
            hits = handler(c)
            for h in hits:
                out.append({
                    "constraint_id": c["id"],
                    "label": c["label"],
                    "severity": c["severity"],
                    "detail": h,
                })
        return out

    def assert_clean(self, stage: str = "") -> None:
        bad = self.validate()
        if bad:
            msg = "; ".join(f"{v['constraint_id']}({v['detail']})" for v in bad)
            raise __import__("mobility.semantic", fromlist=["ConstraintViolation"]).ConstraintViolation(
                f"约束校验失败@{stage}: {msg}"
            )

    # C1 / C2：最小基数
    def _check_min_count(self, c) -> list[str]:
        table = "orders" if c["target"] == "Order" else "trips"
        key = "order_id" if c["target"] == "Order" else "trip_id"
        rel = c["relation"]
        hits = []
        for row in self.store.all(f"SELECT {key} FROM {table}"):
            if c["target"] == "Order":
                # Order 是 initiates 的宾语
                n = self.graph.count_relation(rel, row[key])
            else:
                # Trip 是 executed_by 的主语
                n = self.graph.count_relation_subject(rel, row[key])
            if n < c["min"]:
                hits.append(f"{key}={row[key]} 缺少关系 {rel} (需>={c['min']}，实际{n})")
        return hits

    # C3：行程开始时司机/车辆不能为空
    def _check_required_fields(self, c) -> list[str]:
        hits = []
        for t in self.store.all("SELECT * FROM trips"):
            if t["status"] in c["when_status"]:
                missing = [f for f in c["fields"] if not t.get(f)]
                if missing:
                    hits.append(f"trip_id={t['trip_id']} status={t['status']} 缺字段 {missing}")
        return hits

    # C4：车辆占用区间不得重叠（必须先完成上一单）
    def _check_no_overlap(self, c) -> list[str]:
        hits = []
        vehicles = self.store.all("SELECT vehicle_id FROM vehicles")
        for v in vehicles:
            facts = [f for f in self.graph.intervals("uses_vehicle", v["vehicle_id"])
                     if f["kind"] == "relation"]
            for i, a in enumerate(facts):
                for b in facts[i + 1:]:
                    a_from, b_from = a["valid_from"], b["valid_from"]
                    a_to = a["valid_to"] or "9999-12-31 23:59:59"
                    b_to = b["valid_to"] or "9999-12-31 23:59:59"
                    if a_from < b_to and b_from < a_to:
                        hits.append(
                            f"vehicle={v['vehicle_id']} 区间重叠 "
                            f"[{a_from},{a_to}] vs [{b_from},{b_to}]"
                        )
        return hits

    # C5 / C6：跨表金额比较
    def _check_compare(self, c) -> list[str]:
        hits = []
        left_cls, left_field = c["left"].split(".")
        right_cls, right_field = c["right"].split(".")
        if left_cls == "Payment":
            rows = self.store.all(
                """SELECT p.payment_id, p.amount AS l, b.total AS r
                   FROM payments p JOIN billing_records b ON p.billing_id=b.billing_id"""
            )
            idkey = "payment_id"
        elif left_cls == "Refund":
            rows = self.store.all(
                """SELECT r.refund_id, r.amount AS l, p.amount AS r
                   FROM refunds r JOIN payments p ON r.payment_id=p.payment_id"""
            )
            idkey = "refund_id"
        else:
            return hits
        for row in rows:
            l, r = row["l"], row["r"]
            ok = {"gte": l >= r, "lte": l <= r, "gt": l > r, "lt": l < r}[c["op"]]
            if not ok:
                hits.append(f"{idkey}={row[idkey]} {l} {c['op']} {r} 不成立")
        return hits

    # C7：司机不得同时跑两单
    def _check_no_active_double(self, c) -> list[str]:
        hits = []
        for d in self.store.all("SELECT driver_id FROM drivers"):
            n = self.store.scalar(
                "SELECT COUNT(*) FROM trips WHERE driver_id=? AND status IN ('CREATED','ARRIVED','ONBOARD')",
                (d["driver_id"],),
            )
            if n and n > 1:
                hits.append(f"driver={d['driver_id']} 同时有 {n} 笔进行中行程")
        return hits

    # C8：全量复核关系定义域/值域
    def _check_relation_domain_range(self, c) -> list[str]:
        hits = []
        for f in self.graph.all_relations():
            try:
                self.sem.validate_relation(f["predicate"], f["subject_type"], f["object_type"])
            except Exception as e:
                hits.append(f"fact#{f['id']} {f['subject']}-{f['predicate']}->{f['object']}: {e}")
        return hits
