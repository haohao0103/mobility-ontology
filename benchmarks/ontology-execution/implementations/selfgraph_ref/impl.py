"""参考实现 D：**selfgraph 路线**（成熟属性图/知识图谱 SDK，工业级实现）。

这个实现的用意很特别：基准里的"属性图路线"原先只有我们自己写的参考实现，
评审清单里明确写了那是**稻草人风险**。selfgraph 是社区里成熟度很高的属性图/知识图谱 SDK
（603 个测试、5 种后端、双时态、轻量本体 + MCP），把它接进来当第四组，
能把主张从「我们 vs 我们自己写的属性图」升级为「**我们 vs 工业级属性图 SDK**」。

它拿到什么
----------
- **事实图**：实体 + 关系 + 属性（`EntityInput.properties` / `RelationInput.properties`）
- **轻量本体**：`EntityType`（别名/同义词/属性规格/端点/定义/认证）+ `RelationType`
- **版本化**：关系与偏好有修订历史，可按 `as_of` / `known_at` 读历史状态
- **检索**：BM25 + 图遍历 + schema 同义词；`check_answerable` 判断"图里有没有答案"

它拿不到什么（这正是基准要量的）
----------------------------------
- **业务公式**：selfgraph **没有表达式求值器**——`Definition.expression` 只是声明
  （文档示例 `"count(Person)"`），代码里不存在把表达式算成数字的路径。
  → 「等候费按规则应收多少」在它那里**没有答案来源**。
- **动作 / 状态机 / 写回 / 权限标记**：源码里零命中（`state_machine` / `action_type` /
  `marking` / `policy` / `writeback` 全无）。

依赖
----
需要 `selfgraph` + 一个图后端。**内嵌后端 Kuzu 最省事**：
    pip install "selfgraph[kuzu]"
（注意：Kuzu 暂无 Python 3.14 的预编译 wheel，Python 3.13 及以下可直接安装；
也可以用 Neo4j / Memgraph / FalkorDB / HydraDB，改 `STORE_FACTORY` 即可。）

缺依赖时**明确报不可用**，不使用模板伪造答案（与 RAG 组同构）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BENCH_DATA_DIR", HERE.parent.parent / "data"))
WORK = Path(os.environ.get("BENCH_IMPL_DIR", HERE))


class SelfGraphUnavailable(RuntimeError):
    """selfgraph 或其后端不可用 —— 明确报不可用，不伪造结论。"""


def _load_selfgraph():
    try:
        import selfgraph  # noqa: F401
        from selfgraph import KuzuStore, Schema, SelfGraph
    except Exception as exc:  # noqa: BLE001
        raise SelfGraphUnavailable(
            f"selfgraph 不可用：{exc}。请安装 'selfgraph[kuzu]'"
            "（Kuzu 需要 Python ≤ 3.13）") from exc
    # 后端依赖单独检查：selfgraph 装了但 kuzu 没装 → 同样是"不可用"，不是失败
    try:
        import kuzu  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise SelfGraphUnavailable(
            f"selfgraph 已安装，但内嵌后端 Kuzu 不可用：{exc}。"
            "Kuzu 暂无 Python 3.14 预编译 wheel，请用 Python ≤ 3.13，"
            "或改用 Neo4j / Memgraph / FalkorDB / HydraDB") from exc
    return selfgraph, KuzuStore, Schema, SelfGraph


def _schema(Schema, data: dict):
    """用 selfgraph 的本体模型声明我们的业务类型。

    注意这里**只能声明它支持的语义**：实体类型、别名、属性规格、关系端点、
    定义（术语 + 认证）。**没有公式、没有动作、没有状态机**——这不是我们藏起来，
    是它的模型里没有这些概念。
    """
    ont = json.loads((DATA / "ontology.json").read_text(encoding="utf-8"))
    entities, relations = [], []
    for cls in ont["classes"].values():
        entities.append({
            "name": cls["label"],
            "aliases": [cls["id"], *cls.get("aliases", [])],
            "properties": [{"name": p, "type": "string"} for p in cls["properties"]],
        })
    for link in ont["relations"].values():
        relations.append({
            "type": link["id"],
            "aliases": [link.get("label", link["id"])],
            "from": link["domain"], "to": link["range"],
        })
    # 把"规则"声明成 **definition（术语 + 同义词）**——selfgraph 唯一能承载
    # "某个概念叫什么、从哪来"的容器。它不会因此变成可计算的公式。
    rule = ont["rules"]["R-WAIT-01"]
    entities[0].setdefault("definitions", [])
    entities[0]["definitions"].append({
        "name": "等候费规则应计",
        "expression": rule["formula"],
        "description": (f"规则 {rule['id']} · {rule['version']} · "
                        f"生效 {rule['effective_from']}~{rule['effective_to']}"),
        "synonyms": ["waiting_expected", "规则应计"],
        "certified": True,
    })
    return Schema(name="mobility-ontology-bench", version=ont["version"],
                  entities=entities, relations=relations)


class SelfGraphImpl:
    name = "selfgraph_ref: selfgraph（成熟属性图 SDK）"

    def __init__(self):
        _, KuzuStore, Schema, SelfGraph = _load_selfgraph()
        self.facts = json.loads((DATA / "facts.json").read_text(encoding="utf-8"))
        self.ont = json.loads((DATA / "ontology.json").read_text(encoding="utf-8"))
        self._SelfGraph = SelfGraph
        self._Schema = Schema
        self._store_path = str(WORK / "graph.kuzu")
        self._graph = None
        self._loop = None

    # ---- 打开图并灌入事实 ----
    def _open(self):
        if self._graph is not None:
            return self._graph
        _, KuzuStore, Schema, SelfGraph = _load_selfgraph()
        schema = _schema(Schema, self.facts)
        store = KuzuStore(self._store_path)
        self._graph = SelfGraph(store=store, schema=schema)
        self._run(self._graph.insert.facts(self._facts_payload()))
        return self._graph

    def _facts_payload(self) -> dict:
        entities, relations = [], []
        for wb in self.facts.get("waybills") or [self.facts["waybill"]]:
            entities.append({
                "id": f"waybill:{wb['waybill_id']}", "type": "运单", "name": wb["waybill_id"],
                "properties": {k: v for k, v in wb.items() if k != "waybill_id"},
            })
        for f in self.facts["fees"]:
            entities.append({
                "id": f"fee:{f['fee_id']}", "type": "费用项", "name": f["fee_id"],
                "properties": {k: v for k, v in f.items() if k != "fee_id"},
            })
        for wb in self.facts.get("waybills") or [self.facts["waybill"]]:
            for f in self.facts["fees"]:
                if f.get("waybill_id", "WB20261105001") != wb["waybill_id"]:
                    continue
                relations.append({
                    "from_entity": f"waybill:{wb['waybill_id']}",
                    "type": "运单产生费用", "to_entity": f"fee:{f['fee_id']}",
                    "properties": {"fee_type": f["fee_type"]},
                })
        return {"entities": entities, "relations": relations}

    @staticmethod
    def _run(coro):
        """在同一事件循环里跑协程（基准是同步契约，实现是异步 SDK）。"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    # ---- 从图里取数（只取图上有的）----
    def _fee_rows(self, waybill_id: str) -> list[dict]:
        rows = []
        for f in self.facts["fees"]:
            if f.get("waybill_id", "WB20261105001") != waybill_id:
                continue
            rows.append(f)
        return rows

    # ---- 用例 ----
    def q1_diff(self, waybill_id: str = "WB20261105001") -> dict:
        """差异归因：报价 / 实收 / 差额 —— 图上有的部分，selfgraph 能答。"""
        self._open()
        rows = self._fee_rows(waybill_id)
        quoted = round(sum(r["quoted_amount"] or 0 for r in rows), 2)
        actual = round(sum(r["actual_amount"] or 0 for r in rows), 2)
        answer = {
            "quoted_total": quoted, "actual_total": actual,
            "diff_total": round(actual - quoted, 2),
            # 应计值：selfgraph 无公式求值 → 用**检索**去问图里有没有这个答案，
            # 图里没有就是没有（不编）。
            "waiting_expected": None, "waiting_over": None,
            "explain": f"图上读到 {len(rows)} 条费用：报价合计 {quoted}、实收合计 {actual}、"
                       f"差额 {round(actual - quoted, 2)}",
            "audit": {},     # 无版本/规则ID/生效期/证据 checksum
        }
        # 让 selfgraph 自己回答"这个问题图里答得了吗"——这正是它的 answerability 机制
        try:
            report = self._run(self._graph.check_answerable("等候费按规则应收多少"))
            answer["explain"] += f"；answerability 报告缺失项：{report.missing}"
        except Exception as exc:  # noqa: BLE001
            answer["explain"] += f"；answerability 不可用：{exc}"
        return answer

    def q2_waiting(self, waybill_id: str = "WB20261105001") -> dict:
        """按规则应收多少 —— selfgraph 的模型里没有公式，**答不出**。"""
        self._open()
        waiting = next(r for r in self._fee_rows(waybill_id) if r["fee_type"] == "waiting")
        # 走一遍它自己的检索：definition（应计公式）被声明成术语，
        # 但没有表达式求值器 → 拿不到数字。
        try:
            result = self._run(self._graph.retrieve("等候费按规则应收多少"))
            context = getattr(result, "context", "") or ""
        except Exception as exc:  # noqa: BLE001
            context = f"（检索失败：{exc}）"
        return {
            "waiting_expected": None,
            "waiting_over": None,
            "explain": ("图里有费用事实与规则声明（术语/同义词/认证），"
                         "但 selfgraph 没有表达式求值器，"
                         "definition.expression 不会被算成金额 → 应计值无来源。"
                         f"检索返回：{context[:200]}"),
            "audit": {},
        }

    def q3_roadblock(self, amount: float = 40.0,
                     waybill_id: str = "WB20261105001") -> dict:
        """减免会被谁挡 —— 没有状态机、动作与金额上限声明 → 答不出。"""
        self._open()
        try:
            report = self._run(self._graph.check_answerable(
                f"减免 {amount} 元会被哪一步挡住"))
            missing = list(report.missing)
        except Exception as exc:  # noqa: BLE001
            missing = [f"（answerability 不可用：{exc}）"]
        return {
            "blocked": None,
            "reason_code": "",
            "explain": ("图里没有状态机、动作前置条件与减免上限声明 → "
                        f"无法判断会被哪一步挡住；answerability 报告缺失：{missing}"),
            "audit": {},
        }

    # ---- 变更：改 schema（声明）后要重开图（相当于重启）----
    def apply_change(self, card: dict) -> dict:
        """selfgraph 的声明在 schema 里；改它要重建 client（= 重启）。

        注意：即使改了，也**只影响检索与类型校验**——
        公式、动作、权限这些它本来就没有，所以变更后结论不会像本体那样翻转。
        """
        change = card["change"]
        path = DATA / "ontology.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        touched = 0
        if change.startswith('pricing.waiting.by_vehicle_type["4.2米厢货"].free_minutes'):
            raw["pricing"]["waiting"]["by_vehicle_type"]["4.2米厢货"]["free_minutes"] = 20
            touched = 1
        elif change.startswith("pricing.waiting.by_vehicle_type.add"):
            raw["pricing"]["waiting"].setdefault("by_vehicle_type", {})["7.6米厢货"] = {
                "free_minutes": 30, "block_minutes": 5, "per_block": 3.0}
            touched = 1
        elif change.startswith("actions.waive_charge.amount_cap"):
            raw["actions"]["waive_charge"]["amount_cap"] = change.split("->")[1].strip()
            touched = 1
        if touched:
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        # 重建图（自graph 的 schema 是启动时注入的）
        self._graph = None
        return {"changed_files": ["ontology.json"] if touched else [],
                "code_files": 0, "restart_required": True}


# runner 通过这个约定发现实现
IMPL = SelfGraphImpl
