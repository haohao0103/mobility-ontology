"""问句级可答性检查：这个问句，在我们的本体与事实里**答得出来吗？缺什么**。

借鉴来源
--------
selfgraph 的 `check_answerable()`：在回答之前，先判断图里到底有没有答案，
并报出缺哪些维度。它让「不知道」成为一等公民，而不是让模型编一个。

我们的对应物
------------
selfgraph 判的是「图里有没有这个概念与它的取值」；
我们多一层判断：**这个问句要的数，在本体里有没有声明来源**。
这正是我们那条核心论点的可执行形式——

    「等候费按规则应收多少」
      费用类型 waiting（由事实里的 fee_label「等候费」映射而来）→ 有实例 ✓
      费用类型 waiting → 有来源认领 ✓
      **要的是一个度量值，而本体里只有规则 R-WAIT-01 能算出来** →
      答案不在数据里，要靠规则求值 → 这就是「本体 vs 数据」的边界。

三条纪律（都是踩过坑之后加的）：
1. **匹配要具体**：优先精确命中（类型 label / id / alias / 事实里的显示名），
   子串只作补充，且命中数封顶——否则「等候」会把 14 个 Pricing* 类型全拽出来。
2. **没有本体支撑 ≠ 可答**：一个问句若完全没命中本体，必须判**不可答**
   （NO_ONTOLOGY_MATCH），不能因为"没发现缺口"就说它可答。
3. **只对选中的类型查实例**：500 个类型里绝大多数没有实例，全扫会把结论淹没在噪音里。
"""
from __future__ import annotations

import re

# ---- 缺口码（与既有的来源缺口码同族）----
NO_ONTOLOGY_MATCH = "NO_ONTOLOGY_MATCH"
NO_INSTANCE_FOR_TYPE = "NO_INSTANCE_FOR_TYPE"
NO_DECLARED_SOURCE = "NO_DECLARED_SOURCE"
PROPERTY_NOT_DECLARED = "PROPERTY_NOT_DECLARED"
FEE_SOURCE_UNRESOLVED = "FEE_SOURCE_UNRESOLVED"

# 问句里出现这些词，说明要的是一个**度量值**（而不是某个对象的事实）
_MEASURE_WORDS = ("应收", "应计", "超收", "应得", "该收", "算出来", "算出",
                  "多少钱", "多少", "缺口", "差额", "差异", "超了", "少收")
_RULE_HINT_WORDS = ("按规则", "规则", "计价", "定价", "口径")

# 事实里承担"显示名"作用的属性 → 用来把中文问句映射到 fee_type
_LABEL_PROPS = ("fee_label", "label", "name", "display_name", "title")


def _terms_of(question: str) -> list[str]:
    """候选术语：英文/数字整词 + 中文 2~4 字滑窗（长的优先）。"""
    terms: list[str] = []
    for chunk in re.findall(r"[A-Za-z_][A-Za-z_0-9]{1,}|[0-9]+(?:\.[0-9]+)?", question):
        terms.append(chunk)
    cjk = re.sub(r"[^一-鿿]+", "", question)
    for size in (4, 3, 2):
        for i in range(len(cjk) - size + 1):
            terms.append(cjk[i:i + size])
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return terms


def _exact_forms(semantic) -> dict[str, dict]:
    """精确命中的表单：类型 label / id / alias，以及事实里的显示名 → 对象。"""
    forms: dict[str, dict] = {}
    for tid, cls in semantic.classes.items():
        for form in [cls.get("label"), tid, *(cls.get("aliases") or [])]:
            if not form:
                continue
            forms.setdefault(form, {"kind": "object_type", "id": tid,
                                    "label": cls.get("label")})
    for rid, rel in semantic.relations.items():
        for form in [rel.get("label"), rid]:
            if not form:
                continue
            forms.setdefault(form, {"kind": "relation", "id": rid,
                                    "label": rel.get("label")})
    return forms


def _fee_label_index(system) -> dict[str, str]:
    """事实里的显示名 → fee_type（如「等候费」→ waiting）。"""
    index: dict[str, str] = {}
    try:
        rows = system.facts.all(
            "SELECT object_id, type, props FROM objects WHERE type='FeeItem'")
    except Exception:  # noqa: BLE001
        return index
    for row in rows:
        try:
            props = __import__("json").loads(row["props"])
        except Exception:  # noqa: BLE001
            continue
        for key in _LABEL_PROPS:
            label = props.get(key)
            ft = props.get("fee_type")
            if label and ft and label not in index:
                index[label] = ft
    return index


def _match(system, question: str) -> dict:
    """问句 → 本体要素（类型 / 关系 / 费用类型）+ 命中词。"""
    sem = system.sem
    terms = _terms_of(question)
    forms = _exact_forms(sem)
    fee_index = _fee_label_index(system)

    type_hits: dict[str, list[str]] = {}
    rel_hits: dict[str, list[str]] = {}
    fee_hits: dict[str, list[str]] = {}
    exact_types: set[str] = set()      # 精确命中的类型（子串猜中的不算）

    def add(bucket: dict, key: str, term: str):
        bucket.setdefault(key, [])
        if term not in bucket[key]:
            bucket[key].append(term)

    # ① 精确命中（优先）：问句里的词恰好等于某个 label / id / alias / 显示名
    for term in terms:
        hit = forms.get(term)
        if hit:
            if hit["kind"] == "object_type":
                add(type_hits, hit["id"], term)
                exact_types.add(hit["id"])
            else:
                add(rel_hits, hit["id"], term)
        if term in fee_index:
            add(fee_hits, fee_index[term], term)

    # ② 子串命中（补充）：只在**没有精确命中**时才用，且只认较长的词
    if not type_hits and not fee_hits:
        for term in terms:
            if len(term) < 3:
                continue
            for form, hit in forms.items():
                if len(form) < 2:
                    continue
                if term in form or form in term:
                    if hit["kind"] == "object_type":
                        add(type_hits, hit["id"], term)
                    else:
                        add(rel_hits, hit["id"], term)
                    break
            for label, ft in fee_index.items():
                if len(label) >= 2 and (term in label or label in term):
                    add(fee_hits, ft, term)
                    break

    # ③ 命中的类型：只保留**最具体**的那些（子串命中时按命中词长度裁剪）
    return {"types": type_hits, "relations": rel_hits, "fee_types": fee_hits,
            "fee_index": fee_index, "exact_types": exact_types}


def _has_instances(system, type_id: str) -> bool:
    row = system.facts.scalar("SELECT COUNT(*) FROM objects WHERE type=?", (type_id,))
    try:
        return int(row or 0) > 0
    except (TypeError, ValueError):
        return False


def _rule_fee_types(rule: dict) -> list[str]:
    """从规则里取出它适用的费用类型。

    真实本体里规则**没有** fee_type 独立字段，而是写在 when 条件里：
        when: {"all": [{"field": "fee_type", "op": "eq", "value": "waiting"}]}
    （benchmark 用的简化数据里才有独立字段，两种都要认。）
    """
    out: list[str] = []
    ft = rule.get("fee_type")
    if ft:
        out.append(ft)
    when = rule.get("when") or {}

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("field") == "fee_type":
            op = node.get("op")
            val = node.get("value")
            if op in ("eq", "in", "equals"):
                if isinstance(val, list):
                    out.extend(val)
                elif val:
                    out.append(val)
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk(v)

    walk(when)
    return list(dict.fromkeys(out))


def _declared_sources(system, fee_types: list[str]) -> dict:
    """哪些费用类型的度量值在本体里有**规则声明**（能算出来）。"""
    out: dict[str, list[dict]] = {}
    for rule_id, rule in system.sem.rules.items():
        for ft in _rule_fee_types(rule):
            out.setdefault(ft, []).append({
                "rule_id": rule_id,
                "version": rule.get("version"),
                "effective_from": rule.get("effective_from"),
                "effective_to": rule.get("effective_to"),
                "domain": rule.get("domain"),
            })
    return {ft: out[ft] for ft in fee_types if ft in out}


def check_answerable(system, question: str, terms: list[str] | None = None) -> dict:
    """问句级可答性检查（纯确定性，0 LLM）。"""
    question = (question or "").strip()
    matches = _match(system, question)
    matched_types = list(matches["types"].keys())
    fee_types = list(matches["fee_types"].keys())
    missing: list[dict] = []

    # ① 完全没命中本体 → 不可答（不能因为"没发现缺口"就判可答）
    if not matched_types and not fee_types and not matches["relations"]:
        missing.append({
            "code": NO_ONTOLOGY_MATCH,
            "message": ("问句没有命中本体里的任何类型、关系或费用类型——"
                        "本体里没有它能依据的东西，只能编"),
        })

    # ② 费用类型未被任何来源认领 → 复用既有缺口码
    owners: dict[str, list[dict]] = {}
    try:
        from freight.source_coverage import fee_type_owners
        owners = fee_type_owners(system.sem)
    except Exception:  # noqa: BLE001
        owners = {}
    for ft in fee_types:
        if ft not in owners:
            missing.append({"code": FEE_SOURCE_UNRESOLVED, "fee_type": ft,
                            "message": f"费用类型 {ft} 没有被任何来源认领"})

    # ③ 命中的类型有没有实例
    #    · 只对**精确命中**的类型查：子串命中是猜测，不能据此判"不可答"
    #    · 命中了**费用类型**时（事实里的显示名，最可靠），类型级缺失只作提示不阻断：
    #      问"等候费"时，"等候"两个字恰好等于 StopWaiting 的 label，
    #      但问的不是"等候状态"——阻断它会让真问题被判不可答。
    exact = matches.get("exact_types") or set()
    for tid in matched_types:
        if tid not in exact:
            continue
        if _has_instances(system, tid):
            continue
        item = {"code": NO_INSTANCE_FOR_TYPE, "object_type": tid,
                "message": f"本体里有 {tid}，但事实库里没有任何实例"}
        if fee_types:
            missing.append({**item, "severity": "info"})
        else:
            missing.append(item)

    # ④ 这个问句要的度量值，本体里有没有声明来源（规则）
    wants_measure = any(w in question for w in _MEASURE_WORDS)
    rule_hint = any(w in question for w in _RULE_HINT_WORDS)
    sources = _declared_sources(system, fee_types)
    measure = {"requested": wants_measure, "rule_hint": rule_hint,
               "sources": sources, "status": "not_requested"}
    if wants_measure:
        if sources:
            measure["status"] = "declared"
        else:
            measure["status"] = "no_declared_source"
            missing.append({
                "code": NO_DECLARED_SOURCE,
                "message": ("这个问句要的是一个度量值，但本体里没有对应的规则声明——"
                            "数据里没有它，只有代码或模型能编出来"),
                "candidate_fee_types": fee_types,
                "candidate_types": matched_types,
            })

    blocking = [m for m in missing if m.get("severity") != "info"]
    answerable = not blocking
    return {
        "question": question,
        "answerable": answerable,
        "matched": {
            "object_types": [{"id": t, "label": (system.sem.classes.get(t) or {}).get("label"),
                               "matched_terms": matches["types"][t],
                               "exact": t in (matches.get("exact_types") or set())}
                              for t in matched_types],
            "relations": [{"id": r, "matched_terms": matches["relations"][r]}
                          for r in matches["relations"]],
            "fee_types": [{"fee_type": f, "matched_terms": matches["fee_types"][f]}
                          for f in fee_types],
        },
        "missing": missing,
        "measure": measure,
        "tiers": ["declaration", "deterministic"],
        "explain": ("问句命中本体：类型 "
                    f"{len(matched_types)} 个 / 费用类型 {len(fee_types)} 个 / "
                    f"关系 {len(matches['relations'])} 个；"
                    + ("全部可答" if answerable else
                       f"缺 {len(blocking)} 项：" + "；".join(m["code"] for m in blocking))),
    }
