"""本体建模 DSL：对标 Palantir Object Type / Property Type / Link Type 三件套。

设计目标（与 Palantir 一致）：
1. **类（Object Type）** 元模型字段固定，业务复杂度靠属性数量承接，不靠给类加字段。
2. **属性（Property Type）** 是一等公民：每个属性有独立定义（value_type/base_type/unit/
   marking/timeseries/primary_key/nullable/description/source_column），类只引用属性名。
3. **链接（Link Type）** 是独立注册表：domain/range/cardinality/temporal/label。

建模时用紧凑 DSL 声明属性并显式标注类型，渲染时展开成属性注册表条目。
属性类型必须人工标注，禁止脚本靠命名猜类型 —— 那是另一条虚胖路。

类型短码：
    id    业务主键         string   / string
    str   普通字符串       string   / string
    txt   长文本           text     / string
    dec   金额/度量小数    decimal  / double
    int   整数             integer  / int
    num   数值             number   / double
    bool  布尔             boolean  / boolean
    dt    时间戳           datetime / timestamp
    date  日期             date     / date
    json  结构化快照       json     / string
    enum  枚举             enum     / string
"""
from __future__ import annotations

import uuid

# 域 -> (图标, 颜色)。图标/颜色属展示层元数据（对标 Palantir displayMetadata），
# 与业务语义解耦：同类可换皮，不动物理语义。逐个域人工指定，不按哈希随机分配——
# 哈希分配会让新加一个域就悄悄改变既有域的配色。
DOMAIN_PRESENTATION: dict[str, tuple[str, str]] = {
    "order_transport": ("truck", "#2563EB"),
    "order_domain": ("clipboard", "#3B82F6"),
    "waybill_domain": ("file-text", "#0EA5E9"),
    "cargo_domain": ("package", "#0891B2"),
    "stop_domain": ("map-pin", "#14B8A6"),
    "vehicle_domain": ("truck", "#059669"),
    "driver_domain": ("user", "#10B981"),
    "carrier_domain": ("building", "#65A30D"),
    "subcontract_domain": ("git-branch", "#84CC16"),
    "maintenance_domain": ("wrench", "#CA8A04"),
    "fuel_domain": ("fuel", "#EAB308"),
    "quote_domain": ("tag", "#F59E0B"),
    "pricing_rule_domain": ("calculator", "#F97316"),
    "fee_domain": ("coins", "#EA580C"),
    "settlement_domain": ("scale", "#DC2626"),
    "invoice_domain": ("receipt", "#E11D48"),
    "evidence_domain": ("camera", "#DB2777"),
    "exception_domain": ("alert-triangle", "#C026D3"),
    "insurance_domain": ("shield", "#9333EA"),
    "customs_domain": ("globe", "#7C3AED"),
    "compliance_domain": ("shield-check", "#4F46E5"),
    "warehouse_domain": ("warehouse", "#6366F1"),
    "route_domain": ("route", "#0D9488"),
    "geo_domain": ("map", "#0284C7"),
    "device_domain": ("cpu", "#475569"),
    "document_domain": ("file", "#64748B"),
    "track_domain": ("activity", "#16A34A"),
    "service_domain": ("headset", "#DB2777"),
    "organization_domain": ("users", "#7C2D12"),
    "contract_domain": ("file-signature", "#78350F"),
    "metric_domain": ("bar-chart", "#1E293B"),
}
DEFAULT_PRESENTATION = ("box", "#64748B")

# rid 命名空间与形态。rid 是内核身份（Palantir rid），与可读 id 分离：
# id 给人和 API 看，rid 给引用与血缘看，改名不改 rid。
RID_NAMESPACE = uuid.UUID("6f1d1f4a-5b2c-5c3e-9f00-1a2b3c4d5e6f")
RID_KIND_SLUG = {"class": "object-type", "relation": "link-type"}


def rid_for(kind: str, ident: str) -> str:
    """由 (种类, id) 确定性推导 rid。改名后由 freight/rid_ledger.json 接管。"""
    slug = RID_KIND_SLUG[kind]
    return f"ri.mobility.main.{slug}.{uuid.uuid5(RID_NAMESPACE, f'{kind}:{ident}').hex}"


def display_metadata_of(group: str | None) -> dict:
    icon, color = DOMAIN_PRESENTATION.get(group or "", DEFAULT_PRESENTATION)
    return {
        "icon": icon,
        "color": color,
        "visibility": "normal",
        # category 用域 id 而不是中文 label：机器可用来分组，展示名走 group.label
        "category": group or "ungrouped",
    }

TYPE_MAP: dict[str, tuple[str, str]] = {
    "id": ("string", "string"),
    "str": ("string", "string"),
    "txt": ("text", "string"),
    "dec": ("decimal", "double"),
    "int": ("integer", "int"),
    "num": ("number", "double"),
    "bool": ("boolean", "boolean"),
    "dt": ("datetime", "timestamp"),
    "date": ("date", "date"),
    "json": ("json", "string"),
    "enum": ("enum", "string"),
}

# 属性名 -> 单位。只覆盖真实带物理单位的字段，其余为 None。
UNIT_RULES: tuple[tuple[str, str], ...] = (
    ("_fee", "CNY"),
    ("_amount", "CNY"),
    ("_price", "CNY"),
    ("_cost", "CNY"),
    ("_payment", "CNY"),
    ("_tax", "CNY"),
    ("_deposit", "CNY"),
    ("_penalty", "CNY"),
    ("_km", "km"),
    ("_distance", "km"),
    ("_minutes", "min"),
    ("_hours", "h"),
    ("_days", "day"),
    ("_kg", "kg"),
    ("_t", "t"),
    ("_m3", "m3"),
    ("_volume", "m3"),
    ("_pct", "%"),
    ("_rate", "%"),
    ("_ratio", "%"),
    ("_litre", "L"),
    ("_kwh", "kWh"),
)

# 属性名 -> 安全标记。用「后缀精确匹配」而不是子串匹配：
# 子串匹配会把 fee_label（费用名称）、file_name（文件名）这类标识类字段误标成
# FINANCE / PII，进而在安全层被脱敏成 None —— 那是数据丢失，不是脱敏。
PII_SUFFIXES = ("_phone", "_mobile", "_email", "_id_card",
                "_account_no", "_license_no")
PII_EXACT = ("contact", "plate", "passport_no", "address", "bank_account")
# 自然人姓名的白名单：只有承载个人身份的 _name 才算 PII。
# cargo_name / city_name / file_name 这类标识类字段标成 PII 会在安全层被脱敏成 None，
# 那不是脱敏，是数据丢失。
PII_NAMES = frozenset({
    "agent_name", "contact_name", "driver_name", "employee_name",
    "receiver_name", "shipper_name", "signer_name", "staff_name",
    "account_name",
})
FINANCE_SUFFIXES = ("_fee", "_amount", "_price", "_cost", "_payment", "_tax",
                    "_deposit", "_penalty", "_balance", "_total", "_revenue",
                    "_profit", "_variance")
FINANCE_EXACT = ("amount", "price", "cost", "balance")


def unit_of(prop: str) -> str | None:
    for token, unit in UNIT_RULES:
        if prop.endswith(token) or token in prop:
            return unit
    return None


def marking_of(prop: str) -> str | None:
    """只对真正的个人信息打 PII 标记。

    金额类字段不打 marking：marking 在安全层是「强制列级脱敏」，给运费打 FINANCE
    会让普通角色看不到金额（无法对账）。财务字段的可见性应当走
    property_security_policies（按角色/属性的列级策略），而不是 marking。
    金额语义由 unit=CNY 表达。
    """
    low = prop.lower()
    if low in PII_NAMES or low in PII_EXACT or low.endswith(PII_SUFFIXES):
        return "PII"
    return None


def build_property_type(prop: str, type_code: str, owner_class: str,
                        version: str, effective_from: str) -> dict:
    """把一个 (属性名, 类型码, 属主类) 展开成属性注册表条目（Palantir Property Type）。"""
    if type_code not in TYPE_MAP:
        raise ValueError(f"未知类型码 {type_code!r}（属性 {prop}，类 {owner_class}）")
    value_type, base_type = TYPE_MAP[type_code]
    return {
        "name": prop,
        "label": prop,
        "value_type": value_type,
        "base_type": base_type,
        "unit": unit_of(prop),
        "marking": marking_of(prop),
        "timeseries": prop in ("status",) or prop.endswith("_status"),
        # 主键与可空性都是「对象类型级」语义（Palantir: primaryKeyProperty 挂在
        # Object Type 上），不放在 Property Type 注册表里。同一个 agent_id 在
        # ServiceAgent 是主键、在 ServiceCall 里只是外键，注册表若记 primary_key
        # 就会产生假冲突。主键统一以 class.key 为准。
        "primary_key": False,
        "nullable": True,
        "owner": "freight-core",
        "version": version,
        "iri": f"https://w3id.org/mobility-ontology/freight#{prop}",
        "effective_from": effective_from,
        "used_by": [owner_class],
    }


def C(cid: str, label: str, key: str, desc: str, props: str, statuses: str,
      aliases: tuple[str, ...] = (), group: str | None = None,
      owner: str = "freight-core", version: str = "3.0.0",
      effective_from: str = "2026-11-20") -> dict:
    """声明一个对象类型（Palantir Object Type）。

    props 用 "name:type, name:type" 紧凑写法，每个属性都必须显式标注类型。
    """
    spec: list[tuple[str, str]] = []
    for chunk in props.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, type_code = chunk.partition(":")
        name = name.strip()
        type_code = (type_code or "str").strip()
        if not name:
            raise ValueError(f"{cid} 有空的属性名")
        spec.append((name, type_code))
    names = [n for n, _ in spec]
    if key not in names:
        raise ValueError(f"{cid} 的主键 {key} 不在 props 里")
    if len(names) != len(set(names)):
        dup = [n for n in names if names.count(n) > 1]
        raise ValueError(f"{cid} 属性重名: {sorted(set(dup))}")
    status_list = [s.strip() for s in statuses.split(",") if s.strip()]
    if not status_list:
        raise ValueError(f"{cid} 的 statuses 不能为空")
    return {
        "id": cid,
        "label": label,
        # 中文无语态变化，复数展示名等于 label；英文/i18n 场景由语言层覆盖。
        # 不硬造 "xxx列表" —— 那是在元数据里堆垃圾。
        "plural_display_name": label,
        "key": key,
        "description": desc,
        "aliases": [label, *aliases],
        "statuses": status_list,
        "properties": names,
        "owner": owner,
        "source": "handwritten",
        "version": version,
        "iri": f"https://w3id.org/mobility-ontology/freight#{cid}",
        "effective_from": effective_from,
        "rid": rid_for("class", cid),
        "display_metadata": display_metadata_of(group),
        # 私有键：渲染时剥离，不写入本体 JSON
        "_group": group,
        "_props_spec": spec,
    }


def L(rid: str, label: str, domain: str, range_: str,
      cardinality: str = "many_to_one", temporal: bool = False,
      owner: str = "freight-core", version: str = "3.0.0",
      effective_from: str = "2026-11-20") -> dict:
    """声明一个链接类型（Palantir Link Type）。"""
    if cardinality not in ("one_to_one", "one_to_many", "many_to_one", "many_to_many"):
        raise ValueError(f"链接 {rid} 的基数非法: {cardinality}")
    return {
        "id": rid,
        "label": label,
        "domain": domain,
        "range": range_,
        "temporal": temporal,
        "cardinality": cardinality,
        "owner": owner,
        "source": "handwritten",
        "version": version,
        "iri": f"https://w3id.org/mobility-ontology/freight#{rid}",
        "effective_from": effective_from,
        "rid": rid_for("relation", rid),
        # display_metadata 由渲染阶段按 domain 类的属域补齐（链接自己没有域声明）
    }


def I(iid: str, label: str, desc: str, required_properties: tuple[str, ...] = (),
      required_any_of: tuple[str, ...] = (), required_timeseries: tuple[str, ...] = (),
      owner: str = "freight-core", version: str = "3.0.0") -> dict:
    """声明一个接口（Palantir Interface，structural match 横向多态）。"""
    return {
        "id": iid,
        "label": label,
        "description": desc,
        "match": "structural",
        "required_properties": list(required_properties),
        "required_any_of": list(required_any_of),
        "required_timeseries": list(required_timeseries),
        "owner": owner,
        "version": version,
    }


__all__ = ["C", "L", "I", "build_property_type", "TYPE_MAP", "marking_of", "unit_of",
           "DOMAIN_PRESENTATION", "display_metadata_of", "rid_for", "RID_NAMESPACE"]
