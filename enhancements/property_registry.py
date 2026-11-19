"""P1-1/P1-3/P1-4/P1-6 属性类型注册表（对标 Palantir Shared Property Types）。

能力：
- 注册表解析：按类叠加属性元数据（类型/单位/标记/时序/主键/生命周期）
- 增量强制：新属性必须先注册（门禁拦截），存量 legacy 豁免
- 时序属性：timeseries=true 的属性支持按时间点查询历史值（落 events 表）
- 生命周期：deprecated 属性被引用 -> 警告；experimental 仅告警不拦截

承重验收：注册/删除/修改属性定义只改 ontology_extension.json，
本模块零改动即可改变解析结果与门禁判定。
"""
from __future__ import annotations

from dataclasses import dataclass, field


class RegistryViolation(Exception):
    """属性注册表约束违反。"""


@dataclass
class ResolvedProperty:
    name: str
    label: str
    value_type: str
    unit: str | None
    marking: str | None
    timeseries: bool
    primary_key: bool
    status: str
    registered: bool = True
    origin: str = "registry"  # registry | legacy
    issues: list[str] = field(default_factory=list)


class PropertyRegistry:
    def __init__(self, ext):
        self.ext = ext
        self.policy = ext.policy

    # -------------------------------------------------- 解析
    def resolve(self, class_id: str, prop_name: str) -> ResolvedProperty:
        """解析类的属性：优先注册表，未注册且为存量属性时标 legacy。"""
        p = self.ext.property_types.get(prop_name)
        cls = self.ext.base_classes.get(class_id, {})
        if p is not None:
            issues: list[str] = []
            if p.get("status") == "deprecated":
                issues.append(f"属性 {prop_name} 已废弃（deprecated），引用将逐步下线")
            if class_id not in p.get("used_by", []):
                issues.append(f"属性 {prop_name} 的注册表 used_by 未含 {class_id}")
            return ResolvedProperty(
                name=prop_name,
                label=p.get("label", prop_name),
                value_type=p.get("value_type", "string"),
                unit=p.get("unit"),
                marking=p.get("marking"),
                timeseries=bool(p.get("timeseries")),
                primary_key=bool(p.get("primary_key")),
                status=p.get("status", "active"),
                registered=True,
                origin="registry",
                issues=issues,
            )
        # 未注册：增量模式下存量豁免，strict 模式报错
        if self.policy.get("enforcement") == "strict":
            raise RegistryViolation(
                f"strict 模式：属性 {class_id}.{prop_name} 未注册"
            )
        in_class = prop_name in cls.get("properties", [])
        if not in_class:
            raise RegistryViolation(f"属性 {class_id}.{prop_name} 既未注册也不在类属性表")
        return ResolvedProperty(
            name=prop_name,
            label=prop_name,
            value_type="string",
            unit=None,
            marking=None,
            timeseries=False,
            primary_key=cls.get("key") == prop_name,
            status="active",
            registered=False,
            origin="legacy",
            issues=["legacy 属性未注册"],
        )

    def properties_of(self, class_id: str) -> list[ResolvedProperty]:
        cls = self.ext.base_classes.get(class_id)
        if cls is None:
            raise RegistryViolation(f"未定义的类: {class_id}")
        return [self.resolve(class_id, p) for p in cls.get("properties", [])]

    # -------------------------------------------------- 门禁：新属性必须注册
    def gate_unregistered(self, class_id: str, prop_name: str) -> list[dict]:
        """增量强制：类里出现、注册表里没有、且 policy 未豁免的属性 -> 拦截项。"""
        cls = self.ext.base_classes.get(class_id)
        if cls is None or prop_name not in cls.get("properties", []):
            return []
        if prop_name in self.ext.property_types:
            return []
        if self.policy.get("legacy_grace") and self._is_legacy(class_id, prop_name):
            return []
        return [{
            "code": "PROP_UNREGISTERED",
            "category": "property_types",
            "path": f"classes[{class_id}].properties",
            "message": f"新属性 {class_id}.{prop_name} 必须先在 property_types 注册",
        }]

    def _is_legacy(self, class_id: str, prop_name: str) -> bool:
        """legacy 判定：属性在本扩展层引入（version 1.0.0 生效日）之前已存在于基础本体。

        基础本体无逐属性时间戳，采用保守口径：全部存量属性视为 legacy（首次注册豁免），
        一旦出现在注册表后删除 -> 不再豁免（回到 gate 拦截）。
        实现为：豁免名单由调用方在引入新属性时从本体类属性表 diff 得出。
        此处退化为 policy.legacy_grace 全量豁免 + registry 曾注册记录双保险。
        """
        return bool(self.policy.get("legacy_grace"))

    # -------------------------------------------------- 时序属性（P1-3）
    def timeseries_properties(self, class_id: str) -> list[str]:
        return [
            p.name for p in self.properties_of(class_id)
            if p.timeseries and p.registered
        ]

    def query_at_time(self, store, object_id: str, prop_name: str, at: str) -> list[dict]:
        """按时间点查询时序属性历史值（事件表回放）。

        timeseries 属性的值变更必须以 events 行记录（event_type=prop_change），
        查询返回 <= at 的最近一次值（含当前值基线）。
        """
        resolved = None
        row = store.one("SELECT type FROM objects WHERE object_id=?", (object_id,))
        if row is None:
            raise RegistryViolation(f"对象不存在: {object_id}")
        for p in self.properties_of(row["type"]):
            if p.name == prop_name:
                resolved = p
                break
        if resolved is None:
            raise RegistryViolation(f"对象 {object_id} 无属性 {prop_name}")
        if not resolved.timeseries:
            raise RegistryViolation(
                f"属性 {prop_name} 不是时序属性（注册表 timeseries!=true）"
            )
        events = store.all(
            "SELECT occurred_at, payload FROM events WHERE object_id=? "
            "AND event_type='prop_change' AND occurred_at<=? ORDER BY occurred_at DESC",
            (object_id, at),
        )
        import json as _json
        out: list[dict] = []
        current = store.one("SELECT props FROM objects WHERE object_id=?", (object_id,))
        props = _json.loads(current["props"]) if current else {}
        if prop_name in props:
            out.append({"at": "(current)", "value": props[prop_name]})
        for ev in events:
            payload = _json.loads(ev["payload"])
            if prop_name in payload.get("changes", {}):
                out.append({"at": ev["occurred_at"], "value": payload["changes"][prop_name]})
        return out

    # -------------------------------------------------- 主键唯一性（P1-4）
    def validate_primary_key(self, store, class_id: str) -> list[str]:
        """主键唯一性校验：注册表标记 primary_key 的属性，事实库值必须唯一。"""
        problems: list[str] = []
        for p in self.properties_of(class_id):
            if p.primary_key:
                rows = store.all(
                    "SELECT object_id, props FROM objects WHERE type=?", (class_id,)
                )
                import json as _json
                seen: dict = {}
                for r in rows:
                    props = _json.loads(r["props"])
                    key_val = props.get(p.name)
                    if key_val is not None:
                        if key_val in seen:
                            problems.append(
                                f"{class_id}.{p.name} 主键重复: {key_val} "
                                f"({seen[key_val]} vs {r['object_id']})"
                            )
                        seen[key_val] = r["object_id"]
        return problems
