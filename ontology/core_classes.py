"""核心对象类型（原 18 个手写类）：补齐属性类型标注，属性名与状态机保持一字不变。

这些类此前只有裸属性名，本文件把它们升级为带类型的显式声明，
从而全部属性都能进入 property_types 注册表（Palantir Property Type 一等公民）。
"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

_classes = [
    C("Shipper", "货主", "shipper_id",
      "货主主数据与结算主体。一单货的费用最终由它承担，是结算账期的挂靠对象。",
      "shipper_id:id, shipper_name:str, settlement_subject:str, contact:str, status:enum",
      "ACTIVE,DISABLED",
      aliases=("客户", "发货方"), group="order_transport"),
    C("Order", "订单", "order_id",
      "运输需求单。客户下单产生，是计费口径与时效承诺的源头，确认后才允许生成运单。",
      "order_id:id, shipper_id:str, origin:str, destination:str, cargo:str, "
      "vehicle_type:str, estimated_fee:dec, created_at:dt, status:enum",
      "CREATED,DISPATCHED,CANCELLED,COMPLETED",
      aliases=("运输订单", "需求单"), group="order_transport"),
    C("Waybill", "运单", "waybill_id",
      "履约主体，整条链路唯一的业务主键，费用、结算、异常最终都挂到它身上。",
      "waybill_id:id, order_id:str, driver_id:str, vehicle_id:str, status:enum, "
      "estimated_fee:dec, actual_fee:dec, variance:dec, waiting_minutes:num, "
      "free_wait_minutes:num, accepted_at:dt, arrival_at:dt, loading_started_at:dt, "
      "loading_ended_at:dt, loading_confirmed_at:dt, unloading_started_at:dt, "
      "unloading_ended_at:dt, delivered_at:dt, actual_distance_km:dec, "
      "actual_duration_min:int, proof_of_delivery:str",
      "CREATED,ACCEPTED,REASSIGNED,LOADED,DELIVERED",
      aliases=("运输单", "wb"), group="order_transport"),
    C("Driver", "司机", "driver_id",
      "实际承运的个体。资质有效期与在线状态决定其能否被派单，姓名与联系方式属敏感信息。",
      "driver_id:id, driver_name:str, fleet:str, qualification:str, status:enum",
      "IDLE,ASSIGNED,ON_TRIP,OFFLINE",
      aliases=("驾驶员", "师傅"), group="capacity"),
    C("Vehicle", "车辆", "vehicle_id",
      "承运车辆。车型与核定载重决定可承运的货物范围，车牌属敏感信息按角色脱敏。",
      "vehicle_id:id, plate:str, vehicle_type:str, capacity_t:dec, fleet:str, status:enum",
      "IDLE,IN_USE,MAINTENANCE",
      aliases=("车", "车牌"), group="capacity"),
    C("FeeItem", "费用项", "fee_id",
      "运单上的单项费用。每条费用都要能说清口径、依据的计价规则版本与发生时间，是费用争议的最小单元。",
      "fee_id:id, fee_type:str, fee_label:str, quoted_amount:dec, expected_amount:dec, "
      "actual_amount:dec, waived_amount:dec, calc_basis:str, pricing_rule_id:str, "
      "pricing_rule_version:str, occurred_at:dt, source_system:str, status:enum",
      "NORMAL,DISPUTED,WAIVED",
      aliases=("费用", "收费项"), group="order_transport"),
    C("Settlement", "结算单", "settlement_id",
      "按账期归集费用形成的结算单据。一个结算单覆盖多张运单多笔费用，是资金流与业务流的交汇点。",
      "settlement_id:id, period:str, amount:dec, reversed_amount:dec, status:enum, "
      "posted_at:dt",
      "DRAFT,PENDING,POSTED,ON_HOLD,REVERSED",
      aliases=("对账单", "结算"), group="settlement"),
    C("Invoice", "发票", "invoice_id",
      "结算完成后开具的发票。开票主体、税号与金额必须与结算单一致，抬头错误需作废重开。",
      "invoice_id:id, invoice_no:str, title:str, amount:dec, tax_rate:dec, status:enum, "
      "issued_at:dt",
      "DRAFT,ISSUED,VOIDED",
      aliases=("票据", "开票"), group="settlement"),
    C("ExceptionTicket", "异常工单", "ticket_id",
      "需要人跟进的异常处理单据。从异常识别到责任落定再到赔付关闭，全过程挂在工单上。",
      "ticket_id:id, ticket_type:enum, reason:txt, assignee:str, status:enum, "
      "resolution:txt, opened_at:dt, closed_at:dt",
      "OPEN,PROCESSING,CLOSED",
      aliases=("工单", "异常单"), group="evidence"),
    C("Evidence", "证据", "evidence_id",
      "支撑一次判断的事实凭证。照片、轨迹、过磅单、录音都能作为证据，带校验和以防篡改。",
      "evidence_id:id, evidence_type:str, source_system:str, occurred_at:dt, "
      "version:str, summary:str, checksum:str, status:enum",
      "VALID,VOID",
      aliases=("凭证", "举证"), group="evidence"),
    C("Quote", "报价单", "quote_id",
      "一次报价的完整结果。同一订单可能有多个版本报价，只有 ACTIVE 版本才能转成订单。",
      "quote_id:id, order_id:str, version:str, quoted_at:dt, valid_until:dt, "
      "total_amount:dec, currency:str, pricing_rule_version:str, status:enum",
      "DRAFT,ACTIVE,SUPERSEDED,EXPIRED,VOID",
      aliases=("报价", "价格单"), group="pricing"),
    C("QuoteItem", "报价行", "quote_item_id",
      "报价单中的单项费用明细。每行对应一个费用类型与其计算依据，行金额之和等于报价总额。",
      "quote_item_id:id, quote_id:str, fee_type:str, fee_label:str, amount:dec, "
      "quantity:dec, unit:str, calc_rule_id:str, pricing_rule_version:str, status:enum",
      "NORMAL,VOID",
      aliases=("报价明细", "行"), group="pricing"),
    C("PricingRule", "计价规则", "rule_id",
      "按城市、车型、费用类型确定的计价参数集。规则带版本与有效期，历史费用必须能追溯当时生效的版本。",
      "rule_id:id, fee_type:str, city:str, vehicle_type:str, effective_from:date, "
      "effective_to:date, version:str, currency:str, params:json, source_system:str, "
      "status:enum",
      "DRAFT,ACTIVE,EXPIRED",
      aliases=("规则", "价目"), group="pricing"),
    C("Track", "轨迹", "track_id",
      "车辆在运单期间的位置点集合。起止时间、里程与等候时长从轨迹算出，是里程计费的原始依据。",
      "track_id:id, waybill_id:str, source_system:str, started_at:dt, ended_at:dt, "
      "distance_km:dec, duration_min:int, waiting_minutes:num, point_count:int, "
      "checksum:str, status:enum",
      "VALID,VOID",
      aliases=("轨迹", "定位"), group="evidence"),
    C("Cargo", "货物", "cargo_id",
      "一单货申报的货物信息。重量、体积与件数决定车型选择与装载方案，也是超载判定的依据。",
      "cargo_id:id, name:str, weight_kg:dec, volume_m3:dec, package_count:int, "
      "package_type:enum, status:enum",
      "NORMAL,DAMAGED",
      aliases=("货物", "货"), group="order_transport"),
    C("Carrier", "承运商", "carrier_id",
      "承接运输的外部承运单位。资质、结算主体与承运类型决定其可承接的线路与结算方式。",
      "carrier_id:id, carrier_name:str, carrier_type:str, settlement_subject:str, "
      "qualification:str, contact:str, status:enum",
      "ACTIVE,SUSPENDED,DISABLED",
      aliases=("外协", "供应商"), group="capacity"),
    C("SubcontractOrder", "分包单", "subcontract_id",
      "把运单的一段分包给外部承运商的协议记录。分包金额与运单收入之差即是该段毛利。",
      "subcontract_id:id, waybill_id:str, carrier_id:str, subcontract_mode:str, "
      "segment_start:str, segment_end:str, allocated_amount:dec, waybill_revenue:dec, "
      "min_margin_rate:dec, currency:str, confirmed_at:dt, status:enum",
      "DRAFT,CONFIRMED,COMPLETED,SETTLED,VOID",
      aliases=("外协单", "转包"), group="capacity"),
    C("Stop", "装卸点", "stop_id",
      "运单上的一个装卸停留点。一个运单可有多个停留点，每点的计划与实际时点决定等候费与时效达成。",
      "stop_id:id, waybill_id:str, sequence:int, stop_type:str, address:str, "
      "contact:str, planned_at:dt, arrived_at:dt, departed_at:dt, "
      "waiting_minutes:num, cargo_quantity:dec, status:enum",
      "PLANNED,ARRIVED,COMPLETED,EXCEPTION",
      aliases=("停留点", "站点"), group="order_transport"),
]

DOMAINS.append({
    "id": "order_transport", "label": "订单与运输", "classes": _classes,
    "keep_existing_group": True,
})

CLASSES = _classes
LINKS: list[dict] = []
