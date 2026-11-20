#!/usr/bin/env python3
"""承重实验：**只改本体 JSON 文件，Python 代码一行不动，行为必须变**。

为什么需要这个脚本
------------------
单测里的 `make_ext(mutator)` 是在内存 dict 上做变换，能证明逻辑分支存在，
但证明不了「本体文件是唯一真相」——代码里完全可以再写一份默认值兜底，
测试照样绿。这里换一种更硬的口径：

1. 把仓库的运行时目录整份拷到 `runs/load_bearing_<ts>/`；
2. 在**拷贝里**用 `open()/json.dump` 真真切切改 JSON 文件；
3. 用**子进程**、cwd=拷贝目录 跑探针（模块的 DEFAULT_PATH 因此指向拷贝）；
4. 比对「改前 / 改后」的观测值。

代码零改动、只动 JSON，观测值翻转 -> 说明该约束真的由 JSON 驱动。

用法
----
    python scripts/load_bearing_demo.py            # 跑全部用例
    python scripts/load_bearing_demo.py --keep     # 保留 sandbox 便于手工复核
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# 门禁除了 freight/enhancements 还要读根级 semantic_layer.json、schema/、tests/rules、tests/shacl
COPY_DIRS = ["freight", "enhancements", "schema", "scripts", "ontology", "tools",
             "tests", "mobility"]
COPY_FILES = ["semantic_layer.json", "pyproject.toml"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".pytest_tmp"}
PROBE_MARK = "##PROBE##"


# ======================================================================
# 探针（在 sandbox 子进程里执行）
# ======================================================================
def _sandbox_on_path(root: str) -> None:
    """让 import 命中 sandbox 里的模块，而不是本脚本所在的原仓库。"""
    here = os.path.abspath(HERE)
    kept = [p for p in sys.path if os.path.abspath(p or ".") != here]
    sys.path[:] = [root] + kept


def _fresh_system(seed: bool = True):
    import tempfile

    from freight.engine import FreightSystem

    td = tempfile.mkdtemp(prefix="lb_")
    return FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=seed)


def _err(exc: BaseException) -> dict:
    return {"ok": False, "error": type(exc).__name__,
            "code": getattr(exc, "code", None), "message": str(exc)[:220]}


def probe_gate(root: str, args: dict) -> dict:
    from scripts import semantic_gate as g

    fn = {
        "extension_schema": g.check_extension_schema,
        "policy": g.check_policy_strict,
        "display": g.check_display_metadata,
        "state_machines": g.check_state_machines,
    }[args["check"]]
    item = fn(root)
    return {"ok": item["ok"],
            "codes": sorted({i["code"] for i in item.get("issues") or []})}


def probe_gate_full(root: str, args: dict) -> dict:
    """跑完整门禁（24 项），返回总出口码与失败项。"""
    env = dict(os.environ, PYTHONPATH=root)
    proc = subprocess.run([sys.executable, "-m", "scripts.semantic_gate"],
                          cwd=root, env=env, capture_output=True, text=True)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    if proc.returncode != 0 and not tail:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
    return {"ok": proc.returncode == 0, "exit": proc.returncode, "tail": tail}


def probe_boot(root: str, args: dict) -> dict:
    """种子回放：注册表一改，种子数据能不能过类型校验。"""
    try:
        s = _fresh_system(seed=True)
        return {"ok": True, "objects": s.facts.scalar("SELECT COUNT(*) FROM objects")}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def probe_create(root: str, args: dict) -> dict:
    """只测写入路径：seed=False，避开「种子数据先把异常挡住」。"""
    s = _fresh_system(seed=False)
    try:
        s.add_object("Waybill", "waybill:LBPROBE", "承重探针", None,
                     {"waybill_id": "LBPROBE", **args.get("props", {})})
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - 探针就是要看异常
        return _err(exc)


def probe_update(root: str, args: dict) -> dict:
    """动作侧 UPDATE 路径：先建一个干净对象，再改一个字段。"""
    s = _fresh_system(seed=False)
    try:
        s.add_object("Waybill", "waybill:LBUPD", "更新探针", None,
                     {"waybill_id": "LBUPD", **args.get("create", {})})
        view = s.get_object("waybill:LBUPD")
        props = dict(view["properties"])
        props.update(args.get("props", {}))
        s.facts.update_object("waybill:LBUPD", props, None, view["status"],
                              "2026-11-20T00:00:00+08:00")
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def probe_call(root: str, args: dict) -> dict:
    from enhancements.runtime import EnhancedSystem

    e = EnhancedSystem(_fresh_system())
    try:
        out = e.call_function(args.get("fn", "fee_breakdown"),
                              args.get("args", {}))
        return {"ok": True, "fields": len(out) if isinstance(out, dict) else None}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def probe_properties(root: str, args: dict) -> dict:
    from enhancements.runtime import EnhancedSystem

    e = EnhancedSystem(_fresh_system())
    try:
        props = e.properties.properties_of(args.get("class", "Waybill"))
        return {"ok": True, "count": len(props),
                "legacy": sorted(p.name for p in props if not p.registered)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def probe_security(root: str, args: dict) -> dict:
    from enhancements.runtime import EnhancedSystem
    from enhancements.security import SecurityContext

    e = EnhancedSystem(_fresh_system())
    cid = args.get("class", "Shipper")
    prop = args.get("prop")
    if prop:
        # 必须挑「真的带这个属性」的行：脱敏只对 props 里存在的列生效，
        # 随便抓第一行会因为该行没这列而看不到 mask，得出假的"没生效"。
        row = e.store.one(
            "SELECT * FROM objects WHERE type=? AND props LIKE ? LIMIT 1",
            (cid, f'%"{prop}"%'))
    else:
        row = e.store.one("SELECT * FROM objects WHERE type=? LIMIT 1", (cid,))
    if row is None:
        return {"ok": False, "error": "NO_ROW"}
    props = json.loads(row["props"])
    ctx = SecurityContext(user="probe", roles=set(), markings=set())
    masked = e.security.mask_properties(cid, props, ctx)
    return {
        "ok": True,
        "visible": e.security.visible_object(cid, props, ctx),
        "trace": e.security.marking_trace(cid, props),
        "deny": e.security.deny_reason(cid, props, ctx),
        "nulled": sorted(k for k, v in masked.items() if v is None),
    }


def probe_review(root: str, args: dict) -> dict:
    from freight import change_gate

    def load(rel):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return json.load(f)

    out = change_gate.review(
        load("freight/_pristine_layer.json"),
        load("freight/semantic_layer.json"),
        load("enhancements/ontology_extension.json"),
        root,
    )
    return {"ok": True, "verdict": out["verdict"],
            "breaking": out["breaking"],
            "unapproved": out["unapproved_count"],
            "codes": sorted({c["code"] for c in out["changes"]})}


def probe_projection(root: str, args: dict) -> dict:
    from freight import projection
    from freight.object_query import ObjectQueryEngine

    s = _fresh_system()
    q = {"set": {"from": "Waybill", "where": {"status": {"$eq": "DELIVERED"}}}}
    base = ObjectQueryEngine(s.sem).execute(q, s.facts.store)["total"]
    broken, stale, healed = base, False, base
    if args.get("break"):
        s.conn.execute("DELETE FROM object_props WHERE prop='status'")
        s.conn.commit()
        eng = ObjectQueryEngine(s.sem)
        broken = eng.execute(q, s.facts.store)["total"]
        stale = eng.last_projection_stale
        projection.sync_all(s.conn)
        s.conn.commit()
        healed = ObjectQueryEngine(s.sem).execute(q, s.facts.store)["total"]
    return {"ok": True, "baseline": base, "after_break": broken,
            "stale_flag": stale, "healed": healed,
            "consistency": len(projection.consistency(s.conn))}



def probe_action_param(root: str, args: dict) -> dict:
    """动作参数类型化：同一调用，改声明后必须被拒。"""
    s = _fresh_system(seed=False)
    try:
        oid = "fee:LBPROBE"
        s.add_object("FeeItem", oid, "承重探针", "NORMAL",
                     {"fee_id": "LBPROBE", "fee_type": "waiting",
                      "actual_amount": 100, "quoted_amount": 100})
        out = s.execute_action("dispute_charge", oid, "probe", "customer_service",
                                {"reason": "金额争议"})
        return {"ok": True, "after_status": out.get("after_status")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__,
                "code": getattr(exc, "code", None), "message": str(exc)[:200]}


PROBES = {
    "gate": probe_gate,
    "gate_full": probe_gate_full,
    "boot": probe_boot,
    "create": probe_create,
    "update": probe_update,
    "call": probe_call,
    "properties": probe_properties,
    "security": probe_security,
    "review": probe_review,
    "projection": probe_projection,
    "action_param": probe_action_param,
}


# ======================================================================
# 文件变更（全部是「只改 JSON 文件」）
# ======================================================================
def _edit(root: str, rel: str, fn) -> None:
    path = os.path.join(root, rel)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw = fn(raw)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


EXT = "enhancements/ontology_extension.json"
LAYER = "freight/semantic_layer.json"
APPROVALS = "freight/breaking_approvals.json"


def mut_bogus_key(raw):
    raw["totally_bogus_top_key"] = {"x": 1}
    return raw


def mut_bad_value_type(raw):
    raw["property_types"][0]["value_type"] = "not_a_real_type"
    return raw


def mut_relax_policy(raw):
    raw["property_registry_policy"] = {"enforcement": "incremental",
                                       "legacy_grace": True}
    return raw


def mut_param_type(raw):
    for fn in raw["function_registry"]:
        if fn["id"] == "fee_breakdown":
            for p in fn["signature"]["params"]:
                if p["name"] == "waybill_id":
                    p["type"] = "integer"
    return raw


def mut_shrink_result(raw):
    for rt in raw["result_types"]:
        if rt["id"] == "FeeBreakdown":
            rt["fields"] = [f for f in rt["fields"] if f["name"] != "conclusion"]
    return raw


def mut_ds_marking(raw):
    raw["data_source_markings"] = list(raw.get("data_source_markings") or []) + [{
        "system": "CRM", "marking": "FINANCE",
        "note": "承重实验：只加这一条声明，Shipper 整行应当立刻对无 FINANCE 的用户不可见。",
    }]
    return raw


def mut_waiting_bool(raw):
    for p in raw["property_types"]:
        if p["name"] == "waiting_minutes":
            p["value_type"] = "boolean"
    return raw


def mut_waiting_pii(raw):
    """改**扩展层**那份定义——扩展层是有效来源（覆盖基础层）。"""
    for p in raw["property_types"]:
        if p["name"] == "waiting_minutes":
            p["marking"] = "PII"
    return raw


def mut_waiting_pii_base(raw):
    """只改基础层那份——应被门禁判为二源真相分叉，而不是静默降级。"""
    for p in raw["property_types"]:
        if p["name"] == "waiting_minutes":
            p["marking"] = "PII"
    return raw


def mut_tamper_rid(raw):
    for c in raw["classes"]:
        if c["id"] == "Waybill":
            c["rid"] = c["rid"][:-1] + ("0" if c["rid"][-1] != "0" else "1")
    return raw


def mut_rename_label(raw):
    for c in raw["classes"]:
        if c["id"] == "Waybill":
            c["label"] = "运单（改名承重实验）"
            c["plural_display_name"] = "运单（改名承重实验）"
    return raw


def mut_drop_property(raw):
    for c in raw["classes"]:
        if c["id"] == "Waybill":
            c["properties"] = [p for p in c["properties"] if p != "waiting_minutes"]
    return raw


def mut_approve_drop(raw):
    raw.setdefault("approvals", []).append({
        "path": "classes[Waybill].properties[waiting_minutes]",
        "code": "PROPERTY_REMOVED",
        "approved_by": "load_bearing_demo",
        "reason": "承重实验：补签后评审应放行，用来证明 FAIL 来自「未签字」而不是「一律拒绝」。",
    })
    return raw



def mut_bad_state_machine(raw):
    """把 Waybill 的终态改成还有迁出边的 ACCEPTED -> 完备性判据必须报警。"""
    raw["state_machines"]["Waybill"]["terminal"] = ["ACCEPTED"]
    return raw


def mut_param_type_number(raw):
    """把 dispute_charge.reason 的声明类型从 string 改成 number。"""
    for a in raw.get("actions") or []:
        if a["id"] == "dispute_charge":
            for p in a.get("params") or []:
                if p["name"] == "reason":
                    p["type"] = "number"
    return raw


# ======================================================================
# 用例表
# ======================================================================
CASES = [
    {
        "id": "L1", "gap": "P0-1 扩展层 schema",
        "change": f"{EXT} 顶层塞入垃圾键 totally_bogus_top_key",
        "file": EXT, "mut": mut_bogus_key,
        "probe": ("gate", {"check": "extension_schema"}),
        "expect": "门禁由 PASS 变 FAIL（垃圾键被 schema 拦住）",
        "check": lambda b, m: b["ok"] and not m["ok"],
    },
    {
        "id": "L2", "gap": "P0-1 扩展层 schema",
        "change": f"{EXT} property_types[0].value_type 改成 not_a_real_type",
        "file": EXT, "mut": mut_bad_value_type,
        "probe": ("gate", {"check": "extension_schema"}),
        "expect": "门禁变 FAIL（value_type 只能取枚举内 12 个值）",
        "check": lambda b, m: b["ok"] and not m["ok"],
    },
    {
        "id": "L3", "gap": "P0-2 属性类型执行（启动即拦）",
        "change": f"{LAYER} waiting_minutes.value_type: number -> boolean",
        "file": LAYER, "mut": mut_waiting_bool,
        "probe": ("boot", {}),
        "expect": "种子回放由成功变 OntologyViolation——注册表一改，系统起不来",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and m["error"] == "OntologyViolation",
    },
    {
        "id": "L4", "gap": "P0-2 属性类型执行（创建路径）",
        "change": f"同上（只改 {LAYER} 一个字段）",
        "file": LAYER, "mut": mut_waiting_bool,
        "probe": ("create", {"props": {"waiting_minutes": 12}}),
        "expect": "同一笔创建（等待 12 分钟）由成功变类型不符",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and m["error"] == "OntologyViolation",
    },
    {
        "id": "L5", "gap": "P0-2 属性类型执行（更新路径）",
        "change": f"同上，但走动作侧 UPDATE 而不是新建",
        "file": LAYER, "mut": mut_waiting_bool,
        "probe": ("update", {"props": {"waiting_minutes": 12}}),
        "expect": "直接改一个字段也被拒——校验不是只在新建生效",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and m["error"] == "OntologyViolation",
    },
    {
        "id": "L6", "gap": "P0-3 策略口径",
        "change": f"{EXT} policy 改回 incremental + legacy_grace=true",
        "file": EXT, "mut": mut_relax_policy,
        "probe": ("gate", {"check": "policy"}),
        "expect": "门禁变 FAIL（宽松口径不许存在）",
        "check": lambda b, m: b["ok"] and not m["ok"],
    },
    {
        "id": "L7", "gap": "P1-2 函数参数类型化",
        "change": f"{EXT} fee_breakdown.waybill_id 参数 type: string -> integer",
        "file": EXT, "mut": mut_param_type,
        "probe": ("call", {"fn": "fee_breakdown",
                           "args": {"waybill_id": "waybill:WB20261105001"}}),
        "expect": "同一调用由成功变 SIGNATURE_INVALID（参数类型真的被校验）",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and m["code"] == "SIGNATURE_INVALID",
    },
    {
        "id": "L8", "gap": "P1-2 返回类型",
        "change": f"{EXT} Result Type FeeBreakdown 删掉 conclusion 字段声明",
        "file": EXT, "mut": mut_shrink_result,
        "probe": ("call", {"fn": "fee_breakdown",
                           "args": {"waybill_id": "waybill:WB20261105001"}}),
        "expect": "由成功变 RETURN_FIELD_UNDECLARED（返回值对不上声明就要报）",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and m["code"] in ("RETURN_FIELD_UNDECLARED", "RETURN_FIELD_MISSING"),
    },
    {
        "id": "L9", "gap": "P1-1 rid 不可变",
        "change": f"{LAYER} Waybill.rid 末位改一个字符（模拟手改本体）",
        "file": LAYER, "mut": mut_tamper_rid,
        "probe": ("gate", {"check": "display"}),
        "expect": "门禁变 FAIL，报 RID_LEDGER_DRIFT（台账是权威，手改断链要响）",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and "RID_LEDGER_DRIFT" in m["codes"],
    },
    {
        "id": "L10", "gap": "P1-1 rid 与可读名解耦",
        "change": f"{LAYER} Waybill 的 label / plural_display_name 改名",
        "file": LAYER, "mut": mut_rename_label,
        "probe": ("gate", {"check": "display"}),
        "expect": "门禁仍 PASS——改名不断链，身份只由 rid 承载",
        "check": lambda b, m: b["ok"] and m["ok"],
    },
    {
        "id": "L11", "gap": "P2-2 marking 传播（行级）",
        "change": f"{EXT} data_source_markings 增加「CRM -> FINANCE」一条",
        "file": EXT, "mut": mut_ds_marking,
        "probe": ("security", {"class": "Shipper"}),
        "expect": "Shipper 由可见变整行不可见，拒绝原因指认 CRM 来源",
        "check": lambda b, m: b["ok"] and b["visible"] is True
        and m["visible"] is False and bool(m["deny"]),
    },
    {
        "id": "L12", "gap": "P2-2 marking 层级纪律",
        "change": f"{EXT} waiting_minutes.marking = PII（列级，扩展层是有效来源）",
        "file": EXT, "mut": mut_waiting_pii,
        "probe": ("security", {"class": "Waybill", "prop": "waiting_minutes"}),
        "expect": "行仍可见、只把该列置 null——属性标记不得升级成整行不可见",
        "check": lambda b, m: m["ok"] and m["visible"] is True
        and "waiting_minutes" not in b["nulled"]
        and "waiting_minutes" in m["nulled"],
    },
    {
        "id": "L12b", "gap": "P0-1 二源真相",
        "change": f"只改 {LAYER} 那份 marking，扩展层仍是 null（静默降级）",
        "file": LAYER, "mut": mut_waiting_pii_base,
        "probe": ("gate", {"check": "extension_schema"}),
        "expect": "门禁变 FAIL 报 PROP_MARKING_DIVERGED——脱敏不会被悄悄绕过",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and "PROP_MARKING_DIVERGED" in m["codes"],
    },
    {
        "id": "L13", "gap": "P2-3 破坏性变更门禁",
        "change": f"{LAYER} 从 Waybill.properties 移除 waiting_minutes",
        "file": LAYER, "mut": mut_drop_property,
        "probe": ("review", {}),
        "expect": "评审由 PASS 变 FAIL，未签字 1 条（PROPERTY_REMOVED）",
        "check": lambda b, m: b["verdict"] == "PASS" and m["verdict"] == "FAIL"
        and "PROPERTY_REMOVED" in m["codes"] and m["unapproved"] == 1,
    },
    {
        "id": "L14", "gap": "P2-3 签字放行",
        "change": f"在上一条基础上，给 {APPROVALS} 补一条签字",
        "file": LAYER, "mut": mut_drop_property,
        "mut2": (APPROVALS, mut_approve_drop),
        "probe": ("review", {}),
        "expect": "评审回到 PASS——FAIL 来自「未签字」，不是一律拒绝",
        "check": lambda b, m: b["verdict"] == "PASS" and m["breaking"] is True,
    },
    {
        "id": "L15", "gap": "P2-1 物化新鲜度闸门",
        "change": "把投影写脏（DELETE object_props WHERE prop='status'）",
        "file": None, "mut": None,
        "probe": ("projection", {}), "alt_args": {"break": True},
        "expect": "闸门判定不新鲜 -> 回退全表，条数与基线一致（不静默少行）",
        "check": lambda b, m: b["ok"] and b["stale_flag"] is False
        and m["stale_flag"] is True and m["after_break"] == b["baseline"]
        and m["healed"] == b["baseline"] and m["consistency"] == 0,
    },
    {
        "id": "L16", "gap": "P2-4 状态机完备性",
        "change": f"{LAYER} state_machines[Waybill].terminal 改成 ACCEPTED",
        "file": LAYER, "mut": mut_bad_state_machine,
        "probe": ("gate", {"check": "state_machines"}),
        "expect": "门禁变 FAIL 报 STATE_MACHINE_TERMINAL_HAS_OUTGOING",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and "STATE_MACHINE_TERMINAL_HAS_OUTGOING" in m["codes"],
    },
    {
        "id": "L17", "gap": "P2-5 动作参数类型化",
        "change": f"{LAYER} dispute_charge.reason 参数 type: string -> number",
        "file": LAYER, "mut": mut_param_type_number,
        "probe": ("action_param", {}),
        "expect": "同一调用（传字符串）由成功变 VALIDATION_ERROR",
        "check": lambda b, m: b["ok"] and not m["ok"]
        and m["code"] == "VALIDATION_ERROR",
    },
]



# ======================================================================
# 驱动器
# ======================================================================
def make_sandbox() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(ROOT, "runs", f"load_bearing_{ts}")
    os.makedirs(dest, exist_ok=True)
    for d in COPY_DIRS:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dest, d),
                        ignore=shutil.ignore_patterns(*SKIP_DIRS),
                        dirs_exist_ok=True)
    for f in COPY_FILES:
        src = os.path.join(ROOT, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, f))
    shutil.copy2(os.path.join(ROOT, LAYER),
                 os.path.join(dest, "freight", "_pristine_layer.json"))
    return dest


def run_probe(root: str, name: str, args: dict) -> dict:
    cmd = [sys.executable, os.path.abspath(__file__), "--probe", name,
           "--root", root, "--args", json.dumps(args, ensure_ascii=False)]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=root))
    for line in (proc.stdout or "").splitlines():
        if line.startswith(PROBE_MARK):
            return json.loads(line[len(PROBE_MARK):])
    return {"ok": False, "error": "PROBE_CRASH", "code": None,
            "message": (proc.stderr or proc.stdout or "")[-400:]}


def _restore(root: str, files: list[str]) -> None:
    for rel in files:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(root, rel)
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.remove(dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe")
    ap.add_argument("--root")
    ap.add_argument("--args", default="{}")
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args()

    if ns.probe:
        _sandbox_on_path(ns.root)
        out = PROBES[ns.probe](ns.root, json.loads(ns.args))
        print(PROBE_MARK + json.dumps(out, ensure_ascii=False))
        return 0

    sb = make_sandbox()
    print(f"[sandbox] {sb}\n")
    rows, failures = [], []
    for case in CASES:
        files = [rel for rel in (case["file"],) if rel]
        if case.get("mut2"):
            files.append(case["mut2"][0])
        _restore(sb, files)
        probe, args = case["probe"]
        baseline = run_probe(sb, probe, args)
        if case["mut"]:
            _edit(sb, case["file"], case["mut"])
        if case.get("mut2"):
            _edit(sb, case["mut2"][0], case["mut2"][1])
        mutant = run_probe(sb, probe, case.get("alt_args") or args)
        ok = case["check"](baseline, mutant)
        rows.append({"id": case["id"], "gap": case["gap"],
                     "change": case["change"], "expect": case["expect"],
                     "baseline": baseline, "mutant": mutant, "pass": ok})
        if not ok:
            failures.append(case["id"])
        _restore(sb, files)
        print(f"[{case['id']:>3}] {'PASS' if ok else 'FAIL'}  {case['expect']}")
        print(f"        改前 {json.dumps(baseline, ensure_ascii=False)[:160]}")
        print(f"        改后 {json.dumps(mutant, ensure_ascii=False)[:160]}\n")

    gate = run_probe(sb, "gate_full", {})
    total, good = len(rows), sum(1 for r in rows if r["pass"])
    print("=" * 78)
    print(f"承重用例 {good}/{total} 通过；改回原样后全量门禁 exit={gate.get('exit')}"
          f" ok={gate.get('ok')}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(ROOT, "logs", f"load_bearing_{ts}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"cases": rows, "gate_after": gate,
                   "passed": good, "total": total}, f,
                  ensure_ascii=False, indent=2)
    print(f"[log] {out_json}")
    if not ns.keep:
        shutil.rmtree(sb, ignore_errors=True)
    return 0 if not failures and gate.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
