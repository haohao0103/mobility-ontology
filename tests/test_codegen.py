"""P4-1 OSDK-lite 代码生成承重测试。"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

gen_osdk = importlib.import_module("tools.gen_osdk")


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("osdk"))
    rc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "gen_osdk.py"), "--out", out],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert rc.returncode == 0, rc.stderr
    return out


class TestGeneratedArtifacts:
    def test_typescript_generated(self, generated):
        ts = os.path.join(generated, "typescript.ts")
        assert os.path.exists(ts) and os.path.getsize(ts) > 1000
        content = open(ts, encoding="utf-8").read()
        assert "export interface Waybill" in content
        assert "waybill_id" in content
        assert "export class OntologyClient" in content

    def test_python_client_generated_and_importable(self, generated):
        py = os.path.join(generated, "python_client.py")
        assert os.path.exists(py) and os.path.getsize(py) > 1000
        spec = importlib.util.spec_from_file_location("generated_client", py)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["generated_client"] = mod  # dataclass 解析注解需要
        spec.loader.exec_module(mod)
        wb = mod.Waybill(object_id="waybill:WB001")
        assert wb.type == "Waybill"
        assert callable(mod.OntologyClient)

    def test_openapi_valid(self, generated):
        with open(os.path.join(generated, "openapi.json"), encoding="utf-8") as f:
            spec = json.load(f)
        assert spec["openapi"].startswith("3.")
        assert "/enh/objects" in spec["paths"]
        assert "/enh/functions/{function_id}/call" in spec["paths"]
        assert len(spec["components"]["schemas"]) >= 500
        assert "Waybill" in spec["components"]["schemas"]


class TestLoadBearing:
    def test_new_class_appears_after_regen(self, generated, sem, tmp_path):
        """承重：本体加新类 -> 重新生成 -> 客户端出现新类型（本体驱动客户端）。"""
        from enhancements.registry import ExtensionRegistry
        ext = ExtensionRegistry(base_classes=sem.classes)
        # 复制本体并加新类
        import copy
        classes = copy.deepcopy(sem.classes)
        ghost = copy.deepcopy(classes["Driver"])
        ghost["id"] = "GhostTag"
        ghost["properties"] = ["ghost_id", "ghost_name"]
        ghost["key"] = "ghost_id"
        classes["GhostTag"] = ghost
        ts = gen_osdk.generate_typescript(
            type("S", (), {"classes": classes})(), ext)
        assert "export interface GhostTag" in ts
        assert "ghost_id" in ts
        # 未加类前不含
        ts_before = gen_osdk.generate_typescript(sem, ext)
        assert "GhostTag" not in ts_before
