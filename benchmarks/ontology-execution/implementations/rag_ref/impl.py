"""参考实现 C：**RAG 路线**（检索 + 大模型生成）。

这是本基准的第三条路线，也是最容易被高估的一条 —— 所以这个实现刻意**不给它**
任何本体能力：

- 它**只读** facts.json（业务数据），**不读** ontology.json（本体声明）；
- 它把检索到的片段拼成上下文，交给大模型回答；
- 它**没有**公式、没有状态机、没有动作前置、没有来源归属 —— 这些在 RAG 的世界里
  压根不存在（除非恰好写进了被检索到的文档里）。

因此它能答的只有**数据里直接有的**（报价、实收、差额）；
凡是"答案不在数据里"的（应计值、动作会被谁挡、变更后结论怎么翻转），
它要么答不出，要么给出无法复核的推断 —— 这正是基准要量出来的东西。

**没有配置大模型时，本实现明确报「不可用」，不伪造结论。**
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BENCH_DATA_DIR", HERE.parent.parent / "data"))

DEFAULT_BASE = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.6-pro"


class RAGNotAvailable(RuntimeError):
    """没有可用的大模型端点 —— 明确报不可用，不伪造答案。"""


class _Client:
    """极简 OpenAI 兼容客户端（只用标准库，与本基准的隔离要求一致）。"""

    def __init__(self):
        self.base = (os.environ.get("OPENAI_CHAT_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.key = os.environ.get("OPENAI_CHAT_API_KEY", "")
        self.model = os.environ.get("OPENAI_CHAT_LANGUAGE_MODEL") or DEFAULT_MODEL
        self.timeout = float(os.environ.get("OPENAI_CHAT_TIMEOUT", "120"))
        if not self.key:
            raise RAGNotAvailable(
                "未配置 OPENAI_CHAT_API_KEY —— RAG 路线没有大模型就无法工作；"
                "本实现不会用模板伪造答案")

    def chat(self, prompt: str) -> str:
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                   "temperature": 0.0, "max_tokens": 800, "stream": False}
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise RAGNotAvailable(f"大模型网关 HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RAGNotAvailable(f"大模型网关不可用: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise RAGNotAvailable("大模型返回结构异常") from exc


class RagImpl:
    name = "rag_ref: RAG 路线（检索 + 大模型）"

    def __init__(self):
        self.facts = json.loads((DATA_DIR / "facts.json").read_text(encoding="utf-8"))
        self._client = _Client()      # 没有 key 就直接抛 RAGNotAvailable

    # ---- 检索：把相关片段拼成上下文（模拟 RAG 的 top-k 片段）----
    def _retrieve(self, waybill_id: str, subject: str, limit: int = 8) -> str:
        wb = next(w for w in self.facts.get("waybills") or [self.facts["waybill"]]
                  if w["waybill_id"] == waybill_id)
        lines = [f"运单 {wb['waybill_id']}：预估 {wb.get('estimated_fee')}、"
                 f"实收 {wb.get('actual_fee')}、等候 {wb.get('waiting_minutes')} 分钟、"
                 f"车型 {wb.get('vehicle_type')}、状态 {wb.get('status')}"]
        fees = [f for f in self.facts["fees"] if f.get("waybill_id") == waybill_id]
        for f in fees:
            lines.append(f"费用 {f['fee_id']}（{f['fee_type']}）：报价 {f['quoted_amount']}、"
                         f"实收 {f['actual_amount']}、状态 {f['status']}、"
                         f"来源系统 {f['source_system']}、定价规则 {f.get('pricing_rule_id')}"
                         f" {f.get('pricing_rule_version')}")
        items = [i for i in self.facts.get("quote_items") or []]
        for i in items:
            lines.append(f"报价明细 {i['quote_item_id']}（{i['fee_type']}）：{i['amount']}")
        for e in self.facts.get("evidence") or []:
            lines.append(f"证据 {e['evidence_id']}：{e['evidence_type']} / "
                         f"来源 {e['source_system']} / {e.get('summary', '')}")
        return "\n".join(lines[:limit * 4])

    # ---- 让大模型在检索片段里作答 ----
    def _ask(self, waybill_id: str, question: str, schema_hint: str) -> str:
        context = self._retrieve(waybill_id, question)
        prompt = (
            "你是一个货运结算助手。下面是从业务系统检索到的原始记录片段。\n"
            f"【记录片段】\n{context}\n\n"
            f"【问题】{question}\n\n"
            "请只依据上面的片段回答。若片段里没有依据，就明确说「数据不足，无法判断」，"
            "不要推测。\n"
            f"最后用一行 JSON 给出可抽取的结果，字段：{schema_hint}\n"
            '例如：{"waiting_expected": 12.0, "answer": "..."}\n'
            "（只输出那一行 JSON，不要 markdown、表格或额外说明）"
        )
        return self._client.chat(prompt)

    @staticmethod
    def _number_by_label(text: str, label: str):
        """中文标签兜底抽取：`报价合计**：380.0 + ... = **480.0** → 480.0

        大模型不保证输出 JSON（这次就输出了 markdown 表格）。
        真实 RAG 系统同样必须解析 LLM 的自由文本，所以这里做兜底，
        而不是让 RAG 因格式问题白丢它本该答对的题（那才是稻草人）。
        """
        for line in text.splitlines():
            if label not in line:
                continue
            # 该行里若出现等号，取**等号后**的数（"380.0 + 60.0 + ... = 480.0" → 480.0）
            if "=" in line:
                tail = line.split("=")[-1]
                nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", tail)
                if nums:
                    return float(nums[0].replace(",", ""))
            # 没有等号：取该行最后一个数（"报价合计 480"这种写法）
            nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", line)
            if nums:
                return float(nums[-1].replace(",", ""))
        return None

    @staticmethod
    def _number(text: str, key: str):
        """从模型输出里抽一个数值；抽不到就返回 None（让判定器判 FAIL，而不是猜一个）。

        模型常把 JSON 放进 markdown 代码块、或用中文冒号，因此这里放宽：
        引号可有可无、冒号可半角可全角、允许数字带逗号千分位。
        """
        m = re.search(rf'["\u201c]?{re.escape(key)}["\u201d]?\s*[:：]\s*'
                      rf'(-?\d[\d,]*(?:\.\d+)?)', text)
        return float(m.group(1).replace(",", "")) if m else None

    @staticmethod
    def _bool(text: str, key: str):
        m = re.search(rf'["\u201c]?{re.escape(key)}["\u201d]?\s*[:：]\s*'
                      r'(true|false|是|否)', text, re.IGNORECASE)
        if not m:
            return None
        return m.group(1).lower() in ("true", "是")

    @staticmethod
    def _text(text: str, key: str) -> str:
        m = re.search(rf'["\u201c]?{re.escape(key)}["\u201d]?\s*[:：]\s*'
                      r'["\u201c]([^"\u201d]*)["\u201d]', text)
        return m.group(1) if m else ""

    # ---- 用例 ----
    def q1_diff(self, waybill_id: str = "WB20261105001") -> dict:
        # **一次调用**问完（评测要跑很多次，调用次数直接决定基准跑多久）：
        # 报价合计、实收合计、差额、以及各费用项的报价/实收/差额。
        # 注意：这里**不问"按规则应计"** —— 应计值不在数据里。
        raw = self._ask(
            waybill_id,
            "这单的报价合计、实收合计、差额是多少？请按费用类型列出每项的报价、实收、差额。"
            "只用片段里的数字。",
            "quoted_total, actual_total, diff_total, waiting_expected, waiting_over, answer")
        detail = raw
        return {
            "quoted_total": self._number(raw, "quoted_total")
                           or self._number_by_label(raw, "报价合计"),
            "actual_total": self._number(raw, "actual_total")
                            or self._number_by_label(raw, "实收合计"),
            "diff_total": self._number(raw, "diff_total")
                          or self._number_by_label(raw, "差额"),
            # 应计值：RAG 路线的数据里没有公式，只能给出片段里的某个数（通常是 0 或实收）
            "waiting_expected": self._number(detail, "waiting_expected"),
            "waiting_over": self._number(detail, "waiting_over"),
            "explain": (self._text(raw, "answer") + " | " + self._text(detail, "answer"))[:400],
            "audit": {},      # RAG 路线没有版本/规则ID/生效期/证据 checksum
        }

    def q2_waiting(self, waybill_id: str = "WB20261105001") -> dict:
        raw = self._ask(waybill_id,
                        "这笔等候费**按规则**应该收多少？超收多少？"
                        "（若片段里没有计价规则或应计金额，请回答数据不足）",
                        "waiting_expected, waiting_over, answer")
        return {
            "waiting_expected": self._number(raw, "waiting_expected")
                                or self._number_by_label(raw, "规则应计"),
            "waiting_over": self._number(raw, "waiting_over")
                            or self._number_by_label(raw, "超收"),
            "explain": self._text(raw, "answer")[:400],
            "audit": {},
        }

    def q3_roadblock(self, amount: float = 40.0,
                     waybill_id: str = "WB20261105001") -> dict:
        raw = self._ask(waybill_id,
                        f"对这条费用发起减免 {amount} 元，会被哪一步挡住？"
                        "（是否有金额上限、状态限制、审批要求？片段里没有就回答数据不足）",
                        "blocked, reason_code, answer")
        return {
            "blocked": self._bool(raw, "blocked"),
            "reason_code": self._text(raw, "reason_code"),
            "explain": self._text(raw, "answer")[:400],
            "audit": {},
        }

    # ---- 变更：RAG 路线没有"规则"可改，改动只影响检索到的数据 ----
    def apply_change(self, card: dict) -> dict:
        """RAG 路线没有声明可改：它只能"重新检索"。

        因此这里**不改任何文件**（changed_files=[]、code_files=0），
        也不需要重启——但也**不会**因为业务参数变了而改变结论，
        因为它从来就不是按规则算的。这正是基准要量出来的事实。
        """
        return {"changed_files": [], "code_files": 0, "restart_required": False}


# runner 通过这个约定发现实现
IMPL = RagImpl
