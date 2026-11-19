"""沙箱演示辅助：死循环函数（绝不能在主进程调用）。"""


def spin_forever(**kwargs):
    i = 0
    while True:
        i += 1
