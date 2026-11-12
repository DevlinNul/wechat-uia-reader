"""只读性能基准：把关键阶段的耗时打印成一张表。

用法（仓库根目录）：

    python bench/bench_core.py
    python bench/bench_core.py --rounds 30
    python bench/bench_core.py --hwnd 0x123456

不做任何写操作：不点击、不聚焦、不发送、不唤醒。窗口最小化时会直接
报错退出。数字会随电源状态和机器负载变化，所以每次都会打印当前电源
状态和控件节点数，方便纵向比较。
"""

import argparse
import ctypes
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wechat_uia.core.chat_type_watch import get_chat_type_fast, reset_cache
from wechat_uia.core.classify import classify_snapshot
from wechat_uia.core.errors import WeChatUIAError
from wechat_uia.core.get_chat_type import get_current_chat_type
from wechat_uia.core.send import message_items, message_marker, message_seen
from wechat_uia.core.snapshot import get_root_control, scan_window
from wechat_uia.core.surface import list_surfaces, resolve_surface
from wechat_uia.core.uia_utils import enable_dpi_awareness, iter_descendants


class SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_byte),
        ("BatteryFlag", ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("Reserved1", ctypes.c_byte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def ac_status() -> str:
    """返回当前供电状态；读不到时返回“未知”。"""
    status = SystemPowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return "未知"
    return {0: "电池", 1: "插电", 255: "未知"}.get(
        status.ACLineStatus, "未知"
    )


def measure(name, fn, rounds):
    """预热后测量中位数；只做只读调用。"""
    for _ in range(3):
        fn()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    ordered = sorted(samples)
    print(
        f"{name:<34} med={statistics.median(ordered):8.1f}  "
        f"min={ordered[0]:8.1f}  max={ordered[-1]:8.1f}  (ms)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读性能基准")
    parser.add_argument(
        "--rounds", type=int, default=15, help="每项测量轮数，默认 15"
    )
    parser.add_argument(
        "--hwnd",
        type=lambda value: int(value, 0),
        help="可选：目标窗口句柄；默认自动选择",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    enable_dpi_awareness()

    try:
        resolved = resolve_surface(hwnd=args.hwnd)
        hwnd = resolved.hwnd
        snapshot = scan_window(hwnd)
    except WeChatUIAError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    nodes = 1 + sum(1 for _ in iter_descendants(get_root_control(hwnd)))
    power = ac_status()
    print(f"供电     : {power}")
    print(f"窗口     : {snapshot.window_kind} / {snapshot.layout}")
    print(f"控件节点 : {nodes}")
    print(f"会话名   : {'有' if snapshot.chat_name else '无'}")
    if power == "电池":
        print("警告     : 当前是电池供电，耗时会明显偏高，不要和插电数据直接比较")
    print()

    message_list = snapshot.message_list
    before = message_marker(message_list)

    measure("resolve_surface（默认）", resolve_surface, args.rounds)
    measure(
        "resolve_surface(hwnd=...)",
        lambda: resolve_surface(hwnd=hwnd),
        args.rounds,
    )
    measure("scan_window", lambda: scan_window(hwnd), args.rounds)
    measure(
        "classify_snapshot",
        lambda: classify_snapshot(snapshot),
        args.rounds,
    )
    measure(
        "get_current_chat_type(hwnd=)",
        lambda: get_current_chat_type(hwnd=hwnd),
        args.rounds,
    )
    measure(
        "快速路径（强制刷新）",
        lambda: get_chat_type_fast(hwnd=hwnd, force=True),
        args.rounds,
    )
    reset_cache()
    measure(
        "快速路径（缓存命中）",
        lambda: get_chat_type_fast(hwnd=hwnd),
        args.rounds,
    )
    measure(
        "message_items",
        lambda: message_items(message_list),
        args.rounds,
    )
    measure(
        "message_seen（一次尝试）",
        lambda: message_seen(message_list, before, "不会匹配的内容"),
        args.rounds,
    )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
