"""P0 人群智能纠错。

以显式声明的结构化业务意图（intent_* 字段）为判定锚点，与圈选规则
（rule_expression 或标签定义）做确定性对齐，提前发现“大车/小车错圈、
车贴状态反了、托管城市漏配”等高危问题。名称/描述只在没有结构化意图时
作为弱信号，且结论降级为待人工复核，不作为自动定罪的正确答案。

本模块的冲突判断不依赖 HugeGraph / FastAPI，可独立单测；
只有 referenced_tags / managed_cities / scan 需要读取图数据。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

VEHICLE_ALIASES: Dict[str, List[str]] = {
    "LARGE": ["大车", "大型车", "大货车", "货车", "卡车", "重卡", "large", "truck"],
    "SMALL": ["小车", "小型车", "私家车", "网约车", "轿车", "面包车", "small", "car"],
}
STICKER_ALIASES: Dict[str, List[str]] = {
    "NONE": ["未贴", "没贴", "未粘贴", "无车贴", "车贴未贴", "sticker_none", "none"],
    "ACTIVE": ["已贴", "贴了", "已粘贴", "车贴已贴", "sticker_active", "active"],
}
CONCEPT_ALIASES: Dict[str, List[str]] = {
    "高价值": ["高价值", "高净值", "gmv", "贡献"],
    "通勤": ["通勤", "高峰", "上下班"],
    "新客": ["新客", "新用户", "首单", "注册"],
    "流失风险": ["流失", "召回", "沉默"],
    "优惠敏感": ["优惠", "券", "折扣", "价格"],
}
CITY_FIELDS = {"city_code", "city_name", "city", "city_id", "cities"}
CITY_COUNT_RES = (
    re.compile(r"(\d{1,3})\s*(?:个)?\s*(?:托管)?\s*城市"),
    re.compile(r"(\d{1,3})\s*城"),
)
COND_RE = re.compile(
    r"^\s*([A-Za-z_][\w.]*)\s*(>=|<=|!=|==|=|>|<|\bin\b|\bnot\s+in\b)\s*(.+?)\s*$",
    re.I,
)
LOSS_UNIT = {"HIGH": 120, "MEDIUM": 30, "LOW": 0}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _match_alias(text: str, groups: Dict[str, List[str]]) -> Optional[str]:
    low = (text or "").lower()
    for key, aliases in groups.items():
        if any(a.lower() in low for a in aliases):
            return key
    return None


def _canonical_vehicle(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return _match_alias(text, VEHICLE_ALIASES)


def _canonical_sticker(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return _match_alias(text, STICKER_ALIASES)


def _field_name(field: str) -> str:
    field = (field or "").strip()
    low = field.lower()
    for prefix in ("driver.", "vehicle.", "order.", "crowd.", "profile."):
        if low.startswith(prefix):
            field = field[len(prefix):]
            low = field.lower()
            break
    aliases = {
        "vehicletype": "vehicle_type",
        "sticker": "sticker_status",
        "stickerstate": "sticker_status",
        "city": "city_code",
        "cities": "city_code",
        "cityid": "city_code",
    }
    return aliases.get(low.replace(".", "_"), low.replace(".", "_"))


def _parse_value(raw: str) -> Any:
    raw = (raw or "").strip().rstrip(";")
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = json.loads(raw.replace("'", '"'))
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in re.split(r",\s*", inner) if x.strip()]
    if (len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"')):
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def parse_rule(raw: str) -> Tuple[List[dict], List[str]]:
    """解析 rule_expression，支持 JSON all/and 结构或 A and B 的 DSL。"""
    conditions: List[dict] = []
    errors: List[str] = []
    text = (raw or "").strip()
    if not text:
        return conditions, ["规则为空"]

    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except Exception as e:
            return conditions, [f"JSON 规则解析失败: {e}"]
        rows = data
        if isinstance(data, dict):
            rows = data.get("all") or data.get("and") or data.get("conditions")
        if not isinstance(rows, list):
            return conditions, ["JSON 规则缺少 all/and/conditions 条件数组"]
        for row in rows:
            if not isinstance(row, dict):
                errors.append(f"非法条件: {row}")
                continue
            field = _field_name(str(row.get("field") or ""))
            op = str(row.get("op") or "=").lower()
            if not field:
                errors.append(f"条件缺少 field: {row}")
                continue
            conditions.append({"field": field, "op": op,
                               "value": row.get("value"), "raw": json.dumps(row, ensure_ascii=False)})
        return conditions, errors

    for part in re.split(r"\s+(?:and|AND|&&)\s+", text):
        part = part.strip().strip("()")
        if not part:
            continue
        m = COND_RE.match(part)
        if not m:
            errors.append(f"无法解析条件: {part}")
            continue
        field, op, value = m.group(1), m.group(2).lower(), m.group(3)
        conditions.append({"field": _field_name(field), "op": op,
                           "value": _parse_value(value), "raw": part})
    if not conditions and not errors:
        errors.append("规则未解析出任何条件")
    return conditions, errors


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(v) for v in value) + "]"
    if isinstance(value, str):
        return "'" + value.replace("'", "") + "'"
    return str(value)


def render_rule(conditions: Iterable[dict]) -> str:
    parts = []
    for c in conditions:
        op = c.get("op") or "="
        parts.append(f"{c['field']} {op} {_render_value(c.get('value'))}")
    return " and ".join(parts)


def _as_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in items if str(item).strip()]


def _text_intent(name: str, description: str) -> dict:
    """名称/描述的启发式抽取结果，只作为弱信号，不作为判定依据。"""
    text = f"{name or ''} {description or ''}"
    vehicle = (_match_alias(name or "", VEHICLE_ALIASES)
               or _match_alias(description or "", VEHICLE_ALIASES))
    sticker = (_match_alias(name or "", STICKER_ALIASES)
               or _match_alias(description or "", STICKER_ALIASES))
    city_count = None
    for pattern in CITY_COUNT_RES:
        m = pattern.search(text)
        if m:
            city_count = int(m.group(1))
            break
    concepts = [key for key, aliases in CONCEPT_ALIASES.items()
                if any(a.lower() in text.lower() for a in aliases)]
    managed_only = any(k in text for k in ("托管城市", "托管", "全部城市", "全国"))
    return {
        "text": text.strip(),
        "vehicle_type": vehicle,
        "sticker_status": sticker,
        "city_count": city_count,
        "managed_cities": managed_only,
        "concepts": concepts,
    }


def _structured_intent(props: dict) -> dict:
    """读取显式声明的业务意图字段，作为冲突判定的可信锚点。"""
    vehicle = _canonical_vehicle(props.get("intent_vehicle_type"))
    sticker = _canonical_sticker(props.get("intent_sticker_status"))

    count_source = None
    raw_count = props.get("intent_expected_city_count")
    if raw_count not in (None, ""):
        count_source = "intent_expected_city_count"
    else:
        raw_count = props.get("expected_city_count")
        if raw_count not in (None, ""):
            count_source = "expected_city_count"
    try:
        city_count = int(raw_count) if raw_count not in (None, "") else None
    except (TypeError, ValueError):
        city_count = None

    managed_values = _as_list(props.get("intent_managed_cities"))
    managed_all = any(v.upper() == "ALL" for v in managed_values)
    managed_codes = [v for v in managed_values if v.upper() != "ALL"]
    if city_count is None and managed_codes:
        city_count = len(managed_codes)

    fields: List[str] = []
    if vehicle:
        fields.append("vehicle_type")
    if sticker:
        fields.append("sticker_status")
    if city_count is not None:
        fields.append("city_count")
    if managed_all or managed_codes:
        fields.append("managed_cities")

    return {
        "vehicle_type": vehicle,
        "sticker_status": sticker,
        "city_count": city_count,
        "count_source": count_source,
        "managed_cities": managed_all or bool(managed_codes),
        "managed_city_codes": managed_codes,
        "managed_all": managed_all,
        "fields": fields,
    }


def extract_intent(name: str, description: str, props: dict) -> dict:
    """业务意图：结构化字段优先，名称/描述仅作为弱信号与一致性核对。

    - 结构化意图存在时：以结构化字段为准，名称/描述与结构化字段不一致只报
      WARN（提示文案可能过期），不改变判定结论。
    - 完全没有结构化意图时：只能用名称/描述推断，结论降级为 WARN，
      并标记 needs_review，避免把运营随手写的名称当成业务真值。
    """
    text = _text_intent(name, description)
    structured = _structured_intent(props or {})

    authoritative = set(structured["fields"])
    vehicle = structured["vehicle_type"] or text["vehicle_type"]
    sticker = structured["sticker_status"] or text["sticker_status"]
    city_count = (structured["city_count"]
                  if structured["city_count"] is not None
                  else text["city_count"])
    managed = structured["managed_cities"] or text["managed_cities"]

    disagreements: List[dict] = []
    checks = (
        ("vehicle_type", structured["vehicle_type"], text["vehicle_type"]),
        ("sticker_status", structured["sticker_status"], text["sticker_status"]),
        ("city_count", structured["city_count"], text["city_count"]),
    )
    for field, declared, inferred in checks:
        if declared is not None and inferred is not None and declared != inferred:
            disagreements.append({"field": field, "structured": declared,
                                  "text": inferred})

    if authoritative:
        source = "structured_intent"
        confidence = "MEDIUM" if disagreements else "HIGH"
    elif any(x is not None for x in (text["vehicle_type"], text["sticker_status"],
                                     text["city_count"])) or text["managed_cities"]:
        source = "text_heuristic"
        confidence = "LOW"
    else:
        source = "none"
        confidence = "LOW"

    return {
        "text": text["text"],
        "vehicle_type": vehicle,
        "sticker_status": sticker,
        "city_count": city_count,
        # 兼容展示字段，语义与 city_count 完全一致。
        "expected_city_count": city_count,
        "managed_cities": managed,
        "managed_city_codes": structured["managed_city_codes"],
        "concepts": text["concepts"],
        "source": source,
        "confidence": confidence,
        "authoritative_fields": sorted(authoritative),
        "text_signals": {
            "vehicle_type": text["vehicle_type"],
            "sticker_status": text["sticker_status"],
            "city_count": text["city_count"],
            "managed_cities": text["managed_cities"],
        },
        "disagreements": disagreements,
        "needs_review": confidence != "HIGH",
    }


def _condition_values(conditions: List[dict], fields: set) -> Tuple[List[Any], List[dict]]:
    values: List[Any] = []
    matched: List[dict] = []
    for c in conditions:
        if c.get("field") in fields and c.get("op") in ("=", "==", "in"):
            matched.append(c)
            v = c.get("value")
            if isinstance(v, list):
                values.extend(v)
            else:
                values.append(v)
    return values, matched


def _normalize_city_values(values: Iterable[Any], managed: List[dict]) -> List[str]:
    code_set = {str(c.get("code")) for c in managed if c.get("code")}
    name_map = {str(c.get("name")): str(c.get("code"))
                for c in managed if c.get("name") and c.get("code")}
    out: List[str] = []
    for v in values:
        s = str(v).strip()
        if s in code_set:
            out.append(s)
        elif s in name_map:
            out.append(name_map[s])
        else:
            out.append(s)
    return list(dict.fromkeys(out))


def diagnose_payload(props: dict, crowd_id: str = "", tags: Optional[List[dict]] = None,
                     managed_cities: Optional[List[dict]] = None) -> dict:
    """纯函数：对单个人群的属性、标签、托管城市做语义纠错。"""
    props = props or {}
    tags = tags or []
    managed = managed_cities or []
    name = props.get("name") or ""
    description = props.get("description") or ""

    intent = extract_intent(name, description, props)
    authoritative = set(intent["authoritative_fields"])

    def intent_severity(field: str) -> str:
        """结构化意图是可信锚点时才是 ERROR，否则只能作为待复核告警。"""
        return "ERROR" if field in authoritative else "WARN"

    def intent_basis(field: str) -> str:
        return ("structured_intent" if field in authoritative
                else "text_heuristic")

    raw_rule = props.get("rule_expression") or ""
    conditions, errors = parse_rule(raw_rule)
    rule_source = "rule_expression" if raw_rule else "none"

    # 没有显式规则时，用标签定义作为有效规则，避免把标签人群误判为不可解释。
    if not conditions:
        for tag in tags:
            tag_text = " ".join(str(tag.get(k) or "") for k in (
                "definition", "expression", "description", "name"))
            tag_conditions, tag_errors = parse_rule(tag.get("expression")
                                                    or tag.get("definition") or "")
            if tag_conditions:
                conditions.extend(tag_conditions)
                errors.extend(tag_errors)
        if conditions:
            rule_source = "tags"

    findings: List[dict] = []

    def add(code: str, severity: str, constraint: str, field: str, message: str,
            expected: Any = None, actual: Any = None, suggestion: str = "",
            basis: str = "rule") -> None:
        item = {"code": code, "severity": severity, "constraint": constraint,
                "field": field, "message": message, "basis": basis}
        if expected is not None:
            item["expected"] = expected
        if actual is not None:
            item["actual"] = actual
        if suggestion:
            item["suggestion"] = suggestion
        findings.append(item)

    # 0) 意图可信度：没有结构化意图时，只能人工复核，不能自动定罪
    if not authoritative:
        add("INTENT_UNVERIFIED", "WARN", "C14", "intent",
            "人群没有结构化业务意图字段，名称/描述只能作为弱信号，结论需人工复核",
            suggestion="补充 intent_vehicle_type / intent_sticker_status / "
                       "intent_expected_city_count / intent_managed_cities")
    for d in intent.get("disagreements") or []:
        add("INTENT_TEXT_MISMATCH", "WARN", "C14", d["field"],
            f"名称/描述推断出 {d['text']}，与结构化意图 {d['structured']} 不一致，"
            "以结构化意图为准",
            expected=d["structured"], actual=d["text"],
            suggestion="更新人群名称/描述，避免误导下游使用方",
            basis="text_heuristic")


    if not raw_rule and not conditions:
        add("RULE_MISSING", "ERROR", "C13", "rule_expression",
            "人群没有可解析的圈选规则，无法判断是否圈错",
            suggestion="补充 rule_expression 或至少绑定一个带 expression 的标签")
    elif errors and not conditions:
        add("RULE_UNPARSEABLE", "ERROR", "C13", "rule_expression",
            "圈选规则无法解析", actual="; ".join(errors),
            suggestion="按 field op value [and ...] 或 JSON {all:[...]} 重写规则")

    corrected = [dict(c) for c in conditions]

    # 1) 车型一致性
    if intent["vehicle_type"]:
        values, matched = _condition_values(conditions, {"vehicle_type"})
        allowed = {v for v in (_canonical_vehicle(x) for x in values) if v}
        if matched:
            if intent["vehicle_type"] not in allowed:
                sev = intent_severity("vehicle_type")
                add("VEHICLE_TYPE_CONFLICT", sev, "C14", "vehicle_type",
                    f"人群意图为 {intent['vehicle_type']}，但圈选规则命中 {sorted(allowed) or values}",
                    expected=intent["vehicle_type"], actual=sorted(allowed) or values,
                    suggestion=f"把 vehicle_type 条件改为 {intent['vehicle_type']}",
                    basis=intent_basis("vehicle_type"))
                if sev == "ERROR":
                    for c in corrected:
                        if c.get("field") == "vehicle_type" and c.get("op") in ("=", "=="):
                            c["value"] = intent["vehicle_type"]
                        elif c.get("field") == "vehicle_type" and c.get("op") == "in":
                            c["value"] = [intent["vehicle_type"]]
        else:
            add("VEHICLE_TYPE_UNCONSTRAINED", "WARN", "C14", "vehicle_type",
                f"人群意图为 {intent['vehicle_type']}，但规则未约束车型",
                expected=intent["vehicle_type"],
                suggestion=f"补充条件 vehicle_type = '{intent['vehicle_type']}'",
                basis=intent_basis("vehicle_type"))

    # 2) 车贴状态一致性
    if intent["sticker_status"]:
        values, matched = _condition_values(conditions, {"sticker_status"})
        allowed = {v for v in (_canonical_sticker(x) for x in values) if v}
        if matched:
            if intent["sticker_status"] not in allowed:
                sev = intent_severity("sticker_status")
                add("STICKER_STATUS_CONFLICT", sev, "C14", "sticker_status",
                    f"人群意图为车贴 {intent['sticker_status']}，但圈选规则命中 {sorted(allowed) or values}",
                    expected=intent["sticker_status"], actual=sorted(allowed) or values,
                    suggestion=f"把 sticker_status 条件改为 {intent['sticker_status']}",
                    basis=intent_basis("sticker_status"))
                if sev == "ERROR":
                    for c in corrected:
                        if c.get("field") == "sticker_status" and c.get("op") in ("=", "=="):
                            c["value"] = intent["sticker_status"]
                        elif c.get("field") == "sticker_status" and c.get("op") == "in":
                            c["value"] = [intent["sticker_status"]]
        else:
            add("STICKER_STATUS_UNCONSTRAINED", "WARN", "C14", "sticker_status",
                f"人群意图为车贴 {intent['sticker_status']}，但规则未约束车贴状态",
                expected=intent["sticker_status"],
                suggestion=f"补充条件 sticker_status = '{intent['sticker_status']}'",
                basis=intent_basis("sticker_status"))

    # 3) 引用标签与意图冲突
    for tag in tags:
        tag_text = " ".join(str(tag.get(k) or "") for k in (
            "name", "description", "definition", "expression"))
        tag_vehicle = _match_alias(tag_text, VEHICLE_ALIASES)
        tag_sticker = _match_alias(tag_text, STICKER_ALIASES)
        if intent["vehicle_type"] and tag_vehicle and tag_vehicle != intent["vehicle_type"]:
            add("REFERENCED_TAG_VEHICLE_CONFLICT", intent_severity("vehicle_type"),
                "C14", "referenced_tags",
                f"人群绑定标签 {tag.get('name') or tag.get('id')} 的车型语义与人群意图相反",
                expected=intent["vehicle_type"], actual=tag_vehicle,
                suggestion=f"解绑标签 {tag.get('id')}，改绑 {intent['vehicle_type']} 对应标签",
                basis=intent_basis("vehicle_type"))
        if intent["sticker_status"] and tag_sticker and tag_sticker != intent["sticker_status"]:
            add("REFERENCED_TAG_STICKER_CONFLICT", intent_severity("sticker_status"),
                "C14", "referenced_tags",
                f"人群绑定标签 {tag.get('name') or tag.get('id')} 的车贴语义与人群意图相反",
                expected=intent["sticker_status"], actual=tag_sticker,
                suggestion=f"解绑标签 {tag.get('id')}，改绑车贴状态为 {intent['sticker_status']} 的标签",
                basis=intent_basis("sticker_status"))

    # 4) 城市覆盖一致性
    city_values, city_conds = _condition_values(conditions, CITY_FIELDS)
    # scope 作为规则未限定城市时的兜底声明
    if not city_conds and props.get("scope"):
        city_values = list(props.get("scope") or [])
    rule_codes = _normalize_city_values(city_values, managed)
    managed_codes = [str(c.get("code")) for c in managed if c.get("code")]
    missing_codes = [c for c in managed_codes if c not in set(rule_codes)]
    extra_codes = [c for c in rule_codes if managed_codes and c not in set(managed_codes)]
    expected_count = intent["city_count"]
    actual_count = len(rule_codes) if rule_codes else 0

    if expected_count and not city_conds and not props.get("scope"):
        add("SCOPE_UNCONSTRAINED", intent_severity("city_count"), "C15", "scope",
            f"人群声明覆盖 {expected_count} 个城市，但规则未限定城市范围",
            expected=expected_count, actual=0,
            suggestion=f"补充 city_code in [...]，并核对 {expected_count} 个城市",
            basis=intent_basis("city_count"))
    elif expected_count and actual_count != expected_count:
        add("CITY_COVERAGE_MISMATCH", intent_severity("city_count"), "C15", "scope",
            f"人群声明覆盖 {expected_count} 个城市，规则实际只覆盖 {actual_count} 个，差 {expected_count - actual_count} 个",
            expected=expected_count, actual=actual_count,
            suggestion="补齐缺失城市后再发布",
            basis=intent_basis("city_count"))
    elif intent["managed_cities"] and missing_codes and not expected_count:
        add("MANAGED_CITY_MISSING", intent_severity("managed_cities"), "C15", "scope",
            f"声明覆盖托管城市，但规则漏配 {len(missing_codes)} 个托管城市",
            expected=len(managed_codes), actual=actual_count,
            suggestion="补齐缺失托管城市后再发布",
            basis=intent_basis("managed_cities"))

    if missing_codes and (intent["managed_cities"] or expected_count):
        names = {str(c.get("code")): c.get("name") for c in managed}
        missing_detail = [{"code": c, "name": names.get(c)} for c in missing_codes]
    else:
        missing_detail = []

    if extra_codes and managed_codes and intent["managed_cities"]:
        add("NON_MANAGED_CITY_INCLUDED", "WARN", "C15", "scope",
            f"规则包含 {len(extra_codes)} 个非托管/未知城市",
            actual=extra_codes,
            suggestion="确认这些城市是否应纳入托管范围")

    # 5) 业务概念与标签覆盖（弱提示，不作为主判定）
    tag_text_all = " ".join(str(t.get("name") or "") + " " + str(t.get("description") or "")
                            for t in tags).lower()
    for concept in intent["concepts"]:
        if concept in ("高价值", "通勤", "新客", "流失风险", "优惠敏感"):
            aliases = CONCEPT_ALIASES.get(concept, [])
            if not any(a.lower() in tag_text_all for a in aliases):
                # 规则条件中也可能直接体现
                rule_text = (raw_rule or "").lower()
                if not any(a.lower() in rule_text for a in aliases):
                    add("CONCEPT_NOT_COVERED", "WARN", "C14", "referenced_tags",
                        f"人群名称/描述提到“{concept}”，但标签与规则均未体现",
                        expected=concept,
                        suggestion=f"补充能表达“{concept}”的标签或规则条件")

    errors_found = [f for f in findings if f["severity"] == "ERROR"]
    warnings_found = [f for f in findings if f["severity"] == "WARN"]
    if errors_found:
        risk = "HIGH"
    elif warnings_found:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    score = max(0, 100 - 35 * len(errors_found) - 10 * len(warnings_found))
    estimated_size = props.get("estimated_size")
    try:
        estimated_size = int(estimated_size) if estimated_size not in (None, "") else 0
    except (TypeError, ValueError):
        estimated_size = 0
    avoided_loss = estimated_size * LOSS_UNIT[risk]

    fix_conditions = [c for c in corrected if c.get("field")]
    suggestion_actions: List[dict] = []
    can_autofix_rule = bool(authoritative & {"vehicle_type", "sticker_status"})
    if (raw_rule and can_autofix_rule and fix_conditions
            and render_rule(fix_conditions) != raw_rule):
        suggestion_actions.append({
            "tool": "propose_change",
            "action": "update_properties",
            "asset_id": crowd_id,
            "payload": {"rule_expression": render_rule(fix_conditions)},
            "reason": "纠错：让圈选规则与结构化业务意图一致",
            "requires_approval": True,
        })
    if missing_detail and (authoritative & {"city_count", "managed_cities"}):
        suggestion_actions.append({
            "tool": "propose_change",
            "action": "update_properties",
            "asset_id": crowd_id,
            "payload": {"scope": list(dict.fromkeys(rule_codes + missing_codes))},
            "reason": "纠错：补齐托管城市覆盖范围",
            "requires_approval": True,
        })

    checks = [
        {"name": "rule_parseable", "ok": bool(conditions) and not errors},
        {"name": "vehicle_consistent", "ok": not any(
            f["code"] in ("VEHICLE_TYPE_CONFLICT", "REFERENCED_TAG_VEHICLE_CONFLICT")
            for f in findings)},
        {"name": "sticker_consistent", "ok": not any(
            f["code"] in ("STICKER_STATUS_CONFLICT", "REFERENCED_TAG_STICKER_CONFLICT")
            for f in findings)},
        {"name": "city_coverage_complete", "ok": not any(
            f["code"] in ("CITY_COVERAGE_MISMATCH", "MANAGED_CITY_MISSING",
                          "SCOPE_UNCONSTRAINED")
            for f in findings)},
    ]

    return {
        "asset_id": crowd_id,
        "name": name,
        "display_name": props.get("display_name"),
        "description": description,
        "status": props.get("status"),
        "owner": props.get("owner"),
        "crowd_type": props.get("crowd_type"),
        "estimated_size": estimated_size,
        "risk": {
            "level": risk,
            "score": score,
            "error_count": len(errors_found),
            "warning_count": len(warnings_found),
            "avoided_loss_estimate": avoided_loss,
            "loss_unit": LOSS_UNIT[risk],
            "loss_note": "按每人风险敞口估算，仅用于优先级排序",
        },
        "intent": intent,
        "rule": {
            "source": rule_source,
            "raw": raw_rule,
            "conditions": conditions,
            "parse_ok": bool(conditions) and not errors,
            "errors": errors,
            "tag_count": len(tags),
        },
        "scope": {
            "expected_city_count": expected_count,
            "rule_city_count": actual_count,
            "managed_city_count": len(managed_codes),
            "missing_count": len(missing_detail),
            "missing_cities": missing_detail,
            "extra_cities": extra_codes,
        },
        "referenced_tags": [{
            "id": t.get("id"), "name": t.get("name"),
            "vehicle_type": _match_alias(" ".join(str(t.get(k) or "") for k in (
                "name", "description", "definition", "expression")), VEHICLE_ALIASES),
            "sticker_status": _match_alias(" ".join(str(t.get(k) or "") for k in (
                "name", "description", "definition", "expression")), STICKER_ALIASES),
        } for t in tags],
        "findings": findings,
        "checks": checks,
        "fix": {
            "suggested_rule": render_rule(fix_conditions) if fix_conditions else "",
            "suggested_actions": suggestion_actions,
        },
        "managed_cities_sample": managed[:5],
        "generated_at": _now(),
    }


# 新判定码 -> 既有前端展示码，语义不变，只做展示层收敛。
ISSUE_CODE_MAP = {
    "VEHICLE_TYPE_CONFLICT": "VEHICLE_CONFLICT",
    "STICKER_STATUS_CONFLICT": "STICKER_CONFLICT",
    "REFERENCED_TAG_VEHICLE_CONFLICT": "TAG_INTENT_CONFLICT",
    "REFERENCED_TAG_STICKER_CONFLICT": "TAG_INTENT_CONFLICT",
}


def case_view(result: dict) -> dict:
    """把新的判定结构投影成既有案例视图字段，保持页面契约可用。"""
    findings = result.get("findings") or []
    rule = result.get("rule") or {}
    scope = result.get("scope") or {}
    risk = result.get("risk") or {}
    fix = result.get("fix") or {}
    missing = scope.get("missing_cities") or []

    issues: List[dict] = []
    for f in findings:
        code = ISSUE_CODE_MAP.get(f["code"], f["code"])
        issue = {
            "code": code,
            "raw_code": f["code"],
            "severity": "HIGH" if f["severity"] == "ERROR" else "MEDIUM",
            "message": f["message"],
            "field": f.get("field"),
            "basis": f.get("basis"),
        }
        if f.get("expected") is not None:
            issue["expected"] = f["expected"]
        if f.get("actual") is not None:
            issue["actual"] = f["actual"]
        if code == "CITY_COVERAGE_MISMATCH":
            issue["missing_cities"] = [c.get("name") or c.get("code") for c in missing]
            issue["missing_count"] = len(missing)
        issues.append(issue)

    actions = fix.get("suggested_actions") or []
    suggestions: List[dict] = []
    for action in actions:
        payload = action.get("payload") or {}
        if "rule_expression" in payload:
            before, after = rule.get("raw"), payload["rule_expression"]
        else:
            before = f"{scope.get('rule_city_count', 0)} 城"
            after = f"{len(payload.get('scope') or [])} 城"
        suggestions.append({
            "label": action.get("reason"),
            "reason": action.get("reason"),
            "before": before,
            "after": after,
            "propose_change": {
                "action": action.get("action"),
                "asset_id": action.get("asset_id"),
                "payload": payload,
                "reason": action.get("reason"),
                "actor": "profile_ai_agent",
            },
        })
    if not suggestions:
        for f in findings:
            if not f.get("suggestion"):
                continue
            suggestions.append({
                "label": f["suggestion"],
                "reason": f["message"],
                "basis": f.get("basis"),
            })

    level = risk.get("level") or "LOW"
    verdict = {"HIGH": "BLOCK", "MEDIUM": "REVIEW"}.get(level, "PASS")

    result["crowd_id"] = result.get("asset_id")
    result["risk_level"] = level
    result["verdict"] = verdict
    result["kpi"] = {
        "estimated_size": result.get("estimated_size") or 0,
        "avoided_loss": risk.get("avoided_loss_estimate") or 0,
        "missing_city_count": len(missing),
    }
    result["issues"] = issues
    result["suggestions"] = suggestions
    rule["city_count"] = scope.get("rule_city_count")
    rule["effective_expression"] = rule.get("raw") or fix.get("suggested_rule") or ""
    rule["tag_semantics"] = result.get("referenced_tags") or []
    return result


class CrowdCorrectionEngine:
    """把图数据接入纯纠错函数，并聚合扫描 KPI。"""

    def __init__(self, service):
        self.service = service
        self.semantic = service.semantic
        self.client = service.client
        self._managed_cache: Optional[List[dict]] = None

    def managed_cities(self) -> List[dict]:
        if self._managed_cache is not None:
            return self._managed_cache
        rows: List[dict] = []
        for raw in self.client.list_vertices(label="City", limit=500):
            props = raw.get("properties") or {}
            if not _truthy(props.get("managed")):
                continue
            code = props.get("city_code") or (str(raw.get("id")).split(":")[-1])
            rows.append({"id": str(raw.get("id")), "code": str(code),
                         "name": props.get("city_name") or props.get("name")})
        self._managed_cache = rows
        return rows

    def referenced_tags(self, asset_id: str, props: dict,
                        at: Optional[str] = None) -> List[dict]:
        tag_ids: List[str] = []
        for value in props.get("referenced_tags") or []:
            if isinstance(value, str) and value:
                tag_ids.append(value)
            elif isinstance(value, dict) and value.get("id"):
                tag_ids.append(str(value["id"]))
        try:
            label = self.semantic.edge_label("crowd_has_tag", "Crowd")
            for edge in self.client.list_edges(vertex_id=asset_id, direction="OUT",
                                               label=label, limit=200):
                if at and not self.service._edge_view(edge, asset_id, at=at)["active"]:
                    continue
                _, target = self.client.edge_endpoints(edge)
                if target:
                    tag_ids.append(str(target))
        except Exception:
            pass

        rows: List[dict] = []
        for tag_id in dict.fromkeys(tag_ids):
            raw = self.client.get_vertex(tag_id)
            if not raw:
                continue
            tag_props = raw.get("properties") or {}
            rows.append({
                "id": str(raw.get("id")), "name": tag_props.get("name"),
                "display_name": tag_props.get("display_name"),
                "description": tag_props.get("description"),
                "definition": tag_props.get("definition"),
                "expression": tag_props.get("expression"),
            })
        return rows

    def diagnose(self, crowd_id: str, include_impact: bool = True,
                 at: Optional[str] = None) -> dict:
        at_norm = self.service._normalize_at(at)
        asset = self.service._vertex(crowd_id)
        tags = self.referenced_tags(crowd_id, asset["properties"], at=at_norm)
        result = diagnose_payload(asset["properties"], crowd_id, tags,
                                  self.managed_cities())
        result["as_of"] = at_norm
        if include_impact:
            suggested = str((result.get("fix") or {}).get("suggested_rule") or "").strip()
            impact = self.service.impact_analysis(
                crowd_id, depth=3, at=at_norm, proposed_rule=suggested or None)
            result["impact"] = {
                "risk": impact["risk"],
                "affected_total": impact["summary"]["affected_total"],
                "by_kind": impact["summary"]["by_kind"],
                "by_impact_type": impact["summary"].get("by_impact_type"),
                "quantification": impact.get("quantification"),
                "verdict": impact.get("verdict"),
                "change": impact.get("change"),
                "excluded": impact["summary"].get("skipped_relation_details"),
                "strategies": [n for n in impact["affected"]
                               if n.get("kind") == "strategy"],
                "canvases": [n for n in impact["affected"]
                             if n.get("kind") == "canvas"],
                "orders": [n for n in impact["affected"]
                           if n.get("kind") == "order"],
            }
        return case_view(result)

    def scan(self, limit: int = 200) -> dict:
        limit = max(1, min(int(limit), 500))
        managed = self.managed_cities()
        items: List[dict] = []
        for raw in self.client.list_vertices(label="Crowd", limit=limit):
            props = raw.get("properties") or {}
            crowd_id = str(raw.get("id"))
            tags = self.referenced_tags(crowd_id, props)
            items.append(case_view(diagnose_payload(props, crowd_id, tags, managed)))

        items.sort(key=lambda x: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["risk"]["level"]],
                                  -x["risk"]["score"]))
        by_constraint: Dict[str, int] = {}
        issue_by_code: Dict[str, int] = {}
        for item in items:
            for finding in item["findings"]:
                if finding["severity"] == "ERROR":
                    by_constraint[finding["constraint"]] = (
                        by_constraint.get(finding["constraint"], 0) + 1)
                code = ISSUE_CODE_MAP.get(finding["code"], finding["code"])
                issue_by_code[code] = issue_by_code.get(code, 0) + 1
        high = [i for i in items if i["risk"]["level"] == "HIGH"]
        medium = [i for i in items if i["risk"]["level"] == "MEDIUM"]
        low = [i for i in items if i["risk"]["level"] == "LOW"]
        return {
            "summary": {
                "scanned": len(items),
                "high_risk": len(high),
                "medium_risk": len(medium),
                "low_risk": len(low),
                "blocked": len(high),
                "avoided_loss_estimate": sum(
                    i["risk"]["avoided_loss_estimate"] for i in items),
                "by_constraint": by_constraint,
                "managed_city_count": len(managed),
            },
            "kpi": {
                "crowds_scanned": len(items),
                "high_risk": len(high),
                "medium_risk": len(medium),
                "low_risk": len(low),
                "blocked": len(high),
                "avoided_loss": sum(
                    i["risk"]["avoided_loss_estimate"] for i in items),
                "issue_by_code": issue_by_code,
            },
            "items": items,
            "generated_at": _now(),
        }