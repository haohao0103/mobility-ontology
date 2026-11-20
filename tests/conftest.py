"""测试夹具：临时本体扩展 + 临时事实库。

承重测试核心机制：make_ext(mutator=...) 把 ontology_extension.json 拷贝到
临时文件、应用 dict 级变换（删注册/改标记/删策略）后加载——
模拟"只改本体 JSON、代码零改动"，断言行为随之改变。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.semantic import SemanticLayer  # noqa: E402
from enhancements.registry import ExtensionRegistry  # noqa: E402

EXT_PATH = os.path.join(ROOT, "enhancements", "ontology_extension.json")


@pytest.fixture(scope="session")
def sem() -> SemanticLayer:
    return SemanticLayer()


@pytest.fixture()
def make_ext(sem):
    """工厂：mutator(raw_dict) -> raw_dict，返回按变换后的扩展注册表。"""

    def _make(mutator=None):
        with open(EXT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if mutator is not None:
            raw = mutator(json.loads(json.dumps(raw)))  # 深拷贝
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(raw, tmp, ensure_ascii=False)
        tmp.close()
        return ExtensionRegistry(
            path=tmp.name, base_classes=sem.classes,
            base_property_types=sem.raw.get("property_types"))

    return _make


@pytest.fixture()
def make_ext_without(sem):
    """工厂：从「基础本体属性注册表」中剔除指定属性后构造扩展注册表。

    schema v3 之后基础本体是全量权威注册表，所有属性都已注册。要验证「未注册
    属性 -> legacy / strict 拒绝 / 类型校验跳过」这类承重语义，必须显式把属性
    从基础注册表里摘掉，等价于「该属性从未登记」。
    """

    def _make(drop, mutator=None):
        drop = set(drop)
        with open(EXT_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if mutator is not None:
            raw = mutator(json.loads(json.dumps(raw)))
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(raw, tmp, ensure_ascii=False)
        tmp.close()
        base_pt = [p for p in sem.raw.get("property_types", [])
                   if p["name"] not in drop]
        return ExtensionRegistry(
            path=tmp.name, base_classes=sem.classes,
            base_property_types=base_pt)

    return _make


@pytest.fixture(scope="module")
def system():
    """带种子的 FreightSystem（module 级共享，只读用途）。"""
    from freight.engine import FreightSystem
    td = tempfile.mkdtemp(prefix="enh_test_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


@pytest.fixture()
def enhanced(system):
    from enhancements.runtime import EnhancedSystem
    return EnhancedSystem(system)
