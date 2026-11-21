"""隔离测试：A 组（属性图）不允许 import freight / 读 semantic_layer.json。

这是 ab-value-experiment-plan.md §7 的隔离要求：静态扫描 group_a 源码，
出现违规引用即失败。防止「A 组偷偷用本体能力」破坏公平性。
"""
from __future__ import annotations

from pathlib import Path

GROUP_A_DIR = Path(__file__).resolve().parent.parent / "experiments" / "ab_value" / "group_a"


def test_group_a_does_not_import_freight():
    for py in GROUP_A_DIR.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        assert "from freight" not in src and "import freight" not in src, py
        assert "semantic_layer.json" not in src, py


def test_group_a_rules_yaml_has_no_ontology_reference():
    src = (GROUP_A_DIR / "rules.yaml").read_text(encoding="utf-8")
    assert "semantic" not in src.lower()
    assert "本体" not in src
