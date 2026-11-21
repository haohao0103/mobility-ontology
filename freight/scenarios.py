"""核心业务剧本：把运单-费用链**真实走一遍**。

为什么要有剧本
--------------
本体"能表达"不等于本体"是真的"。仓库里有 500 个对象类型、1961 个属性，
但如果大部分类型没有任何对象实例、动作从没被执行过，我们证明的只是**形式合法**，
不是**业务为真**。

本模块提供一条**可执行的业务故事**：建对象 → 接关系 → 走动作 →
留下状态迁移、事件与审计。它是门禁第 26 项（ABox 深度校验）的数据来源，
也是"运单-费用这条链到底跑不跑得起来"的活体验证。

剧本边界（诚实声明）
--------------------
- 这是**剧本数据**，不是生产实况。它证明"每个类型有实例、被关系引用、被动作执行过"，
  **不能**证明"货拉拉真有这些对象"。任何对外报告都必须标注数据来源。
- 剧本只覆盖**深水区**（声明了 data_sources / 迁移动作 / 状态机的类型），
  不追求全量 500 类的覆盖——那会诱导造垃圾数据。

剧本内容：一单多段运输
- 船东下单 → 运单（2 个站点）→ 分包给承运商 → 报价（2 版）→ 费用 → 争议 → 减免
  → 结算（挂起 / 冲正）→ 发票红冲 → 异常工单关闭
- 15 个迁移动作**每个至少执行一次**，全部留下 APPROVED 审计与状态迁移。
"""
from __future__ import annotations

T0 = "2026-11-21T09:00:00+08:00"
T1 = "2026-11-21T10:00:00+08:00"
T2 = "2026-11-21T11:00:00+08:00"

ACTOR = "scenario"


def _wb(wid: str, order_id: str, driver_id: str, vehicle_id: str,
        fee: float = 620.0) -> dict:
    return {
        "waybill_id": wid, "order_id": order_id, "driver_id": driver_id,
        "vehicle_id": vehicle_id, "estimated_fee": fee, "actual_fee": fee,
        "variance": 0.0, "waiting_minutes": 12, "free_wait_minutes": 30,
    }


def build_objects(system) -> list[str]:
    """补齐深水区里 seed 未覆盖的类型，并建一条完整的业务链路。"""
    created: list[str] = []

    def add(type_id: str, object_id: str, name: str, status: str,
            props: dict, at: str = T0) -> None:
        system.add_object(type_id, object_id, name, status, props, at)
        created.append(object_id)

    # --- 承运商（分包承接方）：seed 里完全没有实例
    add("Carrier", "carrier:SCN01", "华运物流（分包承运）", "ACTIVE", {
        "carrier_id": "SCN01", "carrier_name": "华运物流（分包承运）",
        "carrier_type": "linehaul", "settlement_subject": "月结账户",
        "qualification": "道路运输资质A", "contact": "周经理 139****8801",
    })

    # --- 站点（提货 / 卸货）：运单的装卸节点
    add("Stop", "stop:SCN01", "提货站（安亭物流园）", "PLANNED", {
        "stop_id": "SCN01", "waybill_id": "SCN01", "sequence": 1,
        "stop_type": "PICKUP", "address": "上海市嘉定区安亭物流园",
        "contact": "仓库 王女士", "planned_at": T0, "cargo_quantity": 18,
    })
    add("Stop", "stop:SCN02", "卸货站（张江仓）", "PLANNED", {
        "stop_id": "SCN02", "waybill_id": "SCN01", "sequence": 2,
        "stop_type": "DELIVERY", "address": "上海市浦东新区张江仓",
        "contact": "收货 李先生", "planned_at": T1, "cargo_quantity": 18,
    })

    # --- 货物（站点装载的货）
    add("Cargo", "cargo:SCN01", "家电配件 18 托（剧本）", "NORMAL", {
        "cargo_id": "SCN01", "name": "家电配件 18 托", "weight_kg": 4200,
        "volume_m3": 26.0, "package_count": 18, "package_type": "PALLET",
    })

    # --- 分包单（运单分包给承运商）
    add("SubcontractOrder", "subcontract:SCN01", "分包单 SCN01（上海→浦东）", "DRAFT", {
        "subcontract_id": "SCN01", "waybill_id": "SCN01", "carrier_id": "SCN01",
        "subcontract_mode": "SEGMENT", "segment_start": "安亭物流园",
        "segment_end": "张江仓", "allocated_amount": 320.0,
        "waybill_revenue": 620.0, "min_margin_rate": 0.08, "currency": "CNY",
    })

    # --- 运单（主链路 + 改派链路）
    add("Waybill", "waybill:SCN01", "运单 SCN01（剧本主链路）", "CREATED",
        _wb("SCN01", "SCN01", "D001", "V001"))
    add("Waybill", "waybill:SCN02", "运单 SCN02（剧本改派链路）", "CREATED",
        _wb("SCN02", "SCN01", "D002", "V002"))

    # --- 报价（两版：正常签发 + 改版；改版指向第三张报价）
    for qid, ver in (("SCN01", "1.0"), ("SCN02", "2.0"), ("SCN03", "3.0")):
        add("Quote", f"quote:{qid}", f"报价 {qid}（剧本）", "DRAFT", {
            "quote_id": qid, "order_id": "SCN01", "version": ver,
            "quoted_at": T0, "valid_until": "2026-12-21T23:59:59+08:00",
            "total_amount": 620.0,
            "currency": "CNY", "pricing_rule_version": "3.0.0",
        })

    # --- 费用（争议 / 减免各一条）
    add("FeeItem", "fee:SCN01", "等候费（剧本）", "NORMAL", {
        "fee_id": "SCN01", "fee_type": "waiting", "fee_label": "等候费",
        "quoted_amount": 60.0, "expected_amount": 60.0, "actual_amount": 60.0,
        "waived_amount": 0.0, "calc_basis": "等待 45 分钟，超免费 30 分钟部分计费",
        "pricing_rule_id": "R-WAIT-01", "pricing_rule_version": "3.0.0",
        "occurred_at": T1, "source_system": "TMS",
    })
    add("FeeItem", "fee:SCN02", "装卸费（剧本）", "NORMAL", {
        "fee_id": "SCN02", "fee_type": "loading", "fee_label": "装卸费",
        "quoted_amount": 120.0, "expected_amount": 120.0, "actual_amount": 120.0,
        "waived_amount": 0.0, "calc_basis": "按装卸次数计费",
        "pricing_rule_id": "R-LOAD-01", "pricing_rule_version": "3.0.0",
        "occurred_at": T1, "source_system": "TMS",
    })

    # --- 结算（挂起 / 冲正各一份）
    add("Settlement", "settlement:SCN01", "结算单 SCN01（挂起）", "DRAFT", {
        "settlement_id": "SCN01", "period": "2026-11", "amount": 620.0,
        "reversed_amount": 0.0,
    })
    add("Settlement", "settlement:SCN02", "结算单 SCN02（冲正）", "POSTED", {
        "settlement_id": "SCN02", "period": "2026-10", "amount": 480.0,
        "reversed_amount": 0.0, "posted_at": T0,
    })

    # --- 发票（红冲）
    add("Invoice", "invoice:SCN01", "发票 SCN01（剧本）", "ISSUED", {
        "invoice_id": "SCN01", "invoice_no": "INV-SCN01", "title": "运费服务费",
        "amount": 620.0, "tax_rate": 0.09, "issued_at": T0,
    })

    # --- 异常工单（关闭）
    add("ExceptionTicket", "ticket:SCN01", "异常工单 SCN01（剧本）", "OPEN", {
        "ticket_id": "SCN01", "ticket_type": "FEE_DISPUTE",
        "reason": "等候时长与装卸记录不一致", "assignee": "客服 张三",
        "opened_at": T1,
    })
    return created


def build_relations(system) -> list[tuple[str, str, str]]:
    """把剧本对象接成业务链路（每条边都对应本体里声明的谓词）。"""
    edges = [
        ("waybill_has_stop", "waybill:SCN01", "stop:SCN01"),
        ("waybill_has_stop", "waybill:SCN01", "stop:SCN02"),
        ("stop_handles_cargo", "stop:SCN01", "cargo:SCN01"),
        ("waybill_has_subcontract", "waybill:SCN01", "subcontract:SCN01"),
        ("subcontract_awarded_to_carrier", "subcontract:SCN01", "carrier:SCN01"),
    ]
    for predicate, subject, obj in edges:
        system.add_relation(predicate, subject, obj, T0)
    return edges


# 15 个迁移动作：每个至少执行一次。
# 顺序有依赖（waive 需要先 dispute、depart 需要先 arrive），不能随意换序。
ACTIONS = [
    # 报价：签发 + 改版
    ("issue_quote", "quote:SCN01", "dispatcher", {}),
    ("issue_quote", "quote:SCN02", "dispatcher", {}),
    ("supersede_quote", "quote:SCN02", "supervisor",
     {"new_quote_id": "SCN03", "reason": "客户追加站点，运费上浮"}),
    # 运单：接单 → 装货确认 → 完成
    ("accept_order", "waybill:SCN01", "driver", {}),
    ("confirm_loading", "waybill:SCN01", "driver", {}),
    ("complete", "waybill:SCN01", "driver", {"proof_of_delivery": "POD-SCN01"}),
    # 运单：接单 → 改派（不能带装货确认，否则被规则阻断）
    ("accept_order", "waybill:SCN02", "dispatcher", {}),
    ("reassign_waybill", "waybill:SCN02", "dispatcher", {"new_driver_id": "D003"}),
    # 站点：到达 → 离开
    ("arrive_stop", "stop:SCN01", "driver", {}),
    ("arrive_stop", "stop:SCN02", "driver", {}),
    ("depart_stop", "stop:SCN02", "driver", {}),
    # 费用：申诉 → 减免
    ("dispute_charge", "fee:SCN01", "shipper", {"reason": "等待时长与门禁记录不一致"}),
    ("dispute_charge", "fee:SCN02", "shipper", {"reason": "装卸次数与作业单不一致"}),
    ("waive_charge", "fee:SCN02", "finance", {"amount": 40.0}),
    # 结算：挂起 / 冲正
    ("hold_settlement", "settlement:SCN01", "finance", {"reason": "费用争议未结"}),
    ("reverse_entry", "settlement:SCN02", "finance", {"amount": 120.0}),
    # 发票：红冲
    ("void_invoice", "invoice:SCN01", "finance", {"reason": "运单费用调整，随之红冲"}),
    # 分包：确认
    ("confirm_subcontract", "subcontract:SCN01", "dispatcher", {}),
    # 异常：关闭
    ("close_exception", "ticket:SCN01", "customer_service",
     {"resolution": "核对门禁记录后确认等候时长有误，费用已减免"}),
]


def run_actions(system) -> list[dict]:
    """真实执行 15 个迁移动作（状态迁移 + 事件 + 审计）。"""
    executed: list[dict] = []
    for action_id, object_id, role, payload in ACTIONS:
        result = system.execute_action(action_id, object_id, ACTOR, role,
                                      dict(payload))
        executed.append({
            "action": action_id,
            "object_id": object_id,
            "role": role,
            "before_status": result.get("before_status"),
            "after_status": result.get("after_status"),
        })
    return executed


def run_core_scenario(system) -> dict:
    """完整核心剧本：建对象 → 接关系 → 走动作。返回摘要。"""
    created = build_objects(system)
    edges = build_relations(system)
    executed = run_actions(system)
    return {
        "objects": created,
        "relations": [f"{p}:{s}->{o}" for p, s, o in edges],
        "actions": executed,
        "action_count": len(executed),
        "object_count": len(created),
        "relation_count": len(edges),
    }
