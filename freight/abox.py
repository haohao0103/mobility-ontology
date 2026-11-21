"""ABox 深度校验：深水区覆盖度量（门禁第 26 项的判据内核）。

为什么要有这个模块
------------------
本体"能表达"不等于本体"是真的"。仓库里有 500 个对象类型、1961 个属性，
但如果其中大部分没有任何对象实例、没有边、动作从没被执行过，那么我们证明的
只是**形式合法**，不是**业务为真**。

本模块把"深水区"（声明了 data_sources / 迁移动作 / 状态机的对象类型）的
覆盖度算出来，让它成为**可自动判定的门禁**，而不是报告里的一段描述。

判定口径（与 plan/abox-depth-plan.md 的 V1~V7 对应）：
- V1：深水区每个类至少有 1 个对象实例；
- V2：深水区每个类**不是孤岛**（至少出现在一条关系实例的一端）。
  注意：不能要求「每个类都有出边」——货物、司机、车辆、轨迹、证据、计价规则
  在业务上就是叶子，它们只被引用、不引用别人；按出边判会把正常模型判成缺陷。
  **有迁移动作的类**才额外要求双向有边（它要能被上游指向、也要能驱动下游）；
- V3：每个迁移动作至少有 1 条 `APPROVED` 审计（动作真的被执行过，不是只声明）；
- V4：审计记录里的状态迁移与本体声明的 `to_status` 一致（跑出来的和声明的对得上）；
- V6：规则分支覆盖（哪些规则被真实判定过）。

**边界**：门槛只约束深水区，不约束全量类型——全量覆盖低是建模完备度问题，
硬拉会诱导造垃圾数据；深水区是业务主链路，覆盖不足就是真缺口。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile


def core_types(layer) -> list[str]:
    """深水区类型清单：本体 `abox_targets.core_types` 优先，否则由 data_sources /
    actions / state_machines 派生。清单在本体里，改一处就改一处（承重）。"""
    target = (layer.raw.get("abox_targets") or {})
    declared = target.get("core_types")
    if declared:
        return sorted(dict.fromkeys(declared))
    derived = set()
    for ds in layer.raw.get("data_sources") or []:
        if ds.get("class"):
            derived.add(ds["class"])
    for action in layer.raw.get("actions") or []:
        if action.get("target"):
            derived.add(action["target"])
    derived |= set(layer.raw.get("state_machines") or {})
    return sorted(derived)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def measure(layer, db_path: str) -> dict:
    """深水区覆盖实测（只读）。返回逐类/逐动作/迁移边/规则的覆盖明细。"""
    core = core_types(layer)
    target = (layer.raw.get("abox_targets") or {})
    conn = _connect(db_path)
    try:
        inst = {r["type"]: r["n"] for r in conn.execute(
            "SELECT type, COUNT(*) n FROM objects GROUP BY type")}
        inbound: dict[str, int] = {}
        outbound: dict[str, int] = {}
        for r in conn.execute(
                "SELECT subject_type, object_type, COUNT(*) n "
                "FROM relations GROUP BY subject_type, object_type"):
            outbound[r["subject_type"]] = outbound.get(r["subject_type"], 0) + r["n"]
            inbound[r["object_type"]] = inbound.get(r["object_type"], 0) + r["n"]
        exercised = {r["action_id"] for r in conn.execute(
            "SELECT DISTINCT action_id FROM audit WHERE decision='APPROVED'")}

        # V4：迁移边实测 —— 审计记录里的 after 必须与本体声明的 to_status 一致
        declared_edges = {}
        for a in layer.raw.get("actions") or []:
            if a.get("to_status"):
                declared_edges[a["id"]] = {"target": a["target"],
                                           "from": list(a.get("from_status") or []),
                                           "to": a["to_status"]}
        transitions = []
        for r in conn.execute(
                "SELECT action_id, object_id, result FROM audit "
                "WHERE decision='APPROVED' ORDER BY id"):
            try:
                res = json.loads(r["result"]) if isinstance(r["result"], str) else {}
            except Exception:
                res = {}
            decl = declared_edges.get(r["action_id"])
            if not decl:
                continue
            transitions.append({
                "action": r["action_id"],
                "object_id": r["object_id"],
                "declared_to": decl["to"],
                "observed_to": res.get("after_status"),
                "consistent": res.get("after_status") == decl["to"],
            })

        # V6：规则分支覆盖 —— 对库里每个对象跑一遍规则匹配，统计被判定过的规则
        rule_hits: dict[str, int] = {}
        for r in conn.execute("SELECT type, props FROM objects"):
            try:
                props = json.loads(r["props"])
            except Exception:
                props = {}
            for rule in layer.rules_for(r["type"], props):
                rule_hits[rule["id"]] = rule_hits.get(rule["id"], 0) + 1
    finally:
        conn.close()

    per_class = []
    for t in core:
        per_class.append({
            "type": t,
            "instances": inst.get(t, 0),
            "inbound_edges": inbound.get(t, 0),
            "outbound_edges": outbound.get(t, 0),
        })
    action_ids = sorted({a["id"] for a in layer.raw.get("actions") or []})
    action_types = {a["target"] for a in layer.raw.get("actions") or [] if a["target"]}
    per_action = [{"action": a, "exercised": a in exercised} for a in action_ids]
    covered = sum(1 for c in per_class if c["instances"] > 0)
    edged = sum(1 for c in per_class
                if c["inbound_edges"] > 0 or c["outbound_edges"] > 0)
    acted = sum(1 for a in per_action if a["exercised"])
    declared_rules = sorted(rule["id"] for rule in layer.raw.get("rules") or [])
    return {
        "core_types": core,
        "per_class": per_class,
        "per_action": per_action,
        "action_types": sorted(action_types),
        "transitions": transitions,
        "transition_consistency": {
            "total": len(transitions),
            "consistent": sum(1 for t in transitions if t["consistent"]),
            "inconsistent": [t for t in transitions if not t["consistent"]],
        },
        "rules": {
            "declared": len(declared_rules),
            "judged": len(rule_hits),
            "hits": rule_hits,
            "unjudged": [r for r in declared_rules if r not in rule_hits],
        },
        "class_coverage": {"covered": covered, "total": len(per_class),
                           "ratio": (covered / len(per_class)) if per_class else 0.0},
        "edge_coverage": {"covered": edged, "total": len(per_class),
                          "ratio": (edged / len(per_class)) if per_class else 0.0},
        "action_coverage": {"covered": acted, "total": len(per_action),
                            "ratio": (acted / len(per_action)) if per_action else 0.0},
        "options": {
            "require_instance": bool(target.get("require_instance", True)),
            "require_not_isolated": bool(target.get("require_not_isolated", True)),
            "require_action_class_edges": bool(target.get("require_action_class_edges", True)),
            "require_action_exercised": bool(target.get("require_action_exercised", True)),
        },
    }


def check(layer, db_path: str) -> list[dict]:
    """深水区覆盖判据：返回问题清单（空列表 = 达标）。"""
    m = measure(layer, db_path)
    opts = m["options"]
    issues: list[dict] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append({"code": code, "path": path, "message": message})

    action_types = set(m.get("action_types") or [])
    for c in m["per_class"]:
        if opts["require_instance"] and c["instances"] == 0:
            add("ABOX_CLASS_NO_INSTANCE", f"abox_targets.core_types[{c['type']}]",
                f"深水区类型 {c['type']} 没有任何对象实例——本体能表达，但没被业务证明过")
        if (opts["require_not_isolated"]
                and c["inbound_edges"] == 0 and c["outbound_edges"] == 0):
            add("ABOX_CLASS_ISOLATED", f"abox_targets.core_types[{c['type']}]",
                f"{c['type']} 没有任何关系实例（既无入边也无出边）——它是孤岛")
        if (opts["require_action_class_edges"] and c["type"] in action_types
                and (c["inbound_edges"] == 0 or c["outbound_edges"] == 0)):
            add("ABOX_EDGE_MISSING", f"abox_targets.core_types[{c['type']}]",
                f"{c['type']} 有迁移动作，但关系实例不是双向的"
                f"（入 {c['inbound_edges']} / 出 {c['outbound_edges']}）")
    for a in m["per_action"]:
        if opts["require_action_exercised"] and not a["exercised"]:
            add("ABOX_ACTION_UNEXERCISED", f"actions[{a['action']}]",
                f"动作 {a['action']} 从未被真实执行过（无 APPROVED 审计）")
    # V4：跑出来的迁移必须与本体声明一致（声明与执行对不上，等于两套真相）
    for t in m["transition_consistency"]["inconsistent"]:
        add("ABOX_TRANSITION_MISMATCH", f"actions[{t['action']}]",
            f"动作 {t['action']} 在 {t['object_id']} 上跑出的状态是 "
            f"{t['observed_to']}，本体声明是 {t['declared_to']}")
    # V6：规则分支覆盖——声明了却从没有任何对象触发过的规则，等于没在跑
    for rid in m["rules"]["unjudged"]:
        add("ABOX_RULE_UNJUDGED", f"rules[{rid}]",
            f"规则 {rid} 在剧本数据里从未被判定过——它可能根本不生效")
    return issues


def build_db(with_scenario: bool = True, prefix: str = "abox_") -> str:
    """建一个临时库：种子数据 + 核心业务剧本，返回库路径（调用方负责清理）。

    剧本（`freight/scenarios.py`）会真实执行 15 个迁移动作，所以覆盖度
    度量到的不是"声明了多少"，而是"真的跑过多少"。
    """
    from freight.engine import FreightSystem

    td = tempfile.mkdtemp(prefix=prefix)
    path = os.path.join(td, "facts.db")
    system = FreightSystem(path, fresh=True, seed=True)
    if with_scenario:
        from freight import scenarios

        scenarios.run_core_scenario(system)
        system.conn.commit()
    system.conn.close()
    return path


# 兼容旧名
seed_db_path = build_db
