#!/usr/bin/env python3
"""阶段 1 盲测：运力域（domains_02_capacity）业务口语 → 对象类型恢复能力。

输入：tools/blindtest_capacity_business_text.md（业务方原话，不含任何类 id/字段名）
对照：ontology/domains_02_capacity.py（人工版 98 类）
方法：LLM 按 tools/modeling_prompt.md 的 R1–R10，**分两阶段**建模：
  Stage A 只要对象类型清单（id/label/key/description/why）→ 测类召回率
  Stage B 再补属性与类型（针对 Stage A 的类清单）→ 测字段类型准确率
两阶段的原因（实测）：单次输出装不下一个 98 类域（JSON 会在未闭合处被截断），
分阶段也正是人工建模的顺序（先定对象，再补属性）。

★ 口径（分母定义与来源一并落档，勿改口径后引用旧数字）：
  - 类召回率      = Stage A 匹配上的类数 / 98
                   （分母=人工版类总数，来源=domains_02_capacity.CLASSES）
  - 字段类型准确率 = Σ(匹配类中「两边都有且类型短码相同」的属性数)
                   / Σ(匹配类中「两边都有」的属性数)
                   （分母=Stage B 声明且人工版也有的属性条数；属性名精确匹配）
  - 属性召回（辅助）= Σ(匹配类中「两边都有」的属性数) / Σ(人工版属性总数)
  - 类匹配规则：label 中文精确 → id 精确（大小写不敏感）→ label 归一化（去空白/全半角）
判据（docs/AIGEN_ROLLOUT_PLAN.md §5）：类召回 ≥ 70%，字段类型准确率 ≥ 85%。

只读评估：不碰 semantic_layer.json / 不改 build 源 / 不写台账。
用法：
    python3 tools/blindtest_capacity.py [--max-tokens N] [--out FILE]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.agent import LLMClient, _extract_json_object  # noqa: E402

PROMPT_MD = ROOT / "tools" / "modeling_prompt.md"
BUSINESS_MD = ROOT / "tools" / "blindtest_capacity_business_text.md"
DOMAIN_MODULE = "ontology.domains_02_capacity"
OUT_DIR = ROOT / "tools" / "_out"

RECALL_MIN = 0.70
TYPE_ACC_MIN = 0.85


def _first_code_block(text: str) -> str:
    parts = text.split("```")
    return parts[1].strip() if len(parts) > 2 else ""


def extract_system_prompt(path: Path) -> str:
    """从 modeling_prompt.md 取 System Prompt 代码块（复用团队规范那一份，不另存）。"""
    text = path.read_text(encoding="utf-8")
    if "## System Prompt" not in text:
        raise SystemExit("modeling_prompt.md 里找不到 '## System Prompt' 段")
    return _first_code_block(text.split("## System Prompt", 1)[1])


def extract_stage_templates(path: Path) -> tuple[str, str]:
    """从 modeling_prompt.md 的「分阶段调用」段取 Stage A / Stage B 模板。"""
    text = path.read_text(encoding="utf-8")
    if "## 分阶段调用" not in text:
        raise SystemExit("modeling_prompt.md 里找不到 '## 分阶段调用' 段")
    seg = text.split("## 分阶段调用", 1)[1]
    a_seg = seg.split("**Stage A", 1)[1]
    b_seg = a_seg.split("**Stage B", 1)[1]
    tpl_a = _first_code_block(a_seg)
    tpl_b = _first_code_block(b_seg)
    if not tpl_a or not tpl_b:
        raise SystemExit("Stage A/B 模板的代码块没解析出来")
    return tpl_a, tpl_b


def normalize(text: str) -> str:
    """label 归一化：去空白 + 全角转半角 + 小写。"""
    t = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    return re.sub(r"\s+", "", t)


def split_sections(body: str) -> list[tuple[str, str]]:
    """按业务方描述里的小标题（**...**）切块。"""
    parts = re.split(r"\*\*(.+?)\*\*", body)
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip()
        text = parts[i + 1].strip()
        if title and text:
            out.append((title, text))
    return out


def human_classes() -> list[dict]:
    """人工版类清单（权威对照）。"""
    mod = importlib.import_module(DOMAIN_MODULE)
    out = []
    for c in mod.CLASSES:
        props = {name: code for name, code in (c.get("_props_spec") or [])}
        out.append({
            "id": c["id"], "label": c.get("label"), "key": c.get("key"),
            "props": props,
            "statuses": list(c.get("statuses") or []),
        })
    return out


def match_classes(human: list[dict], llm: list[dict]) -> tuple[dict, list, list]:
    """返回 (匹配表 {llm_index: human_idx}, 漏掉的类, 多出来的类)。"""
    by_label = {normalize(h["label"]): i for i, h in enumerate(human)}
    by_id = {normalize(h["id"]): i for i, h in enumerate(human)}
    pairs: dict[int, int] = {}
    used: set[int] = set()
    extra: list[dict] = []
    for j, cand in enumerate(llm):
        hit = None
        for key in (normalize(cand.get("label")), normalize(cand.get("id"))):
            if key and key in by_label and by_label[key] not in used:
                hit = by_label[key]
                break
            if key and key in by_id and by_id[key] not in used:
                hit = by_id[key]
                break
        if hit is None:
            extra.append(cand)
        else:
            used.add(hit)
            pairs[j] = hit
    missed = [human[i] for i in range(len(human)) if i not in used]
    return pairs, missed, extra


def class_score(human: list[dict], llm: list[dict]) -> dict:
    """类召回（Stage A 指标）。"""
    pairs, missed, extra = match_classes(human, llm)
    n = len(human)
    return {
        "human_class_count": n,
        "llm_class_count": len(llm),
        "matched_class_count": len(pairs),
        "class_recall": round(len(pairs) / n, 4) if n else 0.0,
        "missed_classes": [{"id": h["id"], "label": h["label"]} for h in missed],
        "extra_classes": [{"id": c.get("id"), "label": c.get("label")}
                          for c in extra],
    }


def type_score(human: list[dict], stage_a: list[dict],
               type_map: dict) -> dict:
    """字段类型准确率（Stage B 指标；对 Stage A 中匹配上人工版的类统计）。"""
    pairs, _missed, _extra = match_classes(human, stage_a)
    same = 0
    both = 0
    human_total = 0
    mismatches: list[dict] = []
    for j, h_idx in pairs.items():
        cand = stage_a[j]
        types = type_map.get(cand.get("id")) or {}
        h_props = human[h_idx]["props"]
        human_total += len(h_props)
        for name, code in types.items():
            if name in h_props:
                both += 1
                if str(code).strip() == str(h_props[name]).strip():
                    same += 1
                else:
                    mismatches.append({
                        "class": human[h_idx]["id"], "property": name,
                        "human_type": h_props[name], "llm_type": code})
    return {
        "type_accuracy": round(same / both, 4) if both else None,
        "type_accuracy_denominator": both,
        "type_match_count": same,
        "property_recall": round(both / human_total, 4) if human_total else None,
        "property_recall_denominator": human_total,
        "type_mismatches": mismatches[:60],
        "type_mismatch_count": len(mismatches),
        "classes_with_types": len(type_map),
    }


def judge(report: dict) -> dict:
    recall_ok = report["class_recall"] >= RECALL_MIN
    ta = report.get("type_accuracy")
    type_ok = ta is not None and ta >= TYPE_ACC_MIN
    return {"class_recall_pass": recall_ok, "type_accuracy_pass": type_ok,
            "overall": "PASS" if (recall_ok and type_ok) else "FAIL"}


def call_llm(client, system_prompt: str, user_prompt: str) -> tuple[str, dict | None]:
    text = client.chat(
        [{"role": "system", "content": system_prompt},
         {"role": "user", "content": user_prompt}],
        thinking=False)
    parsed = _extract_json_object(text or "")
    return text or "", (parsed if isinstance(parsed, dict) else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--business", default=str(BUSINESS_MD))
    ap.add_argument("--max-tokens", type=int,
                    default=int(os.environ.get("BLINDTEST_MAX_TOKENS", "8192")),
                    help="单次生成预算（生产 agent 默认 OPENAI_CHAT_STEP_TOKENS=1600）")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    raw_dir = OUT_DIR / "blindtest_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = extract_system_prompt(PROMPT_MD)
    tpl_a, tpl_b = extract_stage_templates(PROMPT_MD)
    business = Path(args.business).read_text(encoding="utf-8")
    body = business.split("## 业务方原话（运力域）", 1)[-1]
    body = body.split("## 验证目标", 1)[0].strip()
    sections = split_sections(body)
    human = human_classes()
    client = LLMClient()
    client.max_tokens = args.max_tokens

    report: dict = {
        "domain": DOMAIN_MODULE,
        "business_input": str(Path(args.business).relative_to(ROOT)),
        "human_class_count": len(human),
        "max_tokens": args.max_tokens,
        "max_tokens_note": "生产 agent 默认 OPENAI_CHAT_STEP_TOKENS=1600；"
                           "盲测单独放大并分阶段（单次输出装不下 98 类）",
        "thresholds": {"class_recall_min": RECALL_MIN,
                       "type_accuracy_min": TYPE_ACC_MIN},
        "sections": [{"title": t, "chars": len(b)} for t, b in sections],
    }

    # ---- Stage A：类清单 ----
    t0 = time.time()
    stage_a: list[dict] = []
    per_block_a: list[dict] = []
    for idx, (title, text) in enumerate(sections, 1):
        print(f"[A {idx}/{len(sections)}] {title} …", flush=True)
        raw, parsed = call_llm(client, system_prompt,
                                tpl_a.replace("<粘贴业务方原话>", text))
        (raw_dir / f"{stamp}_A{idx}.txt").write_text(raw, encoding="utf-8")
        classes = [c for c in ((parsed or {}).get("classes") or [])
                   if isinstance(c, dict)]
        stage_a.extend(classes)
        per_block_a.append({"title": title, "raw_chars": len(raw),
                            "parsed": parsed is not None,
                            "class_count": len(classes),
                            "classes": classes})
        print(f"    → {len(raw)} 字符 / {len(classes)} 类", flush=True)
    report["stage_a"] = class_score(human, stage_a)
    report["stage_a"]["blocks"] = per_block_a
    report["stage_a"]["elapsed_seconds"] = round(time.time() - t0, 1)

    # ---- Stage B：属性与类型 ----
    t0 = time.time()
    type_map: dict = {}
    per_block_b: list[dict] = []
    for idx, ((title, text), blk) in enumerate(zip(sections, per_block_a), 1):
        classes_a = blk.get("classes") or []
        print(f"[B {idx}/{len(sections)}] {title} …", flush=True)
        user = (tpl_b
                .replace("<粘贴 Stage A 的 classes[]>", json.dumps(classes_a,
                                                                ensure_ascii=False))
                .replace("<粘贴同一段业务方原话>", text))
        raw, parsed = call_llm(client, system_prompt, user)
        (raw_dir / f"{stamp}_B{idx}.txt").write_text(raw, encoding="utf-8")
        got = [c for c in ((parsed or {}).get("classes") or [])
               if isinstance(c, dict)]
        for c in got:
            if c.get("id"):
                type_map[c["id"]] = c.get("property_types") or {}
        per_block_b.append({"title": title, "raw_chars": len(raw),
                            "parsed": parsed is not None,
                            "class_count": len(got)})
        print(f"    → {len(raw)} 字符 / {len(got)} 类带类型", flush=True)
    report["stage_b"] = type_score(human, stage_a, type_map)
    report["stage_b"]["blocks"] = per_block_b
    report["stage_b"]["elapsed_seconds"] = round(time.time() - t0, 1)

    report["verdict"] = judge(report["stage_a"] | {
        "type_accuracy": report["stage_b"]["type_accuracy"]})

    # 报告里不留块内类明细（体积），明细在原始输出与候选文件里
    for blk in report["stage_a"]["blocks"]:
        blk.pop("classes", None)

    out_path = Path(args.out) if args.out else (
        OUT_DIR / f"blindtest_capacity_{stamp}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    a, b = report["stage_a"], report["stage_b"]
    print("\n=== 阶段 1 盲测结果 ===")
    print(f"[Stage A 类清单] 人工 {a['human_class_count']} / LLM "
          f"{a['llm_class_count']} / 匹配 {a['matched_class_count']} → 召回 "
          f"{a['class_recall']:.1%}（判据 ≥{RECALL_MIN:.0%}）")
    if b["type_accuracy"] is None:
        print("[Stage B 属性类型] 无可比属性（分母 0）")
    else:
        print(f"[Stage B 属性类型] 类型准确率 {b['type_accuracy']:.1%}"
              f"（分母 {b['type_accuracy_denominator']} 条共同属性，"
              f"判据 ≥{TYPE_ACC_MIN:.0%}）；属性召回 "
              f"{b['property_recall']:.1%}"
              f"（分母 {b['property_recall_denominator']}）")
    print(f"漏 {len(a['missed_classes'])} 类 / 多 {len(a['extra_classes'])} 类 / "
          f"类型不符 {b['type_mismatch_count']} 条 → "
          f"{report['verdict']['overall']}")
    print(f"报告：{out_path.relative_to(ROOT)}；原始输出："
          f"{raw_dir.relative_to(ROOT)}/")
    return 0 if report["verdict"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
