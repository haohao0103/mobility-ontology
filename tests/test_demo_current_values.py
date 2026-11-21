"""门禁第 30 项：演示页里的"当前业务值"必须与本体一致。

为什么要有这道门禁：演示页多数数值已改为运行时动态渲染，但仍有少量手写文案
（如"超过 30 分钟免费时长"）。本体参数一改而页面没同步，演示当天就会出现
"领导看到 30、系统算出 16.8"的事故——这是最难当场察觉的一类事故。

机制：页面里"当前值"用 `data-ontology-value="waiting.free_minutes"` 标注；
门禁解析标注、抽出数字、与本体 `pricing` 比对。历史对比文案（版本 diff 的
30→20）**不加标注**，因此不会误报。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MARKER = re.compile(r'data-ontology-value="([a-z_.]+)"[^>]*>([^<]*)<', re.IGNORECASE)


def _pricing(root: str) -> dict:
    with open(os.path.join(root, "freight", "semantic_layer.json"), encoding="utf-8") as f:
        return json.load(f).get("pricing") or {}


def _resolve(pricing: dict, path: str):
    cur = pricing
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _markers(root: str) -> list[tuple[str, str]]:
    with open(os.path.join(root, "web", "order-ontology.html"), encoding="utf-8") as f:
        html = f.read()
    return MARKER.findall(html)


class TestDemoCurrentValues:
    def test_gate_check_passes_on_current_state(self):
        from scripts.semantic_gate import check_demo_current_values
        item = check_demo_current_values(ROOT)
        assert item["ok"], item["issues"][:3]
        assert item["checked_markers"] >= 1, "演示页应当至少有一处可校验的当前值标注"

    def test_every_marker_matches_ontology(self):
        pricing = _pricing(ROOT)
        for path, text in _markers(ROOT):
            expected = _resolve(pricing, path)
            if expected is None:
                continue
            nums = re.findall(r"\d+(?:\.\d+)?", text)
            assert nums, f"标注 {path} 的文本里没有数字：{text!r}"
            assert abs(float(nums[0]) - float(expected)) < 1e-9, (
                f"演示页写的 {path}={nums[0]}，本体里是 {expected}")

    def test_diverged_value_is_caught(self, tmp_path):
        """本体改了、页面没同步 -> 必须被抓到（反向验证）。"""
        from scripts.semantic_gate import check_demo_current_values
        pricing = _pricing(ROOT)
        path, text = _markers(ROOT)[0]
        expected = _resolve(pricing, path)
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if not nums or expected is None:
            pytest.skip("当前标注没有可比对的数字")
        # 造一个错值：把标注里的数字改成与本体不同的值
        bad = str(int(expected) + 7)
        html_path = os.path.join(ROOT, "web", "order-ontology.html")
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        mutated = html.replace(
            f'data-ontology-value="{path}">{nums[0]}',
            f'data-ontology-value="{path}">{bad}', 1)
        assert mutated != html, "演示页标注结构与预期不符"
        tmp_root = tmp_path / "repo"
        tmp_root.mkdir()
        (tmp_root / "freight").mkdir()
        (tmp_root / "web").mkdir()
        shutil_html = mutated
        with open(tmp_root / "freight" / "semantic_layer.json", "w", encoding="utf-8") as f:
            json.dump({"pricing": pricing}, f, ensure_ascii=False)
        with open(tmp_root / "web" / "order-ontology.html", "w", encoding="utf-8") as f:
            f.write(shutil_html)
        item = check_demo_current_values(str(tmp_root))
        assert item["ok"] is False
        assert any(i["code"] == "DEMO_VALUE_DIVERGED" for i in item["issues"])
