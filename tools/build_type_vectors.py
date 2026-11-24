"""离线构建类型卡向量索引（C3 通道）。

走 OPENAI_EMBEDDINGS_API_BASE / OPENAI_EMBEDDINGS_API_KEY（与 LLM 网关分立，
可指向任何 OpenAI 兼容 /embeddings 端点）。输出 runs/type_vectors.json：
{model, dim, fingerprint, vectors: {type_id: [...]}}。

fingerprint = 类型卡内容指纹：semantic_layer.json 变更后索引自动失效，
检索器降级到 exact+lexical+graph+history 并显式标注（不静默用旧向量）。

当前 MiMo 网关（api.xiaomimimo.com）无 /embeddings（实测 404），
本脚本就绪待端点；无端点时退出码 2 并说明。

用法：
    python3 tools/build_type_vectors.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.retrieval import TypeCard, _content_fingerprint  # noqa: E402
from freight.semantic import SemanticLayer  # noqa: E402

OUT = os.path.join(ROOT, "runs", "type_vectors.json")


def main() -> int:
    base = os.environ.get("OPENAI_EMBEDDINGS_API_BASE")
    key = os.environ.get("OPENAI_EMBEDDINGS_API_KEY")
    model = os.environ.get("OPENAI_EMBEDDINGS_MODEL", "embedding-3")
    if not base or not key:
        print("未配置 OPENAI_EMBEDDINGS_API_BASE / OPENAI_EMBEDDINGS_API_KEY。")
        print("当前 MiMo 网关无 /embeddings（实测 404）；C3 通道保持降级，")
        print("检索走 exact+lexical+graph+history（确定性通道）。")
        return 2

    sem = SemanticLayer()
    retriever_cards: list[TypeCard] = []
    from freight.retrieval import Retriever
    retriever = Retriever(sem)
    retriever_cards = retriever.cards
    texts = []
    for card in retriever_cards:
        fields = " / ".join(
            f"{name}: {text}" for name, text in card.fields.items() if text
        )
        texts.append(f"{card.id} {fields}")
    print(f"类型卡 {len(texts)} 张，请求 {model} 向量…")

    vectors: dict[str, list[float]] = {}
    batch = 32
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        body = json.dumps({"model": model, "input": chunk}).encode()
        req = urllib.request.Request(
            base.rstrip("/") + "/embeddings", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"批次 {i // batch} 失败: {e}")
            return 1
        for j, item in enumerate(data.get("data") or []):
            tid = retriever_cards[i + j].id
            vectors[tid] = list(item["embedding"])
        if (i // batch) % 4 == 0:
            print(f"  {i + len(chunk)}/{len(texts)}")

    fingerprint = _content_fingerprint(sem)
    out = {
        "model": model,
        "dim": len(next(iter(vectors.values()))),
        "fingerprint": fingerprint,
        "count": len(vectors),
        "vectors": vectors,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"已写入 {OUT}（{len(vectors)} 向量，指纹 {fingerprint}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
