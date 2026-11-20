"""域组 1：订单链路主干（订单域 / 运单域 / 货物域 / 装卸域）。

覆盖一单货从下单到装卸完成的主链路。所有属性显式标注类型，不靠脚本猜。
"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

# ────────────────────────────── 订单域 ──────────────────────────────
_order_classes = [
    C("OrderLine", "订单行", "order_line_id",
      "订单下的一行货物明细。一个订单可拆多行，每行独立计费与装载，行级差异是后续改单的最小单位。",
      "order_line_id:id, order_id:str, line_no:str, cargo_name:str, cargo_code:str, "
      "quantity:dec, quantity_unit:str, weight_kg:dec, volume_m3:dec, "
      "package_count:int, is_dangerous:bool, temperature_range:str",
      "DRAFT,CONFIRMED,LOADED,DELIVERED,CANCELLED",
      aliases=("明细行", "货物行"), group="order_domain"),
    C("OrderAmendment", "订单修改", "amendment_id",
      "订单确认之后对数量、地址或时间的变更申请。保留改前改后快照，用于对账追溯与责任判定。",
      "amendment_id:id, order_id:str, amendment_no:str, change_type:enum, reason_code:enum, "
      "before_snapshot:json, after_snapshot:json, applicant_id:str, approver_id:str, "
      "applied_at:dt, approved_at:dt, effective_at:dt",
      "DRAFT,SUBMITTED,APPROVING,APPROVED,REJECTED,WITHDRAWN,EFFECTIVE",
      aliases=("改单", "变更单"), group="order_domain"),
    C("OrderCancellation", "订单取消", "cancellation_id",
      "订单取消申请与结论。记录取消时点、责任方与已产生费用，决定是否收取取消费。",
      "cancellation_id:id, order_id:str, reason_code:enum, reason_text:txt, "
      "liable_party:enum, cancel_fee:dec, requested_by:str, requested_at:dt, "
      "confirmed_at:dt, waybill_generated:bool",
      "REQUESTED,APPROVING,CONFIRMED,REJECTED",
      aliases=("撤单", "取消订单"), group="order_domain"),
    C("OrderApproval", "订单审批", "approval_id",
      "超限订单的审批记录。触发条件包括授信超额、危险品、超重超限、异常价格，按规则路由到对应审批人。",
      "approval_id:id, order_id:str, trigger_rule:str, approval_level:int, "
      "approver_role:str, approver_id:str, decision:enum, comment:txt, "
      "submitted_at:dt, decided_at:dt, sla_deadline:dt",
      "PENDING,APPROVED,REJECTED,ESCALATED,TIMEOUT",
      aliases=("订单审核", "特批"), group="order_domain"),
    C("OrderAttachment", "订单附件", "attachment_id",
      "订单关联的外部文件。包括客户合同、装载清单、特殊作业许可，是费用争议时的举证材料。",
      "attachment_id:id, order_id:str, attachment_type:enum, file_name:str, "
      "file_size_kb:int, content_hash:str, uploaded_by:str, uploaded_at:dt, "
      "verified:bool, storage_uri:str",
      "UPLOADED,VERIFIED,REJECTED,ARCHIVED",
      aliases=("随单文件", "附件"), group="order_domain"),
    C("OrderAuditLog", "订单审计日志", "audit_id",
      "订单全生命周期的字段级变更留痕。谁在什么时间把哪个字段从什么改成什么，用于争议复盘。",
      "audit_id:id, order_id:str, action:enum, field_name:str, old_value:txt, "
      "new_value:txt, operator_id:str, operator_role:str, operated_at:dt, "
      "source_channel:str, trace_id:str",
      "RECORDED,ARCHIVED",
      aliases=("订单日志", "操作留痕"), group="order_domain"),
    C("OrderConfirmation", "订单确认", "confirmation_id",
      "客户对报价与承运条件的最终确认。确认后才允许生成运单，是计费口径锁定的时点。",
      "confirmation_id:id, order_id:str, confirm_channel:enum, confirmed_by:str, "
      "confirmed_at:dt, quote_id:str, agreed_fee:dec, validity_until:dt, "
      "signature_uri:str",
      "SENT,CONFIRMED,DECLINED,EXPIRED",
      aliases=("客户确认", "接单确认"), group="order_domain"),
    C("OrderSource", "订单来源", "order_source_id",
      "订单进入系统的渠道来源。区分 APP 自助下单、客服代下单、API 对接与大客户系统直连，影响计费与结算口径。",
      "order_source_id:id, order_id:str, channel_code:str, channel_type:enum, "
      "partner_id:str, campaign_id:str, device_id:str, first_touch_at:dt",
      "CAPTURED,ATTRIBUTED,EXPIRED",
      aliases=("下单渠道", "来源"), group="order_domain"),
    C("OrderPriority", "订单优先级", "priority_id",
      "订单调度优先级。影响派单顺序与抢单推送范围，高优订单允许跨区调度。",
      "priority_id:id, order_id:str, priority_level:enum, priority_score:num, "
      "reason_code:enum, granted_by:str, granted_at:dt, expires_at:dt",
      "ASSIGNED,ACTIVE,EXPIRED,REVOKED",
      aliases=("优先级", "加急"), group="order_domain"),
    C("OrderSLA", "订单时效承诺", "order_sla_id",
      "订单侧对客户承诺的时效条款。约定取货时间窗、送达时间窗与超时赔付比例，是延误判定的依据。",
      "order_sla_id:id, order_id:str, pickup_window_start:dt, pickup_window_end:dt, "
      "delivery_window_start:dt, delivery_window_end:dt, sla_level:enum, "
      "penalty_rate:dec, breach_count:int",
      "DEFINED,ACTIVE,MET,BREACHED,WAIVED",
      aliases=("时效承诺", "SLA"), group="order_domain"),
    C("OrderTag", "订单标签", "order_tag_id",
      "订单上的业务标签。用于分群、看板统计与规则命中判定，如「月结客户」「夜间装卸」「回单必收」。",
      "order_tag_id:id, order_id:str, tag_code:str, tag_value:str, tag_source:enum, "
      "tagged_at:dt, tagged_by:str, auto_generated:bool",
      "ACTIVE,REMOVED",
      aliases=("订单标记", "打标"), group="order_domain"),
    C("OrderNote", "订单备注", "note_id",
      "订单上的自由文本备注。司机端可见的作业提示（如「从后门进」）与内部备注在可见性上分离。",
      "note_id:id, order_id:str, note_type:enum, content:txt, visibility:enum, "
      "author_id:str, created_at:dt, pinned:bool",
      "PUBLISHED,HIDDEN,DELETED",
      aliases=("备注", "留言"), group="order_domain"),
    C("OrderVersion", "订单版本快照", "version_id",
      "订单在关键节点上的不可变快照。用于回答「下单时是怎么约定的」，与当前状态解耦。",
      "version_id:id, order_id:str, version_no:int, snapshot:json, "
      "snapshot_hash:str, trigger_event:enum, created_at:dt, created_by:str",
      "CREATED,RETAINED,PURGED",
      aliases=("订单快照", "版本"), group="order_domain"),
    C("OrderException", "订单异常", "order_exception_id",
      "订单层面的异常事件。与运单异常不同，订单异常发生在履约之前，如地址不详、货物超限、客户失联。",
      "order_exception_id:id, order_id:str, exception_type:enum, severity:enum, "
      "description:txt, detected_by:enum, detected_at:dt, resolved_at:dt, "
      "resolution:txt, blocking:bool",
      "OPEN,INVESTIGATING,RESOLVED,CLOSED,ESCALATED",
      aliases=("订单异常", "下单异常"), group="order_domain"),
    C("OrderSplit", "拆单记录", "split_id",
      "一个订单拆成多个运单的原因与映射。触发原因通常是超载、多点卸货或不同车型需求。",
      "split_id:id, parent_order_id:str, split_reason:enum, child_order_ids:json, "
      "split_at:dt, split_by:str, total_weight_kg:dec",
      "PLANNED,EXECUTED,REVERTED",
      aliases=("拆单", "分单"), group="order_domain"),
    C("OrderMerge", "合单记录", "merge_id",
      "多个订单合并为一个运单装载的记录。用于拼车场景，需保留原始订单与费用的分摊关系。",
      "merge_id:id, merge_group_id:str, order_ids:json, merge_strategy:enum, "
      "merged_at:dt, merged_by:str, fee_allocation:json",
      "PLANNED,EXECUTED,REVERTED",
      aliases=("合单", "拼单"), group="order_domain"),
    C("OrderRecurring", "定期订单计划", "recurring_id",
      "月结客户的周期性运输计划。按规则自动生成订单，如「每周一三五固定线路」。",
      "recurring_id:id, plan_name:str, customer_id:str, cron_expr:str, "
      "template_order:json, start_date:date, end_date:date, next_run_at:dt, "
      "generated_count:int, active:bool",
      "DRAFT,ACTIVE,PAUSED,EXPIRED,TERMINATED",
      aliases=("定期单", "周期订单"), group="order_domain"),
    C("OrderInquiry", "询价单", "inquiry_id",
      "下单前的询价请求。客户给出起终点与货物信息，系统返回参考价；询价转下单的关键转化节点。",
      "inquiry_id:id, customer_id:str, origin_code:str, dest_code:str, "
      "cargo_desc:txt, weight_kg:dec, volume_m3:dec, quoted_fee:dec, "
      "inquiry_at:dt, expires_at:dt, converted_order_id:str",
      "CREATED,QUOTED,CONVERTED,EXPIRED,ABANDONED",
      aliases=("询价", "报价请求"), group="order_domain"),
    C("OrderDraft", "订单草稿", "draft_id",
      "未提交的订单暂存。客服代下单时常见，草稿不参与调度与计费。",
      "draft_id:id, draft_payload:json, owner_id:str, device_id:str, "
      "created_at:dt, last_modified_at:dt, submit_attempts:int",
      "EDITING,SUBMITTED,ABANDONED",
      aliases=("暂存单", "草稿"), group="order_domain"),
    C("OrderContact", "下单联系人", "contact_id",
      "订单上的具体联系人。区分下单人、发货联系人、收货联系人，联系方式属敏感信息按角色脱敏。",
      "contact_id:id, order_id:str, contact_role:enum, contact_name:str, "
      "contact_phone:str, company_name:str, is_primary:bool, notify_channel:enum",
      "ACTIVE,INVALID,BLOCKED",
      aliases=("联系人", "收发货人"), group="order_domain"),
    C("OrderRequirement", "订单特殊要求", "requirement_id",
      "订单上的非标作业要求。如温控区间、尾板卸货、上楼搬运、危险品隔离，直接决定可选车型与费用项。",
      "requirement_id:id, order_id:str, requirement_code:enum, requirement_value:str, "
      "mandatory:bool, surcharge_expected:dec, verified_at:dt",
      "DECLARED,VERIFIED,UNMET,WAIVED",
      aliases=("特殊要求", "作业要求"), group="order_domain"),
    C("OrderBatch", "订单波次", "batch_id",
      "按线路或时段聚合的订单批次。调度按波次批量派单，波次是运力投放的最小规划单位。",
      "batch_id:id, batch_no:str, batch_date:date, origin_code:str, "
      "order_count:int, total_weight_kg:dec, vehicle_type_required:str, "
      "released_at:dt, closed_at:dt",
      "PLANNING,RELEASED,DISPATCHING,CLOSED,CANCELLED",
      aliases=("波次", "批次"), group="order_domain"),
    C("OrderReference", "外部单号映射", "reference_id",
      "本系统订单号与客户系统单号的映射。大客户对接时必须保留双向可查，对账以外部单号为准的场景很常见。",
      "reference_id:id, order_id:str, external_system:str, external_order_no:str, "
      "sync_status:enum, last_synced_at:dt, sync_error:txt",
      "PENDING,SYNCED,FAILED,DETACHED",
      aliases=("外部单号", "客户单号"), group="order_domain"),
]

# ────────────────────────────── 运单域 ──────────────────────────────
_waybill_classes = [
    C("WaybillSegment", "运单分段", "segment_id",
      "一个运单拆成的运输段。多段运输（提货段+干线+配送段）各段独立指派车辆与司机，费用按段归集。",
      "segment_id:id, waybill_id:str, segment_no:int, segment_type:enum, "
      "origin_code:str, dest_code:str, planned_km:dec, actual_km:dec, "
      "driver_id:str, vehicle_id:str, started_at:dt, ended_at:dt",
      "PLANNED,ASSIGNED,STARTED,ARRIVED,COMPLETED,ABORTED",
      aliases=("运输段", "分段"), group="waybill_domain"),
    C("WaybillAssignment", "派单记录", "assignment_id",
      "运单指派给具体司机与车辆的动作记录。含派单方式（系统指派/抢单/人工指派）与响应结果。",
      "assignment_id:id, waybill_id:str, driver_id:str, vehicle_id:str, "
      "assign_mode:enum, assigned_by:str, assigned_at:dt, accepted_at:dt, "
      "rejected_at:dt, reject_reason:enum, response_seconds:int",
      "DISPATCHED,PENDING_ACCEPT,ACCEPTED,REJECTED,TIMEOUT,REVOKED",
      aliases=("派单", "指派"), group="waybill_domain"),
    C("WaybillReassignment", "改派记录", "reassignment_id",
      "运单从原承运方改派到新承运方的记录。改派原因是运力不足、司机爽约或客户要求，影响成本归属。",
      "reassignment_id:id, waybill_id:str, from_driver_id:str, to_driver_id:str, "
      "from_vehicle_id:str, to_vehicle_id:str, reason_code:enum, "
      "reassigned_at:dt, reassigned_by:str, extra_cost:dec",
      "REQUESTED,APPROVED,EXECUTED,REVERTED",
      aliases=("改派", "换司机"), group="waybill_domain"),
    C("WaybillAmendment", "运单变更", "waybill_amendment_id",
      "履约过程中的运单要素变更。如卸货点变更、增补货物、延长等候，变更必须留下费用影响评估。",
      "waybill_amendment_id:id, waybill_id:str, change_type:enum, "
      "before_snapshot:json, after_snapshot:json, fee_delta:dec, "
      "requested_by:str, approved_at:dt, applied_at:dt",
      "REQUESTED,APPROVED,APPLIED,REJECTED",
      aliases=("运单改单", "途中变更"), group="waybill_domain"),
    C("WaybillCancellation", "运单取消", "waybill_cancellation_id",
      "运单在完成前被取消。区分「未提货取消」与「已提货取消」，后者涉及空驶费与货物回运。",
      "waybill_cancellation_id:id, waybill_id:str, cancel_stage:enum, "
      "reason_code:enum, liable_party:enum, empty_run_fee:dec, "
      "cancelled_at:dt, cancelled_by:str",
      "REQUESTED,CONFIRMED,REJECTED",
      aliases=("运单取消", "作废"), group="waybill_domain"),
    C("WaybillClosure", "运单关单", "closure_id",
      "运单的收尾确认。关单前置条件是回单已收、费用已确认、异常已关闭，关单后进入结算池。",
      "closure_id:id, waybill_id:str, closure_type:enum, pod_received:bool, "
      "fee_confirmed:bool, exceptions_cleared:bool, closed_at:dt, closed_by:str, "
      "blocking_reason:str",
      "CHECKING,BLOCKED,CLOSED,REOPENED",
      aliases=("关单", "收尾"), group="waybill_domain"),
    C("WaybillApproval", "运单审批", "waybill_approval_id",
      "运单层面的审批。针对改派、超成本、异常费用减免等需要授权的操作。",
      "waybill_approval_id:id, waybill_id:str, approval_type:enum, "
      "approver_role:str, approver_id:str, decision:enum, comment:txt, "
      "submitted_at:dt, decided_at:dt",
      "PENDING,APPROVED,REJECTED,ESCALATED",
      aliases=("运单审批", "授权"), group="waybill_domain"),
    C("WaybillAuditLog", "运单审计日志", "waybill_audit_id",
      "运单全生命周期的状态流转留痕。每次状态跃迁记录前后状态、触发动作与操作人。",
      "waybill_audit_id:id, waybill_id:str, from_status:str, to_status:str, "
      "trigger_action:str, operator_id:str, operated_at:dt, remark:txt",
      "RECORDED,ARCHIVED",
      aliases=("运单日志", "流转记录"), group="waybill_domain"),
    C("WaybillMilestone", "运单里程碑", "milestone_id",
      "运单的关键节点打点。取货完成、到达卸货点、卸货完成、签收，是时效分析与赔付判定的标尺。",
      "milestone_id:id, waybill_id:str, milestone_code:enum, planned_at:dt, "
      "actual_at:dt, deviation_minutes:int, source:enum, recorded_at:dt",
      "PENDING,REACHED,DELAYED,MISSED,WAIVED",
      aliases=("里程碑", "节点"), group="waybill_domain"),
    C("WaybillETA", "到达时间预测", "eta_id",
      "运单预计到达时间的历次预测值。用于客户通知与延误预警，预测与实际偏差是模型评估依据。",
      "eta_id:id, waybill_id:str, predicted_at:dt, eta_at:dt, "
      "confidence:num, model_version:str, basis:enum, actual_arrival_at:dt, "
      "error_minutes:int",
      "PREDICTED,SUPERSEDED,CONFIRMED",
      aliases=("预计到达", "ETA"), group="waybill_domain"),
    C("WaybillDelay", "延误记录", "delay_id",
      "运单延误的事实记录与归因。归因到具体原因（拥堵/装卸慢/车辆故障）才能做责任分摊。",
      "delay_id:id, waybill_id:str, delay_minutes:int, delay_reason:enum, "
      "liable_party:enum, detected_at:dt, evidence_id:str, penalty_fee:dec",
      "DETECTED,ATTRIBUTED,CONFIRMED,DISPUTED,WAIVED",
      aliases=("延误", "超时"), group="waybill_domain"),
    C("WaybillMileage", "里程结算记录", "mileage_id",
      "运单实际里程的结算依据。区分导航里程、计价里程与客户认可里程，三者不一致时以合同口径为准。",
      "mileage_id:id, waybill_id:str, nav_km:dec, billing_km:dec, "
      "customer_km:dec, mileage_source:enum, adjusted_reason:str, "
      "settled_at:dt",
      "MEASURED,ADJUSTED,SETTLED,DISPUTED",
      aliases=("里程", "公里数"), group="waybill_domain"),
    C("WaybillVersion", "运单版本快照", "waybill_version_id",
      "运单在关键节点的快照。用于回答「派单时约定的是什么」，与当前状态分离。",
      "waybill_version_id:id, waybill_id:str, version_no:int, snapshot:json, "
      "trigger_event:enum, created_at:dt, created_by:str",
      "CREATED,RETAINED,PURGED",
      aliases=("运单快照", "版本"), group="waybill_domain"),
    C("WaybillNote", "运单备注", "waybill_note_id",
      "运单上的作业备注。司机可见提示与调度内部备注分离，避免作业信息与内部判断混淆。",
      "waybill_note_id:id, waybill_id:str, note_type:enum, content:txt, "
      "visibility:enum, author_id:str, created_at:dt",
      "PUBLISHED,HIDDEN,DELETED",
      aliases=("运单备注",), group="waybill_domain"),
    C("WaybillTag", "运单标签", "waybill_tag_id",
      "运单业务标签。用于看板归因与规则命中，如「夜间作业」「高价单」「投诉关联」。",
      "waybill_tag_id:id, waybill_id:str, tag_code:str, tag_value:str, "
      "tag_source:enum, tagged_at:dt",
      "ACTIVE,REMOVED",
      aliases=("运单标记",), group="waybill_domain"),
    C("WaybillPriority", "运单优先级", "waybill_priority_id",
      "运单在调度队列中的优先级。与订单优先级解耦：订单高优但运力不足时可临时降级。",
      "waybill_priority_id:id, waybill_id:str, priority_level:enum, "
      "score:num, reason_code:enum, granted_at:dt, expires_at:dt",
      "ASSIGNED,ACTIVE,EXPIRED",
      aliases=("运单优先级",), group="waybill_domain"),
    C("WaybillSLA", "运单时效", "waybill_sla_id",
      "运单侧的实际时效达成情况。与订单 SLA 对照，得出是否违约及违约程度。",
      "waybill_sla_id:id, waybill_id:str, order_sla_id:str, pickup_on_time:bool, "
      "delivery_on_time:bool, delay_minutes:int, breach_level:enum, "
      "evaluated_at:dt",
      "PENDING,MET,BREACHED,WAIVED,APPEALED",
      aliases=("运单时效",), group="waybill_domain"),
    C("WaybillCost", "成本归集", "cost_id",
      "运单单票的成本项归集。司机运费、油费、路桥费、装卸费、平台费分别归集后算单票毛利。",
      "cost_id:id, waybill_id:str, cost_type:enum, amount:dec, "
      "payer:enum, source_doc:str, accrued_at:dt, confirmed:bool",
      "ACCRUED,CONFIRMED,ADJUSTED,SETTLED",
      aliases=("成本", "单票成本"), group="waybill_domain"),
    C("WaybillProfit", "单票利润", "profit_id",
      "单票收入减成本的利润结果。用于线路盈利分析与运力定价，负毛利单必须可追溯到原因。",
      "profit_id:id, waybill_id:str, revenue:dec, cost_total:dec, "
      "gross_profit:dec, gross_margin_pct:dec, negative_reason:str, "
      "calculated_at:dt",
      "CALCULATED,REVIEWED,ADJUSTED",
      aliases=("毛利", "单票利润"), group="waybill_domain"),
    C("WaybillHandover", "交接记录", "handover_id",
      "货物在承运方之间的交接凭证。交接时的件数、包装状况与铅封号是货损责任划分的基准。",
      "handover_id:id, waybill_id:str, from_party:str, to_party:str, "
      "handover_at:dt, package_count:int, seal_no:str, condition:enum, "
      "witness:str, photo_uri:str",
      "PENDING,SIGNED,OBJECTED,VOID",
      aliases=("交接", "移交"), group="waybill_domain"),
]

# ────────────────────────────── 货物域 ──────────────────────────────
_cargo_classes = [
    C("CargoItem", "货物件明细", "cargo_item_id",
      "货物按件拆分的明细。件级记录是货差判定的最小单位，少一件和多一件都能落到具体件号。",
      "cargo_item_id:id, cargo_id:str, item_no:str, sku_code:str, "
      "item_name:str, quantity:dec, unit:str, weight_kg:dec, "
      "barcode:str, condition:enum",
      "REGISTERED,LOADED,DELIVERED,MISSING,DAMAGED",
      aliases=("件明细", "货件"), group="cargo_domain"),
    C("CargoPackage", "包装单元", "package_id",
      "货物的包装单元。纸箱、木箱、编织袋的包装强度决定堆叠层数与是否需要打托。",
      "package_id:id, cargo_id:str, package_type:enum, length_cm:dec, "
      "width_cm:dec, height_cm:dec, gross_weight_kg:dec, stackable:bool, "
      "fragile:bool",
      "REGISTERED,LOADED,DELIVERED,DAMAGED,OPENED",
      aliases=("包装", "箱"), group="cargo_domain"),
    C("CargoPallet", "托盘", "pallet_id",
      "托盘化装载单元。托盘规格与归属（自有/客户/租赁）影响装卸效率与托盘回收责任。",
      "pallet_id:id, pallet_no:str, pallet_type:enum, owner_type:enum, "
      "length_cm:dec, width_cm:dec, height_cm:dec, load_weight_kg:dec, "
      "return_required:bool, return_status:enum",
      "AVAILABLE,IN_USE,RETURNED,LOST,DAMAGED",
      aliases=("托盘", "卡板"), group="cargo_domain"),
    C("CargoBatch", "货物批次", "cargo_batch_id",
      "同一生产批次的货物。批次用于追溯质量问题，同批次货损要一并排查。",
      "cargo_batch_id:id, batch_no:str, production_date:date, "
      "expiry_date:date, manufacturer:str, batch_quantity:dec, "
      "quality_status:enum",
      "REGISTERED,IN_TRANSIT,DELIVERED,QUARANTINED,EXPIRED",
      aliases=("批次", "生产批"), group="cargo_domain"),
    C("CargoAttribute", "货物属性", "cargo_attribute_id",
      "货物对运输条件的约束属性。温控、防震、防潮、不可倒置等，决定车型与装载方式。",
      "cargo_attribute_id:id, cargo_id:str, attribute_code:enum, "
      "attribute_value:str, mandatory:bool, verified_by:str, verified_at:dt",
      "DECLARED,VERIFIED,UNMET",
      aliases=("货物属性", "运输条件"), group="cargo_domain"),
    C("CargoRestriction", "运输限制", "restriction_id",
      "货物运输的法规或合同限制。如限行时段、禁运区域、单票上限，命中后不可派单。",
      "restriction_id:id, cargo_id:str, restriction_type:enum, "
      "restriction_detail:txt, legal_basis:str, blocking:bool, "
      "checked_at:dt",
      "ACTIVE,EXPIRED,WAIVED",
      aliases=("运输限制", "禁运"), group="cargo_domain"),
    C("CargoDamage", "货损记录", "damage_id",
      "货物损坏的事实记录。含损坏部位、程度、照片与初判责任方，是理赔的起点。",
      "damage_id:id, cargo_id:str, waybill_id:str, damage_type:enum, "
      "damage_level:enum, damaged_quantity:dec, damage_desc:txt, "
      "photo_uri:str, discovered_at:dt, liable_party:enum, "
      "estimated_loss:dec",
      "REPORTED,INSPECTING,CONFIRMED,DISPUTED,CLAIMED,CLOSED",
      aliases=("货损", "破损"), group="cargo_domain"),
    C("CargoLoss", "货差记录", "loss_id",
      "货物数量不符的记录。少于或多于申报件数都属货差，必须先查清是装载错、途中丢还是清点错。",
      "loss_id:id, cargo_id:str, waybill_id:str, declared_quantity:dec, "
      "actual_quantity:dec, diff_quantity:dec, diff_type:enum, "
      "root_cause:enum, found_at:dt, liable_party:enum, "
      "compensation:dec",
      "REPORTED,INVESTIGATING,ATTRIBUTED,COMPENSATED,CLOSED",
      aliases=("货差", "少货"), group="cargo_domain"),
    C("CargoInspection", "货物查验", "inspection_id",
      "提货或交付时的货物查验记录。查验结论是争议时的第三方视角证据。",
      "inspection_id:id, cargo_id:str, waybill_id:str, inspection_stage:enum, "
      "inspector_id:str, inspector_org:str, result:enum, "
      "inspection_items:json, inspected_at:dt, report_uri:str",
      "SCHEDULED,PASSED,FAILED,CONDITIONAL",
      aliases=("查验", "检查"), group="cargo_domain"),
    C("CargoPhoto", "货物照片", "cargo_photo_id",
      "货物的影像证据。装车前、装车后、卸货前、卸货后四个时点的照片是货损责任判定的关键。",
      "cargo_photo_id:id, cargo_id:str, waybill_id:str, photo_stage:enum, "
      "photo_uri:str, taken_at:dt, taken_by:str, geo_code:str, "
      "content_hash:str",
      "CAPTURED,VERIFIED,DISCARDED",
      aliases=("货物照片", "拍照"), group="cargo_domain"),
    C("CargoWeightTicket", "过磅单", "weight_ticket_id",
      "地磅出具的重量凭证。装前空车重与装后重车重之差为实载重量，是超载判定与计费依据。",
      "weight_ticket_id:id, cargo_id:str, waybill_id:str, weigh_station:str, "
      "empty_weight_kg:dec, loaded_weight_kg:dec, net_weight_kg:dec, "
      "weighed_at:dt, ticket_no:str, overweight:bool",
      "RECORDED,VERIFIED,DISPUTED",
      aliases=("过磅单", "磅单"), group="cargo_domain"),
    C("CargoLoadingPlan", "装载方案", "loading_plan_id",
      "货物在车厢内的装载布局方案。决定装配顺序与重心分布，影响卸货效率与行车安全。",
      "loading_plan_id:id, waybill_id:str, plan_no:str, "
      "slot_layout:json, total_weight_kg:dec, volume_usage_pct:dec, "
      "axle_load_ok:bool, planned_by:str, planned_at:dt",
      "PLANNED,EXECUTED,ADJUSTED",
      aliases=("装载方案", "配载"), group="cargo_domain"),
    C("CargoStackRule", "堆叠规则", "stack_rule_id",
      "货物堆叠的约束规则。最大堆叠层数、禁压标志、承重上限，违规则货损率显著上升。",
      "stack_rule_id:id, cargo_id:str, max_stack_layers:int, "
      "max_load_on_top_kg:dec, no_stack:bool, this_side_up:bool, "
      "rule_source:enum",
      "DEFINED,ENFORCED,VIOLATED",
      aliases=("堆叠规则", "码放"), group="cargo_domain"),
    C("CargoDangerousGoods", "危险品申报", "dg_id",
      "危险货物的运输申报。含 UN 编号、危险类别、包装等级与应急措施，缺申报不得承运。",
      "dg_id:id, cargo_id:str, un_number:str, hazard_class:enum, "
      "packing_group:enum, proper_name:str, emergency_contact:str, "
      "declaration_uri:str, declared_at:dt, approved:bool",
      "DRAFT,DECLARED,APPROVED,REJECTED,EXPIRED",
      aliases=("危品", "危险品"), group="cargo_domain"),
    C("CargoColdChain", "冷链温控", "cold_chain_id",
      "冷链货物的温度记录。按时间序列采集车厢温度，超温区间与时长决定货损赔付。",
      "cold_chain_id:id, cargo_id:str, waybill_id:str, required_min_c:dec, "
      "required_max_c:dec, device_id:str, excursion_count:int, "
      "excursion_minutes:int, record_uri:str",
      "MONITORING,NORMAL,EXCURSION,BREAKDOWN,CLOSED",
      aliases=("冷链", "温控"), group="cargo_domain"),
    C("CargoValuation", "货物价值申报", "valuation_id",
      "货主申报的货物价值。保险与理赔以此为上限，低报高报都会影响赔付结论。",
      "valuation_id:id, cargo_id:str, declared_value:dec, "
      "valuation_basis:enum, invoice_no:str, declared_by:str, "
      "declared_at:dt, verified:bool",
      "DECLARED,VERIFIED,DISPUTED,EXPIRED",
      aliases=("货值", "价值申报"), group="cargo_domain"),
    C("CargoReturn", "退货货物", "return_id",
      "交付后退回的货物。退货原因与二次运输费用归属是常见争议点。",
      "return_id:id, waybill_id:str, cargo_id:str, return_reason:enum, "
      "return_quantity:dec, return_fee:dec, fee_payer:enum, "
      "requested_at:dt, received_at:dt",
      "REQUESTED,APPROVED,IN_TRANSIT,RECEIVED,REJECTED",
      aliases=("退货", "回运"), group="cargo_domain"),
    C("CargoSeal", "铅封记录", "seal_id",
      "车厢或集装箱的铅封记录。封号一致是货物未被中途开封的核心证据。",
      "seal_id:id, waybill_id:str, seal_no:str, seal_position:str, "
      "sealed_at:dt, sealed_by:str, broken_at:dt, broken_by:str, "
      "intact:bool",
      "SEALED,VERIFIED,BROKEN,MISSING",
      aliases=("铅封", "封条"), group="cargo_domain"),
]

# ────────────────────────────── 装卸域 ──────────────────────────────
_stop_classes = [
    C("StopLoading", "装货作业", "loading_id",
      "装货点的实际作业记录。到场、开始装、装完三个时点决定是否产生等候费。",
      "loading_id:id, waybill_id:str, stop_id:str, arrived_at:dt, "
      "started_at:dt, finished_at:dt, package_count:int, "
      "loaded_weight_kg:dec, operator_id:str, remark:txt",
      "SCHEDULED,ARRIVED,IN_PROGRESS,COMPLETED,ABORTED",
      aliases=("装货", "装车"), group="stop_domain"),
    C("StopUnloading", "卸货作业", "unloading_id",
      "卸货点的实际作业记录。卸货慢是最常见的等候费来源，必须按时点留证。",
      "unloading_id:id, waybill_id:str, stop_id:str, arrived_at:dt, "
      "started_at:dt, finished_at:dt, unloaded_count:int, "
      "receiver_name:str, remark:txt",
      "SCHEDULED,ARRIVED,IN_PROGRESS,COMPLETED,ABORTED",
      aliases=("卸货", "卸车"), group="stop_domain"),
    C("StopQueue", "排队记录", "queue_id",
      "车辆在场站外的排队等待。排队时长算不算等候费取决于场站是否在预约时间内放行。",
      "queue_id:id, waybill_id:str, stop_id:str, queue_no:str, "
      "joined_at:dt, called_at:dt, entered_at:dt, queue_minutes:int, "
      "queue_reason:enum",
      "QUEUING,CALLED,ENTERED,ABANDONED",
      aliases=("排队", "等位"), group="stop_domain"),
    C("StopDock", "月台泊位", "dock_id",
      "场站的月台泊位资源。泊位数量与型号（尾板/平地/高台）决定同时可作业的车辆数。",
      "dock_id:id, stop_id:str, dock_no:str, dock_type:enum, "
      "max_vehicle_length_m:dec, has_leveler:bool, status:enum, "
      "occupied_by_waybill:str",
      "IDLE,OCCUPIED,MAINTENANCE,CLOSED",
      aliases=("月台", "泊位"), group="stop_domain"),
    C("StopAppointment", "到场预约", "appointment_id",
      "车辆到场的时间窗口预约。有预约才能进园，无预约车辆在场站外排队。",
      "appointment_id:id, stop_id:str, waybill_id:str, slot_start:dt, "
      "slot_end:dt, plate_no:str, booked_at:dt, booked_by:enum, "
      "reschedule_count:int",
      "BOOKED,CONFIRMED,CHECKED_IN,NO_SHOW,CANCELLED,EXPIRED",
      aliases=("预约", "时间窗"), group="stop_domain"),
    C("StopCheckin", "到场签到", "checkin_id",
      "车辆到场签到记录。扫码或定位签到，是等候时长计算的起算点。",
      "checkin_id:id, waybill_id:str, stop_id:str, checkin_at:dt, "
      "checkin_mode:enum, plate_no:str, driver_id:str, gate_no:str, "
      "geo_code:str",
      "CHECKED_IN,VERIFIED,REJECTED",
      aliases=("签到", "到场"), group="stop_domain"),
    C("StopOperation", "作业明细", "operation_id",
      "装卸作业的工序级明细。拆垛、搬运、码放、加固每道工序的耗时与人工。",
      "operation_id:id, stop_id:str, waybill_id:str, operation_code:enum, "
      "started_at:dt, ended_at:dt, duration_minutes:int, "
      "labor_count:int, equipment_id:str",
      "PENDING,DOING,DONE,SKIPPED",
      aliases=("工序", "作业"), group="stop_domain"),
    C("StopEquipment", "装卸设备", "equipment_id",
      "场站装卸设备。叉车、尾板、吊具的可用性决定作业方式，设备故障会直接产生等候。",
      "equipment_id:id, stop_id:str, equipment_type:enum, equipment_no:str, "
      "capacity_kg:dec, status:enum, last_maintained_at:dt, "
      "operator_required:bool",
      "AVAILABLE,IN_USE,MAINTENANCE,FAULT,OFFLINE",
      aliases=("设备", "叉车"), group="stop_domain"),
    C("StopLabor", "装卸人工", "labor_id",
      "装卸人工投入记录。按人时计价，人工费用归属（含在运价内还是另收）随合同不同。",
      "labor_id:id, stop_id:str, waybill_id:str, labor_count:int, "
      "labor_hours:dec, labor_fee:dec, fee_payer:enum, "
      "supplier_id:str, worked_at:dt",
      "ARRANGED,WORKING,FINISHED,BILLED",
      aliases=("人工", "搬运工"), group="stop_domain"),
    C("StopWaiting", "等候记录", "waiting_id",
      "车辆等候时长记录。免费等候额度用尽后按分钟计费，是「比预估贵」的最常见来源。",
      "waiting_id:id, waybill_id:str, stop_id:str, waiting_start:dt, "
      "waiting_end:dt, waiting_minutes:num, free_minutes:int, "
      "billable_minutes:int, waiting_fee:dec, waiting_reason:enum",
      "STARTED,ENDED,BILLED,WAIVED,DISPUTED",
      aliases=("等候", "压车"), group="stop_domain"),
    C("StopFee", "装卸费", "stop_fee_id",
      "装卸环节产生的费用项。上楼费、尾板费、人工费、二次搬运费按场站与合同规则计价。",
      "stop_fee_id:id, stop_id:str, waybill_id:str, fee_code:enum, "
      "amount:dec, calc_basis:str, fee_payer:enum, "
      "invoiced:bool, occurred_at:dt",
      "ESTIMATED,CONFIRMED,INVOICED,WAIVED",
      aliases=("装卸费", "作业费"), group="stop_domain"),
    C("StopDetention", "滞留费", "detention_id",
      "超期占用车辆或场站资源产生的费用。与等候费的区别：滞留通常按小时或按天计。",
      "detention_id:id, waybill_id:str, stop_id:str, detention_start:dt, "
      "detention_end:dt, detention_hours:dec, detention_fee:dec, "
      "liable_party:enum, waived:bool",
      "STARTED,ENDED,BILLED,WAIVED,DISPUTED",
      aliases=("滞留费", "压车费"), group="stop_domain"),
    C("StopSign", "回单签收", "sign_id",
      "收货人签收动作记录。签名、盖章、电子签三种形式，未签收的运单不能关单。",
      "sign_id:id, waybill_id:str, stop_id:str, signer_name:str, "
      "sign_mode:enum, signed_at:dt, sign_image_uri:str, "
      "objection:txt",
      "PENDING,SIGNED,OBJECTED,REFUSED",
      aliases=("签收", "签字"), group="stop_domain"),
    C("StopPOD", "签收凭证", "pod_id",
      "回单影像与结构化凭证。回单缺失是最常见的结算阻塞原因。",
      "pod_id:id, waybill_id:str, pod_type:enum, file_uri:str, "
      "received_at:dt, received_by:str, verified:bool, "
      "verify_result:enum",
      "MISSING,UPLOADED,VERIFIED,REJECTED,ARCHIVED",
      aliases=("回单", "POD"), group="stop_domain"),
    C("StopAccess", "场站准入", "access_id",
      "车辆进入场站的准入校验。车型限制、排放标准、保险有效性任一不满足即拒入。",
      "access_id:id, stop_id:str, plate_no:str, vehicle_type:str, "
      "access_result:enum, reject_reason:enum, checked_at:dt, "
      "checked_by:str",
      "CHECKED,GRANTED,DENIED,EXPIRED",
      aliases=("准入", "进园"), group="stop_domain"),
    C("StopSafetyCheck", "安全检查", "safety_check_id",
      "场站作业安全检查。消防、危险品隔离、装卸机械操作规范，不合格必须整改后才能作业。",
      "safety_check_id:id, stop_id:str, waybill_id:str, check_items:json, "
      "result:enum, issues_found:int, rectified_at:dt, "
      "inspector_id:str, checked_at:dt",
      "SCHEDULED,PASSED,FAILED,RECTIFIED",
      aliases=("安检", "安全检查"), group="stop_domain"),
    C("StopWeighing", "场站称重", "stop_weighing_id",
      "场站地磅的称重记录。与外部过磅单对照可发现中途装卸或计量误差。",
      "stop_weighing_id:id, stop_id:str, waybill_id:str, weigh_type:enum, "
      "weight_kg:dec, weighed_at:dt, deviation_kg:dec, "
      "remark:txt",
      "RECORDED,MATCHED,DEVIATED,DISPUTED",
      aliases=("称重", "地磅"), group="stop_domain"),
    C("StopSlot", "作业时间窗", "slot_id",
      "场站可预约的作业时间窗。时间窗容量由泊位与人工决定，超售直接导致排队。",
      "slot_id:id, stop_id:str, slot_date:date, slot_start:dt, "
      "slot_end:dt, capacity:int, booked:int, "
      "available:int",
      "OPEN,FULL,CLOSED,BLOCKED",
      aliases=("时间窗", "档期"), group="stop_domain"),
]

# ────────────────────────────── 链接 ──────────────────────────────
_links = [
    L("order_contains_line", "订单含行", "Order", "OrderLine", "one_to_many"),
    L("line_belongs_to_order", "行属订单", "OrderLine", "Order", "many_to_one"),
    L("order_amended_by", "订单被修改", "Order", "OrderAmendment", "one_to_many", temporal=True),
    L("amendment_targets_order", "修改针对订单", "OrderAmendment", "Order", "many_to_one"),
    L("order_cancelled_by", "订单被取消", "Order", "OrderCancellation", "one_to_one"),
    L("order_requires_approval", "订单需审批", "Order", "OrderApproval", "one_to_many"),
    L("order_has_attachment", "订单附件", "Order", "OrderAttachment", "one_to_many"),
    L("order_has_audit_log", "订单留痕", "Order", "OrderAuditLog", "one_to_many", temporal=True),
    L("order_confirmed_by", "订单被确认", "Order", "OrderConfirmation", "one_to_one"),
    L("order_originates_from_source", "订单来源", "Order", "OrderSource", "many_to_one"),
    L("order_has_priority", "订单优先级", "Order", "OrderPriority", "one_to_one"),
    L("order_bound_by_sla", "订单时效承诺", "Order", "OrderSLA", "one_to_one"),
    L("order_tagged_with", "订单打标", "Order", "OrderTag", "many_to_many"),
    L("order_has_note", "订单备注", "Order", "OrderNote", "one_to_many"),
    L("order_has_version", "订单版本", "Order", "OrderVersion", "one_to_many", temporal=True),
    L("order_raises_exception", "订单异常", "Order", "OrderException", "one_to_many"),
    L("order_split_into", "订单拆单", "Order", "OrderSplit", "one_to_many"),
    L("order_merged_into", "订单合单", "Order", "OrderMerge", "one_to_many"),
    L("recurring_generates_order", "定期单生成订单", "OrderRecurring", "Order", "one_to_many", temporal=True),
    L("inquiry_converts_to_order", "询价转订单", "OrderInquiry", "Order", "one_to_one"),
    L("draft_submits_order", "草稿提交为订单", "OrderDraft", "Order", "one_to_one"),
    L("order_has_contact", "订单联系人", "Order", "OrderContact", "one_to_many"),
    L("order_declares_requirement", "订单特殊要求", "Order", "OrderRequirement", "one_to_many"),
    L("batch_groups_order", "波次聚合订单", "OrderBatch", "Order", "one_to_many"),
    L("order_has_reference", "外部单号", "Order", "OrderReference", "one_to_many"),

    L("waybill_split_into_segment", "运单分段", "Waybill", "WaybillSegment", "one_to_many", temporal=True),
    L("waybill_assigned_via", "运单派单", "Waybill", "WaybillAssignment", "one_to_many", temporal=True),
    L("waybill_reassigned_via", "运单改派", "Waybill", "WaybillReassignment", "one_to_many", temporal=True),
    L("waybill_amended_by", "运单变更", "Waybill", "WaybillAmendment", "one_to_many", temporal=True),
    L("waybill_cancelled_by", "运单取消", "Waybill", "WaybillCancellation", "one_to_one"),
    L("waybill_closed_by", "运单关单", "Waybill", "WaybillClosure", "one_to_one"),
    L("waybill_requires_approval", "运单审批", "Waybill", "WaybillApproval", "one_to_many"),
    L("waybill_has_audit_log", "运单留痕", "Waybill", "WaybillAuditLog", "one_to_many", temporal=True),
    L("waybill_has_milestone", "运单里程碑", "Waybill", "WaybillMilestone", "one_to_many", temporal=True),
    L("waybill_has_eta", "运单到达预测", "Waybill", "WaybillETA", "one_to_many", temporal=True),
    L("waybill_has_delay", "运单延误", "Waybill", "WaybillDelay", "one_to_many"),
    L("waybill_settles_mileage_by", "里程结算", "Waybill", "WaybillMileage", "one_to_one"),
    L("waybill_has_version", "运单版本", "Waybill", "WaybillVersion", "one_to_many", temporal=True),
    L("waybill_has_note", "运单备注", "Waybill", "WaybillNote", "one_to_many"),
    L("waybill_tagged_with", "运单打标", "Waybill", "WaybillTag", "many_to_many"),
    L("waybill_has_priority", "运单优先级", "Waybill", "WaybillPriority", "one_to_one"),
    L("waybill_bound_by_sla", "运单时效", "Waybill", "WaybillSLA", "one_to_one"),
    L("waybill_accrues_cost", "运单成本", "Waybill", "WaybillCost", "one_to_many", temporal=True),
    L("waybill_measures_profit", "运单单票利润", "Waybill", "WaybillProfit", "one_to_one"),
    L("waybill_handed_over_via", "运单交接", "Waybill", "WaybillHandover", "one_to_many", temporal=True),
    L("segment_belongs_to_waybill", "分段属运单", "WaybillSegment", "Waybill", "many_to_one"),

    L("cargo_has_item", "货物含件", "Cargo", "CargoItem", "one_to_many"),
    L("cargo_packed_in", "货物包装", "Cargo", "CargoPackage", "one_to_many"),
    L("cargo_loaded_on_pallet", "货物打托", "Cargo", "CargoPallet", "many_to_many"),
    L("cargo_has_batch", "货物批次", "Cargo", "CargoBatch", "many_to_one"),
    L("cargo_has_attribute", "货物属性", "Cargo", "CargoAttribute", "one_to_many"),
    L("cargo_has_restriction", "运输限制", "Cargo", "CargoRestriction", "one_to_many"),
    L("cargo_damage_recorded", "货损记录", "Cargo", "CargoDamage", "one_to_many"),
    L("cargo_loss_recorded", "货差记录", "Cargo", "CargoLoss", "one_to_many"),
    L("cargo_inspected_by", "货物查验", "Cargo", "CargoInspection", "one_to_many"),
    L("cargo_has_photo", "货物照片", "Cargo", "CargoPhoto", "one_to_many", temporal=True),
    L("cargo_weighed_by_ticket", "过磅单", "Cargo", "CargoWeightTicket", "one_to_many"),
    L("cargo_planned_by_loading_plan", "装载方案", "Cargo", "CargoLoadingPlan", "one_to_many"),
    L("cargo_governed_by_stack_rule", "堆叠规则", "Cargo", "CargoStackRule", "one_to_many"),
    L("cargo_declares_dangerous_goods", "危险品申报", "Cargo", "CargoDangerousGoods", "one_to_one"),
    L("cargo_monitored_by_cold_chain", "冷链监控", "Cargo", "CargoColdChain", "one_to_one", temporal=True),
    L("cargo_valued_by", "货值申报", "Cargo", "CargoValuation", "one_to_one"),
    L("cargo_returned_via", "退货", "Cargo", "CargoReturn", "one_to_many"),
    L("cargo_sealed_by", "铅封", "Cargo", "CargoSeal", "one_to_many"),

    L("stop_has_loading", "场站装货", "Stop", "StopLoading", "one_to_many", temporal=True),
    L("stop_has_unloading", "场站卸货", "Stop", "StopUnloading", "one_to_many", temporal=True),
    L("stop_has_queue", "场站排队", "Stop", "StopQueue", "one_to_many", temporal=True),
    L("stop_has_dock", "场站月台", "Stop", "StopDock", "one_to_many"),
    L("stop_booked_by_appointment", "到场预约", "Stop", "StopAppointment", "one_to_many"),
    L("stop_checked_in_by", "到场签到", "Stop", "StopCheckin", "one_to_many", temporal=True),
    L("stop_has_operation", "作业明细", "Stop", "StopOperation", "one_to_many"),
    L("stop_uses_equipment", "装卸设备", "Stop", "StopEquipment", "one_to_many"),
    L("stop_uses_labor", "装卸人工", "Stop", "StopLabor", "one_to_many"),
    L("stop_incurred_waiting", "等候记录", "Stop", "StopWaiting", "one_to_many"),
    L("stop_charges_fee", "装卸费", "Stop", "StopFee", "one_to_many"),
    L("stop_incurred_detention", "滞留费", "Stop", "StopDetention", "one_to_many"),
    L("stop_requires_sign", "回单签收", "Stop", "StopSign", "one_to_one"),
    L("stop_collects_pod", "签收凭证", "Stop", "StopPOD", "one_to_one"),
    L("stop_grants_access", "场站准入", "Stop", "StopAccess", "one_to_many"),
    L("stop_conducts_safety_check", "安全检查", "Stop", "StopSafetyCheck", "one_to_many"),
    L("stop_weighs_at", "场站称重", "Stop", "StopWeighing", "one_to_many"),
    L("stop_publishes_slot", "作业时间窗", "Stop", "StopSlot", "one_to_many"),
]

DOMAINS.extend([
    {"id": "order_domain", "label": "订单域", "classes": _order_classes},
    {"id": "waybill_domain", "label": "运单域", "classes": _waybill_classes},
    {"id": "cargo_domain", "label": "货物域", "classes": _cargo_classes},
    {"id": "stop_domain", "label": "装卸域", "classes": _stop_classes},
])

CLASSES = [c for d in DOMAINS for c in d["classes"]]
LINKS = _links
