"""第三方实现的起步骨架 —— **只有接口，没有实现逻辑**。

用法
----
1. 复制这个文件到 `implementations/<你的名字>/impl.py`；
2. 按 TODO 逐条实现；
3. 跑：
     python runner.py --impl implementations/<你的名字>/impl.py --audit-impl
     python runner.py --impl implementations/<你的名字>/impl.py

设计意图
--------
这份骨架**刻意不含任何实现逻辑**（没有公式、没有费率、没有状态机），
这样你的实现才是**独立的第三方实现**，而不是从我们的参考实现改出来的。
评测器还会做静态隔离检查（不得读 manifest/期望值、不得 import 作者私有包）。

环境变量
--------
BENCH_DATA_DIR  数据目录（内容见 data/）
BENCH_IMPL_DIR  你的实现目录（配置副本请放这里，评测器会复制它到工作区）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BENCH_DATA_DIR", HERE.parent.parent / "data"))
IMPL_DIR = Path(os.environ.get("BENCH_IMPL_DIR", HERE))


class MyImpl:
    """把你的实现写在这里。

    路线建议：**属性图 + 服务代码里的业务规则**——图负责"谁跟谁有关系"，
    规则负责"怎么算、谁能做"，两者都放在服务端代码/配置里。
    这正是这类系统的真实形态。
    """

    name = "third_party: 请改成你的实现名"

    def __init__(self):
        # TODO 1) 读数据
        #   facts = json.loads((DATA_DIR / "facts.json").read_text(encoding="utf-8"))
        #   注意：**只读 facts.json**（业务数据）。不要读 manifest.json（期望值）。
        self.facts = {}
        # TODO 2) 建图：节点 + 边
        #   提示：费用、运单是节点；运单产生费用、费用按规则计价，是边。
        #   费用与运单的归属关系请从 facts.json 里读（每条费用带 waybill_id）。
        self.nodes = {}
        self.edges = []
        # TODO 3) 读你的规则来源（配置/代码）
        #   建议把你的费率参数放在本目录下的一份配置文件里（评测器会复制它到工作区，
        #   你的 apply_change 改的就是它），这样"改配置"是一个真实的动作。
        self.config_path = IMPL_DIR / "config.json"
        self.config = {}

    # ---------------------------------------------------------------- 用例接口
    def q1_diff(self, waybill_id: str = "WB20261105001") -> dict:
        """差异归因。

        返回需包含：quoted_total / actual_total / diff_total /
        waiting_expected / waiting_over / explain / audit
        """
        # TODO 4) 实现：报价合计、实收合计、差额；等候费"按规则应计"与"超收"
        raise NotImplementedError("请实现 q1_diff")

    def q2_waiting(self, waybill_id: str = "WB20261105001") -> dict:
        """等候费按规则应收多少 / 超收多少。

        返回需包含：waiting_expected / waiting_over / explain / audit
        """
        # TODO 5) 实现：按你系统里的规则算应计值
        raise NotImplementedError("请实现 q2_waiting")

    def q3_roadblock(self, amount: float = 40.0,
                     waybill_id: str = "WB20261105001") -> dict:
        """减免预检：会被哪一步挡住。

        返回需包含：blocked / reason_code / explain / audit
        """
        # TODO 6) 实现：状态前置、角色、金额上限（按你系统的语义）
        raise NotImplementedError("请实现 q3_roadblock")

    # ---------------------------------------------------------------- 变更接口
    def apply_change(self, card: dict) -> dict:
        """应用变更卡。

        card 形如 {"id": "C1", "change": "...", "expect": {...}}
        返回需包含：changed_files(list) / code_files(int) / restart_required(bool)
        —— 真实项目里改配置/代码后**通常需要重启**才生效，请如实声明。
        """
        # TODO 7) 实现：把变更落到你的配置/代码上，并如实报告改动面与是否需重启
        raise NotImplementedError("请实现 apply_change")


# 评测器按这个约定发现实现
IMPL = MyImpl
