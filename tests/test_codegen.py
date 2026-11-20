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
    def test_tsc_compiles_and_breaks_on_schema_drift(self, generated):
        """tsc 真编译（需 TSC_BIN 指向 tsc 可执行，或 /tmp/tsc_try 预装）；
        无 tsc 环境时跳过（CI 接入时用 npm 装真实 tsc）。"""
        tsc = os.environ.get("TSC_BIN") or "/tmp/tsc_try/node_modules/.bin/tsc"
        if not os.path.exists(tsc):
            pytest.skip("tsc 不可用（设 TSC_BIN 或预装 typescript）")
        import tempfile
        ts = os.path.join(generated, "typescript.ts")
        # 1. 正常版编译通过
        r = subprocess.run([tsc, "--noEmit", "--target", "es2020",
                            "--lib", "es2020,dom", "--strict", "false", ts],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        # 2. 删类版（模拟本体演进后未重新生成的旧客户端）编译必须失败
        content = open(ts, encoding="utf-8").read()
        stripped = content.replace("export interface WaybillFilters", "export interface _RemovedFilters")
        with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(stripped)
            broken = tf.name
        use = broken.replace(".ts", "_use.ts")
        with open(use, "w", encoding="utf-8") as f:
            f.write("import { WaybillFilters } from './" +
                    os.path.basename(broken)[:-3] + "';\n"
                    "const f: WaybillFilters = {};\nexport default f;\n")
        r2 = subprocess.run([tsc, "--noEmit", "--target", "es2020",
                             "--lib", "es2020,dom", "--strict", "false",
                             "--esModuleInterop", use, broken],
                            capture_output=True, text=True)
        os.unlink(broken)
        os.unlink(use)
        assert r2.returncode != 0, "schema 漂移未被编译期发现"
        assert "TS2305" in r2.stdout or "WaybillFilters" in r2.stdout + r2.stderr

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
            type("S", (), {"classes": classes,
                           "raw": {"derived_types": []}})(), ext)
        assert "export interface GhostTag" in ts
        assert "ghost_id" in ts
        # 未加类前不含
        ts_before = gen_osdk.generate_typescript(sem, ext)
        assert "GhostTag" not in ts_before


class TestDerivedTypesCodegen:
    def test_derived_in_all_three_artifacts(self, generated):
        """派生类型三端同产：TS interface+工厂 / Python dataclass+工厂 / OpenAPI x-derived。"""
        ts = open(os.path.join(generated, "typescript.ts"), encoding="utf-8").read()
        assert "export interface CarrierResource extends OntologyObject" in ts
        assert "export interface CarrierResourceFilters" in ts
        assert "export function carrierResourceQuery" in ts
        assert "DisputedFee" in ts
        py = open(os.path.join(generated, "python_client.py"), encoding="utf-8").read()
        assert "class CarrierResource:" in py
        assert "def carrierResource_query" in py
        spec = json.load(open(os.path.join(generated, "openapi.json"), encoding="utf-8"))
        s = spec["components"]["schemas"]
        assert s["CarrierResource"].get("x-derived") is True
        assert s["DisputedFee"].get("x-derived") is True

    def test_derived_removal_drives_client(self, sem):
        """承重：本体删派生类型 -> 再生成客户端不再含（本体驱动客户端）。"""
        from enhancements.registry import ExtensionRegistry
        ext = ExtensionRegistry(base_classes=sem.classes)
        ts = gen_osdk.generate_typescript(sem, ext)
        assert "CarrierResource" in ts
        raw = dict(sem.raw)
        raw["derived_types"] = []
        stripped = type("S", (), {
            "classes": sem.classes,
            "raw": raw,
        })()
        ts2 = gen_osdk.generate_typescript(stripped, ext)
        assert "CarrierResource" not in ts2


class TestDerivedTypesCodegen:
    def test_derived_in_all_three_artifacts(self, generated):
        """派生类型三端同产：TS interface+工厂 / Python dataclass+工厂 / OpenAPI x-derived。"""
        ts = open(os.path.join(generated, "typescript.ts"), encoding="utf-8").read()
        assert "export interface CarrierResource extends OntologyObject" in ts
        assert "export interface CarrierResourceFilters" in ts
        assert "export function carrierResourceQuery" in ts
        assert "DisputedFee" in ts
        py = open(os.path.join(generated, "python_client.py"), encoding="utf-8").read()
        assert "class CarrierResource:" in py
        assert "def carrierResource_query" in py
        spec = json.load(open(os.path.join(generated, "openapi.json"), encoding="utf-8"))
        s = spec["components"]["schemas"]
        assert s["CarrierResource"].get("x-derived") is True
        assert s["DisputedFee"].get("x-derived") is True

    def test_derived_removal_drives_client(self, sem):
        """承重：本体删派生类型 -> 再生成客户端不再含（本体驱动客户端）。"""
        from enhancements.registry import ExtensionRegistry
        ext = ExtensionRegistry(base_classes=sem.classes)
        ts = gen_osdk.generate_typescript(sem, ext)
        assert "CarrierResource" in ts
        raw = dict(sem.raw)
        raw["derived_types"] = []
        stripped = type("S", (), {
            "classes": sem.classes,
            "raw": raw,
        })()
        ts2 = gen_osdk.generate_typescript(stripped, ext)
        assert "CarrierResource" not in ts2
