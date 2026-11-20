"""货拉拉订单本体运行时：本体只持久化知识，事实经 MCP / API 现查现用。

本体库只保存 semantic_layer.json 中的类、关系、规则、动作与数据源映射；
运单、费用、证据、事件、审计等业务事实来自外部系统，由 FactMCP 按需读取，
在内存中临时组装实例图。所有写入先过语义层定义域/值域校验；动作先过状态、
角色、规则前置条件，成功与拒绝都落外部事实库的审计，保证可回放。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from collections import deque
from datetime import datetime

from .fact_mcp import FactMCP
from .fact_store import FactStore as Store
from .release import diff_semantics, file_checksum, list_manifests, load_manifest, semantic_path
from .semantic import (
    SemanticLayer, BusinessReject, OntologyViolation, is_expression,
    validate_semantic, FEE_AMOUNT_TARGETS,
)

CAUSAL_RELATIONS = {
    "order_generates_waybill", "waybill_assigned_to_driver", "waybill_uses_vehicle",
    "waybill_produces_fee", "fee_included_in_settlement", "settlement_generates_invoice",
    "waybill_has_exception", "invoice_billed_to_shipper",
}
_AGENT_SESSIONS: dict[str, tuple[float, object]] = {}
_AGENT_SESSION_TTL = 600


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class FreightSystem:
    def __init__(self, db_path: str, fresh: bool = False, seed: bool = True):
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        if fresh and os.path.exists(db_path):
            os.remove(db_path)
        self.db_path = db_path
        self.sem = SemanticLayer()
        self.base_sem = self.sem
        self.base_version = self.sem.raw.get("meta", {}).get("version")
        self.active_version = self.base_version
        self.facts = FactMCP(db_path, self.sem)
        self.facts.bind_runtime(self)
        self.conn = self.facts.conn
        self.conn.commit()
        if seed and not self.facts.scalar("SELECT COUNT(*) FROM objects"):
            self.seed()
            self.conn.commit()

    # ------------------------------------------------------------------ 写入原语
    def add_object(self, type_id: str, object_id: str, name: str,
                   status: str | None, props: dict, at: str | None = None) -> None:
        props = {**props}
        if object_id.rsplit(":", 1)[-1] != props.get(self.sem.class_of(type_id)["key"]):
            raise OntologyViolation(f"{type_id} 主键与对象 ID 不一致: {object_id}")
        if status:
            props["status"] = status
        self.sem.validate_object(type_id, props)
        self.facts.insert_object(object_id, type_id, name, status, props, at or now_iso())

    def add_relation(self, predicate: str, subject_id: str, object_id: str,
                     at: str, source_system: str = "SEMANTIC_SEED") -> None:
        subject = self.facts.get_row(subject_id)
        obj = self.facts.get_row(object_id)
        if not subject or not obj:
            raise BusinessReject(f"关系两端对象不存在: {subject_id} -> {object_id}")
        self.sem.validate_relation(predicate, subject["type"], obj["type"])
        self._check_cardinality(predicate, subject_id, object_id)
        self.facts.add_relation(subject_id, predicate, object_id,
                                subject["type"], obj["type"], at, source_system)

    def _check_cardinality(self, predicate: str, subject_id: str,
                           object_id: str) -> None:
        """链接基数写入校验（Palantir Link Type cardinality 语义）。

        读法 domain->range：
          one_to_many  domain 实例可挂多个 range，range 唯一归属（range 侧查重）
          many_to_one  domain 实例唯一挂一个 range（subject 侧查重）
          one_to_one   双侧查重
          many_to_many 不限制（缺省）
        只对活跃关系（valid_to IS NULL）生效；时态关闭后可重新挂。
        """
        rel = self.sem.relations.get(predicate, {})
        card = rel.get("cardinality", "many_to_many")
        if card == "many_to_many":
            return
        if card in ("many_to_one", "one_to_one"):
            dup = self.facts.store.one(
                "SELECT 1 FROM relations WHERE predicate=? AND subject_id=? "
                "AND object_id<>? AND valid_to IS NULL",
                (predicate, subject_id, object_id))
            if dup:
                raise OntologyViolation(
                    f"基数违规 {predicate}[{card}]: {subject_id} 已有其他活跃关联")
        if card in ("one_to_many", "one_to_one"):
            dup = self.facts.store.one(
                "SELECT 1 FROM relations WHERE predicate=? AND object_id=? "
                "AND subject_id<>? AND valid_to IS NULL",
                (predicate, object_id, subject_id))
            if dup:
                raise OntologyViolation(
                    f"基数违规 {predicate}[{card}]: {object_id} 已归属其他对象")

    def add_evidence(self, evidence_id: str, object_id: str, evidence_type: str,
                     source_system: str, occurred_at: str, version: str,
                     summary: str, payload: dict, relation: str) -> None:
        evidence_props = {
            "evidence_id": evidence_id.rsplit(":", 1)[-1],
            "evidence_type": evidence_type,
            "source_system": source_system,
            "occurred_at": occurred_at,
            "version": version,
            "summary": summary,
            "checksum": hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:16],
        }
        self.add_object("Evidence", evidence_id, summary, "VALID", evidence_props, occurred_at)
        self.facts.add_evidence(
            evidence_id, object_id, evidence_type, source_system, occurred_at,
            version, summary, payload, evidence_props["checksum"],
        )
        self.add_relation(relation, object_id, evidence_id, occurred_at, source_system)

    # ------------------------------------------------------------------ 种子数据
    def seed(self) -> None:
        t_order = "2026-11-05T08:30:00+08:00"
        t_accept = "2026-11-05T08:40:00+08:00"
        t_arrive = "2026-11-05T09:12:00+08:00"
        t_load = "2026-11-05T10:05:00+08:00"
        t_deliver = "2026-11-05T12:18:00+08:00"
        t_post = "2026-11-15T10:00:00+08:00"
        t_invoice = "2026-11-15T10:30:00+08:00"
        t_dispute = "2026-11-16T09:30:00+08:00"

        # 主链路：一单真实货，预估 480，实收 518，多 38。
        self.add_object("Shipper", "shipper:S001", "上海云仓供应链有限公司", "ACTIVE", {
            "shipper_id": "S001", "shipper_name": "上海云仓供应链有限公司",
            "settlement_subject": "月结账户", "contact": "王女士 138****2210",
        }, t_order)
        self.add_object("Order", "order:HL20261105001", "订单 HL20261105001", "COMPLETED", {
            "order_id": "HL20261105001", "shipper_id": "S001",
            "origin": "上海市嘉定区安亭物流园", "destination": "上海市浦东新区张江仓",
            "cargo": "家电配件 18 托", "vehicle_type": "4.2米厢货",
            "estimated_fee": 480.0, "created_at": t_order,
        }, t_order)
        self.add_object("Waybill", "waybill:WB20261105001", "运单 WB20261105001", "DELIVERED", {
            "waybill_id": "WB20261105001", "order_id": "HL20261105001",
            "driver_id": "D001", "vehicle_id": "V001", "estimated_fee": 480.0,
            "actual_fee": 518.0, "variance": 38.0, "waiting_minutes": 53,
            "free_wait_minutes": 30, "accepted_at": t_accept,
            "arrival_at": t_arrive, "loading_started_at": t_load,
            "loading_ended_at": "2026-11-05T10:35:00+08:00",
            "loading_confirmed_at": t_load,
            "unloading_started_at": "2026-11-05T12:00:00+08:00",
            "unloading_ended_at": t_deliver,
            "delivered_at": t_deliver,
            "actual_distance_km": 40.0, "actual_duration_min": 218,
            "proof_of_delivery": "POD20261105001",
        }, t_accept)
        self.add_object("Driver", "driver:D001", "张师傅", "IDLE", {
            "driver_id": "D001", "driver_name": "张师傅", "fleet": "华东车队",
            "qualification": "道路运输从业资格证有效至 2028-06",
        }, t_accept)
        self.add_object("Vehicle", "vehicle:V001", "沪A·7D36M", "IDLE", {
            "vehicle_id": "V001", "plate": "沪A·7D36M", "vehicle_type": "4.2米厢货",
            "capacity_t": 4.5, "fleet": "华东车队",
        }, t_accept)

        fees = [
            ("F001", "base", "基础运费", 380.0, 380.0, 380.0,
             "起步价 + 车型费，按订单报价锁定", "TMS", "NORMAL", "R-BASE-01", "PR-BASE-01"),
            ("F002", "mileage", "里程费", 60.0, 60.0, 60.0,
             "实际轨迹 40km × 1.5 元/km", "TMS", "NORMAL", "R-MILEAGE-01", "PR-MILEAGE-01"),
            ("F003", "waiting", "等候费", 0.0, 12.0, 25.0,
             "等候 53 分钟，前 30 分钟免费，每 5 分钟 2.4 元", "TMS", "DISPUTED", "R-WAIT-01", "PR-WAIT-01"),
            ("F004", "highway", "高速费", 0.0, 13.0, 13.0,
             "ETC 实际扣费 13 元，据实结算", "ETC", "NORMAL", "R-HIGHWAY-01", "PR-HIGHWAY-01"),
            ("F005", "loading", "装卸费", 20.0, 20.0, 20.0,
             "装卸服务费，订单报价内", "TMS", "NORMAL", "R-LOADING-01", "PR-LOADING-01"),
            ("F006", "insurance", "保险服务费", 10.0, 10.0, 10.0,
             "货运险，订单报价内", "TMS", "NORMAL", "R-INSURANCE-01", "PR-INSURANCE-01"),
            ("F007", "other", "其他费用", 10.0, 10.0, 10.0,
             "签收单据工本费，订单报价内", "TMS", "NORMAL", "R-OTHER-01", "PR-OTHER-01"),
        ]
        for fid, ftype, label, quoted, expected, actual, basis, source, status, rule_id, pr_id in fees:
            self.add_object("FeeItem", f"fee:{fid}", label, status, {
                "fee_id": fid, "fee_type": ftype, "fee_label": label,
                "quoted_amount": quoted, "expected_amount": expected,
                "actual_amount": actual, "waived_amount": 0.0,
                "calc_basis": basis, "pricing_rule_id": pr_id,
                "pricing_rule_version": "2026.10", "occurred_at": t_deliver,
                "source_system": source,
            }, t_deliver)
            self.add_relation("waybill_produces_fee", "waybill:WB20261105001",
                              f"fee:{fid}", t_deliver)

        # 货物：车型匹配与加价项判断的依据。
        self.add_object("Cargo", "cargo:CG001", "家电配件", "NORMAL", {
            "cargo_id": "CG001", "name": "家电配件", "weight_kg": 3200,
            "volume_m3": 12.5, "package_count": 18, "package_type": "托盘",
        }, t_order)
        self.add_relation("order_has_cargo", "order:HL20261105001",
                          "cargo:CG001", t_order)

        # 定价规则版本：费用与报价都要能追溯到当时生效的费率版本。
        for rid, ftype, params in [
            ("PR-BASE-01", "base", {"base_fare_by_vehicle_type": {"4.2米厢货": 380.0}}),
            ("PR-MILEAGE-01", "mileage", {"per_km": 1.5}),
            ("PR-WAIT-01", "waiting", {
                "free_minutes": 30, "block_minutes": 5, "per_block": 2.4,
                "by_vehicle_type": {
                    "4.2米厢货": {"free_minutes": 30, "block_minutes": 5, "per_block": 2.4},
                    "面包车": {"free_minutes": 20, "block_minutes": 5, "per_block": 1.8},
                    "小货车": {"free_minutes": 30, "block_minutes": 5, "per_block": 2.4},
                    "大货车": {"free_minutes": 40, "block_minutes": 10, "per_block": 4.0},
                    "跑腿": {"free_minutes": 10, "block_minutes": 5, "per_block": 1.0},
                },
            }),
            ("PR-HIGHWAY-01", "highway", {"source": "ETC"}),
            ("PR-LOADING-01", "loading", {"per_order": 20.0}),
            ("PR-INSURANCE-01", "insurance", {"per_order": 10.0}),
            ("PR-OTHER-01", "other", {"per_order": 10.0}),
        ]:
            self.add_object("PricingRule", f"pricing_rule:{rid}", f"定价规则 {rid}", "ACTIVE", {
                "rule_id": rid, "fee_type": ftype, "city": "上海",
                "vehicle_type": "4.2米厢货", "effective_from": "2026-10-01",
                "effective_to": "2026-12-31", "version": "2026.10",
                "currency": "CNY", "params": params, "source_system": "计价中心",
            }, t_order)

        for fee_row in fees:
            self.add_relation("fee_priced_by_rule", f"fee:{fee_row[0]}",
                              f"pricing_rule:{fee_row[-1]}", t_deliver)

        # 报价单：480 元的价格快照，与实际费用逐项对应。
        self.add_object("Quote", "quote:Q20261105001", "报价单 Q20261105001", "ACTIVE", {
            "quote_id": "Q20261105001", "order_id": "HL20261105001", "version": "v1",
            "quoted_at": t_order, "valid_until": "2026-11-05T20:00:00+08:00",
            "total_amount": 480.0, "currency": "CNY", "pricing_rule_version": "2026.10",
        }, t_order)
        self.add_relation("order_has_quote", "order:HL20261105001",
                          "quote:Q20261105001", t_order)
        for index, (fid, ftype, label, quoted, _expected, _actual, _basis,
                    _source, _status, rule_id, pr_id) in enumerate(fees, start=1):
            qid = f"QI{index:03d}"
            self.add_object("QuoteItem", f"quote_item:{qid}", label, "NORMAL", {
                "quote_item_id": qid, "quote_id": "Q20261105001",
                "fee_type": ftype, "fee_label": label, "amount": quoted,
                "quantity": 1, "unit": "单", "calc_rule_id": rule_id,
                "pricing_rule_version": "2026.10",
            }, t_order)
            self.add_relation("quote_contains_item", "quote:Q20261105001",
                              f"quote_item:{qid}", t_order)
            self.add_relation("quote_item_priced_by_rule", f"quote_item:{qid}",
                              f"pricing_rule:{pr_id}", t_order)
            self.add_relation("fee_derived_from_quote_item", f"fee:{fid}",
                              f"quote_item:{qid}", t_deliver)

        # 轨迹：只留摘要与里程，GPS 点位由轨迹平台按需查询。
        self.add_object("Track", "track:TR20261105001", "运单轨迹 TR20261105001", "VALID", {
            "track_id": "TR20261105001", "waybill_id": "WB20261105001",
            "source_system": "GPS", "started_at": t_accept, "ended_at": t_deliver,
            "distance_km": 40.0, "duration_min": 218, "waiting_minutes": 53,
            "point_count": 486, "checksum": "9f2c1a7d4b6e8c03",
        }, t_deliver)
        self.add_relation("waybill_has_track", "waybill:WB20261105001",
                          "track:TR20261105001", t_deliver)

        self.add_object("Settlement", "settlement:ST20261105001", "结算单 ST20261105001", "POSTED", {
            "settlement_id": "ST20261105001", "period": "2026-11",
            "amount": 518.0, "reversed_amount": 0.0, "posted_at": t_post,
        }, t_post)
        self.add_object("Invoice", "invoice:INV20261105001", "发票 INV20261105001", "ISSUED", {
            "invoice_id": "INV20261105001", "invoice_no": "31002611050088",
            "title": "上海云仓供应链有限公司", "amount": 518.0,
            "tax_rate": 9.0, "issued_at": t_invoice,
        }, t_invoice)
        self.add_object("ExceptionTicket", "exception:EX20261105001", "费用异常工单", "OPEN", {
            "ticket_id": "EX20261105001", "ticket_type": "FEE_DISPUTE",
            "reason": "等候费 25 元，按规则应为 12 元，差异 13 元",
            "assignee": "财务共享中心", "resolution": "",
            "opened_at": t_dispute, "closed_at": None,
        }, t_dispute)

        self.add_relation("shipper_places_order", "shipper:S001",
                          "order:HL20261105001", t_order)
        self.add_relation("order_generates_waybill", "order:HL20261105001",
                          "waybill:WB20261105001", t_order)
        self.add_relation("waybill_assigned_to_driver", "waybill:WB20261105001",
                          "driver:D001", t_accept)
        self.add_relation("waybill_uses_vehicle", "waybill:WB20261105001",
                          "vehicle:V001", t_accept)
        for fid, *_ in fees:
            self.add_relation("fee_included_in_settlement", f"fee:{fid}",
                              "settlement:ST20261105001", t_post)
        self.add_relation("settlement_generates_invoice", "settlement:ST20261105001",
                          "invoice:INV20261105001", t_invoice)
        self.add_relation("invoice_billed_to_shipper", "invoice:INV20261105001",
                          "shipper:S001", t_invoice)
        self.add_relation("waybill_has_exception", "waybill:WB20261105001",
                          "exception:EX20261105001", t_dispute)

        self.add_evidence("evidence:E001", "waybill:WB20261105001", "DRIVER_ARRIVAL",
                          "TMS", t_arrive, "v3", "司机到达装货点",
                          {"driver_id": "D001", "gps": "121.158,31.291"}, "waybill_supported_by_evidence")
        self.add_evidence("evidence:E002", "waybill:WB20261105001", "LOADING_CONFIRMED",
                          "TMS", t_load, "v3", "装货完成，系统记录等候 53 分钟",
                          {"loading_confirmed_at": t_load, "waiting_minutes": 53},
                          "waybill_supported_by_evidence")
        self.add_evidence("evidence:E003", "fee:F003", "PRICING_RULE",
                          "BILLING", "2026-11-05T12:18:00+08:00", "2026.10",
                          "等候费规则：30 分钟免费，每 5 分钟 2.4 元",
                          {"free_minutes": 30, "block_minutes": 5, "per_block": 2.4},
                          "fee_supported_by_evidence")
        self.add_evidence("evidence:E004", "fee:F004", "ETC_TOLL",
                          "ETC", "2026-11-05T11:45:00+08:00", "v1",
                          "ETC 实际扣费 13 元",
                          {"amount": 13.0, "transaction_id": "ETC20261105001145"},
                          "fee_supported_by_evidence")
        self.add_evidence("evidence:E005", "settlement:ST20261105001", "SETTLEMENT_POSTED",
                          "ERP", t_post, "v2", "结算单已入账 518 元",
                          {"amount": 518.0, "status": "POSTED"},
                          "settlement_supported_by_evidence")
        self.add_evidence("evidence:E006", "invoice:INV20261105001", "INVOICE_ISSUED",
                          "TAX", t_invoice, "v1", "发票已开具 518 元",
                          {"amount": 518.0, "invoice_no": "31002611050088"},
                          "invoice_supported_by_evidence")
        self.add_evidence("evidence:E007", "exception:EX20261105001", "FEE_DISPUTE",
                          "CRM", t_dispute, "v1", "货主对等候费提出异议",
                          {"reason": "等候费偏高", "claimed_amount": 13.0},
                          "exception_supported_by_evidence")

        # 第二张运单：未装货，可安全改派；第三张：已装货，改派必须被拦截。
        self.add_object("Order", "order:HL20261106001", "订单 HL20261106001", "DISPATCHED", {
            "order_id": "HL20261106001", "shipper_id": "S001",
            "origin": "上海市松江区九亭仓", "destination": "上海市闵行区莘庄仓",
            "cargo": "日用百货 9 托", "vehicle_type": "4.2米厢货",
            "estimated_fee": 260.0, "created_at": "2026-11-06T08:10:00+08:00",
        }, "2026-11-06T08:10:00+08:00")
        self.add_object("Waybill", "waybill:WB20261106001", "运单 WB20261106001", "ACCEPTED", {
            "waybill_id": "WB20261106001", "order_id": "HL20261106001",
            "driver_id": "D002", "vehicle_id": "V002", "estimated_fee": 260.0,
            "actual_fee": 0.0, "variance": 0.0, "waiting_minutes": 0,
            "free_wait_minutes": 30, "accepted_at": "2026-11-06T08:30:00+08:00",
            "loading_confirmed_at": None, "delivered_at": None, "proof_of_delivery": None,
        }, "2026-11-06T08:30:00+08:00")
        self.add_object("Driver", "driver:D002", "李师傅", "ASSIGNED", {
            "driver_id": "D002", "driver_name": "李师傅", "fleet": "华东车队",
            "qualification": "道路运输从业资格证有效至 2027-12",
        }, "2026-11-06T08:30:00+08:00")
        self.add_object("Vehicle", "vehicle:V002", "沪B·3K21L", "IN_USE", {
            "vehicle_id": "V002", "plate": "沪B·3K21L", "vehicle_type": "4.2米厢货",
            "capacity_t": 4.5, "fleet": "华东车队",
        }, "2026-11-06T08:30:00+08:00")
        self.add_relation("shipper_places_order", "shipper:S001",
                          "order:HL20261106001", "2026-11-06T08:10:00+08:00")
        self.add_relation("order_generates_waybill", "order:HL20261106001",
                          "waybill:WB20261106001", "2026-11-06T08:10:00+08:00")
        self.add_relation("waybill_assigned_to_driver", "waybill:WB20261106001",
                          "driver:D002", "2026-11-06T08:30:00+08:00")
        self.add_relation("waybill_uses_vehicle", "waybill:WB20261106001",
                          "vehicle:V002", "2026-11-06T08:30:00+08:00")

        self.add_object("Order", "order:HL20261106002", "订单 HL20261106002", "DISPATCHED", {
            "order_id": "HL20261106002", "shipper_id": "S001",
            "origin": "上海市青浦区华新仓", "destination": "上海市普陀区桃浦仓",
            "cargo": "食品原料 12 托", "vehicle_type": "4.2米厢货",
            "estimated_fee": 180.0, "created_at": "2026-11-06T07:50:00+08:00",
        }, "2026-11-06T07:50:00+08:00")
        self.add_object("Waybill", "waybill:WB20261106002", "运单 WB20261106002", "LOADED", {
            "waybill_id": "WB20261106002", "order_id": "HL20261106002",
            "driver_id": "D003", "vehicle_id": "V003", "estimated_fee": 180.0,
            "actual_fee": 0.0, "variance": 0.0, "waiting_minutes": 0,
            "free_wait_minutes": 30, "accepted_at": "2026-11-06T08:00:00+08:00",
            "loading_confirmed_at": "2026-11-06T10:00:00+08:00",
            "delivered_at": None, "proof_of_delivery": None,
        }, "2026-11-06T08:00:00+08:00")
        self.add_object("Driver", "driver:D003", "王师傅", "ON_TRIP", {
            "driver_id": "D003", "driver_name": "王师傅", "fleet": "沪西车队",
            "qualification": "道路运输从业资格证有效至 2027-03",
        }, "2026-11-06T08:00:00+08:00")
        self.add_object("Vehicle", "vehicle:V003", "沪C·8N52P", "IN_USE", {
            "vehicle_id": "V003", "plate": "沪C·8N52P", "vehicle_type": "4.2米厢货",
            "capacity_t": 4.5, "fleet": "沪西车队",
        }, "2026-11-06T08:00:00+08:00")
        self.add_relation("shipper_places_order", "shipper:S001",
                          "order:HL20261106002", "2026-11-06T07:50:00+08:00")
        self.add_relation("order_generates_waybill", "order:HL20261106002",
                          "waybill:WB20261106002", "2026-11-06T07:50:00+08:00")
        self.add_relation("waybill_assigned_to_driver", "waybill:WB20261106002",
                          "driver:D003", "2026-11-06T08:00:00+08:00")
        self.add_relation("waybill_uses_vehicle", "waybill:WB20261106002",
                          "vehicle:V003", "2026-11-06T08:00:00+08:00")

        for oid, etype, at, actor, payload in [
            ("waybill:WB20261105001", "ORDER_CREATED", t_order, "货主", {"order_id": "HL20261105001"}),
            ("waybill:WB20261105001", "ACCEPTED", t_accept, "张师傅", {"driver_id": "D001"}),
            ("waybill:WB20261105001", "LOADED", t_load, "张师傅", {"waiting_minutes": 53}),
            ("waybill:WB20261105001", "DELIVERED", t_deliver, "张师傅", {"proof": "POD20261105001"}),
            ("settlement:ST20261105001", "POSTED", t_post, "财务共享中心", {"amount": 518.0}),
            ("invoice:INV20261105001", "ISSUED", t_invoice, "税务系统", {"amount": 518.0}),
            ("exception:EX20261105001", "FEE_DISPUTED", t_dispute, "货主", {"fee_id": "F003"}),
        ]:
            self.facts.add_event(oid, etype, at, actor, payload)

        self.facts.add_audit(
            t_accept, "张师傅", "driver", "accept_order", "waybill:WB20261105001",
            "APPROVED", None, {"object_id": "waybill:WB20261105001"},
            {"status": "ACCEPTED"},
        )
        self.facts.add_audit(
            t_post, "财务共享中心", "finance", "post_settlement", "settlement:ST20261105001",
            "APPROVED", None, {"object_id": "settlement:ST20261105001"},
            {"status": "POSTED", "amount": 518.0},
        )
        self.facts.add_audit(
            t_dispute, "货主", "shipper", "dispute_charge", "fee:F003",
            "APPROVED", None, {"object_id": "fee:F003", "reason": "等候费偏高"},
            {"status": "DISPUTED", "ticket_id": "EX20261105001"},
        )

    # ------------------------------------------------------------------ 读取
    def stats(self) -> dict:
        return {
            "objects": self.facts.scalar("SELECT COUNT(*) FROM objects"),
            "relations": self.facts.scalar("SELECT COUNT(*) FROM relations WHERE valid_to IS NULL"),
            "evidence": self.facts.scalar("SELECT COUNT(*) FROM evidence"),
            "events": self.facts.scalar("SELECT COUNT(*) FROM events"),
            "audit": self.facts.scalar("SELECT COUNT(*) FROM audit"),
        }

    def ontology(self) -> dict:
        """本体知识视图：只返回 persistent 的 TBox，不包含任何业务事实。"""
        data = self.sem.dictionary()
        return {
            "generated_at": now_iso(),
            "storage": {
                "kind": "knowledge-only",
                "persisted": "freight/semantic_layer.json",
                "facts": "MCP / API 按需查询，不写入本体库",
            },
            "meta": data["meta"],
            "classes": data["classes"],
            "relations": data["relations"],
            "rules": data["rules"],
            "actions": data["actions"],
            "functions": data.get("functions", []),
            "derived_types": data.get("derived_types", []),
            "primitives": data.get("primitives", []),
            "pricing": data.get("pricing", {}),
            "data_sources": data.get("data_sources", []),
            "stats": {
                "classes": data["class_count"],
                "relations": data["relation_count"],
                "rules": data["rule_count"],
                "actions": data["action_count"],
                "functions": data.get("function_count", 0),
                "derived_types": data.get("derived_type_count", 0),
                "primitives": data.get("primitive_count", 0),
                "pricing": len(data.get("pricing", {})),
                "data_sources": len(data.get("data_sources", [])),
            },
        }

    def storage_inventory(self) -> dict:
        """本地物理存储盘点：本体知识库与事实替身库的真实文件、行数。"""
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def rel(path: str) -> str:
            return os.path.relpath(path, base)

        def size(path: str) -> int:
            return os.path.getsize(path) if os.path.exists(path) else 0

        tables = {
            name: self.facts.scalar(f"SELECT COUNT(*) FROM {name}")
            for name in ("objects", "relations", "evidence", "events", "audit")
        }
        by_type = [
            {"type": row["type"], "count": row["count"]}
            for row in self.facts.all(
                "SELECT type, COUNT(*) AS count FROM objects GROUP BY type ORDER BY count DESC"
            )
        ]
        data = self.sem.dictionary()
        return {
            "generated_at": now_iso(),
            "boundary": {
                "ontology": "唯一持久化的本体文件，只存 TBox 知识",
                "facts": "外部业务系统的演示替身，不属于本体库",
                "memory": "每次请求临时组装实例图，请求结束即释放",
            },
            "ontology": {
                "path": rel(self.sem.path),
                "bytes": size(self.sem.path),
                "sections": {
                    "classes": data["class_count"],
                    "relations": data["relation_count"],
                    "rules": data["rule_count"],
                    "actions": data["action_count"],
                    "functions": data.get("function_count", 0),
                    "derived_types": data.get("derived_type_count", 0),
                    "primitives": data.get("primitive_count", 0),
                    "pricing": len(data.get("pricing", {})),
                    "data_sources": len(data.get("data_sources", [])),
                },
            },
            "facts": {
                "path": rel(self.db_path),
                "bytes": size(self.db_path),
                "adapter": "SQLiteFactStore（演示替身，生产替换为 MCP / API 客户端）",
                "tables": tables,
                "objects_by_type": by_type,
            },
            "memory": {"persisted_bytes": 0, "note": "实例图不落盘"},
        }

    def query_waybill(self, waybill_id: str, include_context: bool = False) -> dict:
        """通过 MCP 实时查询运单事实，返回内存中的临时图与工具调用轨迹。"""
        return self.facts.query_waybill(waybill_id, include_context=include_context)

    def mcp_tools(self, type_id: str | None = None) -> dict:
        return self.facts.tools(type_id)

    def mcp_call(self, tool: str, arguments: dict | None = None) -> dict:
        return self.facts.call(tool, arguments)

    def overview(self) -> dict:
        """跨外部系统的全量事实快照，仅供调试 / 总览；结果不写入本体。"""
        snapshot = self.facts.facts_overview()
        objects = []
        class_counts: dict[str, dict] = {}
        for row in snapshot["objects"]:
            objects.append(row)
            cls = self.sem.classes.get(row["type"], {"label": row["type"]})
            bucket = class_counts.setdefault(row["type"], {
                "type": row["type"], "label": cls["label"],
                "count": 0, "statuses": {},
            })
            bucket["count"] += 1
            bucket["statuses"][row["status"]] = bucket["statuses"].get(row["status"], 0) + 1

        evidence_rows = snapshot["evidence"]
        for ev in evidence_rows:
            owner = self.facts.get_row(ev["object_id"])
            ev["object_type_label"] = (
                self.sem.classes[owner["type"]]["label"]
                if owner and owner["type"] in self.sem.classes else None
            )

        event_rows = snapshot["events"]
        audit_rows = snapshot["audit"]

        return {
            "generated_at": now_iso(),
            "storage": "external-mcp",
            "stats": self.stats(),
            "class_counts": sorted(class_counts.values(),
                                   key=lambda item: item["count"], reverse=True),
            "objects": objects,
            "relations": snapshot["relations"],
            "evidence": evidence_rows,
            "events": event_rows,
            "audit": audit_rows,
            "trace": snapshot["trace"],
        }

    def search(self, type_id: str | None = None, q: str | None = None,
               limit: int = 100) -> list[dict]:
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
        rows = self.facts.all(sql, tuple(args))
        return [self._summary(r) for r in rows]

    def get_object(self, object_id: str) -> dict:
        row = self.facts.get_row(object_id)
        if not row:
            raise BusinessReject(f"对象不存在: {object_id}", "OBJECT_NOT_FOUND")
        row = Store.decode(row)
        props = row["props"]
        cls = self.sem.class_of(row["type"])
        relations_out = self._relations_of(object_id, "out")
        relations_in = self._relations_of(object_id, "in")
        all_actions = []
        for action in self.sem.raw["actions"]:
            if action["target"] == row["type"]:
                all_actions.append(self._action_view(action, row, props))
        available = [a for a in all_actions if a["allowed"]]
        blocked = [a for a in all_actions if not a["allowed"]]
        return {
            "object_id": object_id,
            "type": row["type"],
            "type_label": cls["label"],
            "name": row["name"],
            "status": row["status"],
            "statuses": cls["statuses"],
            "properties": props,
            "property_labels": self._property_labels(row["type"]),
            "relations_out": relations_out,
            "relations_in": relations_in,
            "rules": self.sem.rules_for(row["type"], props),
            "available_actions": available,
            "blocked_actions": blocked,
            "updated_at": row["updated_at"],
        }

    def _summary(self, row: dict) -> dict:
        props = json.loads(row["props"])
        return {
            "object_id": row["object_id"], "type": row["type"],
            "type_label": self.sem.classes[row["type"]]["label"],
            "name": row["name"], "status": row["status"],
            "amount": props.get("actual_amount") or props.get("amount"),
        }

    def _property_labels(self, type_id: str) -> dict:
        labels = {
            "shipper_id": "货主 ID", "shipper_name": "货主名称", "settlement_subject": "结算主体",
            "contact": "联系人", "order_id": "订单号", "shipper_id_ref": "货主",
            "origin": "起点", "destination": "终点", "cargo": "货物", "vehicle_type": "车型",
            "estimated_fee": "预估费用", "created_at": "创建时间", "waybill_id": "运单号",
            "driver_id": "司机 ID", "vehicle_id": "车辆 ID", "actual_fee": "实际费用",
            "variance": "差异", "waiting_minutes": "等候分钟", "free_wait_minutes": "免费等候分钟",
            "accepted_at": "接单时间", "loading_confirmed_at": "装货确认时间",
            "arrival_at": "到达时间", "loading_started_at": "开始装货时间",
            "loading_ended_at": "装货完成时间", "unloading_started_at": "开始卸货时间",
            "unloading_ended_at": "卸货完成时间", "actual_distance_km": "实际里程(km)",
            "actual_duration_min": "实际时长(分钟)",
            "delivered_at": "送达时间", "proof_of_delivery": "回单编号", "driver_name": "司机姓名",
            "fleet": "车队", "qualification": "资质", "plate": "车牌", "capacity_t": "载重(吨)",
            "fee_id": "费用项 ID", "fee_type": "费用类型", "fee_label": "费用名称",
            "quoted_amount": "报价金额", "expected_amount": "规则应计", "actual_amount": "实际金额",
            "waived_amount": "已减免", "calc_basis": "计费依据", "occurred_at": "发生时间",
            "pricing_rule_id": "定价规则版本 ID", "pricing_rule_version": "定价规则版本",
            "source_system": "来源系统", "settlement_id": "结算单号", "period": "账期",
            "amount": "金额", "reversed_amount": "已冲正", "posted_at": "入账时间",
            "invoice_id": "发票 ID", "invoice_no": "发票号码", "title": "抬头", "tax_rate": "税率",
            "issued_at": "开票时间", "ticket_id": "工单号", "ticket_type": "工单类型",
            "reason": "原因", "assignee": "处理人", "resolution": "处理结论",
            "opened_at": "创建时间", "closed_at": "关闭时间", "evidence_id": "证据 ID",
            "evidence_type": "证据类型", "version": "版本", "summary": "摘要", "checksum": "校验值",
            "quote_id": "报价单号", "quote_item_id": "报价明细 ID", "quoted_at": "报价时间",
            "valid_until": "有效期至", "total_amount": "报价总额", "currency": "币种",
            "quantity": "数量", "unit": "单位", "calc_rule_id": "计算规则 ID",
            "city": "城市", "effective_from": "生效开始", "effective_to": "生效结束",
            "params": "规则参数", "track_id": "轨迹 ID", "started_at": "开始时间",
            "ended_at": "结束时间", "distance_km": "轨迹里程(km)", "duration_min": "轨迹时长(分钟)",
            "point_count": "轨迹点数", "cargo_id": "货物 ID", "name": "名称",
            "weight_kg": "重量(kg)", "volume_m3": "体积(m³)", "package_count": "件数",
            "package_type": "包装类型",
        }
        cls = self.sem.class_of(type_id)
        return {p: labels.get(p, p) for p in cls["properties"]}

    def _relations_of(self, object_id: str, direction: str) -> list[dict]:
        if direction == "out":
            rows = self.facts.all(
                "SELECT * FROM relations WHERE subject_id=? ORDER BY predicate, object_id",
                (object_id,))
        else:
            rows = self.facts.all(
                "SELECT * FROM relations WHERE object_id=? ORDER BY predicate, subject_id",
                (object_id,))
        out = []
        for rel in rows:
            other_id = rel["object_id"] if direction == "out" else rel["subject_id"]
            other = self.facts.get_row(other_id)
            out.append({
                "predicate": rel["predicate"],
                "label": self.sem.relation_label(rel["predicate"]),
                "direction": direction,
                "object_id": other_id,
                "name": other["name"] if other else other_id,
                "type": other["type"] if other else None,
                "type_label": self.sem.classes[other["type"]]["label"] if other else None,
                "status": other["status"] if other else None,
                "valid_from": rel["valid_from"],
                "valid_to": rel["valid_to"],
                "active": rel["valid_to"] is None,
            })
        return out

    def graph(self, object_id: str, depth: int = 2) -> dict:
        if not self.facts.get_row(object_id):
            raise BusinessReject(f"对象不存在: {object_id}", "OBJECT_NOT_FOUND")
        depth = max(1, min(depth, 5))
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[tuple] = set()
        queue = deque([(object_id, 0)])
        visited = {object_id}
        while queue:
            current, level = queue.popleft()
            row = self.facts.get_row(current)
            if not row:
                continue
            row = Store.decode(row)
            nodes[current] = {
                "object_id": current, "type": row["type"],
                "type_label": self.sem.classes[row["type"]]["label"],
                "name": row["name"], "status": row["status"], "depth": level,
            }
            if level >= depth:
                continue
            for rel in self._relations_of(current, "out") + self._relations_of(current, "in"):
                if not rel["active"]:
                    continue
                key = (rel["predicate"], current, rel["object_id"], rel["direction"])
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append({
                        "predicate": rel["predicate"], "label": rel["label"],
                        "subject_id": current if rel["direction"] == "out" else rel["object_id"],
                        "object_id": rel["object_id"] if rel["direction"] == "out" else current,
                        "direction": rel["direction"],
                    })
                if rel["object_id"] not in visited and rel["type"] in self.sem.classes:
                    visited.add(rel["object_id"])
                    queue.append((rel["object_id"], level + 1))
        return {"root": object_id, "depth": depth,
                "nodes": list(nodes.values()), "edges": edges}

    # ------------------------------------------------------------------ 费用诊断
    # -------------------------------------------------- 多源费用与报价明细
    _SOURCE_ALIASES = {"BILLING": "计费中心", "PRICING": "计价中心"}

    @classmethod
    def _source_alias(cls, system: str) -> str:
        return cls._SOURCE_ALIASES.get(str(system or "").upper(), str(system or ""))

    def _quote_detail_for_waybill(self, waybill_id: str, wprops: dict) -> dict:
        """从计价中心逐项读取报价明细，作为报价口径的唯一真源。

        旧实现把 Waybill.estimated_fee 当成报价总额：那是运单上的标量快照，
        与报价单明细是两套事实源，报价改版后快照不会自动跟。这里沿
        order_has_quote -> quote_contains_item 独立取数，并校验明细合计、
        报价单声明总额和运单快照三者是否一致。
        """
        order_id = f"order:{wprops.get('order_id')}"
        quote = self.facts.get_quote_for_order(order_id)
        quote_id = (quote or {}).get("object_id")
        items = self.facts.list_quote_items(quote_id) if quote_id else []
        by_fee_type: dict[str, dict] = {}
        by_item_id: dict[str, dict] = {}
        item_rows = []
        for item in items:
            p = item["properties"]
            fee_type = str(p.get("fee_type") or "")
            amount = round(float(p.get("amount") or 0), 2)
            row = {
                "quote_item_id": item["object_id"],
                "fee_type": fee_type,
                "fee_label": p.get("fee_label") or fee_type,
                "amount": amount,
                "quantity": p.get("quantity"),
                "unit": p.get("unit"),
                "pricing_rule_version": p.get("pricing_rule_version"),
            }
            item_rows.append(row)
            by_item_id[item["object_id"]] = row
            entry = by_fee_type.setdefault(fee_type, {
                "fee_label": row["fee_label"], "amount": 0.0, "ids": [],
            })
            entry["amount"] = round(entry["amount"] + amount, 2)
            entry["ids"].append(item["object_id"])
        quoted_total = round(sum(row["amount"] for row in item_rows), 2)
        declared_total = round(
            float((quote or {}).get("properties", {}).get("total_amount") or 0), 2)
        snapshot = round(float(wprops.get("estimated_fee") or 0), 2)
        findings = []
        if not quote:
            findings.append({
                "code": "QUOTE_NOT_FOUND", "severity": "HIGH",
                "title": "订单没有生效报价单",
                "message": f"订单 {order_id} 没有 order_has_quote 活跃关系，报价口径不可核。",
                "quoted": 0.0, "actual": snapshot, "delta": -snapshot,
            })
        elif not item_rows:
            findings.append({
                "code": "QUOTE_ITEMS_MISSING", "severity": "HIGH",
                "title": f"报价单 {quote_id} 没有逐项明细",
                "message": "报价单只有总额、没有 quote_contains_item 明细，无法逐项对账。",
                "quoted": 0.0, "actual": declared_total, "delta": -declared_total,
            })
        elif abs(quoted_total - declared_total) > 0.01:
            findings.append({
                "code": "QUOTE_TOTAL_MISMATCH", "severity": "HIGH",
                "title": "报价明细合计与报价单总额不一致",
                "message": (
                    f"明细合计 {quoted_total:.2f}，报价单声明 {declared_total:.2f}，"
                    f"差异 {quoted_total - declared_total:+.2f}。"
                ),
                "quoted": quoted_total, "actual": declared_total,
                "delta": round(quoted_total - declared_total, 2),
            })
        if quote and item_rows and abs(quoted_total - snapshot) > 0.01:
            findings.append({
                "code": "QUOTE_SNAPSHOT_DRIFT", "severity": "MEDIUM",
                "title": "运单报价快照与报价单明细不一致",
                "message": (
                    f"运单 estimated_fee={snapshot:.2f}，报价单明细合计 "
                    f"{quoted_total:.2f}；本次以明细为准。"
                ),
                "quoted": quoted_total, "actual": snapshot,
                "delta": round(quoted_total - snapshot, 2),
            })
        trace = [
            {"step": "报价单", "tool": "pricing.get_quote", "value": quote_id},
            {"step": "报价明细", "tool": "pricing.list_quote_items",
             "value": f"{len(item_rows)} 项"},
        ]
        return {
            "order_id": order_id,
            "quote_id": quote_id,
            "quote_version": (quote or {}).get("properties", {}).get("version"),
            "quoted_total": quoted_total,
            "declared_total": declared_total,
            "waybill_snapshot": snapshot,
            "source": "pricing.list_quote_items",
            "by_fee_type": by_fee_type,
            "by_item_id": by_item_id,
            "items": item_rows,
            "trace": trace,
            "findings": findings,
        }

    def _quoted_for_fee(self, fee_id: str, quote_detail: dict) -> dict | None:
        """单条费用对应的报价明细：优先查 fee_derived_from_quote_item 关系。"""
        rel = self.facts.one(
            "SELECT object_id FROM relations WHERE predicate='fee_derived_from_quote_item' "
            "AND subject_id=? AND valid_to IS NULL "
            "ORDER BY valid_from DESC LIMIT 1",
            (fee_id,),
        )
        if rel:
            row = quote_detail["by_item_id"].get(rel["object_id"])
            if row:
                return {**row, "link": "fee_derived_from_quote_item"}
        return None

    def _fee_source_coverage(self, waybill_id: str, wprops: dict) -> dict:
        """按 data_sources 声明的连接器逐源取费用明细，并检查证据与新鲜度。

        费用明细不在单表里：计费中心只给费用索引，TMS/ETC 各自提供自己的
        费用行。这里的取数轨迹不是装饰，而是把「哪些费用真的从源系统取到了」
        固化成可审计结果；取不到的费用会显式缺口，不会被静默算成 0。
        """
        collected = self.facts.collect_fee_sources(waybill_id)
        contracts = {
            str(item.get("system") or ""): item
            for item in self.facts.fee_source_contracts()
        }
        reference = (
            _parse_timestamp(wprops.get("delivered_at"))
            or _parse_timestamp(wprops.get("loading_confirmed_at"))
        )
        findings = []
        fee_sources = []
        for fee in collected["fees"]:
            p = fee["props"]
            system = str(p.get("source_system") or "")
            contract = contracts.get(system) or {}
            evidence = [dict(row) for row in self.facts.all(
                "SELECT * FROM evidence WHERE object_id=? ORDER BY occurred_at",
                (fee["object_id"],))]
            allowed_evidence = {
                self._source_alias(item)
                for item in contract.get("evidence_sources") or []
            }
            evidence_systems = {
                self._source_alias(ev.get("source_system")) for ev in evidence
            }
            record = {
                "fee_id": fee["object_id"].split(":", 1)[-1],
                "fee_type": p.get("fee_type"),
                "source_system": system,
                "connector": contract.get("tool"),
                "resolved": bool(fee["source_resolved"]),
                "evidence_count": len(evidence),
                "quote_item_id": None,
            }
            if not fee["source_resolved"]:
                findings.append({
                    "code": "FEE_SOURCE_UNRESOLVED", "severity": "HIGH",
                    "fee_id": record["fee_id"],
                    "title": f"{p.get('fee_label') or p.get('fee_type')}没有可用的源系统连接器",
                    "message": (
                        f"费用记录来源 {system or '未标注'}，但 data_sources 中没有"
                        "声明负责该来源/费用类型的连接器，明细无法取数。"
                    ),
                })
            declared_types = list(contract.get("fee_types") or [])
            if declared_types and str(p.get("fee_type") or "") not in declared_types:
                findings.append({
                    "code": "FEE_SOURCE_TYPE_UNDECLARED", "severity": "HIGH",
                    "fee_id": record["fee_id"],
                    "title": f"{system} 连接器不负责该费用类型",
                    "message": (
                        f"费用类型 {p.get('fee_type')} 不在 {system} 声明的 "
                        f"{declared_types} 范围内，明细口径不可信。"
                    ),
                })
            freshness_hours = contract.get("freshness_hours")
            latest = max(
                [ts for ts in (
                    [_parse_timestamp(ev.get("occurred_at")) for ev in evidence]
                    + [_parse_timestamp(p.get("occurred_at"))]
                ) if ts is not None],
                default=None,
            )
            if (reference and latest and isinstance(freshness_hours, (int, float))):
                age_hours = round(
                    (reference.timestamp() - latest.timestamp()) / 3600.0, 1)
                record["age_hours"] = age_hours
                if age_hours > float(freshness_hours):
                    findings.append({
                        "code": "FEE_SOURCE_STALE", "severity": "MEDIUM",
                        "fee_id": record["fee_id"],
                        "title": f"{p.get('fee_label') or p.get('fee_type')}源数据超过新鲜度窗口",
                        "message": (
                            f"{system} 明细最新时间 {latest.isoformat()}，"
                            f"距结算基准 {age_hours:.1f} 小时，超过 "
                            f"{float(freshness_hours):g} 小时窗口。"
                        ),
                    })
            require = set(contract.get("require_evidence") or [])
            if str(p.get("fee_type") or "") in require and not (allowed_evidence & evidence_systems):
                findings.append({
                    "code": "FEE_EVIDENCE_MISSING", "severity": "HIGH",
                    "fee_id": record["fee_id"],
                    "title": f"{p.get('fee_label') or p.get('fee_type')}缺少源系统证据",
                    "message": (
                        f"要求 {sorted(allowed_evidence)} 证据，实际来源 "
                        f"{sorted(evidence_systems) or '无'}。"
                    ),
                })
            fee_sources.append(record)
        unresolved = len(collected["unresolved_ids"])
        coverage = {
            "contracts": len(contracts),
            "fee_count": len(collected["fees"]),
            "resolved": len(collected["fees"]) - unresolved,
            "unresolved": unresolved,
            "sources": collected["sources"],
            "reference_at": reference.isoformat() if reference else None,
        }
        return {
            "fee_sources": fee_sources,
            "source_coverage": coverage,
            "findings": findings,
            "trace": [{
                "id": f"source:{row['system']}",
                "label": f"{row['system']} 费用明细",
                "tool": row["tool"],
                "value": f"{row['fee_count']} 项 / {row['amount']:.2f} 元",
                "source": row["tool"],
            } for row in collected["sources"]],
        }

    def _pricing_rule_for_fee(self, fee_id: str) -> dict | None:
        """沿 fee_priced_by_rule 关系解析费用项实际绑定的定价规则实例。"""
        rel = self.facts.one(
            "SELECT object_id FROM relations WHERE predicate='fee_priced_by_rule' "
            "AND subject_id=? AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
            (fee_id,),
        )
        if not rel:
            return None
        row = self.facts.get_row(rel["object_id"])
        return Store.decode(row) if row else None

    def _validate_pricing_rule(self, fee: dict, rule: dict | None) -> list[dict]:
        """按 R-PRICE-01 校验费用发生时间是否落在定价规则生效期内。"""
        fee_id = fee.get("fee_id")
        if not rule:
            return [{
                "code": "PRICING_RULE_MISSING", "severity": "HIGH",
                "fee_id": fee_id, "rule_id": "R-PRICE-01",
                "title": f"费用 {fee_id} 未绑定定价规则",
                "message": f"费用 {fee_id} 没有生效中的 fee_priced_by_rule 关系，无法按规则公式核算。",
            }]
        props = rule.get("props") or {}
        rule_id = str(props.get("rule_id") or "")
        findings = []
        if not isinstance(props.get("params"), dict) or not props.get("params"):
            findings.append({
                "code": "PRICING_RULE_PARAMS_MISSING", "severity": "HIGH",
                "fee_id": fee_id, "rule_id": "R-PRICE-01", "pricing_rule_id": rule_id,
                "title": f"定价规则 {rule_id} 缺少计算公式参数",
                "message": f"规则实例 {rule_id} 没有 params，无法按规则公式核算 {fee_id}。",
            })
        fee_type = str(fee.get("fee_type") or "")
        rule_fee_type = str(props.get("fee_type") or "")
        if rule_fee_type and rule_fee_type != fee_type:
            findings.append({
                "code": "PRICING_RULE_TYPE_MISMATCH", "severity": "HIGH",
                "fee_id": fee_id, "rule_id": "R-PRICE-01", "pricing_rule_id": rule_id,
                "title": f"费用 {fee_id} 绑定了不匹配的定价规则",
                "message": f"费用类型 {fee_type} 与规则 {rule_id} 的费用类型 {rule_fee_type} 不一致。",
            })
        occurred = str(fee.get("occurred_at") or "")
        effective_from = str(props.get("effective_from") or "")
        effective_to = str(props.get("effective_to") or "")
        try:
            occurred_at = datetime.fromisoformat(occurred).date()
            start = datetime.fromisoformat(effective_from).date() if effective_from else None
            end = datetime.fromisoformat(effective_to).date() if effective_to else None
        except ValueError:
            findings.append({
                "code": "PRICING_RULE_DATE_INVALID", "severity": "HIGH",
                "fee_id": fee_id, "rule_id": "R-PRICE-01", "pricing_rule_id": rule_id,
                "title": f"费用 {fee_id} 的规则生效期无法解析",
                "message": (
                    f"费用发生时间 {occurred or '（空）'}，规则 {rule_id} 生效期 "
                    f"{effective_from or '（空）'} ~ {effective_to or '（空）'}。"
                ),
            })
        else:
            if (start and occurred_at < start) or (end and occurred_at > end):
                findings.append({
                    "code": "PRICING_RULE_EXPIRED", "severity": "HIGH",
                    "fee_id": fee_id, "rule_id": "R-PRICE-01", "pricing_rule_id": rule_id,
                    "title": f"费用 {fee_id} 发生时间不在定价规则生效期内",
                    "message": (
                        f"费用发生时间 {occurred}，规则 {rule_id} 生效期 "
                        f"{effective_from} ~ {effective_to}，违反 R-PRICE-01。"
                    ),
                })
        bound_rule_id = str(fee.get("pricing_rule_id") or "")
        if bound_rule_id and rule_id and bound_rule_id != rule_id:
            findings.append({
                "code": "PRICING_RULE_ID_MISMATCH", "severity": "HIGH",
                "fee_id": fee_id, "rule_id": "R-PRICE-01", "pricing_rule_id": rule_id,
                "title": f"费用 {fee_id} 的规则身份不一致",
                "message": f"费用记录规则 {bound_rule_id}，关系解析到规则 {rule_id}。",
            })
        fee_version = str(fee.get("pricing_rule_version") or "")
        rule_version = str(props.get("version") or "")
        if fee_version and rule_version and fee_version != rule_version:
            findings.append({
                "code": "PRICING_RULE_VERSION_MISMATCH", "severity": "HIGH",
                "fee_id": fee_id, "rule_id": "R-PRICE-01", "pricing_rule_id": rule_id,
                "title": f"费用 {fee_id} 的规则版本不一致",
                "message": (
                    f"费用记录规则版本 {fee_version}，关系解析到规则 {rule_id} "
                    f"版本 {rule_version}。"
                ),
            })
        return findings

    def _vehicle_type_for_waybill(self, waybill_id: str, waybill: dict) -> str:
        """按运单实际使用的车辆解析车型；没有关系时回退到运单字段。"""
        rel = self.facts.one(
            "SELECT object_id FROM relations WHERE predicate='waybill_uses_vehicle' "
            "AND subject_id=? AND valid_to IS NULL ORDER BY valid_from DESC LIMIT 1",
            (waybill_id,),
        )
        if rel:
            row = self.facts.get_row(rel["object_id"])
            if row:
                props = Store.decode(row)["props"]
                vehicle_type = str(props.get("vehicle_type") or "")
                if vehicle_type:
                    return vehicle_type
        return str(waybill.get("vehicle_type") or "")

    def _expected_from_rule(self, semantic: SemanticLayer, fee_type: str,
                            params: dict, waybill: dict,
                            vehicle_type: str = "") -> tuple[float | None, dict]:
        """执行语义层 judge.compute 公式计算应计金额。

        计算逻辑完全由 semantic_layer.json 定义，运行时不再按 fee_type
        写分支；新增费用类型只需在语义层补定价与公式。
        """
        return semantic.compute_fee(fee_type, params, waybill, vehicle_type)

    def _waiting_tariffs(self, config: dict, wait_minutes: float,
                         selected: str) -> list[dict]:
        """把同一等候时长套到各车型费率上，供演示对照和影响面排查。"""
        by_vehicle_type = config.get("by_vehicle_type") or {}
        rows = []
        for vehicle_type, override in by_vehicle_type.items():
            effective = {**config, **(override or {})}
            free = float(effective.get("free_minutes") or 0)
            block = float(effective.get("block_minutes") or 0)
            per = float(effective.get("per_block") or 0)
            blocks = math.ceil(max(0.0, wait_minutes - free) / block) if block > 0 else 0
            rows.append({
                "vehicle_type": vehicle_type,
                "free_minutes": free,
                "block_minutes": block,
                "per_block": per,
                "blocks": blocks,
                "expected": round(blocks * per, 2),
                "selected": vehicle_type == selected,
            })
        return rows

    def fee_breakdown_batch(self, waybills: list) -> dict:
        """批量费用拆解（object_set 参数形态，对齐 Palantir Function 的
        ObjectSet 入参：一次函数调用吃整个对象集，而非逐对象循环调用）。"""
        if not isinstance(waybills, list) or not waybills:
            raise BusinessReject("waybills 必须是非空对象 ID 列表", "SET_INVALID")
        results = []
        total_variance = 0.0
        for oid in waybills:
            r = self.fee_analysis(oid)
            results.append({
                "waybill_id": oid,
                "quoted_total": r["quoted_total"],
                "actual_total": r["actual_total"],
                "variance": r["variance"],
                "finding_codes": [f["code"] for f in r["findings"]],
            })
            total_variance += float(r["variance"])
        return {
            "count": len(results),
            "total_variance": round(total_variance, 2),
            "results": results,
        }

    def fee_analysis(self, waybill_id: str, sem: SemanticLayer | None = None) -> dict:
        semantic = sem or self.sem
        row = self.facts.get_row(waybill_id)
        if not row or row["type"] != "Waybill":
            raise BusinessReject(f"运单不存在: {waybill_id}", "OBJECT_NOT_FOUND")
        waybill = Store.decode(row)
        wprops = waybill["props"]
        fee_ids = [r["object_id"] for r in self.facts.all(
            "SELECT object_id FROM relations WHERE predicate='waybill_produces_fee' "
            "AND subject_id=? AND valid_to IS NULL ORDER BY object_id", (waybill_id,))]
        fees = []
        for fid in fee_ids:
            fr = self.facts.get_row(fid)
            if fr:
                fees.append(Store.decode(fr))
        actual_total = round(sum(f["props"].get("actual_amount", 0) or 0 for f in fees), 2)
        effective_total = round(sum(
            (f["props"].get("actual_amount", 0) or 0) - (f["props"].get("waived_amount", 0) or 0)
            for f in fees), 2)
        # 报价侧独立回溯计价中心明细；运单 estimated_fee 只作为快照对照，
        # 不再充当报价总额（快照会漂移，报价明细才是源事实）。
        quote_detail = self._quote_detail_for_waybill(waybill_id, wprops)
        quoted_total = quote_detail["quoted_total"]
        source_check = self._fee_source_coverage(waybill_id, wprops)
        variance = round(actual_total - quoted_total, 2)

        waiting_config = semantic.pricing.get("waiting") or {}
        vehicle_type = self._vehicle_type_for_waybill(waybill_id, wprops)
        historical = os.path.abspath(semantic.path) != os.path.abspath(self.base_sem.path)
        wait_minutes = float(wprops.get("waiting_minutes") or 0)
        free_minutes = float(waiting_config.get("free_minutes") or 0)
        block_minutes = float(waiting_config.get("block_minutes") or 0)
        per_block = float(waiting_config.get("per_block") or 0)
        blocks = (
            math.ceil(max(0.0, wait_minutes - free_minutes) / block_minutes)
            if block_minutes > 0 else 0
        )
        rule_wait_fee = round(blocks * per_block, 2)
        version = str(semantic.raw.get("meta", {}).get("version") or "")
        waiting_rule_id = str(waiting_config.get("rule_id") or "")
        waiting_rule_pricing_id = ""
        waiting_rule_version = str(waiting_config.get("version") or version)
        waiting_rule_effective_from = str(waiting_config.get("effective_from") or "")
        waiting_rule_effective_to = str(waiting_config.get("effective_to") or "")
        waiting_rule_formula = (
            f"ceil(max(0, waiting_minutes - {free_minutes:g}) / {block_minutes:g})"
            f" * {per_block:g}"
        )
        waiting_tariffs = self._waiting_tariffs(waiting_config, wait_minutes, vehicle_type)
        waiting_rule_meta: dict = {}

        breakdown = []
        findings = []
        waiting_fee_id = None
        waiting_actual = 0.0
        highway_actual = 0.0
        for fee in fees:
            p = fee["props"]
            quoted_snapshot = round(float(p.get("quoted_amount", 0) or 0), 2)
            quote_item = self._quoted_for_fee(fee["object_id"], quote_detail)
            if quote_item:
                quoted = round(float(quote_item["amount"]), 2)
                quote_item_id = quote_item["quote_item_id"]
                quoted_source = "pricing.list_quote_items"
            else:
                type_entry = quote_detail["by_fee_type"].get(p["fee_type"])
                quoted = round(float(type_entry["amount"]), 2) if type_entry else 0.0
                quote_item_id = None
                quoted_source = (
                    "pricing.list_quote_items(fee_type)" if type_entry else "UNRESOLVED"
                )
            expected = round(float(p.get("expected_amount", 0) or 0), 2)
            actual = round(float(p.get("actual_amount", 0) or 0), 2)
            waived = round(float(p.get("waived_amount", 0) or 0), 2)
            quote_delta = round(actual - quoted, 2)
            if quote_item and abs(quoted - quoted_snapshot) > 0.01:
                findings.append({
                    "code": "QUOTE_SNAPSHOT_DRIFT", "severity": "MEDIUM",
                    "fee_id": p["fee_id"],
                    "title": f"{p['fee_label']}运单报价快照与报价明细不一致",
                    "message": (
                        f"运单快照 {quoted_snapshot:.2f}，报价明细 "
                        f"{quote_item['quote_item_id']} = {quoted:.2f}；以明细为准。"
                    ),
                    "quoted": quoted, "actual": quoted_snapshot,
                    "delta": round(quoted - quoted_snapshot, 2),
                })

            pricing_rule = self._pricing_rule_for_fee(fee["object_id"])
            rule_props = (pricing_rule or {}).get("props") or {}
            rule_findings = self._validate_pricing_rule(p, pricing_rule)
            findings.extend(rule_findings)
            params = dict(rule_props.get("params") or {})
            if p["fee_type"] == "waiting":
                semantic_waiting = dict(semantic.pricing.get("waiting") or {})
                if historical:
                    # 回放历史版本时，以该发布快照的费率为准，避免今天的规则实例覆盖历史标准。
                    for key in ("free_minutes", "block_minutes", "per_block", "by_vehicle_type"):
                        if key in semantic_waiting:
                            params[key] = semantic_waiting[key]
                elif not params:
                    params = semantic_waiting
            computed, rule_meta = self._expected_from_rule(
                semantic, p["fee_type"], params, wprops, vehicle_type
            )
            if p["fee_type"] == "waiting":
                waiting_rule_meta = dict(rule_meta or {})
            semantic_rule_id = str(
                (semantic.pricing.get(p["fee_type"]) or {}).get("rule_id") or ""
            )
            rule_judge = (semantic.rules.get(semantic_rule_id) or {}).get("judge") or {}
            formula_text = str(rule_judge.get("formula") or "")
            ast_target = str((rule_judge.get("calc") or {}).get("target") or "")
            has_fee_ast = "ast" in rule_judge and ast_target in FEE_AMOUNT_TARGETS
            # 规则声明了可核算口径却没有可执行公式时，不能退回到事实里的
            # expected_amount 假装算过；否则删掉本体公式结论照样成立。
            unverifiable = (
                computed is None
                and bool(semantic_rule_id)
                and ("compute" in rule_judge or has_fee_ast
                     or ast_target in FEE_AMOUNT_TARGETS
                     or is_expression(formula_text))
            )
            if computed is not None:
                rule_expected = computed
                rule_delta = round(actual - rule_expected, 2)
            elif unverifiable:
                rule_expected = None
                rule_delta = None
                findings.append({
                    "code": "ONTOLOGY_COMPUTE_MISSING",
                    "severity": "BLOCKER",
                    "fee_id": p["fee_id"],
                    "rule_id": semantic_rule_id or None,
                    "title": f"{p['fee_label']}无法从本体计算应计金额",
                    "message": (
                        f"规则 {semantic_rule_id} 声明了可核算口径，"
                        "但语义层没有可执行的 judge.ast/judge.compute；"
                        "该费用不参与差异结论。"
                    ),
                    "expected": None, "actual": actual, "delta": None,
                })
            else:
                rule_expected = expected
                rule_delta = round(actual - rule_expected, 2)
            if p["fee_type"] == "waiting":
                waiting_fee_id = p["fee_id"]
                waiting_actual = actual
                if pricing_rule:
                    waiting_rule_pricing_id = str(rule_props.get("rule_id") or "")
                    waiting_rule_version = str(
                        rule_props.get("version") or waiting_rule_version
                    )
                    effective_from = str(rule_props.get("effective_from") or "")
                    effective_to = str(rule_props.get("effective_to") or "")
                    waiting_rule_formula = str(rule_props.get("formula") or waiting_rule_formula)
                else:
                    effective_from = effective_to = ""
                if rule_meta:
                    free_minutes = float(
                        rule_meta["free_minutes"]
                        if rule_meta.get("free_minutes") is not None else free_minutes
                    )
                    block_minutes = float(
                        rule_meta["block_minutes"]
                        if rule_meta.get("block_minutes") is not None else block_minutes
                    )
                    per_block = float(
                        rule_meta["per_block"]
                        if rule_meta.get("per_block") is not None else per_block
                    )
                    blocks = int(
                        rule_meta["blocks"]
                        if rule_meta.get("blocks") is not None else blocks
                    )
                    vehicle_type = str(rule_meta.get("vehicle_type") or vehicle_type)
                waiting_rule_formula = (
                    f"ceil(max(0, waiting_minutes - {free_minutes:g}) / {block_minutes:g})"
                    f" * {per_block:g}"
                )
                rule_wait_fee = rule_expected
                waiting_rule_effective_from = effective_from
                waiting_rule_effective_to = effective_to
            if p["fee_type"] == "highway":
                highway_actual = actual

            breakdown.append({
                "fee_id": p["fee_id"], "fee_type": p["fee_type"], "label": p["fee_label"],
                "quoted_amount": quoted, "quoted_snapshot": quoted_snapshot,
                "quote_item_id": quote_item_id, "quoted_amount_source": quoted_source,
                "expected_amount": expected,
                "rule_expected": rule_expected,
                "rule_meta": rule_meta,
                "actual_amount": actual, "waived_amount": waived,
                "quote_delta": quote_delta, "rule_delta": rule_delta,
                "pricing_rule_id": (rule_props.get("rule_id")
                                    or p.get("pricing_rule_id")),
                "pricing_rule_version": (rule_props.get("version")
                                         or p.get("pricing_rule_version")),
                "status": fee["status"],
            })
            if p["fee_type"] == "waiting" and rule_expected is not None:
                severity = "HIGH" if abs(rule_delta) > 0.01 else "OK"
                findings.append({
                    "code": "WAITING_FEE_MISMATCH", "severity": severity,
                    "fee_id": p["fee_id"],
                    "rule_id": semantic_rule_id or waiting_rule_id,
                    "pricing_rule_id": waiting_rule_pricing_id or p.get("pricing_rule_id"),
                    "title": f"等候费按规则应计 {rule_wait_fee:.2f} 元，实际 {actual:.2f} 元",
                    "message": (
                        f"等候 {wait_minutes:.0f} 分钟，免费 {free_minutes:.0f} 分钟，"
                        f"超出 {max(0.0, wait_minutes - free_minutes):.0f} 分钟；"
                        f"按每 {block_minutes:.0f} 分钟 {per_block:.2f} 元"
                        f"应计 {rule_wait_fee:.2f} 元，"
                        f"实际 {actual:.2f} 元，差异 {rule_delta:.2f} 元。"
                    ),
                    "expected": rule_wait_fee, "actual": actual, "delta": rule_delta,
                })
            elif rule_delta is not None and abs(rule_delta) > 0.01:
                findings.append({
                    "code": "RULE_DELTA", "severity": "MEDIUM",
                    "fee_id": p["fee_id"],
                    "rule_id": semantic_rule_id or str(rule_props.get("rule_id") or "") or None,
                    "pricing_rule_id": rule_props.get("rule_id"),
                    "title": f"{p['fee_label']}与规则应计不一致",
                    "message": f"规则应计 {rule_expected:.2f}，实际 {actual:.2f}，差异 {rule_delta:.2f}。",
                    "expected": rule_expected, "actual": actual, "delta": rule_delta,
                })

        findings.extend(quote_detail["findings"])
        findings.extend(source_check["findings"])
        quote_item_by_fee = {
            item["fee_id"]: item["quote_item_id"] for item in breakdown
            if item.get("quote_item_id")
        }
        for fee_source in source_check["fee_sources"]:
            fee_source["quote_item_id"] = quote_item_by_fee.get(fee_source["fee_id"])

        evidence = []
        for target_id in [waybill_id] + fee_ids:
            for ev in self.facts.all(
                    "SELECT * FROM evidence WHERE object_id=? ORDER BY occurred_at", (target_id,)):
                ev = dict(ev)
                ev["payload"] = json.loads(ev["payload"])
                evidence.append(ev)

        settlement_ids = sorted({r["object_id"] for r in self.facts.all(
            "SELECT object_id FROM relations WHERE predicate='fee_included_in_settlement' "
            "AND subject_id IN (%s) AND valid_to IS NULL" % ",".join("?" for _ in fee_ids),
            tuple(fee_ids))}) if fee_ids else []
        exception_ids = [r["object_id"] for r in self.facts.all(
            "SELECT object_id FROM relations WHERE predicate='waybill_has_exception' "
            "AND subject_id=? AND valid_to IS NULL", (waybill_id,))]

        if not fees:
            conclusion = (
                f"运单当前状态 {waybill['status']}，尚未产生费用项；"
                f"预估 {quoted_total:.2f}、实际 0.00，差额 {variance:+.2f} 是计费数据缺口，不是费用异常。"
            )
        elif abs(variance) < 0.005:
            conclusion = f"实际与预估一致，均为 {actual_total:.2f} 元，没有金额差异。"
        else:
            order_id = f"order:{wprops.get('order_id')}"
            quote_ids = [r["object_id"] for r in self.facts.all(
                "SELECT object_id FROM relations WHERE predicate='order_has_quote' "
                "AND subject_id=? AND valid_to IS NULL ORDER BY valid_from DESC", (order_id,))]
            quote_ref = quote_ids[0].split(":", 1)[-1] if quote_ids else "报价单"
            delta_parts = []
            for item in breakdown:
                if abs(item["quote_delta"]) <= 0.005:
                    continue
                if item["quote_delta"] > 0:
                    delta_parts.append(f'{item["label"]}新增 {item["quote_delta"]:.2f} 元')
                else:
                    delta_parts.append(f'{item["label"]}减少 {abs(item["quote_delta"]):.2f} 元')
            overcharges = [
                item for item in breakdown
                if item["rule_delta"] is not None and item["rule_delta"] > 0.005
            ]
            undercharges = [
                item for item in breakdown
                if item["rule_delta"] is not None and item["rule_delta"] < -0.005
            ]
            evidence_by_object: dict[str, list[dict]] = {}
            for ev in evidence:
                evidence_by_object.setdefault(ev["object_id"], []).append(ev)
            supported = []
            for item in breakdown:
                if (item["actual_amount"] <= 0.005
                        or item["rule_delta"] is None
                        or item["rule_delta"] > 0.005):
                    continue
                related = evidence_by_object.get(f'fee:{item["fee_id"]}', [])
                if related:
                    supported.append(
                        f'{item["label"]} {item["actual_amount"]:.2f} 元有 {len(related)} 条证据支持'
                    )
            rule_issues = [
                finding for finding in findings
                if finding["code"].startswith("PRICING_RULE_")
            ]
            conclusion = (
                f"实际比预估{'多' if variance > 0 else '少'} {abs(variance):.2f} 元。"
            )
            if delta_parts:
                conclusion += (
                    f"报价单 {quote_ref} 的差异来自：" + "；".join(delta_parts) + "。"
                )
            if overcharges:
                details = "；".join(
                    f'{item["label"]}规则应计 {item["rule_expected"]:.2f} 元、'
                    f'实际 {item["actual_amount"]:.2f} 元，超收 {item["rule_delta"]:.2f} 元'
                    for item in overcharges
                )
                conclusion += f"其中 {details}。"
            if undercharges:
                details = "；".join(
                    f'{item["label"]}规则应计 {item["rule_expected"]:.2f} 元、'
                    f'实际 {item["actual_amount"]:.2f} 元，少收 {abs(item["rule_delta"]):.2f} 元'
                    for item in undercharges
                )
                conclusion += f"另有 {details}。"
            if supported:
                conclusion += "；".join(supported) + "。"
            if rule_issues:
                conclusion += (
                    "定价规则校验发现："
                    + "；".join(finding["message"] for finding in rule_issues) + "。"
                )
            total_over = round(sum(item["rule_delta"] for item in overcharges), 2)
            if total_over > 0.005:
                conclusion += (
                    f"需处理异常 {total_over:.2f} 元，"
                    "建议走费用申诉、减免与结算冲正。"
                )
            else:
                conclusion += "未发现规则超收。"

        # 前端展示的中间量优先取本体 compute.meta 的结果，
        # 不再由 Python 重新推算一遍计费块数和费率。
        waiting_free = waiting_rule_meta.get("free_minutes", free_minutes)
        waiting_block = waiting_rule_meta.get("block_minutes", block_minutes)
        waiting_blocks = waiting_rule_meta.get("blocks", blocks)
        waiting_per_block = waiting_rule_meta.get("per_block", per_block)
        waiting_vehicle = waiting_rule_meta.get("vehicle_type", vehicle_type)

        traceability = self._traceability(
            waybill_id=waybill_id,
            semantic=semantic,
            wait_minutes=wait_minutes,
            free_minutes=waiting_free,
            block_minutes=waiting_block,
            per_block=waiting_per_block,
            blocks=waiting_blocks,
            rule_wait_fee=rule_wait_fee,
            waiting_fee_id=waiting_fee_id,
            waiting_actual=waiting_actual,
            vehicle_type=waiting_vehicle,
            waiting_rule_id=waiting_rule_id,
            waiting_rule_formula=waiting_rule_formula,
            waiting_rule_effective_from=waiting_rule_effective_from,
            waiting_rule_effective_to=waiting_rule_effective_to,
            evidence=evidence,
            conclusion=conclusion,
            quote=quote_detail,
            source_coverage=source_check,
        )

        return {
            "waybill_id": waybill_id,
            "waybill_no": wprops.get("waybill_id"),
            "status": waybill["status"],
            "ontology_version": version,
            "quoted_total": round(quoted_total, 2),
            "quoted_total_source": quote_detail["source"],
            "quote": quote_detail,
            "source_coverage": {
                **source_check["source_coverage"],
                "fee_sources": source_check["fee_sources"],
            },
            "actual_total": actual_total,
            "effective_total": effective_total,
            "variance": variance,
            "waiting_minutes": wait_minutes,
            "free_wait_minutes": free_minutes,
            "waiting_rule": {
                "rule_id": waiting_rule_id,
                "pricing_rule_id": waiting_rule_pricing_id or "",
                "rule_version": waiting_rule_version,
                "effective_from": waiting_rule_effective_from,
                "effective_to": waiting_rule_effective_to,
                "formula": waiting_rule_formula,
                "free_minutes": waiting_free,
                "block_minutes": waiting_block,
                "blocks": waiting_blocks, "per_block": waiting_per_block,
                "expected": rule_wait_fee,
                "vehicle_type": waiting_vehicle,
                "tariffs": waiting_tariffs,
            },
            "breakdown": breakdown,
            "findings": findings,
            "evidence": evidence,
            "traceability": traceability,
            "settlement_ids": settlement_ids,
            "exception_ids": exception_ids,
            "conclusion": conclusion,
        }

    def quote_reconcile(self, waybill_id: str) -> dict:
        """确定性对比报价明细与实际费用，不依赖模型临时拼查询。"""
        row = self.facts.get_row(waybill_id)
        if not row or row["type"] != "Waybill":
            raise BusinessReject(f"运单不存在: {waybill_id}", "OBJECT_NOT_FOUND")
        waybill = Store.decode(row)
        props = waybill["props"]
        order_id = f"order:{props.get('order_id')}"
        quote_rel = self.facts.one(
            "SELECT object_id FROM relations WHERE predicate='order_has_quote' "
            "AND subject_id=? AND valid_to IS NULL "
            "ORDER BY valid_from DESC LIMIT 1",
            (order_id,),
        )
        quote_id = quote_rel["object_id"] if quote_rel else None
        quote_row = self.facts.get_row(quote_id) if quote_id else None
        quote = Store.decode(quote_row) if quote_row else None
        quote_items: list[dict] = []
        if quote_id:
            item_ids = [r["object_id"] for r in self.facts.all(
                "SELECT object_id FROM relations WHERE predicate='quote_contains_item' "
                "AND subject_id=? AND valid_to IS NULL ORDER BY object_id",
                (quote_id,),
            )]
            for item_id in item_ids:
                item_row = self.facts.get_row(item_id)
                if item_row:
                    quote_items.append(Store.decode(item_row))
        fee_ids = [r["object_id"] for r in self.facts.all(
            "SELECT object_id FROM relations WHERE predicate='waybill_produces_fee' "
            "AND subject_id=? AND valid_to IS NULL ORDER BY object_id",
            (waybill_id,),
        )]
        fees: list[dict] = []
        for fee_id in fee_ids:
            fee_row = self.facts.get_row(fee_id)
            if fee_row:
                fees.append(Store.decode(fee_row))

        quote_by_type: dict[str, dict] = {}
        for item in quote_items:
            p = item["props"]
            fee_type = p.get("fee_type")
            entry = quote_by_type.setdefault(fee_type, {
                "label": p.get("fee_label") or fee_type,
                "amount": 0.0, "ids": [],
            })
            entry["amount"] += float(p.get("amount") or 0)
            entry["ids"].append(item["object_id"])
        fee_by_type: dict[str, dict] = {}
        for fee in fees:
            p = fee["props"]
            fee_type = p.get("fee_type")
            entry = fee_by_type.setdefault(fee_type, {
                "label": p.get("fee_label") or fee_type,
                "quoted": 0.0, "actual": 0.0, "waived": 0.0, "ids": [],
            })
            entry["quoted"] += float(p.get("quoted_amount") or 0)
            entry["actual"] += float(p.get("actual_amount") or 0)
            entry["waived"] += float(p.get("waived_amount") or 0)
            entry["ids"].append(fee["object_id"])

        rows = []
        findings = []
        for fee_type in sorted(set(quote_by_type) | set(fee_by_type)):
            q = quote_by_type.get(fee_type, {})
            f = fee_by_type.get(fee_type, {})
            quoted = round(float(q.get("amount") or 0), 2)
            actual = round(float(f.get("actual") or 0), 2)
            delta = round(actual - quoted, 2)
            included = bool(q) and quoted > 0.005
            status = (
                "MISSING" if not included and actual > 0.005
                else "NOT_BILLED" if included and actual <= 0.005
                else "MATCH" if abs(delta) <= 0.01
                else "AMOUNT_MISMATCH"
            )
            rows.append({
                "fee_type": fee_type,
                "fee_label": f.get("label") or q.get("label") or fee_type,
                "quote_item_ids": q.get("ids", []),
                "fee_ids": f.get("ids", []),
                "quoted_amount": quoted,
                "actual_amount": actual,
                "waived_amount": round(float(f.get("waived") or 0), 2),
                "delta": delta,
                "included_in_quote": included,
                "status": status,
            })
            if status == "MISSING":
                findings.append({
                    "code": "QUOTE_MISSING_FEE_TYPE",
                    "severity": "HIGH",
                    "fee_type": fee_type,
                    "title": f"报价未包含{f.get('label') or fee_type}",
                    "message": (
                        f"报价单没有包含{f.get('label') or fee_type}，"
                        f"实际产生 {actual:.2f} 元。"
                    ),
                    "quoted": quoted, "actual": actual, "delta": delta,
                })
            elif status == "AMOUNT_MISMATCH":
                findings.append({
                    "code": "QUOTE_AMOUNT_MISMATCH",
                    "severity": "MEDIUM",
                    "fee_type": fee_type,
                    "title": f"{f.get('label') or fee_type}报价与实收不一致",
                    "message": (
                        f"报价 {quoted:.2f}，实际 {actual:.2f}，差异 {delta:+.2f}。"
                    ),
                    "quoted": quoted, "actual": actual, "delta": delta,
                })

        quote_total = round(float((quote or {}).get("props", {}).get("total_amount") or 0), 2)
        actual_total = round(sum(float(f["props"].get("actual_amount") or 0) for f in fees), 2)
        missing = [row for row in rows if row["status"] == "MISSING"]
        conclusion = (
            f"报价单 {quote_id} 共 {len(quote_items)} 项、报价总额 {quote_total:.2f} 元；"
            f"实际产生 {len(fees)} 个费用项、实收 {actual_total:.2f} 元。"
            + (
                "报价未包含：" + "、".join(row["fee_label"] for row in missing) + "。"
                if missing else "所有实际费用类型都在报价中。"
            )
        )
        return {
            "waybill_id": waybill_id,
            "waybill_no": props.get("waybill_id"),
            "order_id": order_id,
            "quote_id": quote_id,
            "quote_version": (quote or {}).get("props", {}).get("version"),
            "quote_total": quote_total,
            "actual_total": actual_total,
            "variance": round(actual_total - quote_total, 2),
            "rows": rows,
            "findings": findings,
            "conclusion": conclusion,
        }

    def evidence_chain(self, waybill_id: str) -> dict:
        """确定性汇总运单、费用项及相关对象的证据与事件。"""
        row = self.facts.get_row(waybill_id)
        if not row or row["type"] != "Waybill":
            raise BusinessReject(f"运单不存在: {waybill_id}", "OBJECT_NOT_FOUND")
        waybill = Store.decode(row)
        props = waybill["props"]
        fee_ids = [r["object_id"] for r in self.facts.all(
            "SELECT object_id FROM relations WHERE predicate='waybill_produces_fee' "
            "AND subject_id=? AND valid_to IS NULL ORDER BY object_id",
            (waybill_id,),
        )]
        object_ids = [waybill_id] + fee_ids
        evidence = self.facts.list_evidence(object_ids)
        events = self.facts.list_events(object_ids)
        nodes = []
        for object_id in object_ids:
            node_row = self.facts.get_row(object_id)
            if not node_row:
                continue
            node = Store.decode(node_row)
            nodes.append({
                "object_id": object_id,
                "type": node["type"],
                "name": node["name"],
                "status": node["status"],
            })
        placeholders = ",".join("?" for _ in object_ids)
        edges = [dict(rel) for rel in self.facts.all(
            f"SELECT * FROM relations WHERE subject_id IN ({placeholders}) "
            "AND valid_to IS NULL ORDER BY predicate, object_id",
            tuple(object_ids),
        )]
        for edge in edges:
            edge["label"] = self.sem.relation_label(edge["predicate"])
        evidence_by_object: dict[str, int] = {}
        for item in evidence:
            evidence_by_object[item["object_id"]] = (
                evidence_by_object.get(item["object_id"], 0) + 1
            )
        missing = [
            node for node in nodes
            if node["type"] in ("Waybill", "FeeItem")
            and evidence_by_object.get(node["object_id"], 0) == 0
        ]
        return {
            "waybill_id": waybill_id,
            "waybill_no": props.get("waybill_id"),
            "nodes": nodes,
            "edges": edges,
            "evidence": evidence,
            "events": events,
            "coverage": {
                "evidence_object_ids": sorted(evidence_by_object),
                "missing_evidence_ids": [node["object_id"] for node in missing],
                "evidence_count": len(evidence),
                "event_count": len(events),
            },
            "conclusion": (
                f"运单与 {len(fee_ids)} 个费用项共关联 {len(evidence)} 条证据、"
                f"{len(events)} 条事件；"
                + (
                    "缺少证据：" + "、".join(n["object_id"] for n in missing) + "。"
                    if missing else "关键对象均有证据覆盖。"
                )
            ),
        }

    def action_preflight(self, object_id: str, action_id: str | None = None) -> dict:
        """动作预检：返回可用/被阻止动作、规则阻断原因和影响面。"""
        view = self.get_object(object_id)
        actions = [
            item for item in view["available_actions"] + view["blocked_actions"]
            if not action_id or item["id"] == action_id
        ]
        impact = self.impact(object_id)
        return {
            "object_id": object_id,
            "type": view["type"],
            "type_label": view["type_label"],
            "status": view["status"],
            "actions": actions,
            "available": [item["id"] for item in actions if item["allowed"]],
            "blocked": [
                {"action_id": item["id"], "reason": item.get("blocked_reason")}
                for item in actions if not item["allowed"]
            ],
            "impact": impact,
        }

    def _traceability(self, waybill_id: str, semantic: SemanticLayer,
                      wait_minutes: float, free_minutes: float,
                      block_minutes: float, per_block: float, blocks: int,
                      rule_wait_fee: float, waiting_fee_id: str | None,
                      waiting_actual: float, waiting_rule_id: str,
                      waiting_rule_formula: str,
                      waiting_rule_effective_from: str,
                      waiting_rule_effective_to: str,
                      vehicle_type: str,
                      evidence: list[dict], conclusion: str,
                      quote: dict | None = None,
                      source_coverage: dict | None = None) -> dict:
        """把结论拆成可逐层点击的追溯链，最后落到本体版本与证据 checksum。"""
        version = str(semantic.raw.get("meta", {}).get("version") or "")
        evidence_items = []
        for ev in evidence:
            evidence_items.append({
                "evidence_id": ev["evidence_id"],
                "evidence_type": ev["evidence_type"],
                "source_system": ev["source_system"],
                "occurred_at": ev["occurred_at"],
                "version": ev["version"],
                "checksum": ev["checksum"],
                "summary": ev["summary"],
                "object_id": ev["object_id"],
            })
        support = {
            "DRIVER_ARRIVAL": "证明司机到达时间，是等候时长起点",
            "LOADING_CONFIRMED": "证明装货完成时间，是等候时长终点",
            "PRICING_RULE": "证明等候费规则参数来源",
            "ETC_TOLL": "证明高速费为据实结算",
        }
        for item in evidence_items:
            item["supports"] = support.get(item["evidence_type"], "业务事实佐证")
        quote = quote or {}
        source_coverage = source_coverage or {}
        coverage = source_coverage.get("source_coverage") or {}
        source_steps = [{
            "id": "quote",
            "label": "报价明细",
            "value": quote.get("quote_id") or "未找到报价单",
            "detail": (
                f"报价单版本 {quote.get('quote_version') or '未知'}；"
                f"逐项明细 {len(quote.get('items') or [])} 条，"
                f"合计 {float(quote.get('quoted_total') or 0):.2f} 元"
            ),
            "source": quote.get("source") or "pricing.list_quote_items",
        }]
        source_steps.extend(source_coverage.get("trace") or [])
        source_steps.append({
            "id": "source_coverage",
            "label": "费用源覆盖",
            "value": (
                f"{coverage.get('resolved', 0)}/{coverage.get('fee_count', 0)} 项已解析"
            ),
            "detail": (
                "；".join(
                    f"{row['system']} {row['fee_count']} 项"
                    for row in coverage.get("sources") or []
                ) or "没有生效的费用源连接器"
            ),
            "source": "semantic.data_sources",
        })
        return {
            "version": version,
            "version_source": os.path.relpath(semantic.path, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "rule_id": waiting_rule_id,
            "steps": [
                {"id": "conclusion", "label": "结论", "value": conclusion,
                 "source": "fee_analysis"},
                *source_steps,
                {"id": "fee", "label": "费用项", "value": waiting_fee_id or "无等候费",
                 "detail": f"实际金额 {waiting_actual:.2f} 元", "source": "billing.list_fee_items"},
                {"id": "rule", "label": "规则", "value": waiting_rule_id,
                 "detail": (
                     f"车型 {vehicle_type or '未识别'}；"
                     f"免费 {free_minutes:g} 分钟，每 {block_minutes:g} 分钟 {per_block:g} 元；"
                     f"生效期 {waiting_rule_effective_from or '未限定'} ~ "
                     f"{waiting_rule_effective_to or '未限定'}"
                 ),
                 "source": "pricing_rule · fee_priced_by_rule"},
                {"id": "calc", "label": "计算", "value": (
                    f"{waiting_rule_formula} = {rule_wait_fee:.2f} 元"
                    if isinstance(rule_wait_fee, (int, float))
                    else "本体未提供可执行公式，无法核算"
                ), "detail": f"计费块数 {blocks}", "source": "fee_analysis"},
                {"id": "facts", "label": "事实字段", "value": (
                    f"waiting_minutes={wait_minutes:g}; free_wait_minutes={free_minutes:g}"
                ), "detail": f"运单 {waybill_id}", "source": "tms.get_waybill"},
                {"id": "evidence", "label": "证据", "value": f"{len(evidence_items)} 条",
                 "detail": "每条证据带 sha256 摘要，可回查源系统", "items": evidence_items,
                 "source": "evidence.list"},
                {"id": "version", "label": "本体版本", "value": version,
                 "detail": "结论绑定到具体发布版本，参数变更后可复现", "source": "freight/releases"},
            ],
        }

    def semantic_for(self, version: str | None = None) -> SemanticLayer:
        if not version or version in ("current", self.base_version):
            return self.base_sem
        if version == self.active_version:
            return self.sem
        path = semantic_path(version)
        if not os.path.exists(path):
            raise BusinessReject(f"本体版本不存在: {version}", "VERSION_NOT_FOUND")
        return SemanticLayer(path)

    def semantic_versions(self) -> dict:
        current = {
            "version": self.base_version,
            "label": "当前工作副本",
            "based_on": None,
            "checksum": file_checksum(self.base_sem.path),
            "source": "freight/semantic_layer.json",
            "kind": "workspace",
            "active": self.active_version == self.base_version,
        }
        releases = []
        for manifest in list_manifests():
            item = dict(manifest)
            item["kind"] = "release"
            item["active"] = item.get("version") == self.active_version
            releases.append(item)
        return {
            "active_version": self.active_version,
            "current": current,
            "releases": releases,
        }

    def activate_version(self, version: str) -> dict:
        if not version or version in ("current", self.base_version):
            semantic = self.base_sem
            resolved = self.base_version
        else:
            semantic = self.semantic_for(version)
            resolved = version
        self.sem = semantic
        self.active_version = resolved
        self.facts.sem = semantic
        return {
            "active_version": resolved,
            "meta": semantic.raw.get("meta", {}),
            "storage": semantic.path,
            "activated_at": now_iso(),
        }

    def rollback_version(self) -> dict:
        result = self.activate_version(self.base_version)
        result["message"] = f"已回滚到基线版本 {self.base_version}"
        return result

    @staticmethod
    def _semantic_changes(before: SemanticLayer, after: SemanticLayer) -> list[dict]:
        return [
            change for change in diff_semantics(before.raw, after.raw)
            if not change["path"].startswith("meta.")
        ]

    def semantic_diff(self, from_version: str | None = None,
                      to_version: str | None = None) -> dict:
        before_version = from_version or self.base_version
        after_version = to_version or self.active_version
        before = self.semantic_for(before_version)
        after = self.semantic_for(after_version)
        changes = self._semantic_changes(before, after)
        groups: dict[str, int] = {}
        for change in changes:
            root = change["path"].split(".", 1)[0].split("[", 1)[0]
            groups[root] = groups.get(root, 0) + 1
        return {
            "from_version": before_version,
            "to_version": after_version,
            "from_checksum": file_checksum(before.path),
            "to_checksum": file_checksum(after.path),
            "change_count": len(changes),
            "groups": groups,
            "changes": changes,
        }

    def semantic_impact(self, from_version: str | None = None,
                        to_version: str | None = None) -> dict:
        before_version = from_version or self.base_version
        after_version = to_version or self.active_version
        before = self.semantic_for(before_version)
        after = self.semantic_for(after_version)
        changes = self._semantic_changes(before, after)
        changed_fee_types = sorted({
            change["path"].split(".")[1]
            for change in changes
            if change["path"].startswith("pricing.")
            and len(change["path"].split(".")) > 1
        })
        changed_rules = sorted({
            change["path"].split("[", 1)[1].split("]", 1)[0]
            for change in changes
            if change["path"].startswith("rules[")
        })
        affected = []
        for row in self.facts.all(
                "SELECT object_id FROM objects WHERE type='Waybill' ORDER BY object_id"):
            waybill_id = row["object_id"]
            try:
                old = self.fee_analysis(waybill_id, sem=before)
                new = self.fee_analysis(waybill_id, sem=after)
            except BusinessReject:
                continue
            old_wait = next((f for f in old["findings"] if f["code"] == "WAITING_FEE_MISMATCH"), None)
            new_wait = next((f for f in new["findings"] if f["code"] == "WAITING_FEE_MISMATCH"), None)
            old_expected = old["waiting_rule"]["expected"]
            new_expected = new["waiting_rule"]["expected"]
            old_delta = old_wait["delta"] if old_wait else 0.0
            new_delta = new_wait["delta"] if new_wait else 0.0
            if abs(old_expected - new_expected) < 0.001 and abs(old_delta - new_delta) < 0.001:
                continue
            affected.append({
                "waybill_id": waybill_id,
                "waybill_no": old["waybill_no"],
                "old_waiting_expected": old_expected,
                "new_waiting_expected": new_expected,
                "waiting_delta": round(new_expected - old_expected, 2),
                "old_finding_delta": old_delta,
                "new_finding_delta": new_delta,
                "finding_delta_change": round(new_delta - old_delta, 2),
            })
        total = self.facts.scalar("SELECT COUNT(*) FROM objects WHERE type='Waybill'")
        return {
            "from_version": before_version,
            "to_version": after_version,
            "change_count": len(changes),
            "changed_fee_types": changed_fee_types,
            "changed_rules": changed_rules,
            "affected_count": len(affected),
            "unchanged_count": max(0, total - len(affected)),
            "affected_waybills": affected,
        }

    def semantic_validate(self, version: str | None = None) -> dict:
        semantic = self.semantic_for(version) if version else self.sem
        result = semantic.validate()
        data = semantic.dictionary()
        return {
            **result,
            "checks": len(result["dimensions"]),
            "dimensions": result["dimensions"],
            "counts": {
                "classes": data["class_count"],
                "relations": data["relation_count"],
                "rules": data["rule_count"],
                "actions": data["action_count"],
                "pricing": len(semantic.pricing),
                "data_sources": len(data.get("data_sources", [])),
            },
        }

    def semantic_guard_demo(self) -> dict:
        """构造一条非法规则，演示 CI 在合并前拦截。"""
        bad = json.loads(json.dumps(self.sem.raw, ensure_ascii=False))
        bad_rule = {
            "id": "R-ERR-01",
            "name": "错误规则演示：非法费用类型",
            "domain": "FeeItem",
            "priority": 1,
            "when": {"field": "fee_type", "op": "in", "value": ["waiting", "toll_fee"]},
            "judge": {
                "formula": "actual_amount > expected_amount",
                "explain": "演示规则引用了不存在的费用类型 toll_fee",
                "forbidden_actions": ["settle_waybill"],
            },
            "intent": "验证语义门禁能否拦截未定义的费用类型",
        }
        bad.setdefault("rules", []).append(bad_rule)
        issues = validate_semantic(bad)
        return {
            "blocked": bool(issues),
            "ok": not issues,
            "version": self.sem.raw.get("meta", {}).get("version"),
            "rule": bad_rule,
            "issues": issues,
            "message": (
                "语义门禁在合并前拦截：规则引用了未定义的 fee_type，CI 直接失败，不会进入发布快照。"
                if issues else "未拦截，请检查校验器。"
            ),
        }

    def explain(self, waybill_id: str, audience: str = "cs",
                sem: SemanticLayer | None = None) -> dict:
        audience = (audience or "cs").lower()
        if audience not in {"cs", "finance", "driver"}:
            raise BusinessReject(f"不支持的解释对象: {audience}", "AUDIENCE_UNSUPPORTED")
        analysis = self.fee_analysis(waybill_id, sem=sem)
        waiting = next(
            (f for f in analysis["findings"] if f["code"] == "WAITING_FEE_MISMATCH"), None
        )
        if waiting:
            rule_amount = waiting["expected"]
            actual = waiting["actual"]
            delta = waiting["delta"]
            rule_id = analysis["waiting_rule"]["rule_id"]
            highway = next(
                (item for item in analysis["breakdown"] if item["fee_type"] == "highway"),
                None,
            )
            highway_note = (
                f"高速费 {highway['actual_amount']:.2f} 元有 ETC 证据。"
                if highway else ""
            )
            if audience == "finance":
                answer = (
                    f"运单 {analysis['waybill_no']} 等候费实际 {actual:.2f} 元，规则应计 "
                    f"{rule_amount:.2f} 元，多计 {delta:.2f} 元；{highway_note}"
                    f"建议对等候费发起申诉、减免 {delta:.2f} 元并冲正结算，依据 {rule_id} "
                    f"（本体版本 {analysis['ontology_version']}）。"
                )
            elif audience == "driver":
                answer = (
                    f"本单等候 {analysis['waiting_minutes']:.0f} 分钟，按规则前 "
                    f"{analysis['free_wait_minutes']:.0f} 分钟免费，等候费应计 "
                    f"{rule_amount:.2f} 元；系统当前记账 {actual:.2f} 元，多出的 "
                    f"{delta:.2f} 元会走调整，不影响你的运费结算。"
                )
            else:
                answer = (
                    f"您这单等候费记了 {actual:.2f} 元。按平台规则，前 "
                    f"{analysis['free_wait_minutes']:.0f} 分钟免费，之后每 "
                    f"{analysis['waiting_rule']['block_minutes']:g} 分钟 "
                    f"{analysis['waiting_rule']['per_block']:.2f} 元，应计 {rule_amount:.2f} 元，"
                    f"多出的 {delta:.2f} 元可以申请调整。"
                )
        else:
            answer = f"运单 {analysis['waybill_no']} 的费用核对完成：{analysis['conclusion']}"
        basis = [
            f"规则 {analysis['waiting_rule']['rule_id']}，本体版本 {analysis['ontology_version']}",
            (
                f"等候 {analysis['waiting_minutes']:.0f} 分钟，免费 "
                f"{analysis['free_wait_minutes']:.0f} 分钟，计费块数 "
                f"{analysis['waiting_rule']['blocks']}"
            ),
            f"实际金额来自费用项，规则应计 {analysis['waiting_rule']['expected']:.2f} 元",
            "证据 " + "、".join(
                f"{ev['evidence_id']}({ev['checksum']})" for ev in analysis["evidence"]
            ),
        ]
        return {
            "waybill_id": waybill_id,
            "waybill_no": analysis["waybill_no"],
            "audience": audience,
            "answer": answer,
            "basis": basis,
            "ontology_version": analysis["ontology_version"],
            "traceability": analysis["traceability"],
        }

    # ------------------------------------------------------------------ 动作链 / 回读
    def action_plan(self, waybill_id: str) -> dict:
        """在第 5 步给出动作提案，在第 6 步回读动作前后的业务事实。"""
        analysis = self.fee_analysis(waybill_id)
        wait = next((
            item for item in analysis["breakdown"]
            if item["fee_type"] == "waiting"
            and item["rule_delta"] is not None
            and item["rule_delta"] > 0.01
        ), None)
        wait_fee_id = f'fee:{wait["fee_id"]}' if wait else None
        subject_id = wait_fee_id or waybill_id
        impacted = self.impact(subject_id)["affected"]
        downstream = [
            item for item in impacted
            if item["type"] in ("Settlement", "Invoice", "Shipper")
        ]
        settlement_id = next(
            (item["object_id"] for item in downstream if item["type"] == "Settlement"),
            analysis["settlement_ids"][0] if analysis["settlement_ids"] else None,
        )
        invoice_id = next(
            (item["object_id"] for item in downstream if item["type"] == "Invoice"), None
        )
        shipper_id = next(
            (item["object_id"] for item in downstream if item["type"] == "Shipper"), None
        )
        exception_id = analysis["exception_ids"][0] if analysis["exception_ids"] else None
        order_rel = self.facts.one(
            "SELECT subject_id FROM relations WHERE predicate='order_generates_waybill' "
            "AND object_id=? AND valid_to IS NULL", (waybill_id,)
        )
        order_id = order_rel["subject_id"] if order_rel else None
        exception_row = self.facts.get_row(exception_id) if exception_id else None
        prerequisites = [{
            "action_id": "dispute_charge",
            "label": "发起费用申诉",
            "target": exception_id,
            "status": (
                f'已发起 · 工单 {exception_row["status"]}' if exception_row else "未发起"
            ),
            "done": bool(exception_row),
        }]

        waive_amount = round(
            min(wait["rule_delta"], wait["actual_amount"]) if wait else 0.0, 2
        )
        targets: list[tuple[str, str, dict]] = []
        auto_actions: list[dict] = []
        if wait_fee_id and waive_amount > 0:
            targets.append((wait_fee_id, "waive_charge", {"amount": waive_amount}))
            if settlement_id:
                targets.append((settlement_id, "reverse_entry", {"amount": waive_amount}))
            inv_rel = self.facts.one(
                "SELECT object_id FROM relations "
                "WHERE predicate='settlement_generates_invoice' AND subject_id=? "
                "AND object_id=? AND valid_to IS NULL",
                (settlement_id or "", invoice_id or "")
            )
            if settlement_id and invoice_id and inv_rel:
                auto_actions.append({
                    "action_id": "void_invoice",
                    "label": "发票红冲",
                    "target": invoice_id,
                    "target_type": "Invoice",
                    "reason": "冲正结算时同步红冲，无需单独执行",
                    "triggered_by": "reverse_entry",
                })
        actions = []
        for target_id, action_id, payload in targets:
            row = self.facts.get_row(target_id)
            action = self.sem.actions.get(action_id)
            if not row or not action or action["target"] != row["type"]:
                continue
            view = self.get_object(target_id)
            available = {item["id"]: item for item in view["available_actions"]}
            blocked = {item["id"]: item for item in view["blocked_actions"]}
            actions.append({
                "action_id": action_id,
                "label": action["label"],
                "target": target_id,
                "target_type": row["type"],
                "target_status": row["status"],
                "roles": action["roles"],
                "role": action["roles"][0] if action["roles"] else "",
                "payload": payload,
                "allowed": action_id in available,
                "blocked_reason": blocked.get(action_id, {}).get("blocked_reason"),
            })

        readback = []
        for object_id in [wait_fee_id, settlement_id, invoice_id, exception_id, shipper_id]:
            if not object_id:
                continue
            row = self.facts.get_row(object_id)
            if not row:
                continue
            obj = Store.decode(row)
            props = obj["props"]
            if row["type"] == "FeeItem":
                value = (
                    f'实收 {float(props.get("actual_amount") or 0):.2f} · '
                    f'已减免 {float(props.get("waived_amount") or 0):.2f}'
                )
            elif row["type"] == "Settlement":
                value = (
                    f'金额 {float(props.get("amount") or 0):.2f} · '
                    f'已冲正 {float(props.get("reversed_amount") or 0):.2f}'
                )
            elif row["type"] == "Invoice":
                value = f'金额 {float(props.get("amount") or 0):.2f}'
            else:
                value = "—"
            readback.append({
                "object_id": object_id,
                "type": row["type"],
                "type_label": (self.sem.classes.get(row["type"]) or {}).get(
                    "label", row["type"]
                ),
                "name": row["name"],
                "status": row["status"],
                "value": value,
            })

        return {
            "order_id": order_id,
            "waybill_id": waybill_id,
            "subject_id": subject_id,
            "wait_fee_id": wait_fee_id,
            "exception_id": exception_id,
            "settlement_id": settlement_id,
            "invoice_id": invoice_id,
            "shipper_id": shipper_id,
            "wait_expected": wait["expected_amount"] if wait else 0.0,
            "wait_actual": wait["actual_amount"] if wait else 0.0,
            "wait_delta": wait["rule_delta"] if wait else 0.0,
            "waive_amount": waive_amount,
            "prerequisites": prerequisites,
            "actions": actions,
            "auto_actions": auto_actions,
            "downstream": downstream,
            "readback": readback,
        }

    # ------------------------------------------------------------------ 诊断推理链
    def reasoning_chain(self, waybill_id: str, max_steps: int = 6,
                        question: str = "") -> dict:
        """交互式诊断只走真实 Agent，不提供固定链路或降级替身。"""
        return self.agent_reasoning(waybill_id, max_steps, question=question)

    def agent_reasoning(self, waybill_id: str, max_steps: int = 6,
                        question: str = "") -> dict:
        """业务问题 → 本体圈定类型 → MCP 工具调用 → 观察。"""
        from .agent import ReasoningAgent

        waybill_id = (
            waybill_id if waybill_id.startswith("waybill:")
            else f"waybill:{waybill_id}"
        )
        self.get_object(waybill_id)
        question = (question or "").strip()
        return ReasoningAgent(
            self, waybill_id, max_steps, question=question
        ).run()

    def agent_chain_start(self, waybill_id: str, max_steps: int = 6,
                          question: str = "") -> dict:
        """只创建交互式诊断会话，不调用大模型也不查询业务事实。"""
        from .agent import ReasoningAgent

        waybill_id = (
            waybill_id if waybill_id.startswith("waybill:")
            else f"waybill:{waybill_id}"
        )
        self.get_object(waybill_id)
        question = (question or "").strip()
        agent = ReasoningAgent(self, waybill_id, max_steps, question=question)
        result = agent.start()
        session_id = uuid.uuid4().hex
        _AGENT_SESSIONS[session_id] = (time.time(), agent)
        self._prune_agent_sessions()
        result["session_id"] = session_id
        return result

    def agent_chain_next(self, session_id: str) -> dict:
        """只执行一个动作：一次大模型调用、一次本体查询或一次事实查询。"""
        item = _AGENT_SESSIONS.get(session_id)
        if not item or time.time() - item[0] >= _AGENT_SESSION_TTL:
            _AGENT_SESSIONS.pop(session_id, None)
            raise ValueError("诊断会话不存在或已过期，请重新提问")
        agent = item[1]
        result = agent.next()
        result["session_id"] = session_id
        _AGENT_SESSIONS[session_id] = (time.time(), agent)
        self._prune_agent_sessions()
        return result

    @staticmethod
    def _prune_agent_sessions() -> None:
        now = time.time()
        expired = [
            key for key, (ts, _) in _AGENT_SESSIONS.items()
            if now - ts >= _AGENT_SESSION_TTL
        ]
        for key in expired:
            _AGENT_SESSIONS.pop(key, None)

    def impact(self, object_id: str, max_depth: int = 5) -> dict:
        if not self.facts.get_row(object_id):
            raise BusinessReject(f"对象不存在: {object_id}", "OBJECT_NOT_FOUND")
        affected: list[dict] = []
        visited = {object_id}
        queue = deque([(object_id, 0, None)])
        while queue:
            current, level, via = queue.popleft()
            if level >= max_depth:
                continue
            for rel in self._relations_of(current, "out"):
                if not rel["active"] or rel["predicate"] not in CAUSAL_RELATIONS:
                    continue
                target = rel["object_id"]
                if target in visited:
                    continue
                visited.add(target)
                row = self.facts.get_row(target)
                props = json.loads(row["props"]) if row else {}
                affected.append({
                    "object_id": target, "type": row["type"] if row else None,
                    "type_label": self.sem.classes[row["type"]]["label"] if row else None,
                    "name": row["name"] if row else target,
                    "status": row["status"] if row else None,
                    "amount": props.get("amount"),
                    "depth": level + 1,
                    "via": rel["label"],
                })
                queue.append((target, level + 1, rel["predicate"]))
        return {
            "root": object_id, "affected_count": len(affected),
            "affected": affected,
            "summary": f"若调整 {object_id}，将影响 {len(affected)} 个下游对象。",
        }

    # ------------------------------------------------------------------ 追溯
    def state_history(self, object_id: str) -> dict:
        """对象状态轨迹（实现见事实层 facts.state_history，REST/MCP 共用一份）。"""
        return self.facts.state_history(object_id)

    def state_machine(self, type_id: str | None = None,
                      object_id: str | None = None) -> dict:
        """状态机视图（实现见事实层 facts.state_machine）。"""
        return self.facts.state_machine(type_id, object_id)

    def trace(self, object_id: str) -> dict:
        detail = self.get_object(object_id)
        events = self.facts.all(
            "SELECT * FROM events WHERE object_id=? ORDER BY occurred_at, id", (object_id,))
        for e in events:
            e["payload"] = json.loads(e["payload"])
        ev_rows = self.facts.all(
            "SELECT * FROM evidence WHERE object_id=? ORDER BY occurred_at", (object_id,))
        for e in ev_rows:
            e["payload"] = json.loads(e["payload"])
        audit_rows = self.facts.all(
            "SELECT * FROM audit WHERE object_id=? ORDER BY occurred_at, id", (object_id,))
        for a in audit_rows:
            a["request"] = json.loads(a["request"])
            a["result"] = json.loads(a["result"])
        timeline = []
        for e in events:
            timeline.append({"at": e["occurred_at"], "kind": "event",
                             "title": e["event_type"], "actor": e["actor"],
                             "detail": e["payload"]})
        for e in ev_rows:
            timeline.append({"at": e["occurred_at"], "kind": "evidence",
                             "title": e["summary"], "actor": e["source_system"],
                             "detail": e["payload"]})
        for a in audit_rows:
            timeline.append({"at": a["occurred_at"], "kind": "action",
                             "title": a["action_id"], "actor": f"{a['actor']} / {a['role']}",
                             "detail": a["result"]})
        timeline.sort(key=lambda x: x["at"])
        return {
            "object_id": object_id,
            "object": detail,
            "timeline": timeline,
            "timeline_count": len(timeline),
            "relations": detail["relations_out"] + detail["relations_in"],
        }

    # ------------------------------------------------------------------ 动作
    def _action_view(self, action: dict, row: dict, props: dict) -> dict:
        blockers = []
        if action.get("from_status") and row["status"] not in action["from_status"]:
            blockers.append({
                "code": "INVALID_STATE",
                "message": f"当前状态 {row['status']} 不允许执行「{action['label']}」，"
                           f"允许状态: {', '.join(action['from_status'])}",
            })
        for rule in self.sem.rules_for(row["type"], props):
            if action["id"] in rule.get("judge", {}).get("forbidden_actions", []):
                blockers.append({
                    "code": "RULE_BLOCKED", "rule_id": rule["id"],
                    "message": rule["judge"]["explain"],
                })
        if action["id"] == "reassign_waybill" and props.get("loading_confirmed_at"):
            blockers.append({
                "code": "RULE_BLOCKED", "rule_id": "R-WAYBILL-01",
                "message": "装货完成后改派会破坏履约连续性，必须走异常工单流程。",
            })
        if action["id"] == "hold_settlement" and row["status"] == "POSTED":
            blockers.append({
                "code": "RULE_BLOCKED", "rule_id": "R-SETTLE-01",
                "message": "结算单已入账，不能挂起；只能执行冲正。",
            })
        return {
            "id": action["id"], "label": action["label"],
            "description": action["description"], "roles": action["roles"],
            "from_status": action.get("from_status", []), "to_status": action.get("to_status"),
            "params": action.get("params", []),
            "allowed": not blockers,
            "blocked_reason": blockers[0]["message"] if blockers else None,
            "blockers": blockers,
        }

    def execute_action(self, action_id: str, object_id: str, actor: str, role: str,
                       payload: dict | None = None,
                       idempotency_key: str | None = None) -> dict:
        payload = payload or {}
        if idempotency_key:
            old = self.facts.audit_by_key(idempotency_key)
            if old:
                if old["decision"] == "APPROVED":
                    result = old["result"]
                    result = dict(result) if isinstance(result, dict) else {"result": result}
                    result["replayed"] = True
                    result["audit_id"] = old["id"]
                    return result
                rejected = old["result"] if isinstance(old["result"], dict) else {}
                details = rejected.get("details")
                details = dict(details) if isinstance(details, dict) else {}
                details.update({"audit_id": old["id"], "replayed": True})
                raise BusinessReject(
                    rejected.get("error") or "该幂等键对应的动作已被拒绝",
                    rejected.get("code") or "RULE_REJECTED", details)
        action = self.sem.actions.get(action_id)
        if not action:
            raise BusinessReject(f"未定义的动作: {action_id}", "UNKNOWN_ACTION")
        row = self.facts.get_row(object_id)
        if not row:
            raise BusinessReject(f"对象不存在: {object_id}", "OBJECT_NOT_FOUND")
        if row["type"] != action["target"]:
            raise BusinessReject(
                f"动作 {action_id} 只能作用于 {action['target']}，实际 {row['type']}",
                "ONTOLOGY_VIOLATION")
        at = now_iso()
        request = {"object_id": object_id, **payload}
        try:
            if role not in action["roles"]:
                raise BusinessReject(
                    f"角色 {role} 无权执行「{action['label']}」，允许角色: {', '.join(action['roles'])}",
                    "FORBIDDEN_ROLE")
            props = json.loads(row["props"])
            view = self._action_view(action, row, props)
            if not view["allowed"]:
                raise BusinessReject(
                    view["blocked_reason"] or "动作前置条件不满足", "RULE_REJECTED",
                    {"blockers": view["blockers"]})
            params = action.get("params", [])
            declared = {p["name"] for p in params}
            unknown = sorted(set(payload) - declared)
            if unknown:
                # 未声明参数一律拒收：动作参数是本体声明的契约，多塞一个键
                # 意味着调用方以为它生效了——那种"静默接受"比报错难查得多。
                raise BusinessReject(
                    f"未声明的参数: {', '.join(unknown)}；「{action['label']}」只接受 "
                    f"{', '.join(sorted(declared)) or '（无参数）'}",
                    "PARAM_UNKNOWN")
            for param in params:
                value = payload.get(param["name"])
                if param.get("required") and value in (None, ""):
                    raise BusinessReject(
                        f"缺少必填参数: {param['label']} ({param['name']})",
                        "VALIDATION_ERROR")
                if value in (None, ""):
                    continue
                # 类型口径与函数参数一致（同一个 SemanticLayer._type_ok），
                # 动作与函数不能各判各的。
                ptype = param.get("type", "string")
                if not self.sem._type_ok(ptype, value):
                    raise BusinessReject(
                        f"参数 {param['label']} ({param['name']}) 类型不符："
                        f"声明 {ptype}，实际 {type(value).__name__}（{value!r:.40}）",
                        "VALIDATION_ERROR")

            result = self._apply_action(action, row, props, actor, role, payload, at)
            evidence = self._record_action_evidence(
                action, row, actor, role, payload, at, result)
            if evidence:
                result["evidence"] = evidence
                result["evidence_id"] = evidence["evidence_id"]
            audit_id = self.facts.add_audit(
                at, actor, role, action_id, object_id, "APPROVED", idempotency_key,
                request, result)
            self.conn.commit()
            result["audit_id"] = audit_id
            return result
        except BusinessReject as exc:
            self.conn.rollback()
            audit_id = self.facts.add_audit(
                at, actor, role, action_id, object_id, "REJECTED", idempotency_key,
                request,
                {"error": str(exc), "code": exc.code, "details": exc.details})
            self.conn.commit()
            exc.details = {**(exc.details or {}), "audit_id": audit_id}
            raise

    def _apply_action(self, action: dict, row: dict, props: dict,
                      actor: str, role: str, payload: dict, at: str) -> dict:
        oid = row["object_id"]
        action_id = action["id"]
        props = dict(props)
        new_status = action.get("to_status") or row["status"]
        result: dict = {
            "ok": True, "action": action_id, "label": action["label"],
            "object_id": oid, "actor": actor, "role": role,
            "before_status": row["status"], "after_status": new_status, "at": at,
        }

        if action_id == "accept_order":
            props["accepted_at"] = at
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "ACCEPTED", at, actor, {"role": role})
            result["message"] = "接单成功，运单进入已接单状态。"

        elif action_id == "reassign_waybill":
            new_driver = payload.get("new_driver_id")
            driver = self.facts.get_row(f"driver:{new_driver}")
            if not driver or driver["type"] != "Driver":
                raise BusinessReject(f"新司机不存在: {new_driver}", "VALIDATION_ERROR")
            if props.get("loading_confirmed_at"):
                raise BusinessReject("装货完成后不能改派。", "RULE_REJECTED")
            self.facts.close_relation(oid, "waybill_assigned_to_driver", at)
            self.add_relation("waybill_assigned_to_driver", oid, f"driver:{new_driver}", at)
            props["driver_id"] = new_driver
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "REASSIGNED", at, actor,
                                 {"new_driver_id": new_driver, "role": role})
            result.update({"message": f"改派成功，新司机 {new_driver}。",
                           "new_driver_id": new_driver})

        elif action_id == "confirm_loading":
            props["loading_confirmed_at"] = at
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "LOADED", at, actor, {"role": role})
            result["message"] = "装货确认成功，运单进入已装货状态。"

        elif action_id == "complete":
            props["proof_of_delivery"] = payload["proof_of_delivery"]
            props["delivered_at"] = at
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "DELIVERED", at, actor,
                                 {"proof_of_delivery": payload["proof_of_delivery"]})
            result.update({"message": "送达完成，回单已记录。",
                           "proof_of_delivery": payload["proof_of_delivery"]})

        elif action_id == "dispute_charge":
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            ticket_id = self._ensure_exception(oid, payload.get("reason", ""), actor, at)
            self.facts.add_event(oid, "DISPUTED", at, actor,
                                 {"reason": payload.get("reason", ""), "ticket_id": ticket_id})
            result.update({"message": f"费用申诉已受理，异常工单 {ticket_id}。",
                           "ticket_id": ticket_id})

        elif action_id == "waive_charge":
            amount = float(payload.get("amount") or 0)
            actual = float(props.get("actual_amount") or 0)
            if amount <= 0:
                raise BusinessReject("减免金额必须大于 0。", "VALIDATION_ERROR")
            if amount > actual:
                raise BusinessReject(
                    f"减免金额 {amount:.2f} 不能超过实际金额 {actual:.2f}。",
                    "RULE_REJECTED")
            props["waived_amount"] = round(float(props.get("waived_amount") or 0) + amount, 2)
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "WAIVED", at, actor,
                                 {"amount": amount, "fee_id": props.get("fee_id")})
            result.update({
                "message": f"已审批减免 {amount:.2f} 元；结算单已入账时需继续执行冲正。",
                "amount": amount,
                "next_actions": ["reverse_entry"],
            })

        elif action_id == "hold_settlement":
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "ON_HOLD", at, actor,
                                 {"reason": payload.get("reason", "")})
            result["message"] = "结算单已挂起。"

        elif action_id == "reverse_entry":
            if row["status"] != "POSTED":
                raise BusinessReject("只有已入账结算单可以冲正。", "RULE_REJECTED")
            fee_ids = [r["subject_id"] for r in self.facts.all(
                "SELECT subject_id FROM relations WHERE predicate='fee_included_in_settlement' "
                "AND object_id=? AND valid_to IS NULL", (oid,))]
            waived = 0.0
            for fid in fee_ids:
                fr = self.facts.get_row(fid)
                if fr:
                    fp = json.loads(fr["props"])
                    if fr["status"] == "WAIVED":
                        waived += float(fp.get("waived_amount") or 0)
            amount = payload.get("amount")
            amount = float(amount) if amount not in (None, "") else round(waived, 2)
            if amount <= 0:
                raise BusinessReject("没有可冲正金额；请先完成费用减免。", "RULE_REJECTED")
            old_amount = float(props.get("amount") or 0)
            if amount > old_amount:
                raise BusinessReject(
                    f"冲正金额 {amount:.2f} 不能超过结算金额 {old_amount:.2f}。",
                    "RULE_REJECTED")
            props["amount"] = round(old_amount - amount, 2)
            props["reversed_amount"] = round(
                float(props.get("reversed_amount") or 0) + amount, 2)
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            invoice_ids = [r["object_id"] for r in self.facts.all(
                "SELECT object_id FROM relations WHERE predicate='settlement_generates_invoice' "
                "AND subject_id=? AND valid_to IS NULL", (oid,))]
            for inv_id in invoice_ids:
                inv = self.facts.get_row(inv_id)
                if inv:
                    ip = json.loads(inv["props"])
                    ip["amount"] = props["amount"]
                    ip["status"] = "VOIDED"
                    self.facts.update_object(inv_id, ip, None, "VOIDED", at)
                    self.facts.add_event(inv_id, "VOIDED", at, actor,
                                         {"reason": "结算冲正", "amount": props["amount"]})
            self.facts.add_event(oid, "REVERSED", at, actor,
                                 {"amount": amount, "new_amount": props["amount"]})
            result.update({
                "message": f"冲正 {amount:.2f} 元，结算金额调整为 {props['amount']:.2f}；"
                           "关联发票已同步红冲。",
                "reversed_amount": amount, "new_amount": props["amount"],
                "invoice_ids": invoice_ids,
            })

        elif action_id == "void_invoice":
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "VOIDED", at, actor,
                                 {"reason": payload.get("reason", "")})
            result["message"] = "发票已红冲。"

        elif action_id == "close_exception":
            props["resolution"] = payload.get("resolution", "")
            props["closed_at"] = at
            props["status"] = new_status
            self.facts.update_object(oid, props, None, new_status, at)
            self.facts.add_event(oid, "CLOSED", at, actor,
                                 {"resolution": props["resolution"]})
            result["message"] = "异常工单已关闭，处理结论已留痕。"

        else:
            raise BusinessReject(f"动作 {action_id} 尚无运行时实现。", "NOT_IMPLEMENTED")

        return result

    def _record_action_evidence(self, action: dict, row: dict, actor: str, role: str,
                                payload: dict, at: str, result: dict) -> dict | None:
        """动作成功后生成可核对证据，而不是只在 schema 里写“生成证据”。

        证据是动作执行时写入的 ABox 记录：Evidence 顶点 + 目标对象到证据的关系。
        没有证据关系的目标类型返回 None，不做假数据。
        """
        relation = self.sem.evidence_relation_for(row["type"])
        if not relation:
            return None
        evidence_id = f"evidence:ACT-{uuid.uuid4().hex[:12]}"
        evidence_type = f"ACTION_{action['id'].upper()}"
        summary = (
            f"{action['label']}：{row['object_id']} "
            f"{result['before_status']} → {result['after_status']}"
        )
        body = {
            "action_id": action["id"],
            "action_label": action["label"],
            "object_id": row["object_id"],
            "object_type": row["type"],
            "actor": actor,
            "role": role,
            "before_status": result["before_status"],
            "after_status": result["after_status"],
            "occurred_at": at,
            "request": payload,
        }
        self.add_evidence(
            evidence_id, row["object_id"], evidence_type, "ONTOLOGY_ACTION",
            at, "v1", summary, body, relation,
        )
        return {
            "evidence_id": evidence_id,
            "evidence_type": evidence_type,
            "source_system": "ONTOLOGY_ACTION",
            "occurred_at": at,
            "summary": summary,
            "relation": relation,
        }

    def _ensure_exception(self, fee_id: str, reason: str, actor: str, at: str) -> str:
        fee = self.facts.get_row(fee_id)
        wb_id = None
        for rel in self.facts.all(
                "SELECT subject_id FROM relations WHERE predicate='waybill_produces_fee' "
                "AND object_id=? AND valid_to IS NULL", (fee_id,)):
            wb_id = rel["subject_id"]
        if not wb_id:
            return ""
        existing = self.facts.one(
            "SELECT object_id FROM relations WHERE predicate='waybill_has_exception' "
            "AND subject_id=? AND valid_to IS NULL", (wb_id,))
        if existing:
            ticket_id = existing["object_id"]
            ticket = self.facts.get_row(ticket_id)
            if ticket and ticket["status"] == "CLOSED":
                tp = json.loads(ticket["props"])
                tp["status"] = "OPEN"
                tp["reason"] = reason or tp.get("reason")
                tp["opened_at"] = at
                tp["closed_at"] = None
                self.facts.update_object(ticket_id, tp, None, "OPEN", at)
            return ticket_id
        ticket_id = "exception:EX" + datetime.now().strftime("%Y%m%d%H%M%S")
        ticket_no = ticket_id.split(":", 1)[1]
        self.add_object("ExceptionTicket", ticket_id, "费用异常工单", "OPEN", {
            "ticket_id": ticket_no, "ticket_type": "FEE_DISPUTE",
            "reason": reason or "费用申诉", "assignee": "财务共享中心",
            "resolution": "", "opened_at": at, "closed_at": None,
        }, at)
        self.add_relation("waybill_has_exception", wb_id, ticket_id, at)
        self.facts.add_event(ticket_id, "OPENED", at, actor, {"fee_id": fee_id})
        return ticket_id

    def audit(self, object_id: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM audit"
        args: list = []
        if object_id:
            sql += " WHERE object_id=?"
            args.append(object_id)
        sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        args.append(max(1, min(limit, 500)))
        rows = self.facts.all(sql, tuple(args))
        for r in rows:
            r["request"] = json.loads(r["request"])
            r["result"] = json.loads(r["result"])
        return rows