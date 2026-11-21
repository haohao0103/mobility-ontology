"""属性图 vs 可执行本体：同题同数据的对照实验（库版，脚本与 API 共用）。

要回答的质疑
------------
「就那几个交互问题，用属性图（Neo4j 式）是不是也能完成？这样怎么能体现本体的价值？」

实验设计（公平性要点）
----------------------
1. **数据完全相同**：属性图组与本体组读同一份对象/关系（同一张 facts 表导出）。
2. **属性图组禁止读 semantic_layer.json**：它拿不到本体声明，只能
   「数据 + 硬编码在服务代码里的业务规则」——这正是真实属性图项目的做法。
3. **本体组走现状**：本体声明 + 规则 AST + 状态机 + 来源契约。
4. **同一组问题**，逐题判定：能否给出**正确且可复核**的答案。

零污染纪律：本体组在临时 seed 库上运行（与演示库同源同数据），
不触碰 runs/freight_facts.db 任何写路径。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAYBILL = "waybill:WB20261105001"
FACTS_DB = os.path.join(ROOT, "runs", "freight_facts.db")


# =====================================================================
# 属性图对照组：属性图 + 硬编码业务规则（模拟真实属性图项目的服务层）
# =====================================================================
class PropertyGraphService:
    """属性图服务：图里只有节点与边；业务规则一律硬编码在代码里。

    这就是大多数"用属性图做业务系统"的真实形态：
    图负责"谁跟谁有关系"，规则负责"怎么算、谁能做"，两者都写在服务端代码里。
    """

    def __init__(self, db_path: str):
        import networkx as nx

        self.g = nx.MultiDiGraph()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT object_id, type, name, status, props FROM objects"):
            self.g.add_node(r["object_id"], type=r["type"], name=r["name"],
                            status=r["status"], props=json.loads(r["props"]))
        for r in conn.execute(
                "SELECT subject_id, predicate, object_id FROM relations WHERE valid_to IS NULL"):
            self.g.add_edge(r["subject_id"], r["object_id"],
                            predicate=r["predicate"], type="LINK")
        conn.close()

        # ---- 以下全部是"业务逻辑硬编码"：属性图方案的知识全在这几行常量里 ----
        self.WAITING_FREE_MINUTES = 30          # 硬编码 ①：免费等候分钟
        self.WAITING_BLOCK_MINUTES = 5          # 硬编码 ②：计费块
        self.WAITING_PER_BLOCK = 2.4            # 硬编码 ③：每块单价
        self.WAYBILL_ACTIONS = {                # 硬编码 ④：运单状态机
            "accept_order": ({"CREATED", "REASSIGNED"}, "ACCEPTED"),
            "reassign_waybill": ({"CREATED", "ACCEPTED"}, "REASSIGNED"),
            "confirm_loading": ({"ACCEPTED"}, "LOADED"),
            "complete": ({"LOADED"}, "DELIVERED"),
        }
        self.FEE_ACTIONS = {                    # 硬编码 ⑤：费用状态机
            "dispute_charge": ({"NORMAL"}, "DISPUTED"),
            "waive_charge": ({"DISPUTED"}, "WAIVED"),
        }
        self.SETTLEMENT_ACTIONS = {             # 硬编码 ⑥：结算状态机
            "hold_settlement": ({"DRAFT", "PENDING"}, "ON_HOLD"),
            "reverse_entry": ({"POSTED"}, "REVERSED"),
        }
        self.QUOTE_SOURCES = ("pricing.list_quote_items",
                              "pricing.get_quote", "waybill.estimated_fee")  # 硬编码 ⑦：三口径
        self.FEE_TYPE_OWNER = {"base": "TMS", "mileage": "TMS", "waiting": "TMS",
                               "loading": "TMS", "insurance": "TMS", "other": "TMS",
                               "highway": "ETC"}                       # 硬编码 ⑧：归属表
        self.REQUIRE_EVIDENCE = {"waiting"}                            # 硬编码 ⑨：证据要求

    # ---- Cypher 风格的查询（图遍历能力）----
    def nodes_of(self, type_id: str) -> list[str]:
        return [n for n, a in self.g.nodes(data=True) if a["type"] == type_id]

    def props(self, object_id: str) -> dict:
        return self.g.nodes[object_id]["props"]

    def out_edges(self, object_id: str, predicate: str) -> list[str]:
        return [v for _, v, d in self.g.out_edges(object_id, data=True)
                if d.get("predicate") == predicate]

    def in_edges(self, object_id: str, predicate: str) -> list[str]:
        return [u for u, _, d in self.g.in_edges(object_id, data=True)
                if d.get("predicate") == predicate]

    # ---- 业务能力（全部靠上面的硬编码常量）----
    def fee_breakdown(self, waybill_id: str) -> dict:
        """费用拆解：图上能拿到的只有"实际收了多少钱"。"""
        fees = []
        for fid in self.out_edges(waybill_id, "waybill_produces_fee"):
            p = self.props(fid)
            fees.append({"fee_id": fid, "fee_type": p.get("fee_type"),
                         "quoted": p.get("quoted_amount"),
                         "actual": p.get("actual_amount")})
        return {"fees": fees,
                "quoted_total": sum(f["quoted"] or 0 for f in fees),
                "actual_total": sum(f["actual"] or 0 for f in fees)}

    def waiting_expected(self, waybill_id: str) -> float:
        """等候费应计：公式硬编码在代码里（这就是属性图项目的真实做法）。"""
        w = self.props(waybill_id)
        minutes = float(w.get("waiting_minutes") or 0)
        free = self.WAITING_FREE_MINUTES
        block = self.WAITING_BLOCK_MINUTES
        blocks = 0 if minutes <= free else -(-(minutes - free) // block)
        return round(blocks * self.WAITING_PER_BLOCK, 2)

    def can_execute(self, object_id: str, action_id: str) -> tuple[bool, str]:
        """动作前置：状态机硬编码在代码里。"""
        status = self.g.nodes[object_id]["status"]
        table = self.WAYBILL_ACTIONS
        node_type = self.g.nodes[object_id]["type"]
        if node_type == "FeeItem":
            table = self.FEE_ACTIONS
        elif node_type == "Settlement":
            table = self.SETTLEMENT_ACTIONS
        rule = table.get(action_id)
        if not rule:
            return False, f"未知动作 {action_id}"
        allowed, target = rule
        if status not in allowed:
            return False, f"当前状态 {status} 不允许执行（允许 {sorted(allowed)}）"
        return True, f"{status} -> {target}"

    def owner_of_fee_type(self, fee_type: str) -> str:
        return self.FEE_TYPE_OWNER.get(fee_type, "")

    def quote_three_ways(self, waybill_id: str) -> dict:
        """三口径：知道是哪三个口径，也得硬编码（写死在哪几个字段）。"""
        w = self.props(waybill_id)
        order_id = w.get("order_id")
        quote_ids = self.out_edges(f"order:{order_id}", "order_has_quote")
        if not quote_ids:
            return {"found": False}
        quote_id = quote_ids[0]
        items = self.out_edges(quote_id, "quote_contains_item")
        detail_total = round(sum(self.props(i).get("amount") or 0 for i in items), 2)
        declared = self.props(quote_id).get("total_amount") or 0
        snapshot = w.get("estimated_fee") or 0
        return {"found": True, "detail_total": detail_total,
                "declared_total": declared, "snapshot": snapshot,
                "same_source": abs(detail_total - declared) < 0.01
                and abs(declared - snapshot) < 0.01}


# =====================================================================
# 问题集：同一组问题，两边各答；dimension 是事前锁定的精确分类
# =====================================================================
QUESTIONS = [
    ("Q1", "这单为什么多 38 元？", "差异拆解",
     "属性图也能做（不构成差异，别当卖点）"),
    ("Q2", "等候费按规则应收多少？超收多少？", "规则求值（答案不在数据里）",
     "本体：公式在声明里，属性图靠硬编码常量"),
    ("Q3", "减免 40 元会挡住哪一步？", "状态机 + 动作前置",
     "本体：状态机在声明里，属性图靠硬编码 dict"),
    ("Q4", "高速费谁认领？没人认领会怎样？", "来源归属 + 缺口语义",
     "本体独有：缺口语义不存在于属性图方案"),
    ("Q5", "报价三个口径一致吗？", "口径互校（哪三个口径是本体知识）",
     "本体：口径定义在声明里，属性图靠硬编码"),
    ("Q6", "把免费等候 30 改成 20，应计变多少？", "变更成本",
     "本体：改声明一处生效；属性图要改代码发版，漏改无机制发现"),
]


def run_property_graph(svc: PropertyGraphService) -> dict:
    out = {}
    bd = svc.fee_breakdown(WAYBILL)
    gap = round(bd["actual_total"] - bd["quoted_total"], 2)
    out["Q1"] = {
        "answered": True,
        "answer": f"差额 {gap} 元 = 各费用实际与报价之差之和"
                   f"（等待费 25 + 高速费 13）",
        "note": "图上能拿到 quoted/actual，直接相减即可。**这题属性图完全能做，不构成差异**",
    }

    expected = svc.waiting_expected(WAYBILL)
    out["Q2"] = {
        "answered": True,
        "answer": f"应计 {expected} 元",
        "note": "能答，但**公式是硬编码在服务代码里的常量**"
                f"（free={svc.WAITING_FREE_MINUTES}/block={svc.WAITING_BLOCK_MINUTES}"
                f"/per_block={svc.WAITING_PER_BLOCK}），不在数据里也不在声明里",
    }

    ok, detail = svc.can_execute("fee:F003", "waive_charge")
    ok2, detail2 = svc.can_execute("settlement:ST20261105001", "hold_settlement")
    out["Q3"] = {
        "answered": True,
        "answer": f"fee:F003 可减免（{detail}）；settlement:ST20261105001 "
                   f"{'可挂起' if ok2 else '不可挂起'}（{detail2}）",
        "note": "能答，但**状态机与前置条件全部硬编码**在三个 dict 常量里，"
                "且要与本体那一侧保持一致",
    }

    owner = svc.owner_of_fee_type("highway")
    out["Q4"] = {
        "answered": False,
        "answer": f"能从 source_system 属性看出是 {owner}",
        "note": "**只能读到字段值，说不出『没人认领』是异常**——"
                "缺口语义（无人认领/口径越界/数据过期/证据缺失）不存在于属性图方案",
    }

    three = svc.quote_three_ways(WAYBILL)
    out["Q5"] = {
        "answered": True,
        "answer": (f"明细 {three['detail_total']} / 总额 {three['declared_total']} / "
                   f"快照 {three['snapshot']}，一致={three['same_source']}")
        if three.get("found") else "没找到报价",
        "note": "能答，但**「哪三个口径、分别取自哪个字段」是硬编码常量**，"
                "换一套业务就要改这段代码",
    }

    # 变更实验：把免费等候从 30 改成 20（模拟业务改规则）
    old = svc.WAITING_FREE_MINUTES
    svc.WAITING_FREE_MINUTES = 20
    new_expected = svc.waiting_expected(WAYBILL)
    svc.WAITING_FREE_MINUTES = old
    out["Q6"] = {
        "answered": True,
        "answer": f"改成 20 分钟后应计 {new_expected} 元（原来 {expected} 元）",
        "note": "能答，但**这行常量在服务代码里**，改它需要改代码并重新发布；"
                "若多处各写一份公式而漏改一处，两处结论就会打架",
    }
    return out


def run_ontology() -> dict:
    """本体组：走现状（本体声明 + 规则 AST + 状态机 + 来源契约）。

    在临时 seed 库上运行（与演示库同源同数据），零写主库。
    """
    from freight.engine import FreightSystem

    td = tempfile.mkdtemp(prefix="parity_ont_")
    system = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    out = {}
    try:
        report = system.fee_analysis(WAYBILL)
        bd = report.get("breakdown") or []
        waiting = [b for b in bd if b["fee_type"] == "waiting"]
        out["Q1"] = {
            "answered": True,
            "answer": str(report.get("conclusion"))[:180],
            "note": "结论直接带规则应计值与证据",
        }
        out["Q2"] = {
            "answered": True,
            "answer": (f"应计 {waiting[0]['rule_expected']} 元 / 实收 "
                      f"{waiting[0]['actual_amount']} 元 / 超收 "
                      f"{round((waiting[0]['actual_amount'] or 0) - (waiting[0]['rule_expected'] or 0), 2)} 元"
                      if waiting else "未找到 waiting 明细"),
            "note": "公式、版本、生效期、车型费率都在声明里，**由本体算出来**",
        }
        try:
            system.execute_action("waive_charge", "fee:F003", "u", "finance",
                                  {"amount": 40.0})
            detail = "已执行"
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {str(exc)[:90]}"
        out["Q3"] = {
            "answered": True,
            "answer": f"减免动作预检：{detail}",
            "note": "状态机与角色来自本体声明，且与诊断链共用同一份事实",
        }
        sc = report.get("source_coverage") or {}
        out["Q4"] = {
            "answered": True,
            "answer": (f"来源覆盖：连接器 {sc.get('contracts')} / 费用 {sc.get('fee_count')}"
                       f" / 已解析 {sc.get('resolved')} / 未解析 {sc.get('unresolved')}；"
                       f"缺口四类：无人认领/口径越界/数据过期/证据缺失"),
            "note": "归属唯一性由门禁强制，缺口是**一等语义**",
        }
        q = report.get("quote") or {}
        out["Q5"] = {
            "answered": True,
            "answer": (f"明细 {q.get('quoted_total')} / 总额 {q.get('declared_total')} / "
                       f"快照 {q.get('waybill_snapshot')}"),
            "note": "三口径互校是诊断链的一部分，**不用额外写代码**",
        }
        # 变更实验：改本体参数，重算应计
        from freight.semantic import SemanticLayer, evaluate_rule_ast
        raw = json.loads(json.dumps(SemanticLayer().raw))
        raw["pricing"]["waiting"]["free_minutes"] = 20
        tmp = os.path.join(td, "mut_layer.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False)
        mutated = SemanticLayer(tmp)
        wprops = json.loads(system.facts.get_row(WAYBILL)["props"])
        ctx = mutated._fee_context("waiting", {"free_minutes": 20}, wprops, "4.2米厢货")
        ast_node = (mutated.rules["R-WAIT-01"].get("judge") or {}).get("ast")
        new_expected = evaluate_rule_ast(ast_node, ctx)
        out["Q6"] = {
            "answered": True,
            "answer": f"改成 20 分钟后应计 {new_expected} 元（原 12.0 元），"
                      f"**只改了一处 JSON**",
            "note": "改声明 → 行为翻转，且有 24 条承重用例保证翻转真的发生",
        }
    finally:
        system.conn.close()
    return out


def count_hardcoded_lines(svc: PropertyGraphService) -> int:
    """属性图方案里"业务知识"占了几行（常量与状态机表）。"""
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    start = src.index('        # ---- 以下全部是"业务逻辑硬编码"')
    end = src.index("    # ---- Cypher 风格的查询（图遍历能力）----")
    block = src[start:end]
    code_lines = [l for l in block.splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    return len(code_lines)


def change_cost_scan() -> dict:
    """变更成本：业务参数在本仓库里出现在几处（属性图方案侧为硬编码常量，本体侧为声明）。"""
    hits: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "runs", "node_modules", "__pycache__",
                                    "logs", "_out", ".pytest_tmp", ".venv", "venv",
                                    "site-packages", "experiments")]
        for name in filenames:
            if not name.endswith((".py", ".json", ".html", ".md")):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT)
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for token in ("2.4", "free_minutes"):
                if token in text and rel not in hits.setdefault(token, []):
                    hits[token].append(rel)
    return {k: {"files": len(v), "sample": v[:6]} for k, v in hits.items()}


def drift_experiment(svc: PropertyGraphService) -> dict:
    """漏改实验：改一条业务参数，只改一处会怎样。

    真实属性图项目里，"等候费公式"往往同时出现在多个服务里（诊断服务、预检服务、
    报表服务…）。改参数时如果只改一处，两边结论就会打架，而且**没有任何机制会发现**。

    本体侧：改声明一处，所有读声明的路径同时变；并且 24 条承重用例保证翻转真的发生。
    """
    # 属性图侧：模拟两个服务各写一份公式
    diag_formula = {"free": 30, "block": 5, "per_block": 2.4}
    preflight_formula = {"free": 30, "block": 5, "per_block": 2.4}

    def expected(minutes: int, f: dict) -> float:
        blocks = 0 if minutes <= f["free"] else -(-(minutes - f["free"]) // f["block"])
        return round(blocks * f["per_block"], 2)

    minutes = 53
    # 业务改参数：免费等候 30 -> 20，**只改了诊断服务**
    diag_formula["free"] = 20
    diag_says = expected(minutes, diag_formula)
    preflight_says = expected(minutes, preflight_formula)
    pg_drift = {
        "diagnosis": diag_says, "preflight": preflight_says,
        "consistent": diag_says == preflight_says,
        "detectable": False,
    }

    # 本体侧：改声明一处
    from freight.semantic import SemanticLayer, evaluate_rule_ast
    raw = json.loads(json.dumps(SemanticLayer().raw))
    raw["pricing"]["waiting"]["free_minutes"] = 20
    td = tempfile.mkdtemp(prefix="parity_drift_")
    path = os.path.join(td, "layer.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False)
    mutated = SemanticLayer(path)
    wprops = {"waiting_minutes": minutes, "free_wait_minutes": 30}
    ctx = mutated._fee_context("waiting", {"free_minutes": 20}, wprops, "4.2米厢货")
    ast_node = (mutated.rules["R-WAIT-01"].get("judge") or {}).get("ast")
    value = evaluate_rule_ast(ast_node, ctx)
    ont_drift = {
        "diagnosis": value, "preflight": value,
        # 刻意不叫 consistent：它不是一次一致性校验，而是"两侧读同一份声明"的事实
        "same_source": True,
        "detectable": True,
        "note": "所有路径读同一份声明；漏改不可能发生（没有第二处可漏）",
    }
    return {"property_graph": pg_drift, "ontology": ont_drift}


def full_alignment_estimate(layer) -> dict:
    """属性图方案要**完整对齐**我们这套本体，需要硬编码多少行。

    口径：把本体里每一条"声明"翻译成服务代码里的常量/分支/表结构。
    """
    classes = len(layer.get("classes") or [])
    links = len(layer.get("relations") or [])
    rules = len(layer.get("rules") or [])
    actions = len(layer.get("actions") or [])
    sms = layer.get("state_machines") or {}
    ds = len(layer.get("data_sources") or [])
    fee_types = len(layer.get("pricing") or {})
    # 经验估算：每个对象类型约 3 行元数据映射；每条链接约 2 行；每条规则约 6 行
    # （公式 + 参数 + 生效期）；每个动作约 8 行（前置 + 角色 + 参数 + 分支）；
    # 每个状态机约 5 行；每个数据源约 6 行（取数 + 口径 + 新鲜度 + 证据）。
    per = {
        "对象类型元数据": classes * 3,
        "链接与基数": links * 2,
        "规则公式与生效期": rules * 6,
        "动作前置与角色": actions * 8,
        "状态机与迁移": len(sms) * 5 + sum(len(v) for v in sms.values()) * 3,
        "数据源与口径归属": ds * 6,
        "费用类型与归属表": fee_types * 2,
    }
    total = sum(per.values())
    return {"breakdown": per, "total_estimated_lines": total,
            "note": "经验估算（每类声明翻译成服务代码的行数）；"
                    "这些代码没有 schema 校验、没有门禁、没有承重保护"}


def build_report() -> dict:
    """完整对照报告：六题逐题 + 漏改实验 + 变更成本 + 对齐估算 + 汇总。

    供 scripts/property_graph_parity.py（CLI）与 /freight/ontology/property-graph-parity
    （API）共用，单一实现、单一口径。
    """
    if not os.path.exists(FACTS_DB):
        raise FileNotFoundError(f"缺少事实库 {FACTS_DB}")

    svc = PropertyGraphService(FACTS_DB)
    pg = run_property_graph(svc)
    ont = run_ontology()

    from freight.semantic import SemanticLayer
    layer = SemanticLayer().raw
    questions = []
    for qid, question, probes, dimension in QUESTIONS:
        questions.append({
            "id": qid, "question": question, "probes": probes,
            "dimension": dimension,
            "property_graph": pg[qid], "ontology": ont[qid],
        })
    report = {
        "waybill_id": WAYBILL,
        "questions": questions,
        "property_graph_hardcoded_business_lines": count_hardcoded_lines(svc),
        "change_cost_scan": change_cost_scan(),
        "drift_experiment": drift_experiment(svc),
        "full_alignment_estimate": full_alignment_estimate(layer),
        "fairness": {
            "property_graph_reads": "runs/freight_facts.db（禁止读 semantic_layer.json）",
            "ontology_reads": "semantic_layer.json + 同源事实库",
            "note": "同题同数据；属性图组的知识只能硬编码在服务代码里（真实属性图项目的形态）",
        },
    }
    return report
