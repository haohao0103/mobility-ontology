"""执行档位（tiers）：把「这一步花了什么代价」变成可查询的元数据。

借鉴来源
--------
selfgraph 的 `level`（light / balanced / max）：一个旋钮控制所有能力要不要花钱，
高阶先跑低阶、**只把没定论的往上送**，并且每个结果都报「由哪档产出」。

我们的对应物
------------
selfgraph 管的是「这次调用花多少钱」；我们管的是「这一步**依据什么**产出」。
两者互补，所以我们的档位按**依据强度**划分：

| 档位 | 依据 | 典型耗时 | 例子 |
|---|---|---|---|
| `declaration` | 只读本体声明（TBox），不碰事实 | < 10 ms | ontology.catalog / schema / state_machine |
| `deterministic` | 声明 + 事实 + 规则求值，**不调大模型** | 0.01 ~ 0.1 s | function.fee_breakdown / quote_reconcile / action_preflight |
| `agent` | 需要大模型理解自然语言 | 45 ~ 90 s | reasoning-chain（多步） |

档位声明在本体里（`semantic_layer.json` 的 `tier` 字段）→ **换档不改代码**。
结果统一带 `tiers` 字段，说明「由哪档产出」——这样"这个答案有多可信"是**可查的**，不是靠人记。
"""
from __future__ import annotations

TIER_ORDER = ("declaration", "deterministic", "agent")

TIER_MEANING = {
    "declaration": "只依据本体声明（TBox），未读取业务事实",
    "deterministic": "依据本体声明 + 事实 + 规则求值，未调用大模型",
    "agent": "包含大模型推理（理解自然语言 / 多步规划）",
}


def tier_for(semantic, tool_name: str) -> str:
    """查一个工具/函数的档位。

    优先读本体里声明的 `tier` 字段；没声明时按名字推断（保守：**宁可标高不标低**，
    因为标低了会让人以为答案比实际更确定）。
    """
    for coll in ("primitives", "functions"):
        for item in (semantic.raw.get(coll) or []):
            name = item.get("name") or item.get("id")
            if name == tool_name:
                declared = item.get("tier")
                if declared in TIER_ORDER:
                    return declared
                break
    # 没声明 → 推断
    if tool_name.startswith("ontology.") or tool_name.startswith("reasoning."):
        return "declaration"
    if tool_name.startswith("function."):
        return "deterministic"
    if "reasoning" in tool_name or "chain" in tool_name:
        return "agent"
    return "deterministic"


def with_tiers(result: dict, tiers: list[str] | tuple[str, ...]) -> dict:
    """给结果带上档位信息（就地返回同一 dict）。"""
    if not isinstance(result, dict):
        return result
    seen: list[str] = []
    for t in tiers:
        if t not in seen:
            seen.append(t)
    result["tiers"] = seen
    result["tier_meaning"] = [TIER_MEANING.get(t, t) for t in seen]
    return result


def cheapest_first(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """把候选工具按档位从便宜到贵排序 —— 这就是「只送未决」的执行顺序。

    candidates: [(tool_name, tier), ...]
    返回：按 TIER_ORDER 排序后的同一列表（稳定排序）。
    """
    index = {t: i for i, t in enumerate(TIER_ORDER)}
    return sorted(candidates, key=lambda kv: index.get(kv[1], len(TIER_ORDER)))
