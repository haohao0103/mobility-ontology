"""P0-3 并发承压冒烟：多线程混合读写，验证 WAL + 事务感知回退后 0 死锁、结果一致。"""
from __future__ import annotations

import sys
import threading
import time
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freight.engine import FreightSystem  # noqa: E402

N_READERS = 8
N_READS = 50
N_WRITERS = 2
N_WRITES = 20

errors: list[str] = []


def reader(sys_: FreightSystem, tid: int) -> None:
    for i in range(N_READS):
        try:
            row = sys_.get_object("waybill:WB20261105001")
            if row is None:
                errors.append(f"reader{tid}:{i} 运单对象丢失")
            _ = sys_.stats()
        except Exception as e:  # noqa: BLE001
            errors.append(f"reader{tid}:{i} {type(e).__name__}: {e}")


def writer(sys_: FreightSystem, tid: int, lock: threading.Lock) -> None:
    for i in range(N_WRITES):
        try:
            with lock:  # 写路径串行（与 api 层 guard 语义一致）
                sys_.facts.store.add_event("waybill:WB20261105001", "concurrency_smoke",
                                           "2026-11-19T21:10:00Z", "smoke", {"tid": tid, "i": i})
                sys_.conn.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(f"writer{tid}:{i} {type(e).__name__}: {e}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "smoke.db")
        sys_ = FreightSystem(db, fresh=True, seed=True)
        lock = threading.Lock()
        threads = [threading.Thread(target=reader, args=(sys_, t)) for t in range(N_READERS)]
        threads += [threading.Thread(target=writer, args=(sys_, t, lock)) for t in range(N_WRITERS)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        alive = [t.name for t in threads if t.is_alive()]
        dt = time.time() - t0
        events = sys_.facts.scalar("SELECT COUNT(*) FROM events WHERE event_type='concurrency_smoke'")
        expected = N_WRITERS * N_WRITES

    print(f"读线程 {N_READERS}x{N_READS} + 写线程 {N_WRITERS}x{N_WRITES}，耗时 {dt:.2f}s")
    print(f"死锁线程: {alive or '无'}")
    print(f"事件写入: {events}/{expected}")
    print(f"错误: {len(errors)}")
    for e in errors[:10]:
        print("  -", e)
    ok = (not alive) and events == expected and not errors
    print("结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
