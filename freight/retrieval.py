"""对象类型检索 v4：五路召回 + RRF 融合 + 归一化阈值 + 零命中。

替换 v3 的 bigram 字符串匹配（旧实现已删除，git 历史可查）。

五路召回（plan/ontology-context-retrieval-v4-plan.md §6）：
  C1 exact   归一化精确匹配 id / label / aliases / glossary 术语
  C2 lexical jieba 分词 + BM25（IDF、字段权重、长度归一）
  C3 vector  类型卡向量（runs/type_vectors.json，离线构建；不可用即降级）
  C4 graph   命中类型沿关系闭包 1 跳扩展（衰减 0.5，边界链接同样扩展）
  C5 history 查询历史（runs/retrieval_history.jsonl，问句相似回传实际类型）

纪律：
  - LLM / embedding 不参与在线打分；向量索引离线构建、带内容指纹。
  - 任何降级（向量缺、词典缺）都在结果里显式标注 channels_used / degraded。
  - 归一化 RRF 分低于阈值即丢弃；全通道零命中 → zero_hit=True（宁缺毋滥）。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections import defaultdict

try:  # jieba 为可选依赖：缺失时退化为字符 2-gram（诚实标注 degraded）
    import jieba

    HAS_JIEBA = True
except ImportError:  # pragma: no cover
    HAS_JIEBA = False

from freight.semantic import BusinessReject

RRF_K = 60
DEFAULT_THRESHOLD = 0.02
BM25_K1 = 1.5
BM25_B = 0.75
GRAPH_DECAY = 0.5
HISTORY_SIM_MIN = 0.3

# 字段权重（label > aliases > glossary > 属性名 > 描述）
FIELD_BOOST = {"label": 3.0, "aliases": 2.5, "glossary": 2.0, "properties": 1.5,
               "description": 1.0}

# RRF 通道权重：精确 / 历史是强信号；图扩展是泛化信号（衰减）。
CHANNEL_WEIGHT = {"exact": 1.5, "history": 1.2, "lexical": 1.0, "vector": 1.0,
                  "graph": 0.5}

_WORD_RE = re.compile(r"[a-z0-9_]+")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _bigrams(text: str) -> set[str]:
    n = _normalize(text)
    return {n[i:i + 2] for i in range(len(n) - 1)} if len(n) >= 2 else set()


WORD_RE = re.compile(r"[a-z0-9_]+")

# 中文停用词（高频无区分度，BM25/精确通道一律过滤）
STOPWORDS = {
    "的", "了", "在", "是", "有", "和", "就", "不", "人", "都", "一", "个",
    "这", "那", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "什么", "怎么", "为什么", "哪",
    "哪个", "哪些", "多少", "吗", "呢", "吧", "啊", "呀", "我们", "你们",
    "他们", "帮我", "一下", "有没有", "是不是", "了没", "最近", "什么",
}


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    """中文分词 + 英文/数字整词；jieba 缺失时退化为字符 2-gram。

    停用词默认过滤（保留英文与数字 token）。
    """
    text = str(text or "").lower()
    tokens: list[str] = []
    if HAS_JIEBA:
        tokens = [t.strip() for t in jieba.lcut(text) if t.strip()]
    else:
        for i in range(max(0, len(text) - 1)):
            gram = text[i:i + 2]
            if gram.strip():
                tokens.append(gram)
    tokens.extend(_WORD_RE.findall(text))
    # 单字噪声过滤（保留 2+ 字 token 与英文词）
    tokens = [t for t in tokens if len(t) >= 2 or t.isascii()]
    if not keep_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return tokens


class TypeCard:
    """检索侧类型卡：把类 + glossary 命中的术语拼成带权重字段的文档。"""

    def __init__(self, cid: str, label: str, aliases: list[str],
                 description: str, properties: list[str],
                 glossary_terms: list[str], aggregate: str, context: str):
        self.id = cid
        self.label = label
        self.aliases = aliases
        self.fields = {
            "label": label,
            "aliases": " ".join(aliases),
            "description": description,
            "properties": " ".join(properties),
            "glossary": " ".join(glossary_terms),
        }
        self.aggregate = aggregate
        self.context = context
        self.field_tokens = {
            name: tokenize(text) for name, text in self.fields.items()
        }


class Retriever:
    """对象类型检索器。由 SemanticLayer 持有（懒加载），不反向依赖。"""

    def __init__(self, sem, vectors_path: str | None = None,
                 history_path: str | None = None):
        self.sem = sem
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.vectors_path = vectors_path or os.path.join(root, "runs", "type_vectors.json")
        self.history_path = history_path or os.path.join(
            root, "runs", "retrieval_history.jsonl")
        self._cards: list[TypeCard] | None = None
        self._bm25: _BM25Index | None = None
        self._vectors: dict | None = None
        self._vectors_loaded = False
        self._exact_index: dict[str, list[str]] | None = None
        self._neighbors: dict[str, list[str]] | None = None

    # ── 索引构建（懒加载，本体不变时只建一次） ──────────────────
    def _glossary_by_type(self) -> dict[str, list[str]]:
        """术语表 → 类型文本映射（term + synonyms + colloquial 全部进 BM25 字段）。"""
        by_type: dict[str, list[str]] = defaultdict(list)
        for g in self.sem.glossary:
            base = str(g.get("type_ref") or "").split(".")[0]
            if base:
                by_type[base].append(str(g.get("term")))
                by_type[base].extend(str(s) for s in (g.get("synonyms") or []))
                by_type[base].extend(str(c) for c in (g.get("colloquial") or []))
        return by_type

    def _build_cards(self) -> list[TypeCard]:
        gloss = self._glossary_by_type()
        cards = []
        for cid, cls in self.sem.classes.items():
            cards.append(TypeCard(
                cid=cid,
                label=str(cls.get("label", cid)),
                aliases=[str(a) for a in (cls.get("aliases") or [])],
                description=str(cls.get("description", "")),
                properties=[str(p) for p in (cls.get("properties") or [])],
                glossary_terms=gloss.get(cid, []),
                aggregate=self.sem.aggregate_for_type(cid) or "",
                context=self.sem.context_for_type(cid) or "",
            ))
        # 派生类型同样参与（kind="derived"）
        for dt in self.sem.raw.get("derived_types", []) or []:
            did = dt.get("id")
            if did:
                cards.append(TypeCard(
                    cid=did, label=str(dt.get("label", did)),
                    aliases=[str(a) for a in (dt.get("aliases") or [])],
                    description=str(dt.get("description", "")),
                    properties=[],
                    glossary_terms=[], aggregate="", context="",
                ))
        return cards

    @property
    def cards(self) -> list[TypeCard]:
        if self._cards is None:
            self._cards = self._build_cards()
        return self._cards

    @property
    def bm25(self) -> _BM25Index:
        if self._bm25 is None:
            self._bm25 = _BM25Index(self.cards)
        return self._bm25

    @property
    def exact_index(self) -> dict[str, list[str]]:
        """归一化词 → 类型 id 列表（label / alias / glossary term / id）。"""
        if self._exact_index is not None:
            return self._exact_index
        index: dict[str, list[str]] = defaultdict(list)
        for card in self.cards:
            keys = [card.id, card.label, *card.aliases]
            for g in self.sem.glossary:
                if str(g.get("type_ref") or "").split(".")[0] == card.id:
                    keys.append(str(g.get("term")))
                    keys.extend(str(s) for s in (g.get("synonyms") or []))
            for k in keys:
                nk = _normalize(k)
                if nk and card.id not in index[nk]:
                    index[nk].append(card.id)
        self._exact_index = dict(index)
        return self._exact_index

    @property
    def neighbors(self) -> dict[str, list[str]]:
        """类型 → 1 跳邻居（含边界链接两端）。"""
        if self._neighbors is not None:
            return self._neighbors
        nb: dict[str, set[str]] = defaultdict(set)
        for rel in self.sem.relations.values():
            dom, rng = rel.get("domain"), rel.get("range")
            if dom in self.sem.classes:
                nb[dom].add(rng)
            if rng in self.sem.classes:
                nb[rng].add(dom)
        self._neighbors = {k: sorted(v & set(self.sem.classes))
                           for k, v in nb.items()}
        return self._neighbors

    def _load_vectors(self) -> dict | None:
        if self._vectors_loaded:
            return self._vectors
        self._vectors_loaded = True
        self._vectors = None
        if os.path.exists(self.vectors_path):
            try:
                with open(self.vectors_path, encoding="utf-8") as f:
                    self._vectors = json.load(f)
            except (OSError, json.JSONDecodeError):
                self._vectors = None
        return self._vectors

    def _vector_channel(self, question: str) -> list[str]:
        """C3：向量通道。索引缺失 / 指纹过期 → 空列表（调用方标注降级）。"""
        vec = self._load_vectors()
        if not vec:
            return []
        if vec.get("fingerprint") != _content_fingerprint(self.sem):
            return []
        vectors: dict[str, list[float]] = vec.get("vectors") or {}
        if not vectors:
            return []
        qvec = _embed_remote(question, self.vectors_path, vec.get("model"))
        if not qvec:
            return []
        scored = []
        for tid, v in vectors.items():
            if tid not in self.sem.classes and tid not in {
                d.get("id") for d in self.sem.raw.get("derived_types", []) or []
            }:
                continue
            scored.append((_cosine(qvec, v), tid))
        scored.sort(reverse=True)
        return [tid for _, tid in scored[:20]]

    # ── 通道实现 ──────────────────────────────────────────────
    def _exact_channel(self, question: str) -> list[str]:
        """C1：精确通道（按匹配强度分层排序）。

        层 1：token 与键（id/label/alias/术语/synonym）完全相等（最强）；
        层 2：键（≥2 字）是问句的子串（「计价规则」⊆「计价规则是哪条」）；
        层 3：token（≥2 字）是键的子串（「保险」⊆「保险单」，词法亲和）；
        层 4：术语表 colloquial 口语覆盖——完整子串，或 2-gram 覆盖 ≥60%
              （「货到哪了」完整命中；「多少钱」对「多少钱一车」仅 50% 不命中）。
        """
        qnorm = _normalize(question)
        qtokens = tokenize(question)
        token_set = set(qtokens) | {_normalize(t) for t in qtokens}
        tier1: list[str] = []
        tier2: list[str] = []
        tier3: list[str] = []
        for key, type_ids in self.exact_index.items():
            if not key:
                continue
            if key in token_set:
                tier1.extend(type_ids)
                continue
            if len(key) >= 2 and key in qnorm:
                tier2.extend(type_ids)
                continue
            for tok in token_set:
                if len(tok) >= 2 and len(key) >= 2 and tok in key:
                    tier3.extend(type_ids)
                    break
        tier4: list[str] = []
        q_bigrams = _bigrams(qnorm)
        for g in self.sem.glossary:
            base = str(g.get("type_ref") or "").split(".")[0]
            if base not in self.sem.classes or base in tier1 or base in tier2:
                continue
            for phrase in (str(c) for c in (g.get("colloquial") or [])):
                pn = _normalize(phrase)
                if len(pn) >= 2 and pn in qnorm:
                    # 完整口语短语命中是强信号（与 tier2 同级）
                    tier2.append(base)
                    break
                pb = _bigrams(pn)
                if pb and len(q_bigrams & pb) / len(pb) >= 0.6:
                    tier4.append(base)
                    break
        hits = list(dict.fromkeys(tier1 + tier2 + tier3 + tier4))
        tiers: dict[str, int] = {}
        for tier_no, tier in enumerate((tier1, tier2, tier3, tier4)):
            for tid in tier:
                tiers.setdefault(tid, tier_no)
        return hits, tiers

    def _history_channel(self, question: str) -> list[str]:
        """C5：与历史问句词重叠相似度 > 阈值 → 回传其 effective_types。"""
        if not os.path.exists(self.history_path):
            return []
        qtokens = set(tokenize(question))
        if not qtokens:
            return []
        best: list[tuple[float, list[str]]] = []
        try:
            with open(self.history_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("effective_types") is None:
                        continue
                    ht = set(tokenize(str(rec.get("question") or "")))
                    if not ht:
                        continue
                    sim = len(qtokens & ht) / len(qtokens | ht)
                    if sim >= HISTORY_SIM_MIN:
                        best.append((sim, list(rec["effective_types"])))
        except OSError:
            return []
        best.sort(key=lambda x: -x[0])
        hits: list[str] = []
        for _, types in best[:5]:
            for t in types:
                if t in self.sem.classes and t not in hits:
                    hits.append(t)
        return hits

    def _graph_channel(self, seed_types: list[str]) -> list[str]:
        """C4：命中类型 1 跳扩展（衰减体现在融合排名靠后）。

        排序：跨聚合邻居在前（边界链接闭包是 graph 的核心职责），
        同聚合兄弟在后（聚合注入层会全量提供，这里只补充召回席位）。
        """
        seed_aggs = {self.sem.aggregate_for_type(t) for t in seed_types}
        cross: list[str] = []
        intra: list[str] = []
        for tid in seed_types:
            for nb in self.neighbors.get(tid, []):
                if nb in cross or nb in intra:
                    continue
                if self.sem.aggregate_for_type(nb) in seed_aggs:
                    intra.append(nb)
                else:
                    cross.append(nb)
        return cross + intra

    # ── 主入口 ────────────────────────────────────────────────
    def search(self, question: str, top_k: int = 8,
               group_ids: list[str] | None = None,
               context_ids: list[str] | None = None,
               threshold: float | None = None) -> dict:
        question = str(question or "").strip()
        threshold = DEFAULT_THRESHOLD if threshold is None else float(threshold)

        allowed_types: set[str] | None = None
        if group_ids:
            allowed = set()
            for gid in group_ids:
                if gid not in self.sem.object_type_group_map:
                    raise BusinessReject(
                        f"未定义的对象类型组: {gid}", "SCHEMA_ERROR",
                        {"unknown": gid, "allowed": sorted(self.sem.object_type_group_map)})
                allowed.update(self.sem.object_type_group_map[gid].get("types") or [])
            allowed_types = allowed
        if context_ids:
            ctx_types: set[str] = set()
            for ctx in context_ids:
                for aid in (self.sem.contexts.get(ctx) or {}).get("aggregates") or []:
                    ctx_types.update(self.sem.aggregates.get(aid, {}).get("types") or [])
                if ctx not in self.sem.contexts:
                    raise BusinessReject(
                        f"未定义的上下文: {ctx}", "SCHEMA_ERROR",
                        {"unknown": ctx, "allowed": sorted(self.sem.contexts)})
            allowed_types = ctx_types if allowed_types is None else (allowed_types & ctx_types)

        channels: dict[str, list[str]] = {}
        exact_hits, exact_tiers = self._exact_channel(question)
        channels["exact"] = exact_hits
        lexical = self.bm25.search(question)
        channels["lexical"] = [tid for tid, _ in lexical]
        vector_hits = self._vector_channel(question)
        channels["vector"] = vector_hits
        # 图通道以 exact+lexical（+vector）的头部为种子
        graph_seed = list(dict.fromkeys(
            channels["exact"][:4] + channels["lexical"][:6] + channels["vector"][:4]))
        channels["graph"] = self._graph_channel(graph_seed)
        channels["history"] = self._history_channel(question)

        degraded: list[str] = []
        if not channels["vector"]:
            degraded.append("vector")
        if not HAS_JIEBA:
            degraded.append("tokenizer(jieba)")

        if allowed_types is not None:
            channels = {
                name: [t for t in hits if t in allowed_types]
                for name, hits in channels.items()
            }

        # 加权 RRF 融合：每通道一个排名表（exact/history 强，graph 泛化衰减）。
        # exact 通道内部按匹配强度分层（tier 0-3），rank 加偏移拉开层间差距：
        # tier1 完全相等 / tier2 整词子串 / tier3 词法亲和 / tier4 口语覆盖。
        TIER_OFFSETS = (0, 20, 60, 120)
        rrf: dict[str, float] = defaultdict(float)
        matched: dict[str, set[str]] = defaultdict(set)
        active = {name for name, hits in channels.items() if hits}
        for name, hits in channels.items():
            if not hits:
                continue
            weight = CHANNEL_WEIGHT.get(name, 1.0)
            for rank, tid in enumerate(hits, start=1):
                if name == "exact":
                    rank += TIER_OFFSETS[exact_tiers.get(tid, 3)]
                rrf[tid] += weight / (RRF_K + rank)
                matched[tid].add(name)
        # 归一化：全部通道都排第 1 的加权理论上限
        ceiling = sum(
            CHANNEL_WEIGHT.get(name, 1.0) for name in active) / (RRF_K + 1) if active else 1.0
        ranked = sorted(
            ((tid, score / ceiling) for tid, score in rrf.items()),
            key=lambda x: (-x[1], x[0]),
        )
        # 阈值过滤 + top_k（聚合配额：每聚合最多 6 席，防止单聚合垄断，
        # 为跨聚合类型保留席位——问句常跨上下文）
        types = []
        agg_seats: dict[str, int] = defaultdict(int)
        for tid, score in ranked:
            if score < threshold:
                continue
            agg = self.sem.aggregate_for_type(tid) or ""
            if agg and agg_seats[agg] >= 6:
                continue
            if agg:
                agg_seats[agg] += 1
            cls = self.sem.classes.get(tid)
            dt = None
            if cls is None:
                dt = next((d for d in self.sem.raw.get("derived_types", []) or []
                           if d.get("id") == tid), None)
            entry = {
                "kind": "class" if cls else "derived",
                "id": tid,
                "label": (cls or dt or {}).get("label", tid),
                "rrf_score": round(score, 4),
                "matched_channels": sorted(matched.get(tid, [])),
                "aggregate": self.sem.aggregate_for_type(tid) or "",
                "context": self.sem.context_for_type(tid) or "",
                "groups": self.sem.group_ids_for_types([tid]),
            }
            types.append(entry)
            if len(types) >= max(1, min(int(top_k), 20)):
                break

        zero_hit = not types
        return {
            "question": question,
            "group_ids": sorted(group_ids or []),
            "context_ids": sorted(context_ids or []),
            "channels_used": sorted(active),
            "degraded": degraded,
            "threshold": threshold,
            "zero_hit": zero_hit,
            "types": types,
            "message": (
                "零命中：全部通道无符合阈值的类型（宁缺毋滥）。" if zero_hit else
                f"命中 {len(types)} 个类型（{len(active)} 通道融合）。"
            ),
        }

    def record_history(self, question: str, effective_types: list[str]) -> None:
        """推理结束后回写「问句 → 实际使用类型」（C5 通道数据源）。"""
        question = str(question or "").strip()
        if not question:
            return
        os.makedirs(os.path.dirname(self.history_path), exist_ok=True)
        try:
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "question": question,
                    "effective_types": [t for t in effective_types or []
                                        if t in self.sem.classes],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass


class _BM25Index:
    """字段加权 BM25（k1=1.5, b=0.75），纯 Python，500 类规模瞬时。"""

    def __init__(self, cards: list[TypeCard]):
        self.cards = {c.id: c for c in cards}
        self.doc_len: dict[str, dict[str, int]] = {}
        self.total_len = 0
        self.df: dict[str, int] = defaultdict(int)
        for card in cards:
            lengths = {}
            for name, tokens in card.field_tokens.items():
                lengths[name] = len(tokens)
                self.total_len += len(tokens)
                for tok in set(tokens):
                    self.df[tok] += 1
            self.doc_len[card.id] = lengths
        self.n_docs = max(1, len(cards))
        self.avg_len = max(1.0, self.total_len / self.n_docs)

    def _idf(self, token: str) -> float:
        df = self.df.get(token, 0)
        return math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)

    def search(self, question: str, top: int = 24) -> list[tuple[str, float]]:
        tokens = [t for t in tokenize(question) if t not in STOPWORDS]
        if not tokens:
            return []
        scores: dict[str, float] = defaultdict(float)
        for cid, card in self.cards.items():
            dl = sum(len(t) for t in card.field_tokens.values())
            norm = BM25_K1 * (1 - BM25_B + BM25_B * dl / self.avg_len)
            for name, tokens_field in card.field_tokens.items():
                boost = FIELD_BOOST.get(name, 1.0)
                # 只统计与该字段有交集的 token（停用词/无交集词不产生 tf 噪声）
                matched_tokens = [tok for tok in set(tokens) if tok in tokens_field]
                if not matched_tokens:
                    continue
                tf = sum(tokens_field.count(tok) for tok in matched_tokens)
                idf_sum = sum(self._idf(tok) for tok in matched_tokens)
                scores[cid] += boost * idf_sum * (
                    tf * (BM25_K1 + 1) / (tf + norm))
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:top]


def _content_fingerprint(sem) -> str:
    """类型卡内容指纹：本体变更即失效向量索引。"""
    import hashlib
    parts = []
    for cid in sorted(sem.classes):
        cls = sem.classes[cid]
        parts.append(f"{cid}|{cls.get('label')}|{','.join(cls.get('aliases') or [])}"
                     f"|{cls.get('description', '')[:120]}")
    for g in sorted(sem.glossary, key=lambda x: x.get("term", "")):
        parts.append(f"g|{g.get('term')}|{g.get('type_ref')}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _embed_remote(text: str, vectors_path: str, model: str | None) -> list[float] | None:
    """在线查询向量（仅当离线索引存在时使用；端点未配置返回 None）。"""
    base = os.environ.get("OPENAI_EMBEDDINGS_API_BASE")
    key = os.environ.get("OPENAI_EMBEDDINGS_API_KEY")
    if not base or not key:
        return None
    try:
        import urllib.request
        body = json.dumps({"model": model or "embedding", "input": [text]}).encode()
        req = urllib.request.Request(
            base.rstrip("/") + "/embeddings", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return list(data["data"][0]["embedding"])
    except Exception:
        return None
