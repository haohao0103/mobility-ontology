"""按业务域扩展本体规模，生成类、关系与对象类型组。

用法：
    python3 tools/expand_ontology.py            # 只打印规模，不写文件
    python3 tools/expand_ontology.py --write    # 写回 freight/semantic_layer.json

扩展是可重入的：脚本按确定性 ID 先移除上次生成的条目，再重新追加，
不会动到手写类、规则、动作与数据源映射。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYER = ROOT / "freight" / "semantic_layer.json"

# 类名后缀与中文标签：每个业务域用同一组“通用知识切面”补齐规模。
ASPECTS = [
    ("Master", "主数据"),
    ("Detail", "明细"),
    ("Change", "变更"),
    ("Approval", "审批"),
    ("Task", "任务"),
    ("Record", "记录"),
    ("Snapshot", "快照"),
    ("Template", "模板"),
    ("RuleConfig", "规则配置"),
    ("AuditLog", "审计日志"),
]

# 新增聚合根不重复生成“主数据”切面。
NEW_ROOT_ASPECTS = ASPECTS[1:]

ASPECT_STATUSES = {
    "Approval": ["PENDING", "APPROVED", "REJECTED"],
    "Change": ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED"],
    "Task": ["PENDING", "RUNNING", "DONE", "CANCELLED"],
}
DEFAULT_STATUSES = ["ACTIVE", "INACTIVE"]

DOMAINS = [
    {
        "key": "order", "cn": "订单", "prefix": "Order", "root": "Order",
        "specific": [
            ("OrderLine", "订单行"),
            ("OrderSource", "订单来源"),
            ("OrderChannel", "下单渠道"),
            ("OrderTag", "订单标签"),
            ("OrderAttachment", "订单附件"),
            ("OrderCancellation", "订单取消"),
            ("OrderAmendment", "订单修改"),
            ("OrderConfirmation", "订单确认"),
        ],
    },
    {
        "key": "waybill", "cn": "运单", "prefix": "Waybill", "root": "Waybill",
        "specific": [
            ("WaybillSegment", "运单分段"),
            ("WaybillAssignment", "运单指派"),
            ("WaybillReceipt", "运单回单"),
            ("WaybillMilestone", "运单里程碑"),
            ("WaybillException", "运单异常记录"),
            ("WaybillClosure", "运单关单"),
            ("WaybillLabel", "运单标签"),
            ("WaybillNote", "运单备注"),
        ],
    },
    {
        "key": "cargo", "cn": "货物", "prefix": "Cargo", "root": "Cargo",
        "specific": [
            ("CargoItem", "货物明细"),
            ("CargoPackage", "包装单元"),
            ("CargoBatch", "货物批次"),
            ("CargoCategory", "货物分类"),
            ("CargoHazard", "危险品属性"),
            ("CargoTemperature", "温控要求"),
            ("CargoWeightRecord", "重量记录"),
            ("CargoPhoto", "货物照片"),
        ],
    },
    {
        "key": "stop", "cn": "装卸", "prefix": "Stop", "root": "Stop",
        "specific": [
            ("StopOperation", "装卸作业"),
            ("StopWindow", "装卸时间窗"),
            ("StopContact", "装卸联系人"),
            ("StopEquipment", "装卸设备"),
            ("StopChecklist", "装卸检查项"),
            ("StopSignature", "装卸签字"),
            ("StopDelay", "装卸延误"),
            ("StopPhoto", "装卸照片"),
        ],
    },
    {
        "key": "vehicle", "cn": "车辆", "prefix": "Vehicle", "root": "Vehicle",
        "specific": [
            ("VehicleProfile", "车辆档案"),
            ("VehicleTypeConfig", "车型配置"),
            ("VehiclePlate", "车牌"),
            ("VehicleCapacity", "载重参数"),
            ("VehicleDevice", "车载设备"),
            ("VehicleInspection", "车辆年检"),
            ("VehicleInsurance", "车辆保险"),
            ("VehicleStatusLog", "车辆状态日志"),
        ],
    },
    {
        "key": "driver", "cn": "司机", "prefix": "Driver", "root": "Driver",
        "specific": [
            ("DriverProfile", "司机档案"),
            ("DriverLicense", "驾驶证"),
            ("DriverQualification", "从业资格"),
            ("DriverShift", "司机班次"),
            ("DriverAttendance", "司机考勤"),
            ("DriverRating", "司机评分"),
            ("DriverTraining", "司机培训"),
            ("DriverBlacklist", "司机黑名单"),
        ],
    },
    {
        "key": "carrier", "cn": "承运商", "prefix": "Carrier", "root": "Carrier",
        "specific": [
            ("CarrierProfile", "承运商档案"),
            ("CarrierContract", "承运商合同"),
            ("CarrierQualification", "承运商资质"),
            ("CarrierCapacity", "承运商运力"),
            ("CarrierRating", "承运商评级"),
            ("CarrierSettlement", "承运商结算"),
            ("CarrierContact", "承运商联系人"),
            ("CarrierBlacklist", "承运商黑名单"),
        ],
    },
    {
        "key": "subcontract", "cn": "分包", "prefix": "Subcontract",
        "root": "SubcontractOrder",
        "specific": [
            ("SubcontractQuote", "分包报价"),
            ("SubcontractAssignment", "分包指派"),
            ("SubcontractAcceptance", "分包接单"),
            ("SubcontractDelivery", "分包交付"),
            ("SubcontractFee", "分包费用"),
            ("SubcontractDispute", "分包争议"),
            ("SubcontractDocument", "分包单证"),
            ("SubcontractSettlement", "分包结算"),
        ],
    },
    {
        "key": "quote", "cn": "报价", "prefix": "Quote", "root": "Quote",
        "specific": [
            ("QuoteVersion", "报价版本"),
            ("QuoteRequest", "询价请求"),
            ("QuoteResponse", "报价响应"),
            ("QuoteDiscount", "报价折扣"),
            ("QuoteSurcharge", "报价附加费"),
            ("QuoteApprovalRecord", "报价审批记录"),
            ("QuoteExpiry", "报价有效期"),
            ("QuoteHistory", "报价历史"),
        ],
    },
    {
        "key": "pricing_rule", "cn": "计价", "prefix": "Pricing",
        "root": "PricingRule",
        "specific": [
            ("PricingRuleVersion", "计价规则版本"),
            ("PricingCondition", "计价条件"),
            ("PricingTier", "计价阶梯"),
            ("PricingSurchargeRule", "附加费规则"),
            ("PricingDiscountRule", "折扣规则"),
            ("PricingVehicleFactor", "车型系数"),
            ("PricingDistanceFactor", "里程系数"),
            ("PricingTimeFactor", "时段系数"),
        ],
    },
    {
        "key": "fee", "cn": "费用", "prefix": "Fee", "root": "FeeItem",
        "specific": [
            ("FeeAdjustment", "费用调整"),
            ("FeeWaiver", "费用减免"),
            ("FeeDispute", "费用争议"),
            ("FeeAllocation", "费用分摊"),
            ("FeeTax", "费用税额"),
            ("FeeCurrency", "费用币种"),
            ("FeeReceipt", "费用凭证"),
            ("FeeAudit", "费用审计"),
        ],
    },
    {
        "key": "settlement", "cn": "结算", "prefix": "Settlement",
        "root": "Settlement",
        "specific": [
            ("SettlementBatch", "结算批次"),
            ("SettlementPeriod", "结算周期"),
            ("SettlementLine", "结算明细行"),
            ("SettlementAdjustment", "结算调整"),
            ("SettlementPayment", "结算付款"),
            ("SettlementReconciliation", "结算对账"),
            ("SettlementDispute", "结算争议"),
            ("SettlementAudit", "结算审计"),
        ],
    },
    {
        "key": "invoice", "cn": "发票", "prefix": "Invoice", "root": "Invoice",
        "specific": [
            ("InvoiceLine", "发票行"),
            ("InvoiceRequest", "开票申请"),
            ("InvoiceTitle", "发票抬头"),
            ("InvoiceRed", "红冲发票"),
            ("InvoiceDelivery", "发票交付"),
            ("InvoiceVerification", "发票查验"),
            ("InvoiceTaxRate", "税率"),
            ("InvoiceAttachment", "发票附件"),
        ],
    },
    {
        "key": "evidence", "cn": "证据", "prefix": "Evidence", "root": "Evidence",
        "specific": [
            ("EvidenceFile", "证据文件"),
            ("EvidenceChecksum", "证据校验"),
            ("EvidenceSource", "证据来源"),
            ("EvidenceVersion", "证据版本"),
            ("EvidenceChain", "证据链"),
            ("EvidenceReview", "证据复核"),
            ("EvidenceArchive", "证据归档"),
            ("EvidenceAccessLog", "证据访问日志"),
        ],
    },
    {
        "key": "track", "cn": "轨迹", "prefix": "Track", "root": "Track",
        "specific": [
            ("TrackPoint", "轨迹点"),
            ("TrackSegment", "轨迹段"),
            ("TrackGeofence", "轨迹围栏"),
            ("TrackDeviation", "轨迹偏移"),
            ("TrackGap", "轨迹断点"),
            ("TrackSpeed", "速度记录"),
            ("TrackMileage", "里程记录"),
            ("TrackPlayback", "轨迹回放"),
        ],
    },
    {
        "key": "exception", "cn": "异常", "prefix": "Exception",
        "root": "ExceptionTicket",
        "specific": [
            ("ExceptionCategory", "异常分类"),
            ("ExceptionReport", "异常上报"),
            ("ExceptionAssignment", "异常派单"),
            ("ExceptionResolution", "异常处理"),
            ("ExceptionCompensation", "异常赔付"),
            ("ExceptionEscalation", "异常升级"),
            ("ExceptionTimeline", "异常时间线"),
            ("ExceptionAttachment", "异常附件"),
        ],
    },
    {
        "key": "warehouse", "cn": "仓储", "prefix": "Warehouse",
        "root": "Warehouse", "root_new": ("Warehouse", "仓储"),
        "specific": [
            ("WarehouseZone", "库区"),
            ("WarehouseDock", "月台"),
            ("WarehouseSlot", "库位"),
            ("WarehouseInventory", "库存"),
            ("WarehouseInbound", "入库单"),
            ("WarehouseOutbound", "出库单"),
            ("WarehouseTransfer", "移库单"),
            ("WarehouseCount", "盘点单"),
        ],
    },
    {
        "key": "route", "cn": "线路", "prefix": "Route",
        "root": "Route", "root_new": ("Route", "线路"),
        "specific": [
            ("RoutePlan", "线路计划"),
            ("RouteLeg", "线路分段"),
            ("RouteCost", "线路成本"),
            ("RouteSchedule", "线路班次"),
            ("RouteRestriction", "线路限制"),
            ("RouteOptimization", "线路优化"),
            ("RouteHistory", "线路历史"),
            ("RouteStop", "线路站点"),
        ],
    },
    {
        "key": "geo", "cn": "地理", "prefix": "Geo",
        "root": "GeoZone", "root_new": ("GeoZone", "地理区域"),
        "specific": [
            ("GeoProvince", "省份"),
            ("GeoCity", "城市"),
            ("GeoDistrict", "区县"),
            ("GeoAddress", "地址"),
            ("GeoPoint", "坐标点"),
            ("GeoBoundary", "边界"),
            ("GeoFence", "地理围栏"),
            ("GeoDistance", "距离记录"),
        ],
    },
    {
        "key": "contract", "cn": "合同", "prefix": "Contract",
        "root": "Contract", "root_new": ("Contract", "合同"),
        "specific": [
            ("ContractParty", "合同主体"),
            ("ContractTerm", "合同条款"),
            ("ContractPrice", "合同价格"),
            ("ContractVersion", "合同版本"),
            ("ContractRenewal", "合同续签"),
            ("ContractTermination", "合同终止"),
            ("ContractAttachment", "合同附件"),
            ("ContractAudit", "合同审计"),
        ],
    },
    {
        "key": "insurance", "cn": "保险", "prefix": "Insurance",
        "root": "InsurancePolicy", "root_new": ("InsurancePolicy", "保险保单"),
        "specific": [
            ("InsuranceProduct", "保险产品"),
            ("InsuranceCoverage", "保险责任"),
            ("InsurancePremium", "保费"),
            ("InsuranceClaim", "保险理赔"),
            ("InsuranceClaimDocument", "理赔单证"),
            ("InsuranceRenewal", "保险续保"),
            ("InsuranceBeneficiary", "受益人"),
            ("InsuranceAudit", "保险审计"),
        ],
    },
    {
        "key": "customs", "cn": "关务", "prefix": "Customs",
        "root": "CustomsDeclaration", "root_new": ("CustomsDeclaration", "报关单"),
        "specific": [
            ("CustomsItem", "报关项"),
            ("CustomsDocument", "报关单证"),
            ("CustomsDuty", "关税"),
            ("CustomsClearance", "清关记录"),
            ("CustomsInspection", "海关查验"),
            ("CustomsBroker", "报关代理"),
            ("CustomsStatus", "关务状态"),
            ("CustomsAudit", "关务审计"),
        ],
    },
    {
        "key": "compliance", "cn": "合规", "prefix": "Compliance",
        "root": "ComplianceCase", "root_new": ("ComplianceCase", "合规记录"),
        "specific": [
            ("ComplianceRule", "合规规则"),
            ("ComplianceCheck", "合规检查"),
            ("ComplianceFinding", "合规发现"),
            ("ComplianceRemediation", "合规整改"),
            ("ComplianceTraining", "合规培训"),
            ("ComplianceLicense", "许可证"),
            ("ComplianceIncident", "合规事件"),
            ("ComplianceAudit", "合规审计"),
        ],
    },
    {
        "key": "maintenance", "cn": "维保", "prefix": "Maintenance",
        "root": "MaintenanceOrder", "root_new": ("MaintenanceOrder", "维保工单"),
        "specific": [
            ("MaintenancePlan", "维保计划"),
            ("MaintenanceJob", "维保作业"),
            ("MaintenancePart", "维保配件"),
            ("MaintenanceHistory", "维保历史"),
            ("MaintenanceCost", "维保费用"),
            ("MaintenanceProvider", "维保服务商"),
            ("MaintenanceInspection", "维保检查"),
            ("MaintenanceAudit", "维保审计"),
        ],
    },
    {
        "key": "fuel", "cn": "能源", "prefix": "Fuel",
        "root": "FuelTransaction", "root_new": ("FuelTransaction", "油费交易"),
        "specific": [
            ("FuelCard", "油卡"),
            ("FuelStation", "加油站"),
            ("FuelConsumption", "油耗记录"),
            ("FuelPrice", "油价"),
            ("FuelRecharge", "充值记录"),
            ("FuelAllocation", "油费分摊"),
            ("FuelAnomaly", "油耗异常"),
            ("FuelAudit", "油费审计"),
        ],
    },
    {
        "key": "device", "cn": "设备", "prefix": "Device",
        "root": "Device", "root_new": ("Device", "设备"),
        "specific": [
            ("DeviceProfile", "设备档案"),
            ("DeviceBinding", "设备绑定"),
            ("DeviceTelemetry", "设备遥测"),
            ("DeviceAlarm", "设备告警"),
            ("DeviceMaintenance", "设备维护"),
            ("DeviceFirmware", "固件版本"),
            ("DeviceConnectivity", "设备连接"),
            ("DeviceAudit", "设备审计"),
        ],
    },
    {
        "key": "service", "cn": "客服", "prefix": "Service",
        "root": "ServiceCase", "root_new": ("ServiceCase", "客服工单"),
        "specific": [
            ("ServiceRequest", "服务请求"),
            ("ServiceComplaint", "服务投诉"),
            ("ServiceConsultation", "服务咨询"),
            ("ServiceCallback", "服务回访"),
            ("ServiceSla", "SLA 记录"),
            ("ServiceAgent", "客服坐席"),
            ("ServiceScript", "客服话术"),
            ("ServiceAudit", "客服审计"),
        ],
    },
    {
        "key": "organization", "cn": "组织", "prefix": "Organization",
        "root": "Organization", "root_new": ("Organization", "组织"),
        "specific": [
            ("OrganizationUnit", "组织单元"),
            ("OrganizationRole", "组织角色"),
            ("OrganizationUser", "组织用户"),
            ("OrganizationPermission", "组织权限"),
            ("OrganizationRegion", "组织区域"),
            ("OrganizationHierarchy", "组织层级"),
            ("OrganizationShift", "组织班次"),
            ("OrganizationAudit", "组织审计"),
        ],
    },
    {
        "key": "document", "cn": "单证", "prefix": "Document",
        "root": "Document", "root_new": ("Document", "单证"),
        "specific": [
            ("DocumentType", "单证类型"),
            ("DocumentInstance", "单证实例"),
            ("DocumentSignature", "单证签章"),
            ("DocumentVersion", "单证版本"),
            ("DocumentArchive", "单证归档"),
            ("DocumentReview", "单证审核"),
            ("DocumentDistribution", "单证分发"),
            ("DocumentAudit", "单证审计"),
        ],
    },
    {
        "key": "metric", "cn": "指标", "prefix": "Metric",
        "root": "KpiMetric", "root_new": ("KpiMetric", "指标"),
        "specific": [
            ("MetricDefinition", "指标定义"),
            ("MetricTarget", "指标目标"),
            ("MetricValue", "指标值"),
            ("MetricDimension", "指标维度"),
            ("MetricAlert", "指标告警"),
            ("MetricReport", "指标报告"),
            ("MetricTrend", "指标趋势"),
            ("MetricAudit", "指标审计"),
        ],
    },
]


def snake(name: str) -> str:
    """把 PascalCase 转成稳定的 snake_case 标识。"""
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    second = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first)
    return second.lower()


BASE_IRI = "https://w3id.org/mobility-ontology/freight#"
METADATA_VERSION = "2.1.0"
HANDWRITTEN_SOURCE = "handwritten"
GENERATED_SOURCE = "generated:tools/expand_ontology.py"
HANDWRITTEN_EFFECTIVE_FROM = "2025-01-01"
GENERATED_EFFECTIVE_FROM = "2026-11-19"


def build_iri(kind: str, item_id: str) -> str:
    if kind in ("class", "relation"):
        return f"{BASE_IRI}{item_id}"
    return f"https://w3id.org/mobility-ontology/freight/{kind}#{item_id}"


def attach_metadata(item: dict, kind: str, *, source: str,
                    effective_from: str) -> dict:
    """补齐 G1-02 要求的来源与版本元数据，保留已有值。"""
    item.setdefault("owner", "freight-core")
    item.setdefault("source", source)
    item.setdefault("version", METADATA_VERSION)
    item.setdefault("iri", build_iri(kind, item["id"]))
    item.setdefault("effective_from", effective_from)
    return item


def dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def class_statuses(suffix: str) -> list[str]:
    return list(ASPECT_STATUSES.get(suffix, DEFAULT_STATUSES))


def build_class(class_id: str, label: str, domain: dict, root_label: str,
                is_root: bool, suffix: str) -> dict:
    key = f"{snake(class_id)}_id"
    if is_root:
        properties = [key, "name", "status", "created_at", "updated_at",
                      f"{domain['key']}_code"]
        description = f"{label}，{domain['cn']}域聚合根，按 {key} 检索。"
    else:
        properties = [key, "name", "status", "created_at", "updated_at",
                      f"{snake(domain['root'])}_id"]
        suffix_cn = next(
            (cn for name, cn in ASPECTS if name == suffix), suffix,
        )
        description = (
            f"{label}，{domain['cn']}域的{suffix_cn}，按 {key} 检索。"
        )
    return attach_metadata({
        "id": class_id,
        "label": label,
        "key": key,
        "description": description,
        "aliases": dedupe([
            label, class_id.lower(), domain["cn"],
            domain["prefix"].lower(), root_label,
        ]),
        "statuses": class_statuses(suffix),
        "properties": dedupe(properties),
    }, "class", source=GENERATED_SOURCE,
        effective_from=GENERATED_EFFECTIVE_FROM)


def build_relations(domain: dict, class_ids: list[str], root_label: str) -> list[dict]:
    relations = []
    root = domain["root"]
    for class_id in class_ids:
        if class_id == root:
            continue
        relations.append(attach_metadata({
            "id": f"{snake(class_id)}_belongs_to_{snake(root)}",
            "label": f"属于{root_label}",
            "domain": class_id,
            "range": root,
            "temporal": False,
        }, "relation", source=GENERATED_SOURCE,
            effective_from=GENERATED_EFFECTIVE_FROM))
    if domain.get("root_new"):
        relations.append(attach_metadata({
            "id": f"{snake(root)}_related_to_waybill",
            "label": "关联运单",
            "domain": root,
            "range": "Waybill",
            "temporal": False,
        }, "relation", source=GENERATED_SOURCE,
            effective_from=GENERATED_EFFECTIVE_FROM))
    return relations


def build_group(domain: dict, class_ids: list[str], root_label: str) -> dict:
    types = list(class_ids)
    if not domain.get("root_new") and domain["root"] not in types:
        # 复用现有根类型，保证组内能看到子类到根的归属关系。
        types.insert(0, domain["root"])
    return {
        "id": f"{domain['key']}_domain",
        "label": f"{domain['cn']}域",
        "description": (
            f"{domain['cn']}业务对象：{root_label}及其明细、变更、审批、"
            "任务、快照与审计记录。"
        ),
        "aliases": dedupe([
            domain["cn"], domain["prefix"].lower(), root_label,
        ]),
        "types": types,
    }


def build_extension(raw: dict) -> tuple[list[dict], list[dict], list[dict]]:
    existing_labels = {c["id"]: c.get("label", c["id"]) for c in raw["classes"]}
    classes: list[dict] = []
    relations: list[dict] = []
    groups: list[dict] = []

    for domain in DOMAINS:
        root_new = domain.get("root_new")
        root_label = root_new[1] if root_new else existing_labels.get(
            domain["root"], domain["root"],
        )
        class_ids: list[str] = []
        if root_new:
            root_id, root_cn = root_new
            classes.append(build_class(
                root_id, root_cn, domain, root_label, True, "",
            ))
            class_ids.append(root_id)

        for class_id, label in domain["specific"]:
            classes.append(build_class(
                class_id, label, domain, root_label, False, "",
            ))
            class_ids.append(class_id)

        aspects = NEW_ROOT_ASPECTS if root_new else ASPECTS
        for suffix, suffix_cn in aspects:
            class_id = f"{domain['prefix']}{suffix}"
            classes.append(build_class(
                class_id, f"{domain['cn']}{suffix_cn}",
                domain, root_label, False, suffix,
            ))
            class_ids.append(class_id)

        expected = 18
        if len(class_ids) != expected:
            raise ValueError(
                f"{domain['key']} 生成了 {len(class_ids)} 个类，预期 {expected}",
            )
        relations.extend(build_relations(domain, class_ids, root_label))
        groups.append(build_group(domain, class_ids, root_label))

    return classes, relations, groups


def ensure_metadata(raw: dict) -> None:
    """给手写与生成条目统一补齐元数据；生成条目已有值不会被覆盖。"""
    for item in raw.get("classes", []):
        attach_metadata(item, "class", source=HANDWRITTEN_SOURCE,
                        effective_from=HANDWRITTEN_EFFECTIVE_FROM)
    for item in raw.get("relations", []):
        attach_metadata(item, "relation", source=HANDWRITTEN_SOURCE,
                        effective_from=HANDWRITTEN_EFFECTIVE_FROM)
    for item in raw.get("rules", []):
        attach_metadata(item, "rule", source=HANDWRITTEN_SOURCE,
                        effective_from=HANDWRITTEN_EFFECTIVE_FROM)
    for item in raw.get("actions", []):
        attach_metadata(item, "action", source=HANDWRITTEN_SOURCE,
                        effective_from=HANDWRITTEN_EFFECTIVE_FROM)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="写回语义层文件")
    args = parser.parse_args()

    raw = json.loads(LAYER.read_text(encoding="utf-8"))
    classes, relations, groups = build_extension(raw)

    class_ids = {c["id"] for c in classes}
    relation_ids = {r["id"] for r in relations}
    group_ids = {g["id"] for g in groups}

    raw["classes"] = [c for c in raw["classes"] if c.get("id") not in class_ids]
    raw["relations"] = [
        r for r in raw["relations"] if r.get("id") not in relation_ids
    ]
    raw["object_type_groups"] = [
        g for g in raw.get("object_type_groups", [])
        if g.get("id") not in group_ids
    ]

    duplicate_classes = class_ids & {c["id"] for c in raw["classes"]}
    duplicate_relations = relation_ids & {r["id"] for r in raw["relations"]}
    duplicate_groups = group_ids & {
        g["id"] for g in raw["object_type_groups"]
    }
    if duplicate_classes or duplicate_relations or duplicate_groups:
        raise SystemExit(
            "与手写定义发生 ID 冲突: "
            f"classes={sorted(duplicate_classes)} "
            f"relations={sorted(duplicate_relations)} "
            f"groups={sorted(duplicate_groups)}"
        )

    total_classes = len(raw["classes"]) + len(classes)
    total_relations = len(raw["relations"]) + len(relations)
    total_groups = len(raw["object_type_groups"]) + len(groups)
    print(
        f"新增 {len(classes)} 类 / {len(relations)} 关系 / {len(groups)} 组；"
        f"合计 {total_classes} 类 / {total_relations} 关系 / {total_groups} 组"
    )

    if len(classes) < 500 or len(relations) < 500:
        raise SystemExit("扩展规模不足 500 类 / 500 关系")

    if not args.write:
        print("未写文件；加 --write 落盘")
        return

    raw["meta"]["version"] = "2.1.0"
    raw["meta"]["scope"] = (
        "覆盖订单、运单、货物、装卸、运力、计价、结算、证据、仓储、线路、"
        "地理、合同、保险、关务、合规、维保、能源、设备、客服、组织、单证与"
        f"指标等 {len(DOMAINS)} 个业务域，共 {total_classes} 类 / "
        f"{total_relations} 关系。"
    )
    raw["classes"].extend(classes)
    raw["relations"].extend(relations)
    raw["object_type_groups"].extend(groups)
    ensure_metadata(raw)

    LAYER.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {LAYER}")


if __name__ == "__main__":
    main()