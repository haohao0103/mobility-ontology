"""域组 3：钱链路（报价域 / 计价域 / 费用域 / 结算域 / 发票域）。"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

# ────────────────────────────── 报价域 ──────────────────────────────
_quote = [
    C("QuoteRequest", "报价请求", "quote_request_id",
      "客户或系统发起的报价请求。起终点、货量与时效要求是报价的输入，请求可复用多次报价。",
      "quote_request_id:id, order_id:str, request_channel:enum, "
      "origin_code:str, dest_code:str, weight_kg:dec, volume_m3:dec, "
      "required_at:dt, requested_by:str",
      "CREATED,QUOTING,QUOTED,CONVERTED,EXPIRED,CANCELLED",
      aliases=("询价请求",), group="quote_domain"),
    C("QuoteVersion", "报价版本", "quote_version_id",
      "同一报价的历次版本。规则调整或客户议价都会产生新版本，只有最新 ACTIVE 版本有效。",
      "quote_version_id:id, quote_id:str, version_no:int, "
      "total_amount:dec, diff_amount:dec, change_reason:txt, "
      "created_at:dt, created_by:str",
      "CREATED,ACTIVE,SUPERSEDED,WITHDRAWN",
      aliases=("报价版本",), group="quote_domain"),
    C("QuoteAdjustment", "报价调整", "quote_adjustment_id",
      "对已出报价的手工调整。调整必须留原因与审批，否则会变成随意定价。",
      "quote_adjustment_id:id, quote_id:str, adjust_type:enum, "
      "adjust_amount:dec, adjust_reason:txt, applied_by:str, "
      "applied_at:dt, approved_by:str",
      "REQUESTED,APPROVED,APPLIED,REJECTED",
      aliases=("调价",), group="quote_domain"),
    C("QuoteApproval", "报价审批", "quote_approval_id",
      "偏离标准价的报价审批。折扣超限或毛利过低必须逐级审批。",
      "quote_approval_id:id, quote_id:str, deviation_pct:dec, "
      "approval_level:int, approver_id:str, decision:enum, "
      "decided_at:dt, comment:txt",
      "PENDING,APPROVED,REJECTED,ESCALATED",
      aliases=("报价审批",), group="quote_domain"),
    C("QuoteDiscount", "折扣", "discount_id",
      "报价中的折扣项。折扣来源（协议折扣/活动折扣/临期折扣）决定其可持续性。",
      "discount_id:id, quote_id:str, discount_type:enum, "
      "discount_rate:dec, discount_amount:dec, discount_source:str, "
      "approved:bool",
      "APPLIED,EXPIRED,REVOKED", aliases=("让价",), group="quote_domain"),
    C("QuoteSurcharge", "附加费", "surcharge_id",
      "报价中的附加费项。夜间、节假日、偏远、超重各有加收规则，必须明示给客户。",
      "surcharge_id:id, quote_id:str, surcharge_type:enum, "
      "surcharge_amount:dec, calc_basis:str, disclosed:bool",
      "APPLIED,WAIVED", aliases=("附加费用",), group="quote_domain"),
    C("QuoteValidity", "报价有效期", "validity_id",
      "报价的有效时间与失效原因。过期报价不得转单，需重新询价。",
      "validity_id:id, quote_id:str, valid_from:dt, "
      "valid_until:dt, expired_at:dt, expiry_reason:enum",
      "ACTIVE,EXPIRING,EXPIRED,EXTENDED",
      aliases=("有效期",), group="quote_domain"),
    C("QuoteComparison", "报价对比", "comparison_id",
      "多个报价的横向对比结果。价格、时效、服务三项加权，用于选价决策。",
      "comparison_id:id, request_id:str, quote_ids:json, "
      "best_price_quote_id:str, best_transit_quote_id:str, "
      "selected_quote_id:str, compared_at:dt",
      "DRAFT,COMPLETED,SELECTED",
      aliases=("比价",), group="quote_domain"),
    C("QuoteRejection", "报价拒绝", "rejection_id",
      "客户拒绝报价的记录。拒绝原因（价格高/时效长/服务不符）是定价优化的输入。",
      "rejection_id:id, quote_id:str, reject_reason:enum, "
      "reject_detail:txt, rejected_at:dt, competitor_price:dec",
      "RECORDED,ANALYZED",
      aliases=("拒报价",), group="quote_domain"),
    C("QuoteTemplate", "报价模板", "quote_template_id",
      "常用场景的报价模板。固定线路与车型的模板能显著缩短报价时长。",
      "quote_template_id:id, template_name:str, route_code:str, "
      "vehicle_type:str, items:json, version:str, active:bool",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("报价模板",), group="quote_domain"),
    C("QuoteHistory", "报价历史", "quote_history_id",
      "同一客户或线路的历史报价记录。历史价格是议价时的参考锚点。",
      "quote_history_id:id, customer_id:str, route_code:str, "
      "quoted_amount:dec, quoted_at:dt, won:bool, "
      "quote_id:str",
      "RECORDED,ARCHIVED", aliases=("历史报价",), group="quote_domain"),
    C("QuoteConversion", "报价转单", "conversion_id",
      "报价被客户接受后转为订单的记录。转单时点锁定计费口径。",
      "conversion_id:id, quote_id:str, order_id:str, "
      "converted_at:dt, converted_by:enum, "
      "agreed_amount:dec",
      "PENDING,CONVERTED,FAILED", aliases=("转订单",), group="quote_domain"),
    C("QuoteTax", "报价税额", "quote_tax_id",
      "报价中的税额拆分。含税与不含税报价必须明确，否则结算时会产生争议。",
      "quote_tax_id:id, quote_id:str, tax_mode:enum, "
      "tax_rate:dec, tax_amount:dec, net_amount:dec",
      "CALCULATED,CONFIRMED", aliases=("税",), group="quote_domain"),
    C("QuoteMargin", "报价毛利", "quote_margin_id",
      "报价相对成本的毛利预估。报价阶段就要看毛利，否则接单即亏损。",
      "quote_margin_id:id, quote_id:str, est_cost:dec, "
      "est_revenue:dec, est_margin:dec, margin_rate:dec, "
      "below_floor:bool",
      "ESTIMATED,REVIEWED,FLAGGED", aliases=("预估毛利",), group="quote_domain"),
    C("QuoteRuleBinding", "报价规则绑定", "binding_id",
      "报价行与计价规则的绑定关系。同一报价行可能命中多条规则，需记录优先级。",
      "binding_id:id, quote_item_id:str, rule_id:str, "
      "rule_version:str, rule_priority:int, matched:bool",
      "BOUND,SUPERSEDED", aliases=("规则绑定",), group="quote_domain"),
    C("QuoteCurrencyRate", "报价汇率", "rate_id",
      "跨境业务的报价汇率。汇率波动需在报价有效期内锁定。",
      "rate_id:id, quote_id:str, from_currency:str, "
      "to_currency:str, rate:dec, locked_until:dt",
      "LOCKED,EXPIRED", aliases=("汇率",), group="quote_domain"),
]

# ────────────────────────────── 计价域 ──────────────────────────────
_pricing = [
    C("PricingRuleVersion", "规则版本", "rule_version_id",
      "计价规则的版本快照。历史费用必须能追溯到当时生效的版本，否则无法解释差异。",
      "rule_version_id:id, rule_id:str, version_no:int, "
      "params_snapshot:json, effective_from:date, "
      "effective_to:date, published_at:dt",
      "DRAFT,PUBLISHED,ACTIVE,EXPIRED",
      aliases=("规则版本",), group="pricing_rule_domain"),
    C("PricingRuleParam", "规则参数", "param_id",
      "计价规则的参数项。单价、系数、阈值都是参数，参数变更即计费口径变更。",
      "param_id:id, rule_id:str, param_code:str, "
      "param_value:str, param_type:enum, unit:str, "
      "description:txt",
      "ACTIVE,DEPRECATED", aliases=("参数",), group="pricing_rule_domain"),
    C("PricingRuleScope", "规则适用范围", "scope_id",
      "规则的适用条件。城市、车型、时段、客户任一维度不符则不命中。",
      "scope_id:id, rule_id:str, city:str, vehicle_type:str, "
      "time_range:str, customer_ids:json, priority:int",
      "ACTIVE,EXPIRED", aliases=("适用范围",), group="pricing_rule_domain"),
    C("PricingTier", "阶梯价格", "tier_id",
      "按量阶梯定价。重量或里程越大单价越低，阶梯边界是报价谈判的焦点。",
      "tier_id:id, rule_id:str, tier_no:int, "
      "lower_bound:dec, upper_bound:dec, unit_price:dec, "
      "bound_unit:enum",
      "ACTIVE,EXPIRED", aliases=("阶梯",), group="pricing_rule_domain"),
    C("PricingBasePrice", "基础价", "base_price_id",
      "路段的基础运价。基础价乘以系数再加附加费构成最终报价。",
      "base_price_id:id, route_code:str, vehicle_type:str, "
      "base_amount:dec, currency:str, effective_from:date, "
      "version:str",
      "DRAFT,ACTIVE,EXPIRED", aliases=("基价",), group="pricing_rule_domain"),
    C("PricingMileagePrice", "里程价", "mileage_price_id",
      "按公里计价的单价。里程价通常分段（前 N 公里与超出部分不同）。",
      "mileage_price_id:id, route_code:str, vehicle_type:str, "
      "per_km_price:dec, included_km:dec, tier_prices:json, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("公里价",), group="pricing_rule_domain"),
    C("PricingWaitingPrice", "等候价", "waiting_price_id",
      "等候时长的计费单价。免费额度与单价共同决定等候费规模，是费用争议重点。",
      "waiting_price_id:id, city:str, vehicle_type:str, "
      "free_minutes:int, per_minute_price:dec, "
      "daily_cap:dec, effective_from:date",
      "ACTIVE,EXPIRED", aliases=("等候单价",), group="pricing_rule_domain"),
    C("PricingHighwayFee", "高速费规则", "highway_fee_id",
      "高速通行费的承担方式。实报实销与含在运价内两种口径完全不同。",
      "highway_fee_id:id, route_code:str, vehicle_type:str, "
      "bearing_mode:enum, reference_amount:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("路桥费",), group="pricing_rule_domain"),
    C("PricingLoadingPrice", "装卸价", "loading_price_id",
      "装卸作业的计价标准。按件、按托、按小时三种口径适用不同场景。",
      "loading_price_id:id, city:str, load_type:enum, "
      "charge_mode:enum, unit_price:dec, min_charge:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("装卸单价",), group="pricing_rule_domain"),
    C("PricingSurchargeRule", "加收规则", "surcharge_rule_id",
      "触发加收的条件与幅度。夜间、节假日、偏远、超重超限分别配置。",
      "surcharge_rule_id:id, rule_code:str, trigger_condition:json, "
      "surcharge_mode:enum, surcharge_value:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("加收",), group="pricing_rule_domain"),
    C("PricingMinCharge", "起步价", "min_charge_id",
      "单票的最低收费。短途单按起步价收，起步价是亏本单的最后防线。",
      "min_charge_id:id, city:str, vehicle_type:str, "
      "min_amount:dec, applicable_km:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("最低收费",), group="pricing_rule_domain"),
    C("PricingPromotion", "优惠活动", "promotion_id",
      "面向客户的优惠活动。活动价与标准价的差额需在费用项中体现，否则对账不平。",
      "promotion_id:id, promotion_name:str, promotion_type:enum, "
      "discount_desc:json, start_at:dt, end_at:dt, "
      "budget:dec",
      "DRAFT,ACTIVE,ENDED,CANCELLED",
      aliases=("活动",), group="pricing_rule_domain"),
    C("PricingApproval", "调价审批", "pricing_approval_id",
      "计价规则变更的审批。调价影响面广，必须评估存量订单影响。",
      "pricing_approval_id:id, rule_id:str, change_type:enum, "
      "impact_orders:int, approver_id:str, decision:enum, "
      "decided_at:dt",
      "PENDING,APPROVED,REJECTED", aliases=("调价审批",), group="pricing_rule_domain"),
    C("PricingSimulation", "计价试算", "simulation_id",
      "规则变更前的试算结果。用存量订单回放，验证调价影响。",
      "simulation_id:id, rule_id:str, sample_orders:int, "
      "old_total:dec, new_total:dec, diff_total:dec, "
      "simulated_at:dt",
      "RUNNING,COMPLETED,FAILED", aliases=("试算",), group="pricing_rule_domain"),
    C("PricingAnomaly", "计价异常", "pricing_anomaly_id",
      "计价结果偏离基线的识别记录。异常不一定是错，但必须能解释。",
      "pricing_anomaly_id:id, fee_id:str, expected_amount:dec, "
      "actual_amount:dec, deviation_pct:dec, "
      "anomaly_type:enum, detected_at:dt",
      "DETECTED,EXPLAINED,CONFIRMED,EXONERATED",
      aliases=("计价异常",), group="pricing_rule_domain"),
    C("PricingRuleAudit", "规则审计", "rule_audit_id",
      "计价规则的变更审计。谁在何时改了哪个参数，是费用争议时的追责依据。",
      "rule_audit_id:id, rule_id:str, action:enum, "
      "field_name:str, old_value:txt, new_value:txt, "
      "operator_id:str, operated_at:dt",
      "RECORDED,ARCHIVED", aliases=("规则审计",), group="pricing_rule_domain"),
]

# ────────────────────────────── 费用域 ──────────────────────────────
_fee = [
    C("FeeDetail", "费用明细", "fee_detail_id",
      "费用项的构成明细。一条费用可能由多项计算组成，明细是解释「为什么是这个数」的依据。",
      "fee_detail_id:id, fee_id:str, detail_no:int, "
      "detail_type:enum, quantity:dec, unit_price:dec, "
      "amount:dec, formula_expr:str",
      "COMPUTED,CONFIRMED,VOID", aliases=("费用明细",), group="fee_domain"),
    C("FeeAdjustment", "费用调整", "fee_adjustment_id",
      "对已生成费用的调整。调整必须留原因、审批与原始值，否则无法审计。",
      "fee_adjustment_id:id, fee_id:str, adjust_type:enum, "
      "before_amount:dec, after_amount:dec, adjust_reason:txt, "
      "applied_by:str, approved_by:str",
      "REQUESTED,APPROVED,APPLIED,REJECTED",
      aliases=("费用调整",), group="fee_domain"),
    C("FeeWaiver", "费用减免", "waiver_id",
      "费用减免记录。减免是成本，必须有审批与责任归属，不能由客服随意给。",
      "waiver_id:id, fee_id:str, waiver_amount:dec, "
      "waiver_reason:enum, granted_by:str, granted_at:dt, "
      "cost_bearer:enum",
      "REQUESTED,APPROVED,GRANTED,REJECTED",
      aliases=("减免", "免单"), group="fee_domain"),
    C("FeeDispute", "费用争议", "dispute_id",
      "客户对费用的争议。争议期间费用挂起，必须先举证再裁定。",
      "dispute_id:id, fee_id:str, dispute_amount:dec, "
      "dispute_reason:txt, raised_by:str, raised_at:dt, "
      "resolution:txt, resolved_at:dt",
      "RAISED,EVIDENCE_COLLECTING,NEGOTIATING,RESOLVED,CLOSED",
      aliases=("费用争议", "扯费"), group="fee_domain"),
    C("FeeAllocation", "费用分摊", "fee_allocation_id",
      "把公共费用分摊到各运单或订单。合单、拼车与共配场景必须分摊，否则单票毛利失真。",
      "fee_allocation_id:id, fee_id:str, target_type:enum, "
      "target_id:str, allocated_amount:dec, basis:enum, "
      "allocated_at:dt",
      "DRAFT,CONFIRMED,ADJUSTED", aliases=("分摊",), group="fee_domain"),
    C("FeeReconciliation", "费用对账", "reconciliation_id",
      "与客户或承运商的费用核对结果。差异项必须逐条解释，不能只对总额。",
      "reconciliation_id:id, counterparty_id:str, period:str, "
      "our_total:dec, their_total:dec, diff_amount:dec, "
      "diff_items:json, reconciled_at:dt",
      "DRAFT,IN_PROGRESS,MATCHED,DIFF_FOUND,CLOSED",
      aliases=("对账",), group="fee_domain"),
    C("FeeConfirmation", "费用确认", "fee_confirmation_id",
      "客户对费用的确认动作。未确认的费用不进入结算，是结算前的最后一道门。",
      "fee_confirmation_id:id, fee_id:str, confirmed_by:str, "
      "confirmed_at:dt, confirm_channel:enum, remark:txt",
      "SENT,CONFIRMED,OBJECTED,EXPIRED",
      aliases=("费用确认",), group="fee_domain"),
    C("FeeCategory", "费用分类", "category_id",
      "费用的分类字典。运费、附加费、代垫费、赔付四大类决定其税务与账务处理。",
      "category_id:id, category_code:str, category_name:str, "
      "parent_code:str, tax_treatment:enum, "
      "account_subject:str",
      "ACTIVE,DEPRECATED", aliases=("费用类别",), group="fee_domain"),
    C("FeeStandard", "费用标准", "standard_id",
      "某类费用的标准值。标准是判断实际费用是否合理的基线。",
      "standard_id:id, fee_type:str, city:str, "
      "standard_amount:dec, tolerance_pct:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("标准值",), group="fee_domain"),
    C("FeeEstimate", "费用预估", "estimate_id",
      "下单时的费用预估。预估与实际差异是「为什么比预估贵」问题的起点。",
      "estimate_id:id, order_id:str, estimate_amount:dec, "
      "estimate_items:json, estimated_at:dt, rule_version:str",
      "CREATED,SUPERSEDED,CONFIRMED", aliases=("预估",), group="fee_domain"),
    C("FeeVariance", "费用偏差", "variance_id",
      "预估与实际的差额归因。按费用类型拆解差异，指出哪一项贡献了多少。",
      "variance_id:id, waybill_id:str, estimate_id:str, "
      "total_variance:dec, item_variances:json, "
      "main_cause:enum, explained_at:dt",
      "COMPUTED,EXPLAINED,DISPUTED", aliases=("偏差", "差异"), group="fee_domain"),
    C("FeeAudit", "费用审计", "fee_audit_id",
      "费用合规性审计。抽查费用口径、审批与凭证完整性，发现的问题须整改。",
      "fee_audit_id:id, scope:str, period:str, "
      "sampled_count:int, issue_count:int, "
      "findings:json, audited_at:dt",
      "PLANNED,ONGOING,COMPLETED,FOLLOW_UP",
      aliases=("费用审计",), group="fee_domain"),
    C("FeeWriteOff", "费用核销", "write_off_id",
      "无法收回费用的核销。核销必须有依据，否则成为成本黑洞。",
      "write_off_id:id, fee_id:str, write_off_amount:dec, "
      "write_off_reason:enum, approved_by:str, "
      "written_off_at:dt",
      "REQUESTED,APPROVED,WRITTEN_OFF,REJECTED",
      aliases=("核销",), group="fee_domain"),
    C("FeePrepay", "费用预付", "prepay_id",
      "客户预付的费用。预付额度与消耗进度决定是否暂停服务。",
      "prepay_id:id, customer_id:str, prepay_amount:dec, "
      "consumed_amount:dec, balance:dec, recharged_at:dt",
      "ACTIVE,EXHAUSTED,REFUNDED",
      aliases=("预付",), group="fee_domain"),
    C("FeeRefund", "费用退款", "refund_id",
      "多收费用的退款。退款原因与审批链必须闭环，避免重复退。",
      "refund_id:id, fee_id:str, refund_amount:dec, "
      "refund_reason:enum, refunded_at:dt, "
      "channel:enum, approved_by:str",
      "REQUESTED,APPROVED,REFUNDED,REJECTED",
      aliases=("退款",), group="fee_domain"),
    C("FeeSplit", "费用拆分", "fee_split_id",
      "一条费用拆给多个承担方。客户承担、承运商承担、平台承担的比例必须明确。",
      "fee_split_id:id, fee_id:str, bearer:enum, "
      "split_amount:dec, split_ratio:dec, agreed:bool",
      "DRAFT,CONFIRMED,DISPUTED", aliases=("拆分",), group="fee_domain"),
    C("FeeBilling", "计费记录", "billing_id",
      "费用生成的计费事件。记录了用哪条规则、哪个版本、什么输入算出了这笔费用。",
      "billing_id:id, waybill_id:str, fee_type:str, "
      "rule_id:str, rule_version:str, input_params:json, "
      "output_amount:dec, billed_at:dt",
      "COMPUTED,CONFIRMED,REVERSED", aliases=("计费",), group="fee_domain"),
]

# ────────────────────────────── 结算域 ──────────────────────────────
_settlement = [
    C("SettlementBatch", "结算批次", "batch_id",
      "一批待结算运单的集合。按周期与对手方分组，批次是结算执行的最小单位。",
      "batch_id:id, batch_no:str, counterparty_id:str, "
      "period:str, waybill_count:int, total_amount:dec, "
      "released_at:dt",
      "DRAFT,RELEASED,PROCESSING,COMPLETED,CANCELLED",
      aliases=("结算批次",), group="settlement_domain"),
    C("SettlementDetail", "结算明细", "settlement_detail_id",
      "结算单下的逐笔明细。每笔对应一张运单或一条费用，是结算可解释性的基础。",
      "settlement_detail_id:id, settlement_id:str, waybill_id:str, "
      "fee_id:str, amount:dec, deduction:dec, "
      "net_amount:dec",
      "INCLUDED,EXCLUDED,ADJUSTED", aliases=("结算明细",), group="settlement_domain"),
    C("SettlementCycle", "结算周期", "cycle_id",
      "结算的周期定义。周结、月结、票结三种口径对应不同的资金占用成本。",
      "cycle_id:id, counterparty_id:str, cycle_type:enum, "
      "cutoff_day:int, payment_days:int, "
      "effective_from:date",
      "ACTIVE,CHANGED,EXPIRED", aliases=("结算周期",), group="settlement_domain"),
    C("SettlementApproval", "结算审批", "settlement_approval_id",
      "结算单的审批记录。金额超限或存在争议项时必须走审批。",
      "settlement_approval_id:id, settlement_id:str, "
      "approval_level:int, approver_id:str, decision:enum, "
      "decided_at:dt, comment:txt",
      "PENDING,APPROVED,REJECTED,ESCALATED",
      aliases=("结算审批",), group="settlement_domain"),
    C("SettlementReversal", "结算冲销", "reversal_id",
      "已过账结算的冲销。冲销必须与原结算对应，不能直接改数。",
      "reversal_id:id, settlement_id:str, reversal_amount:dec, "
      "reversal_reason:enum, reversed_at:dt, "
      "approved_by:str",
      "REQUESTED,APPROVED,REVERSED,REJECTED",
      aliases=("冲销", "红冲"), group="settlement_domain"),
    C("SettlementAdvance", "预付款", "advance_id",
      "结算前支付的预付款。预付款冲抵结算款，需逐笔核销。",
      "advance_id:id, counterparty_id:str, advance_amount:dec, "
      "used_amount:dec, balance:dec, paid_at:dt",
      "PAID,PARTIALLY_USED,EXHAUSTED,REFUNDED",
      aliases=("预付",), group="settlement_domain"),
    C("SettlementBalance", "结算余额", "balance_id",
      "对手方的往来余额。余额方向（应收/应付）决定后续是收款还是付款。",
      "balance_id:id, counterparty_id:str, balance_date:date, "
      "receivable:dec, payable:dec, net_balance:dec",
      "CALCULATED,CONFIRMED", aliases=("余额",), group="settlement_domain"),
    C("SettlementDeduction", "扣款", "deduction_id",
      "结算时的扣减项。违约金、货损赔付、代垫费用都从结算款中扣。",
      "deduction_id:id, settlement_id:str, deduction_type:enum, "
      "deduct_amount:dec, deduction_source:str, "
      "disputed:bool",
      "APPLIED,DISPUTED,REVOKED", aliases=("扣减",), group="settlement_domain"),
    C("SettlementConfirmation", "结算确认", "confirmation_id",
      "对手方对结算单的确认。未确认的结算不能付款，确认后进入付款流程。",
      "confirmation_id:id, settlement_id:str, confirmed_by:str, "
      "confirmed_at:dt, confirm_channel:enum, objection:txt",
      "SENT,CONFIRMED,OBJECTED,EXPIRED",
      aliases=("结算确认",), group="settlement_domain"),
    C("SettlementPayment", "付款记录", "payment_id",
      "结算款的支付记录。付款单号、到账时间与银行流水必须可对账。",
      "payment_id:id, settlement_id:str, pay_amount:dec, "
      "pay_channel:enum, paid_at:dt, bank_serial_no:str, "
      "arrived:bool",
      "PENDING,PAID,ARRIVED,FAILED",
      aliases=("付款",), group="settlement_domain"),
    C("SettlementHold", "结算挂起", "hold_id",
      "结算被挂起的原因与处理。回单缺失、争议未决、发票未到都会触发挂起。",
      "hold_id:id, settlement_id:str, hold_reason:enum, "
      "held_at:dt, released_at:dt, released_by:str",
      "HELD,RELEASED,ESCALATED", aliases=("挂起",), group="settlement_domain"),
    C("SettlementStatement", "对账单", "statement_id",
      "发给对手方的对账单。分期对账与总额对账两种模式，差异须逐条列明。",
      "statement_id:id, counterparty_id:str, period:str, "
      "statement_no:str, total_amount:dec, "
      "sent_at:dt, confirmed_at:dt",
      "DRAFT,SENT,CONFIRMED,OBJECTED",
      aliases=("对账单",), group="settlement_domain"),
    C("SettlementReconcile", "结算对账", "reconcile_id",
      "结算与账务系统的核对结果。业务账与财务账不一致必须查清。",
      "reconcile_id:id, period:str, biz_total:dec, "
      "fin_total:dec, diff_amount:dec, diff_reason:txt, "
      "reconciled_at:dt",
      "DRAFT,MATCHED,DIFF_FOUND,RESOLVED",
      aliases=("结算对账",), group="settlement_domain"),
    C("SettlementRuleSnapshot", "结算规则快照", "snapshot_id",
      "结算时点的规则快照。规则中途变更不能影响已生成的结算单。",
      "snapshot_id:id, settlement_id:str, rule_snapshot:json, "
      "captured_at:dt, captured_by:str",
      "CAPTURED,VERIFIED", aliases=("规则快照",), group="settlement_domain"),
]

# ────────────────────────────── 发票域 ──────────────────────────────
_invoice = [
    C("InvoiceRequest", "开票申请", "invoice_request_id",
      "开票申请单。抬头、税号、金额与开票类型必须与结算单一致。",
      "invoice_request_id:id, settlement_id:str, apply_amount:dec, "
      "invoice_type:enum, title_id:str, requested_by:str, "
      "requested_at:dt",
      "SUBMITTED,REVIEWING,APPROVED,REJECTED,ISSUED",
      aliases=("开票申请",), group="invoice_domain"),
    C("InvoiceDetail", "发票明细", "invoice_detail_id",
      "发票的商品明细行。税率、金额与数量必须与结算明细对应，否则无法抵扣。",
      "invoice_detail_id:id, invoice_id:str, item_name:str, "
      "spec:str, quantity:dec, unit_price:dec, "
      "amount:dec, tax_rate:dec",
      "INCLUDED,VOID", aliases=("发票明细",), group="invoice_domain"),
    C("InvoiceTitle", "发票抬头", "title_id",
      "开票抬头信息。企业名称与税号错一个字都不能用，抬头库需去重校验。",
      "title_id:id, title_name:str, tax_no:str, "
      "address:str, bank_name:str, bank_account:str, "
      "verified:bool",
      "PENDING,VERIFIED,INVALID", aliases=("抬头",), group="invoice_domain"),
    C("InvoiceDelivery", "发票交付", "delivery_id",
      "发票交付给客户的方式与状态。电子与纸质两种，未交付的发票客户无法抵扣。",
      "delivery_id:id, invoice_id:str, delivery_mode:enum, "
      "delivered_at:dt, receiver:str, "
      "tracking_no:str, acknowledged:bool",
      "PENDING,SENT,DELIVERED,ACKNOWLEDGED,FAILED",
      aliases=("发票交付",), group="invoice_domain"),
    C("InvoiceVoid", "发票作废", "void_id",
      "发票作废记录。当月开具的发票可作废，跨月只能红冲。",
      "void_id:id, invoice_id:str, void_reason:enum, "
      "voided_at:dt, operator_id:str, reissued_invoice_id:str",
      "APPLIED,APPROVED,VOIDED,REJECTED",
      aliases=("作废",), group="invoice_domain"),
    C("InvoiceRedLetter", "红字发票", "red_letter_id",
      "冲减已开发票的红字发票。必须关联原蓝票，金额为负。",
      "red_letter_id:id, original_invoice_id:str, "
      "red_amount:dec, red_reason:enum, issued_at:dt, "
      "confirmed_by:str",
      "APPLIED,APPROVED,ISSUED,CONFIRMED",
      aliases=("红冲", "负数票"), group="invoice_domain"),
    C("InvoiceVerify", "发票查验", "verify_id",
      "发票真伪查验结果。假票会导致进项税无法抵扣并面临处罚。",
      "verify_id:id, invoice_id:str, verify_channel:enum, "
      "verify_result:enum, verified_at:dt, detail:json",
      "PENDING,PASSED,FAILED,ERROR",
      aliases=("查验", "验真"), group="invoice_domain"),
    C("InvoiceTax", "发票税额", "invoice_tax_id",
      "发票的税额拆分。价税分离必须准确，否则申报会出问题。",
      "invoice_tax_id:id, invoice_id:str, net_amount:dec, "
      "tax_amount:dec, tax_rate:dec, tax_category:enum",
      "CALCULATED,CONFIRMED", aliases=("税额",), group="invoice_domain"),
    C("InvoiceQuota", "发票额度", "quota_id",
      "开票额度管控。单月开票额度受限，超额需申请临时增量。",
      "quota_id:id, entity_id:str, period:str, "
      "quota_amount:dec, used_amount:dec, "
      "remaining:dec",
      "ACTIVE,EXHAUSTED,ADJUSTED", aliases=("额度",), group="invoice_domain"),
    C("InvoiceReceipt", "收票", "receipt_id",
      "收到的供应商发票。收票后需查验、认证才能抵扣。",
      "receipt_id:id, invoice_no:str, supplier_id:str, "
      "amount:dec, received_at:dt, verified:bool, "
      "certified:bool",
      "RECEIVED,VERIFIED,CERTIFIED,REJECTED",
      aliases=("收票", "进项"), group="invoice_domain"),
    C("InvoiceRejection", "拒票", "rejection_id",
      "客户拒收发票的记录。抬头错误、金额不符、税率错误是主因。",
      "rejection_id:id, invoice_id:str, reject_reason:enum, "
      "reject_detail:txt, rejected_at:dt, reissue_required:bool",
      "RECORDED,REISSUING,CLOSED", aliases=("拒收",), group="invoice_domain"),
    C("InvoiceArchive", "发票归档", "archive_id",
      "发票归档记录。电子档案需满足留存年限与可检索要求。",
      "archive_id:id, invoice_id:str, archive_no:str, "
      "archive_uri:str, archived_at:dt, retention_years:int",
      "PENDING,ARCHIVED,RETRIEVED",
      aliases=("归档",), group="invoice_domain"),
    C("InvoiceMatch", "三单匹配", "match_id",
      "订单、入库单、发票三单匹配结果。三单不一致不得付款。",
      "match_id:id, invoice_id:str, order_id:str, "
      "receipt_no:str, match_result:enum, "
      "diff_items:json, matched_at:dt",
      "PENDING,MATCHED,MISMATCH,RESOLVED",
      aliases=("三单匹配",), group="invoice_domain"),
    C("InvoiceTaxRate", "税率", "tax_rate_id",
      "适用税率字典。运输服务与仓储服务的税率不同，选错会导致申报异常。",
      "tax_rate_id:id, tax_category:enum, rate:dec, "
      "effective_from:date, policy_ref:str",
      "ACTIVE,EXPIRED", aliases=("税率",), group="invoice_domain"),
]

_links = [
    L("quote_request_generates_quote", "请求生成报价", "QuoteRequest", "Quote", "one_to_many"),
    L("quote_has_version", "报价版本", "Quote", "QuoteVersion", "one_to_many", temporal=True),
    L("quote_adjusted_by", "报价调整", "Quote", "QuoteAdjustment", "one_to_many"),
    L("quote_requires_approval", "报价审批", "Quote", "QuoteApproval", "one_to_many"),
    L("quote_applies_discount", "折扣", "Quote", "QuoteDiscount", "one_to_many"),
    L("quote_applies_surcharge", "附加费", "Quote", "QuoteSurcharge", "one_to_many"),
    L("quote_has_validity", "报价有效期", "Quote", "QuoteValidity", "one_to_one"),
    L("request_compared_by", "报价对比", "QuoteRequest", "QuoteComparison", "one_to_one"),
    L("quote_rejected_by", "报价拒绝", "Quote", "QuoteRejection", "one_to_many"),
    L("quote_from_template", "报价模板", "Quote", "QuoteTemplate", "many_to_one"),
    L("customer_has_quote_history", "报价历史", "Quote", "QuoteHistory", "one_to_many"),
    L("quote_converted_by", "报价转单", "Quote", "QuoteConversion", "one_to_one"),
    L("quote_has_tax", "报价税额", "Quote", "QuoteTax", "one_to_one"),
    L("quote_measures_margin", "报价毛利", "Quote", "QuoteMargin", "one_to_one"),
    L("quote_item_binds_rule", "报价行绑规则", "QuoteItem", "QuoteRuleBinding", "one_to_many"),
    L("quote_has_currency_rate", "报价汇率", "Quote", "QuoteCurrencyRate", "one_to_many"),

    L("pricing_rule_has_version", "规则版本", "PricingRule", "PricingRuleVersion", "one_to_many", temporal=True),
    L("pricing_rule_has_param", "规则参数", "PricingRule", "PricingRuleParam", "one_to_many"),
    L("pricing_rule_has_scope", "适用范围", "PricingRule", "PricingRuleScope", "one_to_many"),
    L("pricing_rule_has_tier", "阶梯价格", "PricingRule", "PricingTier", "one_to_many"),
    L("route_based_on_base_price", "基础价", "PricingRule", "PricingBasePrice", "one_to_many"),
    L("route_priced_by_mileage", "里程价", "PricingRule", "PricingMileagePrice", "one_to_many"),
    L("city_priced_by_waiting", "等候价", "PricingRule", "PricingWaitingPrice", "one_to_many"),
    L("route_has_highway_fee", "高速费规则", "PricingRule", "PricingHighwayFee", "one_to_many"),
    L("city_has_loading_price", "装卸价", "PricingRule", "PricingLoadingPrice", "one_to_many"),
    L("pricing_rule_defines_surcharge", "加收规则", "PricingRule", "PricingSurchargeRule", "one_to_many"),
    L("city_has_min_charge", "起步价", "PricingRule", "PricingMinCharge", "one_to_many"),
    L("promotion_applies_rule", "优惠活动", "PricingRule", "PricingPromotion", "one_to_many"),
    L("pricing_change_needs_approval", "调价审批", "PricingRule", "PricingApproval", "one_to_many"),
    L("pricing_rule_simulated_by", "计价试算", "PricingRule", "PricingSimulation", "one_to_many", temporal=True),
    L("pricing_rule_flagged_anomaly", "计价异常", "PricingRule", "PricingAnomaly", "one_to_many"),
    L("pricing_rule_has_audit", "规则审计", "PricingRule", "PricingRuleAudit", "one_to_many", temporal=True),

    L("fee_has_detail", "费用明细", "FeeItem", "FeeDetail", "one_to_many"),
    L("fee_adjusted_by", "费用调整", "FeeItem", "FeeAdjustment", "one_to_many", temporal=True),
    L("fee_waived_by", "费用减免", "FeeItem", "FeeWaiver", "one_to_many"),
    L("fee_disputed_by", "费用争议", "FeeItem", "FeeDispute", "one_to_many"),
    L("fee_allocated_by", "费用分摊", "FeeItem", "FeeAllocation", "one_to_many"),
    L("fee_reconciled_by", "费用对账", "FeeItem", "FeeReconciliation", "one_to_many"),
    L("fee_confirmed_by", "费用确认", "FeeItem", "FeeConfirmation", "one_to_one"),
    L("fee_classified_as", "费用分类", "FeeItem", "FeeCategory", "many_to_one"),
    L("fee_measured_against_standard", "费用标准", "FeeItem", "FeeStandard", "many_to_one"),
    L("order_has_estimate", "费用预估", "Order", "FeeEstimate", "one_to_many"),
    L("waybill_has_variance", "费用偏差", "Waybill", "FeeVariance", "one_to_one"),
    L("fee_audited_by", "费用审计", "FeeItem", "FeeAudit", "one_to_many"),
    L("fee_written_off_by", "费用核销", "FeeItem", "FeeWriteOff", "one_to_one"),
    L("customer_has_prepay", "预付", "Shipper", "FeePrepay", "one_to_many"),
    L("fee_refunded_by", "费用退款", "FeeItem", "FeeRefund", "one_to_many"),
    L("fee_split_by", "费用拆分", "FeeItem", "FeeSplit", "one_to_many"),
    L("waybill_has_billing", "计费记录", "Waybill", "FeeBilling", "one_to_many", temporal=True),

    L("settlement_has_batch", "结算批次", "Settlement", "SettlementBatch", "many_to_one"),
    L("settlement_has_detail", "结算明细", "Settlement", "SettlementDetail", "one_to_many"),
    L("counterparty_has_cycle", "结算周期", "Settlement", "SettlementCycle", "many_to_one"),
    L("settlement_requires_approval", "结算审批", "Settlement", "SettlementApproval", "one_to_many"),
    L("settlement_reversed_by", "结算冲销", "Settlement", "SettlementReversal", "one_to_one"),
    L("counterparty_has_advance", "预付款", "Settlement", "SettlementAdvance", "one_to_many"),
    L("counterparty_has_balance", "结算余额", "Settlement", "SettlementBalance", "one_to_many", temporal=True),
    L("settlement_deducted_by", "扣款", "Settlement", "SettlementDeduction", "one_to_many"),
    L("settlement_confirmed_by", "结算确认", "Settlement", "SettlementConfirmation", "one_to_one"),
    L("settlement_paid_by", "付款记录", "Settlement", "SettlementPayment", "one_to_many"),
    L("settlement_held_by", "结算挂起", "Settlement", "SettlementHold", "one_to_many"),
    L("settlement_has_statement", "对账单", "Settlement", "SettlementStatement", "one_to_one"),
    L("settlement_reconciled_by", "结算对账", "Settlement", "SettlementReconcile", "one_to_many"),
    L("settlement_has_rule_snapshot", "规则快照", "Settlement", "SettlementRuleSnapshot", "one_to_one"),

    L("settlement_requests_invoice", "开票申请", "Settlement", "InvoiceRequest", "one_to_many"),
    L("invoice_has_detail", "发票明细", "Invoice", "InvoiceDetail", "one_to_many"),
    L("invoice_uses_title", "发票抬头", "Invoice", "InvoiceTitle", "many_to_one"),
    L("invoice_delivered_by", "发票交付", "Invoice", "InvoiceDelivery", "one_to_one"),
    L("invoice_voided_by", "发票作废", "Invoice", "InvoiceVoid", "one_to_one"),
    L("invoice_red_lettered_by", "红字发票", "Invoice", "InvoiceRedLetter", "one_to_one"),
    L("invoice_verified_by", "发票查验", "Invoice", "InvoiceVerify", "one_to_one"),
    L("invoice_has_tax", "发票税额", "Invoice", "InvoiceTax", "one_to_one"),
    L("entity_has_invoice_quota", "发票额度", "Invoice", "InvoiceQuota", "one_to_many"),
    L("invoice_has_receipt", "收票", "Invoice", "InvoiceReceipt", "one_to_one"),
    L("invoice_rejected_by", "拒票", "Invoice", "InvoiceRejection", "one_to_many"),
    L("invoice_archived_by", "发票归档", "Invoice", "InvoiceArchive", "one_to_one"),
    L("invoice_matched_by", "三单匹配", "Invoice", "InvoiceMatch", "one_to_one"),
    L("invoice_applies_tax_rate", "税率", "Invoice", "InvoiceTaxRate", "many_to_one"),
]

DOMAINS.extend([
    {"id": "quote_domain", "label": "报价域", "classes": _quote},
    {"id": "pricing_rule_domain", "label": "计价域", "classes": _pricing},
    {"id": "fee_domain", "label": "费用域", "classes": _fee},
    {"id": "settlement_domain", "label": "结算域", "classes": _settlement},
    {"id": "invoice_domain", "label": "发票域", "classes": _invoice},
])

CLASSES = [c for d in DOMAINS for c in d["classes"]]
LINKS = _links
