"""把 v3 本体升级为 v4：限界上下文（contexts）× 聚合（aggregates）× 边界链接。

与 v3 的本质区别：v3 的 35 个 object_type_groups 是「类型名前缀族」，被当成
上下文裁剪单元使用；v4 按业务语义声明 6 个限界上下文、30 个聚合，旧域降级为
类型多标签（Palantir Groups 模式：仅 UI 导航，不参与裁剪）。跨聚合关系升级为
显式边界链接（boundary=true），注入层永不静默丢弃。

纪律（对应 plan/ontology-context-retrieval-v4-plan.md §5.3）：
1. 每个类恰属一个 primary_aggregate；每个聚合恰属一个 context。
2. 每条跨聚合关系 boundary=true（脚本计算并显式写入声明）。
3. 映射表必须显式逐条声明，禁止用类型名前缀推断归属。
4. glossary 条目的 type_ref 必须解析到存在的类型。
5. 每个聚合至少含 1 条聚合内关系（防「空壳聚合」）。

幂等：归属源 = object_type_groups（稳定）+ 显式映射表；重跑覆盖
primary_aggregate / labels / boundary / glossary，不累积。

用法：
    python3 tools/build_ontology_v4.py            # 渲染 v4
    python3 tools/build_ontology_v4.py --check    # 只校验不写盘
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from freight.semantic import ontology_pin_digest  # noqa: E402  发布 pin 单一事实源

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONTOLOGY_JSON = os.path.join(ROOT, "freight", "semantic_layer.json")
MEMBERSHIP_MD = os.path.join(ROOT, "plan", "v4_type_membership.md")
VERSION = "4.0.0"
EFFECTIVE_FROM = "2026-11-24"

# ───────────────────────── 6 个限界上下文 ─────────────────────────
CONTEXTS: dict[str, dict] = {
    "fulfillment": {
        "label": "订单履约",
        "description": "从接单到签收的执行过程。唯一写入点：运单状态机；"
                       "订单只表达客户意图，运单才是执行事实。",
        "owner": "fulfillment-team",
        "glossary_ref": ["运单", "单子", "这单", "货", "在途", "签收", "改派", "回单"],
    },
    "pricing_billing": {
        "label": "计价与结算",
        "description": "报价 → 计价 → 费用 → 结算 → 开票的资金链路。"
                       "费用项是金额的唯一载体，争议走费用差异。",
        "owner": "finance-team",
        "glossary_ref": ["多收钱", "贵了", "等待费", "压车费", "对账", "账期"],
    },
    "capacity": {
        "label": "运力",
        "description": "人、车、承运商与外协运力的供给与资质。"
                       "自有运力看司机车辆，外协看分包与承运商。",
        "owner": "capacity-team",
        "glossary_ref": ["司机", "车", "承运商", "外协", "挂靠", "资质"],
    },
    "evidence_exception": {
        "label": "证据与异常",
        "description": "履约过程的取证与异常处置。证据链服务费用与责任判定的举证。",
        "owner": "risk-team",
        "glossary_ref": ["回单照片", "异常", "索赔", "拒赔", "举证"],
    },
    "master_data": {
        "label": "主数据",
        "description": "组织、合同、地理、报关、单证、设备等支撑数据。"
                       "变化慢、被各上下文引用，自身不发起业务流程。",
        "owner": "master-data-team",
        "glossary_ref": ["合同", "地址", "发票抬头", "设备"],
    },
    "operations": {
        "label": "运营支撑",
        "description": "客服工单与指标报表。面向人的运营界面，不写入履约事实。",
        "owner": "ops-team",
        "glossary_ref": ["工单", "投诉", "报表", "KPI"],
    },
}

# 聚合 id → (context, 根类型, 中文标签)
AGGREGATES: dict[str, tuple[str, str, str]] = {
    # fulfillment
    "order":         ("fulfillment", "Order", "订单聚合"),
    "waybill":       ("fulfillment", "Waybill", "运单聚合"),
    "cargo":         ("fulfillment", "Cargo", "货物聚合"),
    "stop":          ("fulfillment", "Stop", "装卸停靠聚合"),
    "track":         ("fulfillment", "Track", "轨迹聚合"),
    "route":         ("fulfillment", "Route", "路由聚合"),
    "warehouse":     ("fulfillment", "Warehouse", "仓储聚合"),
    # pricing_billing
    "quote":         ("pricing_billing", "Quote", "报价聚合"),
    "pricing_rule":  ("pricing_billing", "PricingRule", "计价规则聚合"),
    "fee":           ("pricing_billing", "FeeItem", "费用聚合"),
    "settlement":    ("pricing_billing", "Settlement", "结算聚合"),
    "invoice":       ("pricing_billing", "Invoice", "发票聚合"),
    # capacity
    "vehicle":       ("capacity", "Vehicle", "车辆聚合"),
    "driver":        ("capacity", "Driver", "司机聚合"),
    "carrier":       ("capacity", "Carrier", "承运商聚合"),
    "subcontract":   ("capacity", "SubcontractOrder", "分包聚合"),
    "maintenance":   ("capacity", "MaintenanceOrder", "维修保养聚合"),
    "fuel":          ("capacity", "FuelTransaction", "能耗聚合"),
    # evidence_exception
    "evidence":      ("evidence_exception", "Evidence", "证据聚合"),
    "exception":     ("evidence_exception", "ExceptionTicket", "异常聚合"),
    "insurance":     ("evidence_exception", "InsurancePolicy", "保险聚合"),
    "compliance":    ("evidence_exception", "ComplianceRule", "合规聚合"),
    # master_data
    "organization":  ("master_data", "Organization", "组织聚合"),
    "contract":      ("master_data", "Contract", "合同聚合"),
    "geo":           ("master_data", "GeoAddress", "地理聚合"),
    "customs":       ("master_data", "CustomsDeclaration", "报关聚合"),
    "document":      ("master_data", "Document", "单证聚合"),
    "device":        ("master_data", "Device", "设备聚合"),
    # operations
    "service":       ("operations", "ServiceTicket", "客服工单聚合"),
    "metric":        ("operations", "Metric", "指标聚合"),
}

# 旧域（object_type_groups id）→ 聚合 id。显式逐条，禁止前缀推断。
DOMAIN_TO_AGGREGATE: dict[str, str] = {
    "order_domain": "order", "waybill_domain": "waybill", "cargo_domain": "cargo",
    "stop_domain": "stop", "track_domain": "track", "route_domain": "route",
    "warehouse_domain": "warehouse",
    "quote_domain": "quote", "pricing_rule_domain": "pricing_rule",
    "fee_domain": "fee", "settlement_domain": "settlement",
    "invoice_domain": "invoice",
    "vehicle_domain": "vehicle", "driver_domain": "driver",
    "carrier_domain": "carrier", "subcontract_domain": "subcontract",
    "maintenance_domain": "maintenance", "fuel_domain": "fuel",
    "evidence_domain": "evidence", "exception_domain": "exception",
    "insurance_domain": "insurance", "compliance_domain": "compliance",
    "organization_domain": "organization", "contract_domain": "contract",
    "geo_domain": "geo", "customs_domain": "customs", "document_domain": "document",
    "device_domain": "device", "service_domain": "service", "metric_domain": "metric",
}

# 核心域（v3 的 5 个非 *_domain 组）的类 → 聚合。显式逐条声明。
CORE_TYPE_AGGREGATE: dict[str, str] = {
    # order_transport 核心 6 类
    "Shipper": "order", "Order": "order", "Waybill": "waybill",
    "FeeItem": "fee", "Cargo": "cargo", "Stop": "stop",
    # capacity 核心 4 类
    "Driver": "driver", "Vehicle": "vehicle", "Carrier": "carrier",
    "SubcontractOrder": "subcontract",
    # pricing 核心 3 类
    "Quote": "quote", "QuoteItem": "quote", "PricingRule": "pricing_rule",
    # settlement 核心 2 类
    "Settlement": "settlement", "Invoice": "invoice",
    # evidence 核心 3 类
    "ExceptionTicket": "exception", "Evidence": "evidence", "Track": "track",
}

# ───────────────────────── 业务术语表（人工审核版） ─────────────────────────
# 结构：term / synonyms / colloquial / type_ref / context。
# 术语表是检索一等公民（C1 精确通道 + answerability 分诊信号），
# 条目按「跨类口语 / 缩写 / 同义词」收录；类自身 aliases 由 C2 词法通道
# 索引，不在此重复。LLM 生成的候选走 tools/build_glossary.py，人工审核后
# 才允许并入本表（在线零 LLM）。
GLOSSARY_EXTRA: list[dict] = [
    # pricing_billing 口语
    {"term": "多收钱了", "synonyms": ["超收", "多扣了", "费用偏高", "贵了", "涨价"],
     "colloquial": ["怎么这么贵", "被坑了", "贵了不少"],
     "type_ref": "FeeVariance", "context": "pricing_billing"},
    {"term": "少收钱了", "synonyms": ["漏收", "少扣了", "费用偏低"],
     "colloquial": ["收少了", "亏了"],
     "type_ref": "FeeVariance", "context": "pricing_billing"},
    {"term": "等候费", "synonyms": ["等待费", "压车费", "待时费"],
     "colloquial": ["白等了", "等着卸货的钱"],
     "type_ref": "FeeDetail", "context": "pricing_billing"},
    {"term": "对账", "synonyms": ["核账", "账目核对"],
     "colloquial": ["对不上账", "账对不齐"],
     "type_ref": "Settlement", "context": "pricing_billing"},
    {"term": "账期", "synonyms": ["结算周期", "付款周期", "月结"],
     "colloquial": ["多久回款", "什么时候打款"],
     "type_ref": "SettlementCycle", "context": "pricing_billing"},
    {"term": "开发票", "synonyms": ["开票", "发票申请"],
     "colloquial": ["发票多久开", "发票开了没"],
     "type_ref": "InvoiceRequest", "context": "pricing_billing"},
    {"term": "报价", "synonyms": ["询价", "运价"],
     "colloquial": ["多少钱一车", "运费怎么算", "单价多少"],
     "type_ref": "Quote", "context": "pricing_billing"},
    {"term": "费用明细", "synonyms": ["费用拆分", "费用构成"],
     "colloquial": ["钱都花哪了", "费用怎么组成的"],
     "type_ref": "FeeDetail", "context": "pricing_billing"},
    {"term": "减免", "synonyms": ["豁免", "减免申请"],
     "colloquial": ["能不能便宜点", "免掉这部分"],
     "type_ref": "FeeWaiver", "context": "pricing_billing"},
    {"term": "计价规则", "synonyms": ["定价规则", "运价规则"],
     "colloquial": ["怎么算出来的", "按什么标准收费"],
     "type_ref": "PricingRule", "context": "pricing_billing"},
    # fulfillment 口语
    {"term": "这单货", "synonyms": ["这单", "该订单", "本单"],
     "colloquial": ["我这单", "那个单子"],
     "type_ref": "Order", "context": "fulfillment"},
    {"term": "在途", "synonyms": ["运输中", "运送中"],
     "colloquial": ["货到哪了", "走到哪了", "还在路上吗"],
     "type_ref": "Track", "context": "fulfillment"},
    {"term": "签收", "synonyms": ["送达", "妥投"],
     "colloquial": ["到了没", "收到了没", "签了没"],
     "type_ref": "Stop", "context": "fulfillment"},
    {"term": "改派", "synonyms": ["重新派车", "调派"],
     "colloquial": ["换个司机", "车换了"],
     "type_ref": "WaybillReassignment", "context": "fulfillment"},
    {"term": "回单", "synonyms": ["签收单", "回执"],
     "colloquial": ["回单照片", " POD"],
     "type_ref": "Evidence", "context": "evidence_exception"},
    {"term": "改单", "synonyms": ["订单变更", "修改订单"],
     "colloquial": ["地址改了", "数量改了"],
     "type_ref": "OrderAmendment", "context": "fulfillment"},
    {"term": "拆单", "synonyms": ["分单", "一单拆多单"],
     "colloquial": ["怎么拆成几车", "分开运", "拆成两车", "拆成几车"],
     "type_ref": "WaybillSegment", "context": "fulfillment"},
    {"term": "时效", "synonyms": ["时效承诺", "SLA"],
     "colloquial": ["超时了没", "会不会晚", "多久能到"],
     "type_ref": "OrderSLA", "context": "fulfillment"},
    {"term": "轨迹", "synonyms": ["行车轨迹", "GPS 记录"],
     "colloquial": ["走的哪条路", "路线对不对"],
     "type_ref": "TrackPoint", "context": "fulfillment"},
    {"term": "停靠", "synonyms": ["装卸点", "停靠点"],
     "colloquial": ["在哪装货", "在哪卸货", "排队等位"],
     "type_ref": "Stop", "context": "fulfillment"},
    {"term": "入仓", "synonyms": ["进仓", "入库", "入库单"],
     "colloquial": ["这票货入哪个仓", "放哪个仓"],
     "type_ref": "Warehouse", "context": "fulfillment"},
    # capacity 口语
    {"term": "司机资质", "synonyms": ["驾驶员资格", "从业资格"],
     "colloquial": ["证件齐不齐", "司机证件"],
     "type_ref": "DriverQualificationCert", "context": "capacity"},
    {"term": "挂靠", "synonyms": ["车辆挂靠", "外协挂靠"],
     "colloquial": ["不是自己的车", "个体车"],
     "type_ref": "Carrier", "context": "capacity"},
    {"term": "外协", "synonyms": ["外包", "分包", "转包"],
     "colloquial": ["找外面的车拉", "给别人拉"],
     "type_ref": "SubcontractOrder", "context": "capacity"},
    {"term": "空驶", "synonyms": ["放空", "空车返回"],
     "colloquial": ["白跑一趟", "空车跑"],
     "type_ref": "Route", "context": "capacity"},
    {"term": "油耗", "synonyms": ["百公里油耗", "柴油消耗"],
     "colloquial": ["烧了多少油", "油钱多少"],
     "type_ref": "FuelConsumption", "context": "capacity"},
    {"term": "维保", "synonyms": ["维修保养", "车辆维修"],
     "colloquial": ["车坏了修", "保养记录"],
     "type_ref": "MaintenanceOrder", "context": "capacity"},
    # evidence_exception 口语
    {"term": "货损", "synonyms": ["货物破损", "货坏了"],
     "colloquial": ["货摔坏了", "破了几个箱子"],
     "type_ref": "ExceptionTicket", "context": "evidence_exception"},
    {"term": "索赔", "synonyms": ["理赔", "保险赔付", "赔付申请"],
     "colloquial": ["找谁赔", "能赔多少", "承运险理赔"],
     "type_ref": "InsuranceClaim", "context": "evidence_exception"},
    {"term": "保额", "synonyms": ["保险保额", "保障额度"],
     "colloquial": ["保额多少"],
     "type_ref": "InsuranceCoverage", "context": "evidence_exception"},
    {"term": "举证", "synonyms": ["提供证据", "留证"],
     "colloquial": ["拿证据说话", "照片呢"],
     "type_ref": "EvidenceChain", "context": "evidence_exception"},
    {"term": "异常单", "synonyms": ["异常工单", "异常报告"],
     "colloquial": ["出问题了", "炸单了"],
     "type_ref": "ExceptionReport", "context": "evidence_exception"},
    # master_data 口语
    {"term": "提单", "synonyms": ["提货单", "B/L"],
     "colloquial": ["提单原件在哪"],
     "type_ref": "Document", "context": "master_data"},
    {"term": "抬头", "synonyms": ["发票抬头", "开票信息"],
     "colloquial": ["发票开给谁"],
     "type_ref": "InvoiceTitle", "context": "master_data"},
    {"term": "报关", "synonyms": ["清关", "通关"],
     "colloquial": ["海关那边", "过关"],
     "type_ref": "CustomsDeclaration", "context": "master_data"},
    {"term": "合同到期", "synonyms": ["合同终止", "合约期满"],
     "colloquial": ["合同还有多久"],
     "type_ref": "Contract", "context": "master_data"},
    # operations 口语
    {"term": "投诉", "synonyms": ["客户投诉", "客诉"],
     "colloquial": ["客户不满意", "被投诉了"],
     "type_ref": "ServiceComplaint", "context": "operations"},
    {"term": "KPI", "synonyms": ["考核指标", "绩效指标"],
     "colloquial": ["考核什么", "指标多少"],
     "type_ref": "Metric", "context": "operations"},
]


def build_type_membership(raw: dict) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """旧域 + 核心类映射 → 逐类归属。返回 (type→聚合, 聚合→类型列表, 问题列表)。"""
    old_group_of: dict[str, str] = {}
    problems: list[str] = []
    for group in raw.get("object_type_groups") or []:
        for t in group.get("types") or []:
            if t in old_group_of:
                problems.append(f"类型 {t} 在多个旧域: {old_group_of[t]} / {group['id']}")
            old_group_of[t] = group["id"]

    agg_of: dict[str, str] = {}
    for t in old_group_of:
        gid = old_group_of[t]
        if t in CORE_TYPE_AGGREGATE:
            # 核心域类以显式 CORE_TYPE_AGGREGATE 为准
            agg_of[t] = CORE_TYPE_AGGREGATE[t]
            continue
        agg = DOMAIN_TO_AGGREGATE.get(gid)
        if agg is None:
            problems.append(f"旧域 {gid} 未声明聚合映射（类型 {t}）")
            continue
        agg_of[t] = agg

    # 核心域里声明了但旧域没有的类型（防漏）
    for t, agg in CORE_TYPE_AGGREGATE.items():
        agg_of.setdefault(t, agg)

    members: dict[str, list[str]] = {a: [] for a in AGGREGATES}
    for t, agg in agg_of.items():
        members.setdefault(agg, []).append(t)
    return agg_of, members, problems


def aggregate_contexts() -> list[dict]:
    return [
        {
            "id": cid,
            "label": spec["label"],
            "description": spec["description"],
            "owner": spec["owner"],
            "aggregates": [a for a, (ctx, _, _) in AGGREGATES.items() if ctx == cid],
            "glossary_ref": spec["glossary_ref"],
            "version": VERSION,
            "effective_from": EFFECTIVE_FROM,
        }
        for cid, spec in CONTEXTS.items()
    ]


def aggregate_declarations(members: dict[str, list[str]]) -> list[dict]:
    out: list[dict] = []
    for aid, (ctx, root, label) in AGGREGATES.items():
        types = sorted(members.get(aid) or [])
        if root not in types:
            out.append({
                "id": aid, "context": ctx, "root": root, "label": label,
                "types": types, "labels": [], "invariants": [],
                "warning": f"根类型 {root} 不在聚合成员中",
            })
            continue
        ordered = [root] + [t for t in types if t != root]
        out.append({
            "id": aid,
            "context": ctx,
            "root": root,
            "label": label,
            "types": ordered,
            # 旧域 id 降级为多标签（UI 筛选导航用，不参与裁剪）
            "labels": [gid for gid, agg in DOMAIN_TO_AGGREGATE.items() if agg == aid],
            "invariants": [],
            "version": VERSION,
        })
    return out


def build_glossary(raw: dict, agg_of: dict[str, str]) -> tuple[list[dict], list[str]]:
    """费用名（pricing 键）+ 人工条目 → glossary；返回 (条目, 问题)。"""
    entries: list[dict] = []
    problems: list[str] = []
    seen_terms: set[str] = set()
    class_ids = {c["id"] for c in raw["classes"]}
    for item in GLOSSARY_EXTRA:
        t = item["type_ref"]
        base = t.split(".")[0]
        if base not in class_ids:
            problems.append(f"glossary 条目「{item['term']}」type_ref 不存在: {t}")
            continue
        entry = dict(item)
        entry["context"] = entry.get("context") or agg_of.get(base, "")
        entries.append(entry)
        seen_terms.add(item["term"])

    # 费用类型名 → FeeItem（问句里高频出现，且 fee_type 是费用链的锚）
    for fee_type in (raw.get("pricing") or {}):
        if fee_type in seen_terms:
            continue
        entries.append({
            "term": fee_type,
            "synonyms": [],
            "colloquial": [],
            "type_ref": "FeeItem",
            "context": "pricing_billing",
        })
        seen_terms.add(fee_type)
    return entries, problems


def write_membership_md(members: dict[str, list[str]], raw: dict) -> None:
    class_map = {c["id"]: c for c in raw["classes"]}
    lines = [
        "# v4 类型归属表（build_ontology_v4.py 生成，人工审核档）",
        "",
        f"- 类总数：{sum(len(v) for v in members.values())}",
        f"- 聚合数：{len(members)} / 上下文数：{len(CONTEXTS)}",
        "- 规则：每个类恰属一个聚合（primary_aggregate），旧 35 域降级为多标签。",
        "",
    ]
    for ctx_id, spec in CONTEXTS.items():
        lines.append(f"## {ctx_id} · {spec['label']}")
        lines.append("")
        for aid, (ctx, root, label) in AGGREGATES.items():
            if ctx != ctx_id:
                continue
            types = members.get(aid) or []
            lines.append(f"- **{aid}**（{label}，根 `{root}`，{len(types)} 类）")
            for t in types:
                cls = class_map.get(t, {})
                lines.append(
                    f"  - `{t}` · {cls.get('label', '')} · {cls.get('description', '')[:48]}"
                )
        lines.append("")
    os.makedirs(os.path.dirname(MEMBERSHIP_MD), exist_ok=True)
    with open(MEMBERSHIP_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写盘")
    args = ap.parse_args()

    with open(ONTOLOGY_JSON, encoding="utf-8") as f:
        raw = json.load(f)

    agg_of, members, problems = build_type_membership(raw)
    class_map = {c["id"]: c for c in raw["classes"]}
    class_ids = set(class_map)
    problems += [f"类 {t} 未归属任何聚合" for t in class_ids if t not in agg_of]
    dangling = [t for t in agg_of if t not in class_ids]
    problems += [f"归属表引用了不存在的类: {t}" for t in dangling]
    if problems:
        print("归属校验失败：")
        for p in problems[:20]:
            print("  -", p)
        return 1

    # 关系边界标记：跨聚合 = boundary
    boundary_count = 0
    for rel in raw["relations"]:
        dom_agg = agg_of.get(rel["domain"])
        rng_agg = agg_of.get(rel["range"])
        rel["boundary"] = bool(dom_agg and rng_agg and dom_agg != rng_agg)
        if rel["boundary"]:
            boundary_count += 1

    # 空壳聚合校验（每个聚合至少 1 条聚合内关系）
    intra: dict[str, int] = {a: 0 for a in AGGREGATES}
    for rel in raw["relations"]:
        dom_agg = agg_of.get(rel["domain"])
        if dom_agg and dom_agg == agg_of.get(rel["range"]):
            intra[dom_agg] += 1
    hollow = [a for a, n in intra.items() if n == 0]
    if hollow:
        print(f"空壳聚合（0 条聚合内关系）: {hollow}")
        return 1

    glossary, glossary_problems = build_glossary(raw, agg_of)
    if glossary_problems:
        print("glossary 校验失败：")
        for p in glossary_problems[:10]:
            print("  -", p)
        return 1

    # 类：primary_aggregate + labels（旧 group 降级为多标签）
    old_group_of: dict[str, str] = {}
    for group in raw.get("object_type_groups") or []:
        for t in group.get("types") or []:
            old_group_of[t] = group["id"]
    for cls in raw["classes"]:
        cid = cls["id"]
        cls["primary_aggregate"] = agg_of[cid]
        labels = [old_group_of[cid]] if cid in old_group_of else []
        cls["labels"] = labels

    aggregates = aggregate_declarations(members)
    raw["contexts"] = aggregate_contexts()
    raw["aggregates"] = aggregates
    raw["glossary"] = glossary
    meta = raw.get("meta") or {}
    meta["version"] = VERSION
    meta["scope"] = (
        f"覆盖 {len(CONTEXTS)} 个限界上下文 / {len(AGGREGATES)} 个聚合，"
        f"共 {len(raw['classes'])} 类 / {len(raw['relations'])} 条链接"
        f"（边界链接 {boundary_count} 条）/ {len(glossary)} 条术语。"
    )
    meta["note"] = (
        "schema v4：限界上下文 × 聚合两层声明（Palantir Groups 降级为多标签）；"
        "跨聚合关系显式边界链接（boundary），注入层永不静默丢弃；"
        "glossary 业务术语表是检索一等公民。"
    )
    raw["meta"] = meta

    # 发布钉死：内容摘要写进 meta.payload_sha256（OntoSmith packs 模式）。
    # 摘要函数与加载侧同一来源（freight.semantic.ontology_pin_digest），
    # 排除 pin 字段自身避免自引用；加载时复核，不符即拒。
    meta.pop("payload_sha256", None)
    raw["meta"] = meta
    meta["payload_sha256"] = ontology_pin_digest(raw)
    raw["meta"] = meta

    # v4 目录预算重标定：注入单位从「域」改为「聚合」——聚合是语义完整
    # 单元（根 + 成员 + 聚合内关系 + 边界链接），必须整体注入；6000 字符
    # 是 v3 域时代的经验值，连一个核心聚合（waybill ≈6k 字符）都装不下，
    # 会把问句聚合全部裁掉、目录恒定只剩 seed。16000 ≈ 5k token，
    # 仍远低于全量目录（≈98k 字符），「装不下才缩聚合」的设计不变。
    catalog_cfg = raw.get("catalog") or {}
    catalog_cfg["context_budget_chars"] = 16000
    catalog_cfg["mode"] = "full_if_fits"
    catalog_cfg["note"] = (
        "先给全量紧凑目录；超出上下文预算时，按聚合缩域（当前对象聚合必选 + "
        "object_type_search 召回的问句聚合），跨聚合关系作为边界链接注入。"
    )
    raw["catalog"] = catalog_cfg

    write_membership_md(members, raw)

    total = sum(len(v) for v in members.values())
    print(f"上下文 {len(CONTEXTS)} / 聚合 {len(AGGREGATES)} / 类 {total} / "
          f"边界链接 {boundary_count} / 术语 {len(glossary)}")
    print(f"聚合规模分布: " + ", ".join(
        f"{a}={len(members.get(a) or [])}" for a in list(AGGREGATES)[:8]) + " …")

    if args.check:
        print("check 模式：不写盘")
        return 0

    with open(ONTOLOGY_JSON, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    print(f"已写入 {ONTOLOGY_JSON}")
    print(f"归属表已写入 {MEMBERSHIP_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
