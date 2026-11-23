#!/usr/bin/env python3
"""一次性迁移：pricing 的 by_vehicle_type -> decision table matrix（唯一参数源）。

规则（用户铁律：不留双口径）：
- matrix 行 = conditions(条件组合) + params(参数)，首行命中；
- 旧 by_vehicle_type 每个车型迁一行（conditions: vehicle_type eq X），
  params 与旧 override 逐值一致；
- 旧顶层默认三参迁为默认行（conditions 为空，放最后）；
- 删掉 by_vehicle_type 与顶层重复标量（free_minutes/block_minutes/per_block）；
- waiting 额外声明 dimensions + time_windows，并加大货车雨天行
  （per_block 4.0 -> 4.8，货拉拉真实场景：恶劣天气等候加价）。

跑完后跑 tests/ 验证零漂移（WB20261105001 应计仍为 12 元）。
另做一件事：给 Waybill 挂 weather 属性并注册 property_type——
天气是 waiting 决策表的维度之一，事实字段不进 TBox，维度就是空的。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYER = ROOT / "freight" / "semantic_layer.json"

# 天气属性（计价维度的事实字段；值域 normal/rain/snow/fog）
WEATHER_PROPERTY_TYPE = {
    "name": "weather",
    "label": "天气",
    "value_type": "string",
    "base_type": "string",
    "unit": None,
    "marking": None,
    "timeseries": False,
    "primary_key": False,
    "nullable": True,
    "owner": "freight-core",
    "version": "3.0.0",
    "iri": "https://w3id.org/mobility-ontology/freight#weather",
    "description": "作业期间天气状况（normal/rain/snow/fog），计价多维决策表的维度之一",
}

# 货拉拉真实时段定义（演示声明，可评审可改）
TIME_WINDOWS = {
    "peak": {"days": "mon-fri", "from": "07:00", "to": "09:30",
             "label": "工作日早高峰"},
    "night": {"days": "mon-sun", "from": "22:00", "to": "06:00",
              "label": "夜间"},
    "normal": {"default": True, "label": "平峰"},
}

# 大货车雨天加价行（waiting 专用）
TRUCK_RAIN_ROW = {
    "id": "truck-rain",
    "label": "大货车 · 雨天（等候加价 20%）",
    "conditions": {"all": [
        {"field": "vehicle_type", "op": "eq", "value": "大货车"},
        {"field": "weather", "op": "eq", "value": "rain"},
    ]},
    "params": {"free_minutes": 40, "block_minutes": 10, "per_block": 4.8},
}

# 维度必须与 matrix 行条件**实际用到的字段**一致：声明了却没有任何行
# 条件用它，等于告诉读声明的人「这一维会影响计价」——假承诺。
# 规范顺序（有就用，没有就不出现）。
_DIM_ORDER = ("vehicle_type", "time_window", "weather")


def _dimensions_from_matrix(matrix: list) -> list[str]:
    used = set()
    for row in matrix:
        atoms = ((row.get("conditions") or {}).get("all")) or []
        for atom in atoms:
            if isinstance(atom, dict) and atom.get("field"):
                used.add(str(atom["field"]))
    return [d for d in _DIM_ORDER if d in used]


TIME_SOURCE = "accepted_at"  # 计价时间基准：接单时间


def _ensure_weather_dimension(layer: dict) -> str:
    """Waybill 挂 weather 属性 + property_types 注册（幂等）。"""
    regs = layer.setdefault("property_types", [])
    if not any(isinstance(p, dict) and p.get("name") == "weather"
               for p in regs):
        regs.append(dict(WEATHER_PROPERTY_TYPE))
    for cls in layer.get("classes") or []:
        if isinstance(cls, dict) and cls.get("id") == "Waybill":
            props = cls.setdefault("properties", [])
            if "weather" not in props:
                props.append("weather")
            return "weather property attached"
    return "Waybill class not found"


def _update_waiting_rule_explain(layer: dict) -> str:
    """R-WAIT-01 的 explain 改写成决策表口径（推理链第 3 步直接展示它）。

    旧文案只描述「按车型取费率」，迁移后计价是 车型×时段×天气 首行命中——
    规则说明不跟上，诊断推理链里就看不到天气加价这一维。
    版本 2.1.0 -> 2.2.0：计价口径变了（新增 truck-rain 行 + 决策表化）。
    """
    for rule in layer.get("rules") or []:
        if isinstance(rule, dict) and rule.get("id") == "R-WAIT-01":
            rule.setdefault("judge", {})["explain"] = (
                "等候费按决策表取费率：车型 × 天气多维组合，首行命中"
                "（pricing.waiting.matrix 是参数唯一源，conditions 为空的行为默认行、必须最后）。"
                "行：大货车+雨天（truck-rain）40 分钟免费、每 10 分钟 4.8 元（恶劣天气加价）；"
                "4.2米厢货 30 分钟免费、每 5 分钟 2.4 元；面包车 20/5/1.8；小货车 30/5/2.4；"
                "大货车 40/10/4.0；跑腿 10/5/1.0；默认 30/5/2.4。"
                "天气取运单 weather 事实字段。时段归窗机制已声明"
                "（peak=工作日 07:00-09:30 / night=22:00-06:00 / 其余平峰，按 accepted_at 归窗），"
                "当前没有行按时段分支，属于预留维度。公式不变：应计 = "
                "ceil(max(0, 等候分钟 - tariff.free_minutes) / tariff.block_minutes) "
                "× tariff.per_block，参数全部来自命中行。"
            )
            rule["version"] = "2.2.0"
            return "R-WAIT-01 explain updated -> 2.2.0"
    return "R-WAIT-01 not found"


def main() -> int:
    layer = json.loads(LAYER.read_text(encoding="utf-8"))
    print(_ensure_weather_dimension(layer))
    print(_update_waiting_rule_explain(layer))
    pricing = layer["pricing"]
    report = []
    for fee_type, config in pricing.items():
        if not isinstance(config, dict):
            continue
        by_vehicle = config.get("by_vehicle_type") or {}
        # 默认行携带全部非结构参数（含 base_fare_by_vehicle_type/per_km 等），
        # 规则 AST 的 params.* 引用从 matrix 默认行取——参数唯一源
        skip = {"rule_id", "matrix", "dimensions", "time_windows",
                "time_source", "by_vehicle_type"}
        defaults = {k: v for k, v in config.items() if k not in skip}
        rows = []
        # 决策表首行命中：条件更具体的行必须排在更通用的行之前
        # （truck-rain 是 车型∧天气 两条件，比单车种行更具体，必须先放）
        if fee_type == "waiting":
            rows.append(dict(TRUCK_RAIN_ROW))
        for vehicle, params in by_vehicle.items():
            rows.append({
                "id": f"vehicle:{vehicle}",
                "label": f"{vehicle}",
                "conditions": {"all": [
                    {"field": "vehicle_type", "op": "eq", "value": vehicle}]},
                "params": dict(params),
            })
        # 默认行（conditions 为空 = 兜底命中），必须最后
        rows.append({"id": "default", "label": "默认参数", "conditions": {},
                     "params": defaults})
        config["matrix"] = rows
        # 维度 = 行条件实际用到的字段。声明了却没有行条件用它，就是假承诺
        # （读声明的人会以为这一维会翻转价格）。时段归窗机制由
        # time_windows/time_source 声明承载，当前无行按时段分支，
        # 因此不进 dimensions——能力在位，承诺不虚。
        config["dimensions"] = _dimensions_from_matrix(rows)
        if fee_type in ("waiting", "mileage"):
            config["time_windows"] = TIME_WINDOWS
            config["time_source"] = TIME_SOURCE
        # 顶层参数键全部删掉——默认行已逐值携带（唯一源）。
        # 不能用固定名单：base_fare_by_vehicle_type/per_km/source 等
        # 同样是参数，漏删就是在 matrix 之外留第二口径。
        for key in list(defaults) + ["by_vehicle_type"]:
            config.pop(key, None)
        report.append((fee_type, len(by_vehicle), len(rows)))

    LAYER.write_text(json.dumps(layer, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    for fee_type, old_rows, new_rows in report:
        print(f"{fee_type}: by_vehicle_type {old_rows} 行 -> matrix {new_rows} 行")
    print("迁移完成。跑 tests/ 验证零漂移。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
