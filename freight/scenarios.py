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

from freight.fact_store import FactStore as Store

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
    """完整核心剧本：建对象 → 接关系 → 走动作。返回摘要。

    幂等：存量库上已有剧本对象时直接跳过，避免重复建对象或重复走动作。
    """
    if system.facts.scalar("SELECT COUNT(*) FROM objects WHERE object_id = ?",
                           ("waybill:SCN01",)):
        return {
            "objects": [], "relations": [], "actions": [],
            "action_count": 0, "object_count": 0, "relation_count": 0,
            "skipped": True,
        }
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


# ======================================================================
# 决策表计价样本：天气维度（truck-rain 行）
# ======================================================================
#
# seed() 只在空库上跑，存量演示库（如 11-21 建的 runs/freight_facts.db）
# 拿不到迁移后新增的天气维度——诊断推理链里因此看不到「大货车+雨天」
# 命中 truck-rain 行、每 10 分钟 4.8 元这条变化。本函数把样本补齐：
# 幂等（缺什么补什么，已在的不动），每次带场景启动都会收敛。
PRICING_SAMPLE_WAYBILL = "waybill:WB20261106002"
PRICING_SAMPLE_FEE = "fee:F101"
PRICING_SAMPLE_EVIDENCE = "evidence:E101"
PRICING_SAMPLE_AT = "2026-11-06T12:00:00+08:00"


def _merge_props(system, object_id: str, extra: dict) -> bool:
    """把 extra 合并进对象现有 props 写回（update_object 是整体替换）。"""
    row = system.facts.get_row(object_id)
    if not row:
        return False
    props = dict(Store.decode(row)["props"])
    if all(props.get(k) == v for k, v in extra.items()):
        return False
    props.update(extra)
    system.facts.update_object(object_id, props, None,
                               props.get("status"), PRICING_SAMPLE_AT)
    return True


def _has_relation(system, subject_id: str, predicate: str,
                  object_id: str) -> bool:
    return bool(system.facts.scalar(
        "SELECT COUNT(*) FROM relations WHERE subject_id=? AND predicate=? "
        "AND object_id=? AND valid_to IS NULL", (subject_id, predicate, object_id)))


def ensure_pricing_samples(system) -> dict:
    """补齐决策表计价样本（天气维度）。返回本步做了什么。"""
    done = []
    # 主运单显式声明 normal：缺省时 weather=None 也能正确落到车型行，
    # 但演示与推理链里要把「天气是一维」摆到明面上。
    if _merge_props(system, "waybill:WB20261105001", {"weather": "normal"}):
        done.append("WB20261105001.weather=normal")
    # 多维样本：大货车 + 雨天 + 等候 53 分钟 → 命中 truck-rain 行，
    # 应计 ceil(13/10)×4.8 = 9.60；结算按旧口径（4.0）实收 8.00，
    # 诊断里呈现「天气加价行少收 1.60 元」。
    if _merge_props(system, PRICING_SAMPLE_WAYBILL,
                    {"weather": "rain", "waiting_minutes": 53,
                     "free_wait_minutes": 40}):
        done.append("WB20261106002.weather=rain")
    # 样本车也要对齐当前 seed 定义：存量库里 V003 还是旧的 4.2米厢货，
    # 车型解析走 waybill_uses_vehicle → V003，不对齐就命不中 truck-rain 行
    # （4.2米厢货行 30/5/2.4 会算出 12.00，天气维度白补）。
    if _merge_props(system, "vehicle:V003",
                    {"vehicle_type": "大货车", "capacity_t": 12.0}):
        done.append("V003.vehicle_type=大货车")
    # 清掉存量规则实例上的参数副本：实例 params 非空时会**覆盖**决策表
    # （_fee_context 的显式参数通道），旧库 PR-WAIT-01 还带着
    # by_vehicle_type + 30/5/2.4，会把 truck-rain 的 40/10/4.8 盖回 12.00。
    # 新 seed 已不写副本，这里把存量库收敛到同一口径。
    for row in system.facts.all(
            "SELECT object_id, props FROM objects WHERE type='PricingRule'"):
        props = dict(Store.decode(row)["props"])
        if props.get("params"):
            if _merge_props(system, row["object_id"], {"params": {}}):
                done.append(f"{row['object_id']}.params cleared")
    if not system.facts.get_row(PRICING_SAMPLE_FEE):
        system.add_object("FeeItem", PRICING_SAMPLE_FEE,
                          "等候费（大货车·雨天样本）", "DISPUTED", {
            "fee_id": "F101", "fee_type": "waiting", "fee_label": "等候费",
            "quoted_amount": 0.0, "expected_amount": 9.6,
            "actual_amount": 8.0, "waived_amount": 0.0,
            "calc_basis": "等候 53 分钟，大货车+雨天命中 truck-rain 行："
                          "40 分钟免费、每 10 分钟 4.8 元，应计 9.60；"
                          "结算按旧口径（每 10 分钟 4.0 元）实收 8.00",
            "pricing_rule_id": "PR-WAIT-01",
            "pricing_rule_version": "2026.10",
            "occurred_at": PRICING_SAMPLE_AT,
            "source_system": "TMS", "status": "DISPUTED",
        }, PRICING_SAMPLE_AT)
        done.append("fee:F101 created")
    if not _has_relation(system, PRICING_SAMPLE_WAYBILL,
                         "waybill_produces_fee", PRICING_SAMPLE_FEE):
        system.add_relation("waybill_produces_fee", PRICING_SAMPLE_WAYBILL,
                            PRICING_SAMPLE_FEE, PRICING_SAMPLE_AT)
        done.append("waybill_produces_fee -> F101")
    if not _has_relation(system, PRICING_SAMPLE_FEE, "fee_priced_by_rule",
                         "pricing_rule:PR-WAIT-01"):
        system.add_relation("fee_priced_by_rule", PRICING_SAMPLE_FEE,
                            "pricing_rule:PR-WAIT-01", PRICING_SAMPLE_AT)
        done.append("fee_priced_by_rule -> PR-WAIT-01")
    if not system.facts.get_row(PRICING_SAMPLE_EVIDENCE):
        system.add_evidence(PRICING_SAMPLE_EVIDENCE, PRICING_SAMPLE_FEE,
                            "PRICING_RULE", "BILLING", PRICING_SAMPLE_AT,
                            "2026.10",
                            "等候费规则：大货车雨天 40 分钟免费，每 10 分钟 4.8 元",
                            {"free_minutes": 40, "block_minutes": 10,
                             "per_block": 4.8, "matched_row": "truck-rain"},
                            "fee_supported_by_evidence")
        done.append("evidence:E101 created")
    system.conn.commit()
    return {"done": done, "skipped": not done}


# ======================================================================
# 缺口探针：故意构造四类来源缺口，证明「检测能力」真的在
# ======================================================================
#
# 这四条费用是**人为构造的异常数据**，用途是验证检测链路真的会报警：
#   GAP-UNRESOLVED  无人认领（source_system 没有对应连接器）
#   GAP-TYPE        口径越界（TMS 不认领 highway，却挂了一条 highway 费用）
#   GAP-STALE       数据过期（源数据时间远早于结算基准，超过 freshness_hours）
#   GAP-NOEVIDENCE  证据缺失（waiting 要求证据，但一条证据都没有）
#
# **必须与主诊断剧本隔离**：主剧本的正常诊断里不应该出现这些告警。
# 门禁第 28 项会同时断言「探针库报得出来」与「正常库不报」。

GAP_PROBE_WAYBILL = "waybill:GAPPROBE"
GAP_DELIVERED_AT = "2026-11-05T12:18:00+08:00"


def build_gap_cases(system) -> dict:
    """构造缺口探针运单与四条缺口费用；返回各缺口对应的费用 id。"""
    system.add_object("Waybill", GAP_PROBE_WAYBILL, "缺口探针运单", "DELIVERED", {
        "waybill_id": "GAPPROBE", "order_id": "GAPPROBE",
        "driver_id": "D001", "vehicle_id": "V001",
        "estimated_fee": 100.0, "actual_fee": 100.0, "variance": 0.0,
        # 计价规则要读这些字段，缺一个就会在规则求值时报「AST 字段不存在」
        "waiting_minutes": 10, "free_wait_minutes": 30,
        "actual_distance_km": 12.0, "actual_duration_min": 90,
        "loading_confirmed_at": "2026-11-05T10:35:00+08:00",
        "delivered_at": GAP_DELIVERED_AT,
    }, T0)
    # 车型必须挂上：计价规则按 vehicle_type 选费率（base 规则要读
    # params.base_fare_by_vehicle_type），车型为空会直接导致规则求值失败
    system.add_relation("waybill_uses_vehicle", GAP_PROBE_WAYBILL, "vehicle:V001", T0)

    # 金额取「规则应计值」，让探针只暴露来源缺口，不被金额差异的噪声淹没
    cases = {
        # 无人认领：来源系统没有任何连接器声明
        "unresolved": ("fee:GAP-UNRESOLVED", "第三方GPS", "other", 10.0,
                       "2026-11-05T11:00:00+08:00"),
        # 口径越界：highway 归 ETC，不归 TMS
        "type_undeclared": ("fee:GAP-TYPE", "TMS", "highway", 0.0,
                            "2026-11-05T11:10:00+08:00"),
        # 数据过期：源数据时间远早于结算基准（基准=delivered_at，窗口 24h）
        "stale": ("fee:GAP-STALE", "TMS", "base", 380.0,
                  "2026-10-01T08:00:00+08:00"),
        # 证据缺失：waiting 要求证据，但一条都没有（时间取基准附近，避免同时报过期）
        "no_evidence": ("fee:GAP-NOEVIDENCE", "TMS", "waiting", 0.0,
                        "2026-11-05T12:00:00+08:00"),
        # 正常被 ETC 认领的 highway：用于验证「把 ETC 的 highway 认领拿掉 →
        # 这条费用立刻变成无人认领」（归属声明的承重）
        "etc_highway": ("fee:GAP-ETC-HIGHWAY", "ETC", "highway", 0.0,
                        "2026-11-05T11:20:00+08:00"),
    }
    # 每条探针费用必须挂上定价规则实例：规则求值从 PricingRule.params 取参数，
    # 不挂规则就会在算费时抛「AST 字段不存在」（而不是产出告警）。
    # 费用上的 pricing_rule_id 与关系两端都指向**规则实例**（PR-xxx）：
    # 校验比较的是绑定实例与实际规则实例（R-PRICE-01 比的是生效期，不是 id）
    rule_by_type = {
        "base": ("R-BASE-01", "pricing_rule:PR-BASE-01"),
        "mileage": ("R-MILEAGE-01", "pricing_rule:PR-MILEAGE-01"),
        "waiting": ("R-WAIT-01", "pricing_rule:PR-WAIT-01"),
        "highway": ("R-HIGHWAY-01", "pricing_rule:PR-HIGHWAY-01"),
        "loading": ("R-LOADING-01", "pricing_rule:PR-LOADING-01"),
        "insurance": ("R-INSURANCE-01", "pricing_rule:PR-INSURANCE-01"),
        "other": ("R-OTHER-01", "pricing_rule:PR-OTHER-01"),
    }
    for _, (fee_id, system_name, fee_type, amount, occurred_at) in cases.items():
        system.add_object("FeeItem", fee_id, f"缺口探针 {fee_id}", "NORMAL", {
            "fee_id": fee_id.split(":", 1)[-1], "fee_type": fee_type,
            "fee_label": f"缺口探针-{fee_type}", "quoted_amount": 0.0,
            "expected_amount": 0.0, "actual_amount": amount, "waived_amount": 0.0,
            "calc_basis": "缺口探针数据（人为构造）",
            # 与 seed 同口径：写实例的短 id（PR-xxx），关系指向全 id
            "pricing_rule_id": (rule_by_type.get(fee_type, ("", ""))[1]
                                .split(":", 1)[-1]),
            "pricing_rule_version": "2026.10",
            "occurred_at": occurred_at, "source_system": system_name,
        }, T0)
        system.add_relation("waybill_produces_fee", GAP_PROBE_WAYBILL, fee_id, T0)
        if rule_by_type.get(fee_type):
            system.add_relation("fee_priced_by_rule", fee_id,
                                rule_by_type[fee_type][1], T0)
    # ETC highway 这条探针在正常状态下由 ETC 认领，给它补上 ETC 证据，
    # 免得再叠加一条与主题无关的「缺证据」噪声
    add_gap_evidence(system, "fee:GAP-ETC-HIGHWAY", "ETC")
    return {k: v[0] for k, v in cases.items()}


def add_gap_evidence(system, fee_id: str, source_system: str = "TMS") -> None:
    """给某条费用补一条源系统证据（用于证明「数据修好 → 告警消失」）。"""
    system.facts.add_evidence(
        f"evidence:GAPFIX-{fee_id.split(':', 1)[-1]}", fee_id,
        "LOAD_GATE_RECORD", source_system, "2026-11-05T12:00:00+08:00", "1.0",
        "缺口探针补证据（人为构造）", {"note": "gap probe evidence"},
        "gap-probe-checksum")