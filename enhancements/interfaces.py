"""P1-2 接口多态（对标 Palantir Interfaces）。

能力：
- structural 匹配：按属性 shape 自动解析实现类（不依赖显式列表）
- conforms(object_type, interface) 判定
- 接口多态函数：accepts_interface 的函数可接受任意实现类对象

承重验收：接口定义在 ontology_extension.json；删 required_timeseries
-> TemporalTracked 不再匹配任何类 -> 函数拒绝执行。
"""
from __future__ import annotations


class InterfaceViolation(Exception):
    """对象类型不符合接口定义。"""


class InterfaceRegistry:
    """接口注册表：显式声明优先，structural 形状匹配兜底。

    对标 Palantir 的两种接口：
    - 显式 implements（ImplementsInterfaces2）：接口声明「谁实现我」，带属性映射；
    - structural（按 shape 自动匹配）：接口只声明必需属性，任何满足的类都算实现。

    两者不能混着当同一件事：structural 会在无关类上误命中（一个恰巧有
    actual_fee/status 的「报销单」也被判成 Chargeable），显式声明没有这个毛病，
    但需要人维护。所以显式声明一旦存在就是权威，structural 只在没有显式声明时生效。
    """

    def __init__(self, ext):
        self.ext = ext

    def _declared(self, interface_id: str) -> list[dict]:
        iface = self.ext.interfaces.get(interface_id) or {}
        return iface.get("implements") or []

    def property_map(self, interface_id: str, class_id: str) -> dict:
        """接口属性名 -> 实现类属性名（Palantir interface 属性映射）。缺省同名。"""
        for item in self._declared(interface_id):
            if item.get("object_type") == class_id:
                return item.get("property_map") or {}
        return {}

    def implementations(self, interface_id: str) -> list[str]:
        iface = self.ext.interfaces.get(interface_id)
        if iface is None:
            raise InterfaceViolation(f"未定义的接口: {interface_id}")
        declared = [i["object_type"] for i in self._declared(interface_id)
                    if i.get("object_type") in self.ext.base_classes]
        if declared:
            return sorted(set(declared))
        if iface.get("match") != "structural":
            raise InterfaceViolation(
                f"接口 {interface_id} 匹配模式 {iface.get('match')} 且无 implements 声明"
            )
        out = []
        for cid, cls in self.ext.base_classes.items():
            if self._matches(cls, iface):
                out.append(cid)
        return sorted(out)

    def conforms(self, class_id: str, interface_id: str) -> bool:
        iface = self.ext.interfaces.get(interface_id)
        if iface is None:
            raise InterfaceViolation(f"未定义的接口: {interface_id}")
        cls = self.ext.base_classes.get(class_id)
        if cls is None:
            return False
        declared_ids = {i.get("object_type") for i in self._declared(interface_id)}
        if class_id in declared_ids:
            return True  # 显式声明即权威
        if declared_ids:
            return False  # 有显式声明时，未声明的类一律不算实现
        return self._matches(cls, iface)

    def _matches(self, cls: dict, iface: dict) -> bool:
        props = set(cls.get("properties", []))
        for p in iface.get("required_properties", []):
            if p not in props:
                return False
        any_of = iface.get("required_any_of")
        if any_of and not any(p in props for p in any_of):
            return False
        for p in iface.get("required_timeseries", []):
            if p not in props:
                return False
            pt = self.ext.property_types.get(p)
            if pt is None or not pt.get("timeseries"):
                return False
        return True

    def violations(self) -> list[str]:
        """校验显式 implements 不是空头支票：声明的类必须真的具备接口要求。

        「声明了就算实现」如果没人核对，implements 会变成愿望清单：
        接口要求 actual_fee，实现类却没有这个属性，多态函数一进来就崩。
        """
        problems: list[str] = []
        for iid, iface in self.ext.interfaces.items():
            for item in self._declared(iid):
                cid = item.get("object_type")
                cls = self.ext.base_classes.get(cid)
                if cls is None:
                    problems.append(f"接口 {iid} 声明实现类 {cid} 未定义")
                    continue
                pm = item.get("property_map") or {}
                props = set(cls.get("properties", []))
                for req in iface.get("required_properties", []):
                    actual = pm.get(req, req)
                    if actual not in props:
                        problems.append(
                            f"接口 {iid} 声明 {cid} 实现，但 {cid} 缺少必需属性 {actual}")
                any_of = iface.get("required_any_of") or []
                if any_of:
                    mapped = [pm.get(p, p) for p in any_of]
                    if not any(p in props for p in mapped):
                        problems.append(
                            f"接口 {iid} 声明 {cid} 实现，但 {cid} 一个都没有 {any_of}")
        return problems

    # -------------------------------------------------- 多态函数路由
    def check_function_arg(self, fn_id: str, class_id: str) -> None:
        """函数按接口接受参数：实参类必须 conforms 声明接口。"""
        fn = self.ext.function_registry.get(fn_id)
        if fn is None:
            return  # 未注册函数走 function_registry 的 UNKNOWN 路径
        iface_id = fn.get("accepts_interface")
        if not iface_id:
            return
        if not self.conforms(class_id, iface_id):
            impls = self.implementations(iface_id)
            raise InterfaceViolation(
                f"函数 {fn_id} 要求接口 {iface_id}（实现类: {', '.join(impls) or '无'}），"
                f"实参类 {class_id} 不符合"
            )


# -------------------------------------------------- 接口多态函数实现（被
# function_registry 以 implementation 引用；只改 JSON 中的注册即可路由到此）
def compute_variance(object_id: str, system=None) -> int:
    """Chargeable 接口多态函数：对任意实现类计算金额口径差异（分）。

    Waybill: actual_fee - estimated_fee
    FeeItem: actual_amount - expected_amount
    新类只要符合 Chargeable shape（注册表 timeseries/属性齐备）即可被本函数处理，
    函数代码零改动 —— 这就是接口多态的承重证明。
    """
    if system is None:
        raise InterfaceViolation("compute_variance 需要 system 运行时")
    try:
        obj = system.get_object(object_id)
    except Exception as exc:  # noqa: BLE001
        raise InterfaceViolation(f"对象不存在: {object_id}") from exc
    type_id = obj.get("type")
    ext = getattr(system, "ext", None)
    if ext is not None and ext.function_registry.get("chargeable_variance") is not None:
        interfaces = getattr(system, "interfaces", None)
        if interfaces is not None:
            interfaces.check_function_arg("chargeable_variance", type_id)
    props = obj.get("properties") or {}
    if "actual_fee" in props and "estimated_fee" in props:
        return int(props["actual_fee"]) - int(props["estimated_fee"])
    if "actual_amount" in props and "expected_amount" in props:
        return int(props["actual_amount"]) - int(props["expected_amount"])
    if "quoted_amount" in props:
        return int(props.get("quoted_amount", 0))
    raise InterfaceViolation(f"对象 {object_id} 无金额属性，无法计算差异")
