"""把 ontology/ 建模包渲染进 freight/semantic_layer.json（schema v3）。

与旧 expand_ontology.py 的本质区别：旧脚本用同一模板复制 540 次凑数字；
本脚本只做「合并 + 注册表构建 + 结构校验」，业务内容全部来自 ontology/ 下的人工建模。

用法：
    python3 tools/build_ontology_v3.py            # 渲染
    python3 tools/build_ontology_v3.py --check    # 只校验不写盘
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ontology  # noqa: E402

ONTOLOGY_JSON = os.path.join(ROOT, "freight", "semantic_layer.json")
RID_LEDGER = os.path.join(ROOT, "freight", "rid_ledger.json")
VERSION = "3.0.0"
EFFECTIVE_FROM = "2026-11-20"

# 旧手写关系（v2 时代 28 条）里缺 cardinality 的，按业务语义逐条补齐。
# 不用「默认 many_to_one」糊过去：基数错会让写入校验放过真实违规。
LEGACY_CARDINALITY = {
    "shipper_places_order": "one_to_many",
    "fee_supported_by_evidence": "many_to_many",
    "settlement_supported_by_evidence": "many_to_many",
    "invoice_supported_by_evidence": "many_to_many",
    "exception_supported_by_evidence": "many_to_many",
    "waybill_has_stop": "one_to_many",
    "stop_handles_cargo": "many_to_many",
    "stop_supported_by_evidence": "many_to_many",
    "waybill_has_subcontract": "one_to_many",
    "subcontract_awarded_to_carrier": "many_to_one",
    "subcontract_allocates_fee": "one_to_many",
    "subcontract_supported_by_evidence": "many_to_many",
}

# 旧本体里要保留的非类/关系字段
PRESERVED_KEYS = ("meta", "catalog", "closure", "diagnosis", "pricing", "primitives",
                  "functions", "data_sources", "relation_source", "rules", "actions",
                  "derived_types")


def load_domain_modules() -> tuple[list[dict], list[dict], list[dict]]:
    """加载 ontology 包下所有 domains_*.py 与 core_classes.py。"""
    classes: list[dict] = []
    links: list[dict] = []
    domains: list[dict] = []
    pkg_dir = os.path.dirname(ontology.__file__)
    for info in sorted(pkgutil.iter_modules([pkg_dir])):
        name = info.name
        if not (name.startswith("domains_") or name == "core_classes"):
            continue
        mod = importlib.import_module(f"ontology.{name}")
        classes.extend(getattr(mod, "CLASSES", []))
        links.extend(getattr(mod, "LINKS", []))
        domains.extend(getattr(mod, "DOMAINS", []))
    return classes, links, domains


def dedupe(items: list[dict], kind: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in items:
        key = item["id"]
        if key in seen:
            raise SystemExit(f"{kind} id 重复: {key}")
        seen[key] = item
    return list(seen.values())


def strip_private(cls: dict) -> dict:
    return {k: v for k, v in cls.items() if not k.startswith("_")}


def build_property_registry(classes: list[dict]) -> tuple[list[dict], list[str]]:
    """按 Palantir Property Type 模型构建注册表。同名属性合并 used_by，类型冲突即报错。"""
    problems: list[str] = []
    registry: dict[str, dict] = {}
    seen_type: dict[str, tuple[str, str]] = {}  # name -> (value_type, 首次出现的类)

    for cls in classes:
        cid = cls["id"]
        for prop, type_code in cls.get("_props_spec", []):
            entry = ontology.build_property_type(
                prop, type_code, cid, VERSION, EFFECTIVE_FROM)
            if prop not in registry:
                registry[prop] = entry
                seen_type[prop] = (entry["value_type"], cid)
                continue
            prev_type, prev_cls = seen_type[prop]
            if prev_type != entry["value_type"]:
                problems.append(
                    f"属性 {prop} 类型冲突: {prev_cls} 声明 {prev_type}，"
                    f"{cid} 声明 {entry['value_type']}")
                continue
            if cid not in registry[prop]["used_by"]:
                registry[prop]["used_by"].append(cid)
            reg = registry[prop]
            # 任一类把它当 status 用即视为时序属性（timeseries 是属性级语义）
            if entry["timeseries"]:
                reg["timeseries"] = True

    out = [registry[k] for k in sorted(registry)]
    return out, problems


def build_groups(domains: list[dict], classes: list[dict],
                 old_groups: list[dict]) -> list[dict]:
    """重建 object_type_groups：每个域挂自己的类，保留旧域的顺序与标签。"""
    by_domain: dict[str, list[str]] = defaultdict(list)
    for cls in classes:
        gid = cls.get("_group")
        if gid:
            by_domain[gid].append(cls["id"])

    old_by_id = {g["id"]: g for g in old_groups}
    ordered_ids: list[str] = []
    for g in old_groups:
        ordered_ids.append(g["id"])
    for d in domains:
        if d["id"] not in ordered_ids:
            ordered_ids.append(d["id"])

    out: list[dict] = []
    for gid in ordered_ids:
        types = by_domain.get(gid, [])
        old = old_by_id.get(gid, {})
        if not types and not old:
            continue
        out.append({
            "id": gid,
            "label": old.get("label") or next(
                (d["label"] for d in domains if d["id"] == gid), gid),
            "types": types,
        })
    # 兜底：未归属任何域的类型单独成组，避免类「失联」
    assigned = {t for g in out for t in g["types"]}
    orphans = [c["id"] for c in classes if c["id"] not in assigned]
    if orphans:
        out.append({"id": "ungrouped", "label": "未分组", "types": orphans})
    return out


def apply_rid_ledger(classes: list[dict], relations: list[dict]) -> dict:
    """用 rid 台账接管 rid，保证「改名不断链」。

    rid 的默认形态是 uuid5(种类:id) —— 一旦改 id，rid 就变了，所有引用断掉。
    台账（freight/rid_ledger.json）是人类登记的「这个资源一直是这个 rid」，
    优先级高于推导值：改名时在台账里把 新键 -> 旧 rid 写一行，身份就续上了。

    台账不回删：已下线资源的 rid 永久保留，避免 rid 被回收后指到别的对象。
    """
    ledger: dict = {}
    if os.path.exists(RID_LEDGER):
        with open(RID_LEDGER, encoding="utf-8") as f:
            ledger = json.load(f)
    reused = 0
    for kind, items in (("class", classes), ("relation", relations)):
        for item in items:
            item.setdefault("rid", ontology.rid_for(kind, item["id"]))
            key = f"{kind}:{item['id']}"
            if key in ledger:
                if item.get("rid") != ledger[key]:
                    item["rid"] = ledger[key]
                    reused += 1
            else:
                ledger[key] = item["rid"]
    if reused:
        print(f"rid 台账接管 {reused} 条（改名续链）")
    return ledger


def attach_link_display(relations: list[dict], classes_with_group: list[dict]) -> None:
    """链接的展示元数据按 domain 类的属域补齐（链接自身没有域声明）。"""
    group_of = {c["id"]: c.get("_group") for c in classes_with_group}
    for rel in relations:
        rel["display_metadata"] = ontology.display_metadata_of(group_of.get(rel["domain"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写盘")
    args = ap.parse_args()

    new_classes_raw, new_links, domains = load_domain_modules()
    classes = dedupe([strip_private(c) for c in new_classes_raw], "class")
    relations = dedupe(new_links, "relation")

    with open(ONTOLOGY_JSON, encoding="utf-8") as f:
        old = json.load(f)

    # 保留旧本体里的手写关系（生成关系全部淘汰）。
    # 幂等：上一轮渲染写入的关系已在新 LINKS 里，按 id 去重时以新定义优先（带 cardinality）。
    new_rel_ids = {r["id"] for r in relations}
    handwritten_rels = []
    for r in old.get("relations", []):
        if str(r.get("source", "")).startswith("generated"):
            continue
        if r["id"] in new_rel_ids:
            continue
        if not r.get("cardinality") and r["id"] in LEGACY_CARDINALITY:
            r = dict(r, cardinality=LEGACY_CARDINALITY[r["id"]])
        handwritten_rels.append(r)
    relations = dedupe(relations + handwritten_rels, "relation")

    class_ids = {c["id"] for c in classes}
    rel_bad = [r["id"] for r in relations
               if r["domain"] not in class_ids or r["range"] not in class_ids]
    if rel_bad:
        raise SystemExit(f"关系引用了不存在的类: {rel_bad[:10]}")

    # 属性注册表
    prop_spec_map = {c["id"]: c for c in new_classes_raw}
    classes_with_spec = []
    for cls in classes:
        raw = prop_spec_map[cls["id"]]
        cls["_props_spec"] = raw["_props_spec"]
        cls["_group"] = raw.get("_group")
        classes_with_spec.append(cls)
    property_types, problems = build_property_registry(classes_with_spec)
    if problems:
        print("属性注册表冲突：")
        for p in problems[:20]:
            print("  -", p)
        if not args.check:
            raise SystemExit("属性类型冲突，拒绝写盘")

    # 每个类属性必须全覆盖注册表
    reg_names = {p["name"] for p in property_types}
    uncovered = []
    for c in classes:
        for p in c["properties"]:
            if p not in reg_names:
                uncovered.append(f"{c['id']}.{p}")
    if uncovered:
        print(f"未注册属性 {len(uncovered)} 个：{uncovered[:10]}")
        if not args.check:
            raise SystemExit("存在未注册属性，拒绝写盘")

    groups = build_groups(domains, classes_with_spec, old.get("object_type_groups", []))

    attach_link_display(relations, classes_with_spec)
    ledger = apply_rid_ledger(classes, relations)

    meta = dict(old.get("meta", {}))
    meta["version"] = VERSION
    meta.setdefault("name", "freight")
    # scope/note 由实际统计重写：手写死的「558 类 / 568 关系」是上一轮虚胖数字的
    # 残留，文档里留着它比没有更糟——它会让下一个读的人以为规模没变。
    meta["scope"] = (
        f"覆盖 {len(groups)} 个业务域，共 {len(classes)} 类 / {len(relations)} 条链接 / "
        f"{len(property_types)} 个已注册属性。"
    )
    meta["note"] = ("schema v3：Property Type 升为一等公民（对标 Palantir Object Type / "
                    "Property Type / Link Type 三件套）；类级元模型字段保持固定。"
                    "类与链接均带不可变 rid + display_metadata；物化走轻量对象投影。")
    # 已有对象属性投影层 -> storage_policy 不再是 knowledge-only
    meta["storage_policy"] = "projection"

    out = {"meta": meta}
    for key in PRESERVED_KEYS:
        if key == "meta":
            continue
        if key in old:
            out[key] = old[key]
    out["object_type_groups"] = groups
    out["classes"] = [strip_private(c) for c in classes]
    out["relations"] = relations
    out["property_types"] = property_types

    prop_counts = [len(c["properties"]) for c in classes]
    print(f"类 {len(classes)} 个 / 链接 {len(relations)} 条 / 属性 {len(property_types)} 个 / 域 {len(groups)} 个")
    print(f"属性数分布 {dict(sorted(Counter(prop_counts).items()))}")
    print(f"状态机长度分布 {dict(sorted(Counter(len(c['statuses']) for c in classes).items()))}")
    print(f"主键属性 {sum(1 for p in property_types if p['primary_key'])} 个 / "
          f"PII {sum(1 for p in property_types if p['marking'] == 'PII')} 个 / "
          f"FINANCE {sum(1 for p in property_types if p['marking'] == 'FINANCE')} 个")

    if args.check:
        print("check 模式：不写盘")
        return 0

    with open(ONTOLOGY_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(RID_LEDGER, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(ledger.items())), f, ensure_ascii=False, indent=1)
    print(f"已写入 {ONTOLOGY_JSON}")
    print(f"rid 台账 {len(ledger)} 条 -> {RID_LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
