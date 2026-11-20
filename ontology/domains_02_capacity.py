"""域组 2：运力侧（车辆域 / 司机域 / 承运商域 / 分包域 / 维保域 / 能源域）。"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

# ────────────────────────────── 车辆域 ──────────────────────────────
_vehicle = [
    C("VehicleProfile", "车辆档案", "vehicle_profile_id",
      "车辆的基础档案信息。车型、轴数与核定参数决定它能拉什么货，档案错误会直接导致派单错配。",
      "vehicle_profile_id:id, vehicle_id:str, brand:str, model:str, "
      "axle_count:int, fuel_type:enum, emission_standard:str, "
      "registered_at:date, owner_type:enum",
      "DRAFT,VERIFIED,ACTIVE,RETIRED",
      aliases=("车辆资料",), group="vehicle_domain"),
    C("VehicleRegistration", "车辆登记", "registration_id",
      "车辆在车管所的登记信息。行驶证号、登记日期与使用性质是合规校验的输入。",
      "registration_id:id, vehicle_id:str, plate_no:str, license_no:str, "
      "register_date:date, usage_nature:enum, seat_count:int, "
      "registration_authority:str",
      "REGISTERED,TRANSFERRED,DEREGISTERED",
      aliases=("行驶证", "登记"), group="vehicle_domain"),
    C("VehicleType", "车型字典", "vehicle_type_id",
      "车型的标准定义。厢式、平板、高栏、冷藏各有装载约束与计价基准，是报价与派单的公共字典。",
      "vehicle_type_id:id, type_code:str, type_name:str, body_type:enum, "
      "max_length_m:dec, max_load_t:dec, has_tail_lift:bool, "
      "suitable_cargo:json",
      "ACTIVE,DEPRECATED", aliases=("车型",), group="vehicle_domain"),
    C("VehicleCapacity", "核定载重", "capacity_id",
      "车辆的核定载重与容积。实载超过核载即为超载，是法规红线也是费用加收依据。",
      "capacity_id:id, vehicle_id:str, rated_load_t:dec, rated_volume_m3:dec, "
      "axle_load_limit_t:dec, verified_at:dt, cert_no:str",
      "DECLARED,VERIFIED,EXPIRED", aliases=("核载", "载重"), group="vehicle_domain"),
    C("VehicleDimension", "车厢尺寸", "dimension_id",
      "车厢内尺寸与开门方式。长宽高决定能装多少托盘，尾板高度决定装卸方式。",
      "dimension_id:id, vehicle_id:str, inner_length_m:dec, inner_width_m:dec, "
      "inner_height_m:dec, door_type:enum, tail_lift_capacity_kg:dec",
      "MEASURED,VERIFIED", aliases=("车厢", "尺寸"), group="vehicle_domain"),
    C("VehicleInspection", "车辆年检", "inspection_record_id",
      "车辆定期检验记录。年检过期车辆不得承运，是准入校验的硬条件。",
      "inspection_record_id:id, vehicle_id:str, inspection_date:date, "
      "next_due_date:date, result:enum, station:str, "
      "cert_no:str, fee:dec",
      "DUE,PASSED,FAILED,OVERDUE", aliases=("年检", "检验"), group="vehicle_domain"),
    C("VehicleLicense", "道路运输证", "transport_license_id",
      "营运车辆的道路运输许可证。无证营运属违规，直接影响承运合法性与保险理赔。",
      "transport_license_id:id, vehicle_id:str, license_no:str, "
      "issue_date:date, expiry_date:date, authority:str, "
      "business_scope:str",
      "VALID,EXPIRING,EXPIRED,REVOKED", aliases=("营运证",), group="vehicle_domain"),
    C("VehicleDeviceBinding", "车辆设备绑定", "binding_id",
      "车载设备与车辆的绑定关系。定位设备、温控设备、摄像头绑定后才能采集对应数据。",
      "binding_id:id, vehicle_id:str, device_id:str, device_type:enum, "
      "bound_at:dt, unbound_at:dt, active:bool",
      "BOUND,UNBOUND,FAULT", aliases=("设备绑定",), group="vehicle_domain"),
    C("VehicleFault", "车辆故障", "fault_id",
      "车辆故障记录。故障码、发生部位与处理方式决定车辆能否继续承运，是改派的常见触发。",
      "fault_id:id, vehicle_id:str, fault_code:str, fault_part:str, "
      "severity:enum, occurred_at:dt, repaired_at:dt, "
      "repair_cost:dec, out_of_service:bool",
      "REPORTED,DIAGNOSING,REPAIRING,FIXED,SCRAPPED",
      aliases=("故障", "报修"), group="vehicle_domain"),
    C("VehicleTelematics", "车联网数据", "telematics_id",
      "车辆实时状态数据。速度、转速、油耗、里程按时间序列采集，是驾驶行为与油耗分析的数据源。",
      "telematics_id:id, vehicle_id:str, collected_at:dt, speed_kmh:dec, "
      "engine_rpm:int, fuel_level_pct:dec, odometer_km:dec, "
      "engine_on:bool",
      "COLLECTED,PROCESSED,EXPIRED",
      aliases=("车联网", "TBOX"), group="vehicle_domain"),
    C("VehicleRestriction", "车辆限行", "restriction_id",
      "车辆在特定时段或区域的通行限制。限行信息参与派单可行性判断，否则会派出无法通行的车。",
      "restriction_id:id, vehicle_id:str, restriction_type:enum, "
      "area_code:str, start_at:dt, end_at:dt, restriction_reason:str",
      "ACTIVE,EXPIRED", aliases=("限行", "禁行"), group="vehicle_domain"),
    C("VehicleSubstitute", "替车记录", "substitute_id",
      "原车无法承运时替换为另一辆车的记录。替车必须同步更新运单上的车辆关联与保险覆盖。",
      "substitute_id:id, waybill_id:str, original_vehicle_id:str, "
      "substitute_vehicle_id:str, substitute_reason:enum, substituted_at:dt, "
      "approved_by:str, cost_delta:dec",
      "REQUESTED,APPROVED,EXECUTED,REVERTED",
      aliases=("换车", "替车"), group="vehicle_domain"),
    C("VehicleOwnership", "车辆归属", "ownership_id",
      "车辆的所有权归属。自有、挂靠、租赁三种归属影响成本归集与责任承担。",
      "ownership_id:id, vehicle_id:str, owner_type:enum, owner_id:str, "
      "effective_from:date, effective_to:date, monthly_rent:dec, "
      "contract_id:str",
      "ACTIVE,TRANSFERRED,TERMINATED",
      aliases=("归属", "产权"), group="vehicle_domain"),
    C("VehicleScrap", "车辆报废", "scrap_id",
      "车辆报废或退出运营的记录。残值处理与设备回收必须闭环，否则会出现「幽灵车」被继续派单。",
      "scrap_id:id, vehicle_id:str, scrap_reason:enum, scrap_at:date, "
      "residual_value:dec, device_recovered:bool, "
      "deregistered:bool",
      "APPLIED,APPROVED,SCRAPPED",
      aliases=("报废", "退出"), group="vehicle_domain"),
    C("VehicleInsurancePolicy", "车辆保险单", "policy_id",
      "车辆的交强险与商业险保单。保额与有效期是承运准入条件，脱保车辆不得派单。",
      "policy_id:id, vehicle_id:str, policy_no:str, insurer:str, "
      "coverage_type:enum, premium:dec, coverage_amount:dec, "
      "start_date:date, end_date:date",
      "QUOTED,EFFECTIVE,EXPIRING,EXPIRED,CLAIMED",
      aliases=("车险", "保单"), group="vehicle_domain"),
    C("VehicleMileageLog", "车辆里程台账", "mileage_log_id",
      "车辆累计里程的月度台账。用于保养周期触发与折旧计算，与运单里程互为校验。",
      "mileage_log_id:id, vehicle_id:str, period:str, begin_km:dec, "
      "end_km:dec, total_km:dec, loaded_km:dec, empty_km:dec",
      "OPEN,CLOSED,RECONCILED", aliases=("里程台账",), group="vehicle_domain"),
    C("VehiclePhoto", "车辆照片", "vehicle_photo_id",
      "车辆外观与货厢照片。用于货损争议时确认车辆初始状态，也是验车环节的凭证。",
      "vehicle_photo_id:id, vehicle_id:str, photo_stage:enum, photo_uri:str, "
      "taken_at:dt, taken_by:str, content_hash:str",
      "CAPTURED,VERIFIED,DISCARDED", aliases=("车照",), group="vehicle_domain"),
    C("VehicleComplianceCheck", "车辆合规校验", "compliance_check_id",
      "派单前的车辆合规校验结果。证照、保险、年检、限行四项全过才可派单。",
      "compliance_check_id:id, vehicle_id:str, check_items:json, "
      "result:enum, failed_items:json, checked_at:dt",
      "PASSED,FAILED,WAIVED", aliases=("合规校验",), group="vehicle_domain"),
]

# ────────────────────────────── 司机域 ──────────────────────────────
_driver = [
    C("DriverProfile", "司机档案", "driver_profile_id",
      "司机的完整档案。驾龄、常跑线路与历史服务分决定派单优先级，基础信息属敏感数据。",
      "driver_profile_id:id, driver_id:str, gender:enum, birth_date:date, "
      "driving_years:int, home_city:str, preferred_routes:json, "
      "service_score:num",
      "DRAFT,VERIFIED,ACTIVE,SUSPENDED",
      aliases=("司机资料",), group="driver_domain"),
    C("DriverLicense", "驾驶证", "driver_license_id",
      "司机驾驶证信息。准驾车型不符即不可承运对应车型，有效期到期自动失去派单资格。",
      "driver_license_id:id, driver_id:str, license_no:str, "
      "license_class:str, first_issue_date:date, expiry_date:date, "
      "issuing_authority:str",
      "VALID,EXPIRING,EXPIRED,REVOKED", aliases=("驾照",), group="driver_domain"),
    C("DriverQualificationCert", "从业资格证", "qualification_id",
      "道路运输从业资格证。货运、危险品、冷链各有对应资格，超范围承运属违规。",
      "qualification_id:id, driver_id:str, cert_no:str, cert_type:enum, "
      "issue_date:date, expiry_date:date, training_hours:int",
      "VALID,EXPIRING,EXPIRED,SUSPENDED", aliases=("资格证",), group="driver_domain"),
    C("DriverShift", "司机排班", "shift_id",
      "司机的班次安排。班次决定可用时段，跨班次派单会触发加班成本。",
      "shift_id:id, driver_id:str, shift_date:date, shift_type:enum, "
      "start_at:dt, end_at:dt, vehicle_id:str, status:enum",
      "PLANNED,ACTIVE,FINISHED,ABSENT,SWAPPED",
      aliases=("排班", "班次"), group="driver_domain"),
    C("DriverAttendance", "司机考勤", "attendance_id",
      "司机出勤记录。迟到、缺勤直接影响运力可用量与准时率考核。",
      "attendance_id:id, driver_id:str, attendance_date:date, "
      "check_in_at:dt, check_out_at:dt, late_minutes:int, "
      "attendance_type:enum",
      "NORMAL,LATE,ABSENT,LEAVE", aliases=("考勤",), group="driver_domain"),
    C("DriverPerformance", "司机绩效", "performance_id",
      "按周期统计的司机绩效。完单量、准时率、货损率、投诉数四项构成综合评分。",
      "performance_id:id, driver_id:str, period:str, completed_orders:int, "
      "on_time_rate:dec, damage_rate:dec, complaint_count:int, "
      "composite_score:num",
      "CALCULATED,REVIEWED,APPEALED,CONFIRMED",
      aliases=("绩效",), group="driver_domain"),
    C("DriverViolation", "司机违章", "violation_id",
      "交通违章与作业违规记录。违章扣分与罚款归属（司机自担或公司承担）随协议不同。",
      "violation_id:id, driver_id:str, violation_type:enum, "
      "violation_at:dt, location:str, penalty_points:int, "
      "fine_amount:dec, fee_payer:enum",
      "RECORDED,CONFIRMED,APPEALED,CLOSED",
      aliases=("违章",), group="driver_domain"),
    C("DriverTraining", "司机培训", "training_id",
      "司机参加的培训。安全、冷链、危险品培训是特定业务的前置条件。",
      "training_id:id, driver_id:str, training_type:enum, "
      "training_at:dt, duration_hours:dec, passed:bool, "
      "cert_issued:str",
      "ENROLLED,ONGOING,COMPLETED,FAILED",
      aliases=("培训",), group="driver_domain"),
    C("DriverBlacklist", "司机黑名单", "blacklist_id",
      "被限制派单的司机名单。原因包括货损、盗货、恶意投诉，进名单后需审批才能解除。",
      "blacklist_id:id, driver_id:str, reason_code:enum, description:txt, "
      "effective_at:dt, released_at:dt, released_by:str, "
      "blocking:bool",
      "LISTED,EFFECTIVE,RELEASED,PERMANENT",
      aliases=("黑名单", "封禁"), group="driver_domain"),
    C("DriverBankAccount", "司机收款账户", "driver_account_id",
      "司机的结算收款账户。账户名必须与身份证一致，属最敏感的财务信息。",
      "driver_account_id:id, driver_id:str, account_name:str, "
      "bank_name:str, account_no:str, verified:bool, "
      "verified_at:dt",
      "PENDING,VERIFIED,REJECTED,FROZEN",
      aliases=("收款账户",), group="driver_domain"),
    C("DriverContract", "司机协议", "driver_contract_id",
      "司机与平台的合作协议。约定结算方式、抽成比例、责任划分，是费用归属判断的依据。",
      "driver_contract_id:id, driver_id:str, contract_no:str, "
      "cooperation_mode:enum, commission_rate:dec, "
      "start_date:date, end_date:date",
      "DRAFT,EFFECTIVE,EXPIRING,TERMINATED",
      aliases=("司机合同",), group="driver_domain"),
    C("DriverTeam", "司机班组", "team_id",
      "司机的组织归属。班组影响调度范围与考核口径，跨班组派单需协调。",
      "team_id:id, team_code:str, team_name:str, leader_driver_id:str, "
      "city:str, member_count:int, vehicle_count:int",
      "ACTIVE,MERGED,DISSOLVED", aliases=("班组", "车队"), group="driver_domain"),
    C("DriverCheckin", "出车签到", "checkin_id",
      "司机出车前的签到与车况自检。未签到不得接单，自检发现问题须报修。",
      "checkin_id:id, driver_id:str, vehicle_id:str, checkin_at:dt, "
      "self_check_items:json, self_check_result:enum, "
      "geo_code:str",
      "CHECKED_IN,ABNORMAL,BLOCKED", aliases=("出车签到",), group="driver_domain"),
    C("DriverHealthCheck", "司机健康检查", "health_check_id",
      "司机定期健康检查记录。长途与危险品运输对健康有额外要求。",
      "health_check_id:id, driver_id:str, check_date:date, "
      "check_items:json, result:enum, next_due_date:date, "
      "hospital:str",
      "SCHEDULED,PASSED,FAILED,EXPIRED",
      aliases=("体检",), group="driver_domain"),
    C("DriverReward", "司机奖励", "reward_id",
      "司机因优质服务或超额完成获得的奖励。奖励规则与发放状态影响司机留存。",
      "reward_id:id, driver_id:str, reward_type:enum, amount:dec, "
      "reason:txt, granted_at:dt, paid:bool",
      "GRANTED,APPROVED,PAID,CANCELLED",
      aliases=("奖励", "奖金"), group="driver_domain"),
    C("DriverAvailability", "司机可用性", "availability_id",
      "司机的实时可用状态。在线、空闲、忙碌、休息四态决定能否被派单。",
      "availability_id:id, driver_id:str, availability:enum, "
      "current_waybill_id:str, updated_at:dt, busy_until:dt",
      "ONLINE,IDLE,BUSY,OFFLINE",
      aliases=("可用状态", "在线状态"), group="driver_domain"),
    C("DriverRoutePreference", "司机线路偏好", "preference_id",
      "司机的常跑线路与回避区域。偏好匹配度是派单成功率的重要因子。",
      "preference_id:id, driver_id:str, route_code:str, "
      "preference_level:enum, avoid_areas:json, "
      "updated_at:dt",
      "ACTIVE,PAUSED,EXPIRED",
      aliases=("线路偏好",), group="driver_domain"),
]

# ────────────────────────────── 承运商域 ──────────────────────────────
_carrier = [
    C("CarrierProfile", "承运商档案", "carrier_profile_id",
      "外部承运商的完整档案。注册资本、成立年限与主营区域决定可承接的业务范围。",
      "carrier_profile_id:id, carrier_id:str, unified_social_code:str, "
      "registered_capital:dec, found_date:date, main_regions:json, "
      "employee_count:int",
      "DRAFT,VERIFIED,ACTIVE,SUSPENDED",
      aliases=("外协档案",), group="carrier_domain"),
    C("CarrierQualification", "承运资质", "carrier_qualification_id",
      "承运商的道路运输经营许可证。经营范围决定可承接的货类，无资质承接属违规。",
      "carrier_qualification_id:id, carrier_id:str, cert_no:str, "
      "business_scope:str, issue_date:date, expiry_date:date, "
      "authority:str",
      "VALID,EXPIRING,EXPIRED,REVOKED",
      aliases=("资质",), group="carrier_domain"),
    C("CarrierEvaluation", "承运商考核", "evaluation_id",
      "按周期对承运商的综合考核。准时率、货损率、投诉率、配合度四项加权。",
      "evaluation_id:id, carrier_id:str, period:str, on_time_rate:dec, "
      "damage_rate:dec, complaint_rate:dec, cooperation_score:num, "
      "grade:enum",
      "DRAFT,CALCULATED,CONFIRMED,APPEALED",
      aliases=("考核", "评级"), group="carrier_domain"),
    C("CarrierContract", "承运商合同", "carrier_contract_id",
      "与承运商签订的合作合同。约定线路、价格、结算周期与违约条款。",
      "carrier_contract_id:id, carrier_id:str, contract_no:str, "
      "contract_type:enum, start_date:date, end_date:date, "
      "settlement_cycle:enum, sign_at:dt",
      "DRAFT,EFFECTIVE,EXPIRING,TERMINATED,RENEWED",
      aliases=("合作协议",), group="carrier_domain"),
    C("CarrierCapacity", "承运商运力", "carrier_capacity_id",
      "承运商可调用的运力池。车辆数、司机数与覆盖城市决定其能承接的峰值单量。",
      "carrier_capacity_id:id, carrier_id:str, city:str, "
      "vehicle_count:int, driver_count:int, daily_capacity:int, "
      "peak_capacity:int, updated_at:dt",
      "DECLARED,VERIFIED,UNDER_REVIEW",
      aliases=("运力池",), group="carrier_domain"),
    C("CarrierSettlementRule", "承运商结算规则", "carrier_rule_id",
      "承运商的结算口径。按单、按里程、按吨公里三种口径的计费方式完全不同。",
      "carrier_rule_id:id, carrier_id:str, settlement_mode:enum, "
      "unit_price:dec, price_unit:enum, min_charge:dec, "
      "tax_rate:dec, effective_from:date",
      "DRAFT,ACTIVE,EXPIRED",
      aliases=("结算规则",), group="carrier_domain"),
    C("CarrierBankAccount", "承运商收款账户", "carrier_account_id",
      "承运商的结算账户。账户名必须与合同主体一致，否则无法付款。",
      "carrier_account_id:id, carrier_id:str, account_name:str, "
      "bank_name:str, account_no:str, verified:bool",
      "PENDING,VERIFIED,REJECTED,FROZEN",
      aliases=("收款账户",), group="carrier_domain"),
    C("CarrierContact", "承运商联系人", "carrier_contact_id",
      "承运商的对接联系人。调度、结算、异常各有不同对接人，联系方式按角色脱敏。",
      "carrier_contact_id:id, carrier_id:str, contact_role:enum, "
      "contact_name:str, contact_phone:str, email:str, is_primary:bool",
      "ACTIVE,INVALID", aliases=("联系人",), group="carrier_domain"),
    C("CarrierPenalty", "承运商处罚", "carrier_penalty_id",
      "承运商违约的处罚记录。从保证金或结算款中扣减，累计触线会触发暂停合作。",
      "carrier_penalty_id:id, carrier_id:str, waybill_id:str, "
      "penalty_type:enum, amount:dec, reason:txt, "
      "deducted_at:dt, disputed:bool",
      "IMPOSED,DEDUCTED,DISPUTED,REVOKED",
      aliases=("处罚", "扣款"), group="carrier_domain"),
    C("CarrierSuspension", "暂停合作", "suspension_id",
      "承运商被暂停合作的记录。暂停期间不得派单，恢复需重新审核。",
      "suspension_id:id, carrier_id:str, reason_code:enum, "
      "start_at:dt, end_at:dt, resumed_at:dt, approved_by:str",
      "REQUESTED,EFFECTIVE,RESUMED,TERMINATED",
      aliases=("暂停", "停合作"), group="carrier_domain"),
    C("CarrierOnboarding", "承运商准入", "onboarding_id",
      "承运商入驻的准入流程。证照核验、实地考察、试运行三段全过才可正式合作。",
      "onboarding_id:id, carrier_id:str, stage:enum, "
      "documents_verified:bool, site_visited:bool, "
      "trial_status:enum, approved_at:dt",
      "SUBMITTED,REVIEWING,SITE_VISIT,TRIAL,APPROVED,REJECTED",
      aliases=("入驻", "准入"), group="carrier_domain"),
    C("CarrierLiabilityInsurance", "承运人责任险", "carrier_insurance_id",
      "承运商投保的承运人责任险。保额不足的承运商不得承接高货值订单。",
      "carrier_insurance_id:id, carrier_id:str, policy_no:str, "
      "insurer:str, coverage_amount:dec, start_date:date, "
      "end_date:date",
      "QUOTED,EFFECTIVE,EXPIRING,EXPIRED",
      aliases=("责任险",), group="carrier_domain"),
    C("CarrierSubcontractQuota", "分包额度", "quota_id",
      "分配给承运商的业务额度。按月度金额或单量控制，超额须走特批。",
      "quota_id:id, carrier_id:str, period:str, amount_limit:dec, "
      "amount_used:dec, order_limit:int, order_used:int",
      "ACTIVE,EXHAUSTED,FROZEN",
      aliases=("额度",), group="carrier_domain"),
    C("CarrierAudit", "承运商审核", "carrier_audit_id",
      "对承运商的定期或专项审核。审核发现的问题与整改情况决定是否继续合作。",
      "carrier_audit_id:id, carrier_id:str, audit_type:enum, "
      "audit_date:date, findings:json, result:enum, "
      "rectified_at:dt",
      "PLANNED,ONGOING,PASSED,FAILED,FOLLOW_UP",
      aliases=("审核", "稽查"), group="carrier_domain"),
    C("CarrierBlacklist", "承运商黑名单", "carrier_blacklist_id",
      "被永久或长期限制的承运商。原因包括盗货、严重违规、恶意欠款。",
      "carrier_blacklist_id:id, carrier_id:str, reason_code:enum, "
      "description:txt, listed_at:dt, permanent:bool",
      "LISTED,EFFECTIVE,RELEASED",
      aliases=("黑名单",), group="carrier_domain"),
    C("CarrierServiceArea", "承运商服务范围", "service_area_id",
      "承运商覆盖的城市与线路。派单时按服务范围匹配，范围外的承运商不进入候选池。",
      "service_area_id:id, carrier_id:str, city:str, "
      "route_codes:json, coverage_type:enum, effective_from:date",
      "ACTIVE,EXPIRED", aliases=("服务范围",), group="carrier_domain"),
    C("CarrierPriceList", "承运商价格表", "price_list_id",
      "承运商对外报价的价格表。按车型与线路定价，是分包成本的计算基准。",
      "price_list_id:id, carrier_id:str, vehicle_type:str, "
      "route_code:str, unit_price:dec, price_unit:enum, "
      "version:str, effective_from:date",
      "DRAFT,ACTIVE,SUPERSEDED,EXPIRED",
      aliases=("价格表",), group="carrier_domain"),
]

# ────────────────────────────── 分包域 ──────────────────────────────
_subcontract = [
    C("SubcontractQuote", "分包报价", "subcontract_quote_id",
      "向承运商询得的分包报价。同一段路可询多家，取价与时效的平衡点。",
      "subcontract_quote_id:id, waybill_id:str, carrier_id:str, "
      "quoted_amount:dec, valid_until:dt, transit_hours:dec, "
      "quoted_at:dt",
      "REQUESTED,RECEIVED,ACCEPTED,REJECTED,EXPIRED",
      aliases=("外协报价",), group="subcontract_domain"),
    C("SubcontractSegment", "分包段", "segment_link_id",
      "分包覆盖的具体运输段。分段起止点与运单分段一一对应，费用按段分摊。",
      "segment_link_id:id, subcontract_id:str, segment_no:int, "
      "segment_start:str, segment_end:str, distance_km:dec, "
      "allocated_amount:dec",
      "PLANNED,ACTIVE,COMPLETED,ABORTED",
      aliases=("分包段",), group="subcontract_domain"),
    C("SubcontractSettlement", "分包结算", "subcontract_settlement_id",
      "与承运商结算分包费用的单据。必须与运单结算分离，避免收入成本混算。",
      "subcontract_settlement_id:id, subcontract_id:str, period:str, "
      "settle_amount:dec, deduction:dec, status:enum, settled_at:dt",
      "DRAFT,PENDING,CONFIRMED,PAID,DISPUTED",
      aliases=("外协结算",), group="subcontract_domain"),
    C("SubcontractInvoice", "分包发票", "subcontract_invoice_id",
      "承运商开给平台的发票。进项税抵扣依赖它，缺失会导致税负上升。",
      "subcontract_invoice_id:id, subcontract_id:str, invoice_no:str, "
      "amount:dec, tax_rate:dec, received_at:dt, verified:bool",
      "PENDING,RECEIVED,VERIFIED,REJECTED",
      aliases=("进项票",), group="subcontract_domain"),
    C("SubcontractMargin", "分包毛利", "subcontract_margin_id",
      "分包段的收入减成本。负毛利段必须能归因到具体原因，否则视为定价错误。",
      "subcontract_margin_id:id, subcontract_id:str, revenue:dec, "
      "cost:dec, gross_profit:dec, margin_rate:dec, negative_reason:str",
      "CALCULATED,REVIEWED,ADJUSTED",
      aliases=("分包毛利",), group="subcontract_domain"),
    C("SubcontractDispute", "分包争议", "subcontract_dispute_id",
      "与承运商在费用或责任上的争议。争议期间结算挂起，需举证后裁定。",
      "subcontract_dispute_id:id, subcontract_id:str, dispute_type:enum, "
      "claim_amount:dec, description:txt, raised_at:dt, "
      "resolved_at:dt, resolution:txt",
      "RAISED,NEGOTIATING,ESCALATED,RESOLVED,CLOSED",
      aliases=("争议",), group="subcontract_domain"),
    C("SubcontractAdjustment", "分包调整", "adjustment_id",
      "分包费用的调整记录。等候、空驶、货损等都可能触发追加或扣减。",
      "adjustment_id:id, subcontract_id:str, adjust_type:enum, "
      "adjust_amount:dec, reason:txt, applied_at:dt, approved_by:str",
      "REQUESTED,APPROVED,APPLIED,REJECTED",
      aliases=("调整",), group="subcontract_domain"),
    C("SubcontractApproval", "分包审批", "subcontract_approval_id",
      "分包决策的审批记录。超额、超价或首次合作的分包必须走审批。",
      "subcontract_approval_id:id, subcontract_id:str, approval_type:enum, "
      "approver_role:str, decision:enum, comment:txt, decided_at:dt",
      "PENDING,APPROVED,REJECTED,ESCALATED",
      aliases=("分包审批",), group="subcontract_domain"),
    C("SubcontractCancellation", "分包取消", "subcontract_cancel_id",
      "分包取消记录。已发生的空驶或等候费用仍须结算，取消不等于免责。",
      "subcontract_cancel_id:id, subcontract_id:str, cancel_stage:enum, "
      "reason_code:enum, cost_incurred:dec, cancelled_at:dt",
      "REQUESTED,CONFIRMED,REJECTED",
      aliases=("取消分包",), group="subcontract_domain"),
    C("SubcontractPerformance", "分包履约", "performance_id",
      "承运商在分包段上的实际履约表现。准时率与货损率直接影响其后续可分配额度。",
      "performance_id:id, subcontract_id:str, on_time:bool, "
      "damage_occurred:bool, complaint_occurred:bool, "
      "evaluated_at:dt",
      "PENDING,EVALUATED,CONFIRMED",
      aliases=("履约表现",), group="subcontract_domain"),
    C("SubcontractDocument", "分包单据", "document_id",
      "分包相关的单据影像。协议、回单、发票三类单据必须齐全才能结算。",
      "document_id:id, subcontract_id:str, document_type:enum, "
      "file_uri:str, uploaded_at:dt, verified:bool",
      "MISSING,UPLOADED,VERIFIED,REJECTED",
      aliases=("分包单据",), group="subcontract_domain"),
    C("SubcontractTax", "分包税务", "subcontract_tax_id",
      "分包业务的税务处理。进项抵扣、代扣代缴与差额征税三种情形适用条件不同。",
      "subcontract_tax_id:id, subcontract_id:str, tax_mode:enum, "
      "tax_rate:dec, input_tax:dec, is_deductible:bool, "
      "declared_at:dt",
      "PENDING,CALCULATED,DECLARED,ADJUSTED",
      aliases=("分包税",), group="subcontract_domain"),
    C("SubcontractTemplate", "分包模板", "template_id",
      "常用分包段的模板。固定线路的常用承运商与价格，减少重复询价。",
      "template_id:id, template_name:str, carrier_id:str, "
      "origin_code:str, dest_code:str, reference_price:dec, "
      "active:bool",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("模板",), group="subcontract_domain"),
    C("SubcontractAllocation", "费用分摊", "allocation_id",
      "合单或分段场景下把费用分摊到各订单。分摊口径不一致是客户投诉的高频原因。",
      "allocation_id:id, subcontract_id:str, allocation_basis:enum, "
      "allocation_detail:json, allocated_total:dec, allocated_at:dt",
      "DRAFT,CONFIRMED,ADJUSTED",
      aliases=("分摊",), group="subcontract_domain"),
    C("SubcontractConfirmation", "分包确认", "confirmation_id",
      "承运商对分包条件的确认。未确认的分包不得执行，确认后才生成承运任务。",
      "confirmation_id:id, subcontract_id:str, confirmed_by:str, "
      "confirmed_at:dt, confirm_channel:enum, remark:txt",
      "SENT,CONFIRMED,DECLINED,EXPIRED",
      aliases=("确认",), group="subcontract_domain"),
]

# ────────────────────────────── 维保域 ──────────────────────────────
_maintenance = [
    C("MaintenanceOrder", "维保工单", "maintenance_order_id",
      "车辆保养或维修的工单。从报修到完工到费用结算，全过程挂在工单上。",
      "maintenance_order_id:id, vehicle_id:str, order_type:enum, "
      "fault_desc:txt, opened_at:dt, closed_at:dt, "
      "supplier_id:str, total_cost:dec",
      "OPEN,DIAGNOSING,REPAIRING,PENDING_PARTS,COMPLETED,CLOSED",
      aliases=("维保工单", "维修单"), group="maintenance_domain"),
    C("MaintenancePlan", "保养计划", "plan_id",
      "按里程或时间触发的保养计划。计划逾期会加速车辆损耗并影响年检。",
      "plan_id:id, vehicle_id:str, plan_type:enum, trigger_km:dec, "
      "trigger_days:int, last_done_km:dec, last_done_at:date, "
      "next_due_km:dec",
      "PLANNED,DUE,OVERDUE,DONE",
      aliases=("保养计划",), group="maintenance_domain"),
    C("MaintenanceItem", "保养项目", "item_id",
      "保养计划中的具体项目。机油、滤芯、刹车片各有更换周期与工时标准。",
      "item_id:id, plan_id:str, item_code:str, item_name:str, "
      "interval_km:dec, interval_days:int, labor_hours:dec, "
      "part_price:dec",
      "ACTIVE,DEPRECATED", aliases=("保养项",), group="maintenance_domain"),
    C("MaintenanceRecord", "维保记录", "maintenance_record_id",
      "完成的维保事实记录。含更换配件、工时与费用，是车辆健康档案的主体。",
      "maintenance_record_id:id, vehicle_id:str, maintenance_order_id:str, "
      "done_at:dt, odometer_km:dec, items:json, "
      "labor_cost:dec, part_cost:dec",
      "RECORDED,VERIFIED,ARCHIVED",
      aliases=("维保记录",), group="maintenance_domain"),
    C("MaintenancePart", "配件", "part_id",
      "维保使用的配件。原厂与副厂的价格与寿命差异显著，影响后续故障率。",
      "part_id:id, part_code:str, part_name:str, brand:str, "
      "spec:str, unit_price:dec, origin_type:enum, "
      "warranty_days:int",
      "ACTIVE,DISCONTINUED", aliases=("备件",), group="maintenance_domain"),
    C("MaintenanceSupplier", "维修商", "supplier_id",
      "提供维保服务的外部维修商。价格、时效与质保期是选择依据。",
      "supplier_id:id, supplier_name:str, city:str, "
      "service_types:json, rating:num, settlement_mode:enum, "
      "status:enum",
      "ACTIVE,SUSPENDED,TERMINATED",
      aliases=("修理厂",), group="maintenance_domain"),
    C("MaintenanceCost", "维保费用", "cost_id",
      "维保产生的费用。按车辆归集后计算单公里维保成本，是运力成本的重要组成。",
      "cost_id:id, vehicle_id:str, maintenance_order_id:str, "
      "cost_type:enum, amount:dec, paid_at:dt, payer:enum",
      "ACCRUED,CONFIRMED,PAID,ADJUSTED",
      aliases=("维保费用",), group="maintenance_domain"),
    C("MaintenanceReminder", "保养提醒", "reminder_id",
      "推送给司机的保养提醒。提前触达能减少逾期保养带来的故障。",
      "reminder_id:id, vehicle_id:str, reminder_type:enum, "
      "due_at:dt, sent_at:dt, acknowledged:bool",
      "PENDING,SENT,ACKNOWLEDGED,IGNORED",
      aliases=("提醒",), group="maintenance_domain"),
    C("RepairQuote", "维修报价", "repair_quote_id",
      "维修商给出的维修报价。多家比价能显著降低维修成本。",
      "repair_quote_id:id, maintenance_order_id:str, supplier_id:str, "
      "quoted_amount:dec, quote_detail:json, valid_until:dt, "
      "selected:bool",
      "RECEIVED,SELECTED,REJECTED,EXPIRED",
      aliases=("维修报价",), group="maintenance_domain"),
    C("RepairPartsUsage", "配件领用", "usage_id",
      "维修时领用配件的明细。配件出库与工单必须对应，否则会出现账实不符。",
      "usage_id:id, maintenance_order_id:str, part_id:str, "
      "part_quantity:int, unit_price:dec, total_amount:dec, "
      "issued_at:dt",
      "ISSUED,USED,RETURNED",
      aliases=("领料",), group="maintenance_domain"),
    C("TireRecord", "轮胎台账", "tire_id",
      "轮胎的使用与磨损记录。按花纹深度判断更换时机，爆胎是高速事故主要诱因。",
      "tire_id:id, vehicle_id:str, position:str, brand:str, "
      "install_date:date, installed_km:dec, tread_depth_mm:dec, "
      "status:enum",
      "INSTALLED,WEARING,REPLACED,SCRAPPED",
      aliases=("轮胎",), group="maintenance_domain"),
    C("TireChange", "换胎记录", "tire_change_id",
      "轮胎更换记录。换胎原因（磨损/爆胎/扎钉）与里程影响成本分析。",
      "tire_change_id:id, vehicle_id:str, tire_id:str, "
      "change_type:enum, change_at:dt, odometer_km:dec, cost:dec",
      "RECORDED,VERIFIED", aliases=("换胎",), group="maintenance_domain"),
    C("BreakdownRecord", "抛锚记录", "breakdown_id",
      "车辆途中抛锚记录。抛锚导致的延误与转运成本须归因到车辆状况。",
      "breakdown_id:id, vehicle_id:str, waybill_id:str, "
      "breakdown_at:dt, location:str, cause:enum, "
      "resolved_at:dt, transfer_cost:dec",
      "REPORTED,RESCUING,RESOLVED,CLOSED",
      aliases=("抛锚", "坏车"), group="maintenance_domain"),
    C("RoadRescue", "道路救援", "rescue_id",
      "道路救援服务记录。救援响应时长直接决定延误规模。",
      "rescue_id:id, breakdown_id:str, rescue_provider:str, "
      "dispatched_at:dt, arrived_at:dt, rescued_at:dt, "
      "rescue_cost:dec",
      "DISPATCHED,EN_ROUTE,ON_SITE,COMPLETED,CANCELLED",
      aliases=("救援",), group="maintenance_domain"),
    C("VehicleAnnualReview", "车辆年审", "annual_review_id",
      "车辆营运证年审记录。年审未过车辆须停运整改。",
      "annual_review_id:id, vehicle_id:str, review_year:int, "
      "submitted_at:dt, result:enum, next_due_date:date, "
      "review_org:str",
      "SUBMITTED,PASSED,FAILED,RECTIFYING",
      aliases=("年审",), group="maintenance_domain"),
    C("MaintenanceWarranty", "维保质保", "warranty_id",
      "维保项目的质保承诺。质保期内同部位再故障应免费返修。",
      "warranty_id:id, maintenance_order_id:str, warranty_days:int, "
      "warranty_start_date:date, warranty_end_date:date, claim_count:int",
      "ACTIVE,EXPIRED,CLAIMED",
      aliases=("质保",), group="maintenance_domain"),
]

# ────────────────────────────── 能源域 ──────────────────────────────
_fuel = [
    C("FuelCard", "油卡", "fuel_card_id",
      "分配给车辆或司机的加油卡。卡号与限额绑定，异常消费须人工核查。",
      "fuel_card_id:id, card_no:str, holder_id:str, "
      "holder_type:enum, monthly_limit:dec, balance:dec, status:enum",
      "ISSUED,ACTIVE,FROZEN,LOST,CANCELLED",
      aliases=("加油卡",), group="fuel_domain"),
    C("FuelTransaction", "加油交易", "fuel_transaction_id",
      "一次加油的明细。升数、单价与金额是油耗核算与异常检测的原始数据。",
      "fuel_transaction_id:id, fuel_card_id:str, vehicle_id:str, "
      "station_id:str, litres:dec, unit_price:dec, amount:dec, "
      "fuel_type:enum, transacted_at:dt",
      "RECORDED,VERIFIED,ANOMALY,DISPUTED",
      aliases=("加油",), group="fuel_domain"),
    C("FuelPrice", "油价", "fuel_price_id",
      "按地区与油品记录的油价。油价波动直接影响运输成本与报价有效期。",
      "fuel_price_id:id, region_code:str, fuel_type:enum, "
      "price:dec, effective_date:date, price_source:str",
      "ACTIVE,SUPERSEDED", aliases=("油价",), group="fuel_domain"),
    C("FuelConsumption", "油耗", "consumption_id",
      "按周期统计的百公里油耗。油耗偏离基线通常指向驾驶习惯或车辆状况问题。",
      "consumption_id:id, vehicle_id:str, period:str, total_km:dec, "
      "total_litres:dec, consumption_per_100km:dec, "
      "baseline_per_100km:dec, deviation_pct:dec",
      "CALCULATED,REVIEWED,FLAGGED",
      aliases=("油耗",), group="fuel_domain"),
    C("FuelStation", "加油站", "station_id",
      "合作加油站。网点密度影响司机加油便利性，也影响油卡使用率。",
      "station_id:id, station_name:str, brand:str, "
      "region_code:str, address:str, cooperation:enum",
      "ACTIVE,SUSPENDED,TERMINATED",
      aliases=("加油站",), group="fuel_domain"),
    C("FuelSubsidy", "油补", "subsidy_id",
      "给司机或承运商的燃油补贴。补贴口径（按里程/按单/按升）必须在协议中明确。",
      "subsidy_id:id, recipient_id:str, period:str, "
      "subsidy_mode:enum, amount:dec, granted_at:dt, paid:bool",
      "CALCULATED,APPROVED,PAID,CANCELLED",
      aliases=("油补", "燃油补贴"), group="fuel_domain"),
    C("FuelQuota", "油卡额度", "quota_id",
      "油卡的周期额度设置。额度用尽后需充值或走特批，避免途中无油。",
      "quota_id:id, fuel_card_id:str, period:str, "
      "quota_amount:dec, used_amount:dec, reset_at:dt",
      "ACTIVE,EXHAUSTED,RESET",
      aliases=("额度",), group="fuel_domain"),
    C("ChargingRecord", "充电记录", "charging_id",
      "新能源车的充电记录。电量、时长与费用决定电车的单公里能源成本。",
      "charging_id:id, vehicle_id:str, station_id:str, "
      "kwh:dec, unit_price:dec, amount:dec, "
      "started_at:dt, ended_at:dt",
      "RECORDED,VERIFIED,ANOMALY",
      aliases=("充电",), group="fuel_domain"),
    C("ChargingStation", "充电站", "charging_station_id",
      "充电站信息。桩型、功率与排队情况影响电车补能时间，进而影响时效。",
      "charging_station_id:id, station_name:str, operator:str, "
      "pile_type:enum, power_kw:dec, pile_count:int, "
      "region_code:str",
      "ACTIVE,MAINTENANCE,CLOSED",
      aliases=("充电站",), group="fuel_domain"),
    C("FuelAnomaly", "加油异常", "anomaly_id",
      "加油行为异常的识别记录。超量、频次异常或非合作站加油都会触发。",
      "anomaly_id:id, fuel_transaction_id:str, vehicle_id:str, "
      "anomaly_type:enum, severity:enum, detected_at:dt, "
      "handled_result:enum",
      "DETECTED,INVESTIGATING,CONFIRMED,EXONERATED",
      aliases=("加油异常",), group="fuel_domain"),
    C("FuelAllocation", "油耗分摊", "allocation_id",
      "把燃油成本分摊到具体运单。合单或混装场景必须按里程或重量分摊。",
      "allocation_id:id, waybill_id:str, vehicle_id:str, "
      "allocated_litres:dec, allocated_amount:dec, "
      "basis:enum",
      "CALCULATED,CONFIRMED,ADJUSTED",
      aliases=("油耗分摊",), group="fuel_domain"),
    C("EnergyCost", "能源成本", "energy_cost_id",
      "按车辆与周期归集的能源成本。与里程台账结合可算出单公里能源成本。",
      "energy_cost_id:id, vehicle_id:str, period:str, "
      "energy_type:enum, amount:dec, per_km_cost:dec, "
      "settled_at:dt",
      "ACCRUED,CONFIRMED,SETTLED",
      aliases=("能源成本",), group="fuel_domain"),
    C("EmissionRecord", "排放记录", "emission_id",
      "车辆排放检测记录。国四以下车辆在多数城市受限行，排放超标须整改。",
      "emission_id:id, vehicle_id:str, standard:enum, "
      "tested_at:date, result:enum, tested_by:str",
      "TESTED,PASSED,FAILED,EXPIRED",
      aliases=("排放",), group="fuel_domain"),
    C("CarbonFootprint", "碳足迹", "footprint_id",
      "运单或车辆周期的碳排放核算。大客户绿色物流需求正在成为投标门槛。",
      "footprint_id:id, waybill_id:str, vehicle_id:str, "
      "distance_km:dec, emission_kg:dec, "
      "calculation_method:enum, calculated_at:dt",
      "CALCULATED,VERIFIED,REPORTED",
      aliases=("碳排",), group="fuel_domain"),
    C("FuelCardRecharge", "油卡充值", "recharge_id",
      "油卡充值记录。充值金额与到账状态影响司机途中补能能力。",
      "recharge_id:id, fuel_card_id:str, amount:dec, "
      "channel:enum, recharged_at:dt, arrived:bool",
      "PENDING,PAID,ARRIVED,FAILED",
      aliases=("充值",), group="fuel_domain"),
]

_links = [
    L("vehicle_has_profile", "车辆档案", "Vehicle", "VehicleProfile", "one_to_one"),
    L("vehicle_has_registration", "车辆登记", "Vehicle", "VehicleRegistration", "one_to_one"),
    L("vehicle_of_type", "车辆车型", "Vehicle", "VehicleType", "many_to_one"),
    L("vehicle_has_capacity", "核定载重", "Vehicle", "VehicleCapacity", "one_to_one"),
    L("vehicle_has_dimension", "车厢尺寸", "Vehicle", "VehicleDimension", "one_to_one"),
    L("vehicle_undergoes_inspection", "车辆年检", "Vehicle", "VehicleInspection", "one_to_many", temporal=True),
    L("vehicle_holds_license", "道路运输证", "Vehicle", "VehicleLicense", "one_to_one"),
    L("vehicle_binds_device", "设备绑定", "Vehicle", "VehicleDeviceBinding", "one_to_many", temporal=True),
    L("vehicle_has_fault", "车辆故障", "Vehicle", "VehicleFault", "one_to_many", temporal=True),
    L("vehicle_reports_telematics", "车联网数据", "Vehicle", "VehicleTelematics", "one_to_many", temporal=True),
    L("vehicle_subject_to_restriction", "车辆限行", "Vehicle", "VehicleRestriction", "one_to_many"),
    L("vehicle_replaced_by_substitute", "替车", "Vehicle", "VehicleSubstitute", "one_to_many", temporal=True),
    L("vehicle_has_ownership", "车辆归属", "Vehicle", "VehicleOwnership", "one_to_many", temporal=True),
    L("vehicle_scrapped_via", "车辆报废", "Vehicle", "VehicleScrap", "one_to_one"),
    L("vehicle_covered_by_policy", "车辆保险", "Vehicle", "VehicleInsurancePolicy", "one_to_many"),
    L("vehicle_keeps_mileage_log", "里程台账", "Vehicle", "VehicleMileageLog", "one_to_many", temporal=True),
    L("vehicle_has_photo", "车辆照片", "Vehicle", "VehiclePhoto", "one_to_many"),
    L("vehicle_passes_compliance", "合规校验", "Vehicle", "VehicleComplianceCheck", "one_to_many", temporal=True),

    L("driver_has_profile", "司机档案", "Driver", "DriverProfile", "one_to_one"),
    L("driver_holds_license", "驾驶证", "Driver", "DriverLicense", "one_to_one"),
    L("driver_holds_qualification", "从业资格证", "Driver", "DriverQualificationCert", "one_to_many"),
    L("driver_assigned_shift", "司机排班", "Driver", "DriverShift", "one_to_many", temporal=True),
    L("driver_has_attendance", "司机考勤", "Driver", "DriverAttendance", "one_to_many", temporal=True),
    L("driver_measured_by_performance", "司机绩效", "Driver", "DriverPerformance", "one_to_many", temporal=True),
    L("driver_committed_violation", "司机违章", "Driver", "DriverViolation", "one_to_many", temporal=True),
    L("driver_takes_training", "司机培训", "Driver", "DriverTraining", "one_to_many", temporal=True),
    L("driver_listed_in_blacklist", "司机黑名单", "Driver", "DriverBlacklist", "one_to_many"),
    L("driver_owns_account", "收款账户", "Driver", "DriverBankAccount", "one_to_one"),
    L("driver_bound_by_contract", "司机协议", "Driver", "DriverContract", "one_to_many"),
    L("driver_belongs_to_team", "司机班组", "Driver", "DriverTeam", "many_to_one"),
    L("driver_checks_in", "出车签到", "Driver", "DriverCheckin", "one_to_many", temporal=True),
    L("driver_has_health_check", "健康检查", "Driver", "DriverHealthCheck", "one_to_many", temporal=True),
    L("driver_receives_reward", "司机奖励", "Driver", "DriverReward", "one_to_many", temporal=True),
    L("driver_has_availability", "可用状态", "Driver", "DriverAvailability", "one_to_one", temporal=True),
    L("driver_declares_preference", "线路偏好", "Driver", "DriverRoutePreference", "one_to_many"),

    L("carrier_has_profile", "承运商档案", "Carrier", "CarrierProfile", "one_to_one"),
    L("carrier_holds_qualification", "承运资质", "Carrier", "CarrierQualification", "one_to_many"),
    L("carrier_evaluated_by", "承运商考核", "Carrier", "CarrierEvaluation", "one_to_many", temporal=True),
    L("carrier_bound_by_contract", "承运商合同", "Carrier", "CarrierContract", "one_to_many"),
    L("carrier_has_capacity", "承运商运力", "Carrier", "CarrierCapacity", "one_to_many", temporal=True),
    L("carrier_settles_by_rule", "结算规则", "Carrier", "CarrierSettlementRule", "one_to_many"),
    L("carrier_owns_account", "收款账户", "Carrier", "CarrierBankAccount", "one_to_one"),
    L("carrier_has_contact", "承运商联系人", "Carrier", "CarrierContact", "one_to_many"),
    L("carrier_penalized_by", "承运商处罚", "Carrier", "CarrierPenalty", "one_to_many", temporal=True),
    L("carrier_suspended_via", "暂停合作", "Carrier", "CarrierSuspension", "one_to_many", temporal=True),
    L("carrier_onboarded_via", "承运商准入", "Carrier", "CarrierOnboarding", "one_to_one"),
    L("carrier_covered_by_insurance", "责任险", "Carrier", "CarrierLiabilityInsurance", "one_to_many"),
    L("carrier_allocated_quota", "分包额度", "Carrier", "CarrierSubcontractQuota", "one_to_many", temporal=True),
    L("carrier_audited_by", "承运商审核", "Carrier", "CarrierAudit", "one_to_many", temporal=True),
    L("carrier_listed_in_blacklist", "承运商黑名单", "Carrier", "CarrierBlacklist", "one_to_many"),
    L("carrier_covers_area", "服务范围", "Carrier", "CarrierServiceArea", "one_to_many"),
    L("carrier_publishes_price_list", "承运商价格表", "Carrier", "CarrierPriceList", "one_to_many", temporal=True),

    L("subcontract_quoted_via", "分包报价", "SubcontractOrder", "SubcontractQuote", "one_to_many"),
    L("subcontract_covers_segment", "分包段", "SubcontractOrder", "SubcontractSegment", "one_to_many"),
    L("subcontract_settled_by", "分包结算", "SubcontractOrder", "SubcontractSettlement", "one_to_one"),
    L("subcontract_invoiced_by", "分包发票", "SubcontractOrder", "SubcontractInvoice", "one_to_many"),
    L("subcontract_measures_margin", "分包毛利", "SubcontractOrder", "SubcontractMargin", "one_to_one"),
    L("subcontract_raises_dispute", "分包争议", "SubcontractOrder", "SubcontractDispute", "one_to_many"),
    L("subcontract_adjusted_by", "分包调整", "SubcontractOrder", "SubcontractAdjustment", "one_to_many"),
    L("subcontract_requires_approval", "分包审批", "SubcontractOrder", "SubcontractApproval", "one_to_many"),
    L("subcontract_cancelled_by", "分包取消", "SubcontractOrder", "SubcontractCancellation", "one_to_one"),
    L("subcontract_evaluated_by", "分包履约", "SubcontractOrder", "SubcontractPerformance", "one_to_one"),
    L("subcontract_has_document", "分包单据", "SubcontractOrder", "SubcontractDocument", "one_to_many"),
    L("subcontract_taxed_by", "分包税务", "SubcontractOrder", "SubcontractTax", "one_to_one"),
    L("subcontract_from_template", "分包模板", "SubcontractOrder", "SubcontractTemplate", "many_to_one"),
    L("subcontract_allocated_by", "费用分摊", "SubcontractOrder", "SubcontractAllocation", "one_to_many"),
    L("subcontract_confirmed_by", "分包确认", "SubcontractOrder", "SubcontractConfirmation", "one_to_one"),

    L("vehicle_has_maintenance_order", "维保工单", "Vehicle", "MaintenanceOrder", "one_to_many", temporal=True),
    L("vehicle_follows_maintenance_plan", "保养计划", "Vehicle", "MaintenancePlan", "one_to_many"),
    L("maintenance_plan_has_item", "保养项目", "MaintenancePlan", "MaintenanceItem", "one_to_many"),
    L("vehicle_has_maintenance_record", "维保记录", "Vehicle", "MaintenanceRecord", "one_to_many", temporal=True),
    L("maintenance_uses_part", "维保配件", "MaintenanceOrder", "MaintenancePart", "many_to_many"),
    L("maintenance_served_by_supplier", "维修商", "MaintenanceOrder", "MaintenanceSupplier", "many_to_one"),
    L("maintenance_accrues_cost", "维保费用", "Vehicle", "MaintenanceCost", "one_to_many", temporal=True),
    L("vehicle_receives_reminder", "保养提醒", "Vehicle", "MaintenanceReminder", "one_to_many", temporal=True),
    L("maintenance_has_quote", "维修报价", "MaintenanceOrder", "RepairQuote", "one_to_many"),
    L("maintenance_consumes_parts", "配件领用", "MaintenanceOrder", "RepairPartsUsage", "one_to_many"),
    L("vehicle_keeps_tire_record", "轮胎台账", "Vehicle", "TireRecord", "one_to_many"),
    L("vehicle_has_tire_change", "换胎记录", "Vehicle", "TireChange", "one_to_many", temporal=True),
    L("vehicle_breaks_down", "抛锚记录", "Vehicle", "BreakdownRecord", "one_to_many", temporal=True),
    L("breakdown_triggers_rescue", "道路救援", "BreakdownRecord", "RoadRescue", "one_to_one"),
    L("vehicle_annual_review", "车辆年审", "Vehicle", "VehicleAnnualReview", "one_to_many", temporal=True),
    L("maintenance_has_warranty", "维保质保", "MaintenanceOrder", "MaintenanceWarranty", "one_to_one"),

    L("vehicle_bound_to_fuel_card", "油卡绑定", "Vehicle", "FuelCard", "one_to_many"),
    L("fuel_card_has_transaction", "加油交易", "FuelCard", "FuelTransaction", "one_to_many", temporal=True),
    L("fuel_transaction_priced_by", "油价", "FuelTransaction", "FuelPrice", "many_to_one"),
    L("vehicle_measures_consumption", "油耗", "Vehicle", "FuelConsumption", "one_to_many", temporal=True),
    L("fuel_card_used_at_station", "加油站", "FuelCard", "FuelStation", "many_to_many"),
    L("driver_receives_subsidy", "油补", "Driver", "FuelSubsidy", "one_to_many", temporal=True),
    L("fuel_card_has_quota", "油卡额度", "FuelCard", "FuelQuota", "one_to_many"),
    L("vehicle_has_charging_record", "充电记录", "Vehicle", "ChargingRecord", "one_to_many", temporal=True),
    L("charging_record_at_station", "充电站", "ChargingRecord", "ChargingStation", "many_to_one"),
    L("fuel_transaction_flagged_anomaly", "加油异常", "FuelTransaction", "FuelAnomaly", "one_to_one"),
    L("waybill_allocates_fuel", "油耗分摊", "Waybill", "FuelAllocation", "one_to_many"),
    L("vehicle_accrues_energy_cost", "能源成本", "Vehicle", "EnergyCost", "one_to_many", temporal=True),
    L("vehicle_has_emission_record", "排放记录", "Vehicle", "EmissionRecord", "one_to_many", temporal=True),
    L("waybill_has_carbon_footprint", "碳足迹", "Waybill", "CarbonFootprint", "one_to_one"),
    L("fuel_card_recharged_via", "油卡充值", "FuelCard", "FuelCardRecharge", "one_to_many", temporal=True),
]

DOMAINS.extend([
    {"id": "vehicle_domain", "label": "车辆域", "classes": _vehicle},
    {"id": "driver_domain", "label": "司机域", "classes": _driver},
    {"id": "carrier_domain", "label": "承运商域", "classes": _carrier},
    {"id": "subcontract_domain", "label": "分包域", "classes": _subcontract},
    {"id": "maintenance_domain", "label": "维保域", "classes": _maintenance},
    {"id": "fuel_domain", "label": "能源域", "classes": _fuel},
])

CLASSES = [c for d in DOMAINS for c in d["classes"]]
LINKS = _links
