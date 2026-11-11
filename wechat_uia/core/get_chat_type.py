"""会话类型判定与身份提取的兼容入口。

实现已拆分到：

- :mod:`wechat_uia.core.classify`：类型与可发送性判定；
- :mod:`wechat_uia.core.session`：身份提取与匹配。

本模块保留原有导入路径和命令行用法，不复制实现。
"""

import argparse
import json
import statistics
import sys
import time

from ..cli_common import add_surface_arguments, positive_int
from .chat_type_watch import get_chat_type_fast
from .classify import (
    BROADCAST,
    FEED,
    GROUP,
    LIST,
    NOTICE,
    OFFICIAL,
    PRIVATE,
    TYPE_LABELS,
    UNKNOWN,
    VIEW_TYPES,
    attach_window_info,
    classify_snapshot,
    collect_class_names,
    iter_leaf_text_controls,
    parse_member_count,
)
from .errors import WindowUnavailableError
from .session import (
    assert_session,
    normalize_expected_session,
    session_identity_from_snapshot,
    session_mismatches,
)
from .snapshot import scan_window
from .surface import resolve_surface
from .uia_utils import enable_dpi_awareness


def get_session_identity(
    hwnd: int | None = None, chat_name: str | None = None
) -> dict:
    """返回当前会话的完整身份信息，供 fill_input 等下游做一致性校验。"""
    resolved = resolve_surface(hwnd=hwnd, chat_name=chat_name)
    return session_identity_from_snapshot(scan_window(resolved.hwnd))


def get_current_chat_type(
    hwnd: int | None = None, chat_name: str | None = None
) -> dict:
    """对外接口：返回当前会话/视图类型信息。

    窗口不可用时抛 WindowUnavailableError。
    """
    resolved = resolve_surface(hwnd=hwnd, chat_name=chat_name)
    snapshot = scan_window(resolved.hwnd)
    result = classify_snapshot(snapshot)
    return attach_window_info(
        result,
        hwnd=snapshot.hwnd,
        window_state=snapshot.window_state,
        window_kind=getattr(snapshot, "window_kind", None),
        window_automation_id=getattr(snapshot, "window_automation_id", None),
        layout=getattr(snapshot, "layout", None),
    )


def print_result(result: dict) -> None:
    """打印类型、可发送性与判定依据。"""
    title = "当前视图" if result["type"] in VIEW_TYPES else "当前会话"
    print(f"{title} : {result['chat_name']!r}")
    print(f"类型     : {result['label']} ({result['type']})")
    print(f"可发送   : {'是' if result.get('can_send') else '否'}")
    if result.get("member_count") is not None:
        print(f"成员数   : {result['member_count']}")
    window_role = result.get("window_role") or "unknown"
    window_id = result.get("window_automation_id")
    print(
        f"窗口     : {window_role}"
        + (f" ({window_id})" if window_id else "")
    )
    print(f"判定依据 : {result['reason']}")


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="判定当前会话/视图类型与身份"
    )
    parser.add_argument(
        "--identity",
        action="store_true",
        help="输出完整会话身份 JSON",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="用快速轮询路径读取一次，不走完整扫描",
    )
    parser.add_argument(
        "--watch",
        type=positive_int,
        metavar="次数",
        help="连续轮询指定次数，打印每次耗时",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        metavar="秒",
        help="--watch 的轮询间隔，默认 0.2 秒",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略缓存，每次都重新读取",
    )
    add_surface_arguments(parser)
    return parser


def run_watch(args) -> int:
    """连续轮询并打印耗时；会话名或类型变化时标注出来。"""
    interval = max(0.0, args.interval)
    elapsed = []
    previous = None
    for index in range(args.watch):
        if index and interval:
            time.sleep(interval)
        start = time.perf_counter()
        result = get_chat_type_fast(
            hwnd=args.hwnd,
            chat_name=args.chat,
            force=args.force,
        )
        cost = (time.perf_counter() - start) * 1000
        elapsed.append(cost)
        name = result.get("chat_name") or result.get("label")
        changed = ""
        if previous is not None and (
            previous.get("type") != result.get("type")
            or previous.get("chat_name") != result.get("chat_name")
        ):
            changed = "  <- 变化"
        previous = result
        print(
            f"#{index + 1:<3} {cost:7.1f} ms  "
            f"{result['label']}  {name}{changed}"
        )
    ordered = sorted(elapsed)
    print(
        f"共 {len(ordered)} 次："
        f"最小 {ordered[0]:.1f} ms  "
        f"中位 {statistics.median(ordered):.1f} ms  "
        f"最大 {ordered[-1]:.1f} ms"
    )
    return 0


def main() -> int:
    """命令行入口：判定当前会话/视图类型。"""
    args = build_parser().parse_args()
    enable_dpi_awareness()

    if args.identity:
        try:
            identity = get_session_identity(
                hwnd=args.hwnd, chat_name=args.chat
            )
        except WindowUnavailableError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(identity, ensure_ascii=False, indent=2))
        return 0

    try:
        if args.watch:
            return run_watch(args)
        if args.fast:
            result = get_chat_type_fast(
                hwnd=args.hwnd,
                chat_name=args.chat,
                force=args.force,
            )
        else:
            result = get_current_chat_type(
                hwnd=args.hwnd, chat_name=args.chat
            )
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_result(result)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
