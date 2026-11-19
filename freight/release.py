"""本体发布快照与语义 Diff。

当前 `semantic_layer.json` 是工作副本；`freight/releases/<version>/` 存放
不可变发布快照。快照由 `scripts/build_release.py` 生成，运行时只读。
"""
from __future__ import annotations

import hashlib
import json
import os

RELEASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "releases")


def file_checksum(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def release_dir(version: str) -> str:
    return os.path.join(RELEASES_DIR, version)


def semantic_path(version: str) -> str:
    return os.path.join(release_dir(version), "semantic_layer.json")


def manifest_path(version: str) -> str:
    return os.path.join(release_dir(version), "manifest.json")


def load_manifest(version: str) -> dict:
    path = manifest_path(version)
    if not os.path.exists(path):
        raise FileNotFoundError(f"版本不存在: {version}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_manifests() -> list[dict]:
    if not os.path.isdir(RELEASES_DIR):
        return []
    items = []
    for name in sorted(os.listdir(RELEASES_DIR)):
        path = manifest_path(name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest.setdefault("version", name)
        manifest.setdefault("source", f"freight/releases/{name}/semantic_layer.json")
        items.append(manifest)
    return items


def diff_values(before, after, path: str = "") -> list[dict]:
    """递归比较 JSON，列表按 id 对齐，输出可展示的字段级变更。"""
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else key
            if key not in before:
                changes.append({"path": child, "from": None, "to": after[key], "kind": "added"})
            elif key not in after:
                changes.append({"path": child, "from": before[key], "to": None, "kind": "removed"})
            else:
                changes.extend(diff_values(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        before_ids = [item.get("id") for item in before if isinstance(item, dict)]
        after_ids = [item.get("id") for item in after if isinstance(item, dict)]
        if before_ids and after_ids and all(before_ids) and all(after_ids):
            changes = []
            before_map = {item["id"]: item for item in before}
            after_map = {item["id"]: item for item in after}
            for item_id in sorted(set(before_map) | set(after_map)):
                child = f"{path}[{item_id}]"
                if item_id not in before_map:
                    changes.append({"path": child, "from": None, "to": after_map[item_id], "kind": "added"})
                elif item_id not in after_map:
                    changes.append({"path": child, "from": before_map[item_id], "to": None, "kind": "removed"})
                else:
                    changes.extend(diff_values(before_map[item_id], after_map[item_id], child))
            return changes
        if before == after:
            return []
        return [{"path": path or "$", "from": before, "to": after, "kind": "changed"}]
    if before != after:
        return [{"path": path or "$", "from": before, "to": after, "kind": "changed"}]
    return []


def diff_semantics(before: dict, after: dict) -> list[dict]:
    return diff_values(before, after)