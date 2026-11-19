"""生成不可变本体发布快照。

示例：
  python scripts/build_release.py \
    --version 1.1.0-demo \
    --label "演示：免费等候 30→20 分钟" \
    --set pricing.waiting.free_minutes=20 \
    --set 'pricing.waiting.by_vehicle_type["4.2米厢货"].per_block=3.0' \
    --set rules.R-WAIT-01.judge.explain="等候 20 分钟免费..."
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.release import diff_semantics, file_checksum  # noqa: E402
from freight.semantic import validate_semantic  # noqa: E402

BASE_PATH = os.path.join(ROOT, "freight", "semantic_layer.json")
RELEASES_DIR = os.path.join(ROOT, "freight", "releases")


def parse_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_path(path: str) -> list[str]:
    """解析 a.b["c.d"].e 形式的路径，允许键名本身包含点号。"""
    parts: list[str] = []
    buffer = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if buffer:
                parts.append(buffer)
                buffer = ""
        elif char == "[" and index + 1 < len(path) and path[index + 1] in "\"'":
            if buffer:
                parts.append(buffer)
                buffer = ""
            quote = path[index + 1]
            end = path.find(f"{quote}]", index + 2)
            if end < 0:
                raise ValueError(f"路径括号未闭合: {path}")
            parts.append(path[index + 2:end])
            index = end + 1
        else:
            buffer += char
        index += 1
    if buffer:
        parts.append(buffer)
    return parts


def set_path(node, parts: list[str], value) -> None:
    key = parts[0]
    if isinstance(node, dict):
        if len(parts) == 1:
            node[key] = value
            return
        if key not in node or not isinstance(node[key], (dict, list)):
            node[key] = {}
        set_path(node[key], parts[1:], value)
        return
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and item.get("id") == key:
                if len(parts) == 1:
                    raise ValueError(f"不能直接替换列表项: {key}")
                set_path(item, parts[1:], value)
                return
        raise ValueError(f"列表中不存在 id={key}")
    raise ValueError(f"路径无法解析: {key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--label", default="")
    parser.add_argument("--based-on", default=None)
    parser.add_argument("--set", action="append", default=[], metavar="PATH=VALUE")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    with open(BASE_PATH, encoding="utf-8") as f:
        base = json.load(f)
    manifest = copy.deepcopy(base)
    based_on = args.based_on or str(base.get("meta", {}).get("version") or "")
    manifest.setdefault("meta", {})["version"] = args.version
    manifest["meta"]["based_on"] = based_on
    manifest["meta"]["released_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    for assignment in args.set:
        if "=" not in assignment:
            raise SystemExit(f"--set 需要 PATH=VALUE: {assignment}")
        path, raw = assignment.split("=", 1)
        set_path(manifest, parse_path(path), parse_value(raw))

    issues = validate_semantic(manifest)
    if issues:
        print(json.dumps({"ok": False, "issues": issues}, ensure_ascii=False, indent=2))
        return 1

    release_dir = os.path.join(RELEASES_DIR, args.version)
    semantic_file = os.path.join(release_dir, "semantic_layer.json")
    manifest_file = os.path.join(release_dir, "manifest.json")
    if os.path.exists(semantic_file) and not args.force:
        raise SystemExit(f"版本已存在且不可变: {args.version}（如需重建请加 --force）")
    os.makedirs(release_dir, exist_ok=True)
    with open(semantic_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    changes = diff_semantics(base, manifest)
    payload = {
        "version": args.version,
        "label": args.label or args.version,
        "based_on": based_on,
        "created_at": manifest["meta"]["released_at"],
        "checksum": file_checksum(semantic_file),
        "source": f"freight/releases/{args.version}/semantic_layer.json",
        "change_count": len(changes),
        "changes": changes,
        "validation": {"ok": True, "checks": 7},
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(json.dumps({
        "ok": True,
        "version": args.version,
        "checksum": payload["checksum"],
        "change_count": len(changes),
        "changes": changes,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())