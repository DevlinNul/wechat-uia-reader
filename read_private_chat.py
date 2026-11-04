"""读取当前私聊会话信息，并按需向上滚动获取更多聊天记录。

前置条件：
- 微信主窗口正常（默认要求未最小化、未隐藏到托盘、未移出屏幕）
- 微信 UI Automation 控件树可用
- 当前位于私聊会话

本脚本只读：不发送消息、不点击消息、不切换会话。
滚动使用鼠标滚轮，默认不恢复滚动位置；需要时加 --restore。
默认不主动改变微信窗口状态；需要时可用 --wake-minimized /
--wake-tray 允许自动还原最小化窗口或从托盘唤起。

用法:
    python read_private_chat.py
    python read_private_chat.py --limit 500 --max-scrolls 40
    python read_private_chat.py --wake-tray
    python read_private_chat.py --no-scroll
    python read_private_chat.py --json

作为库使用:
    from read_private_chat import read_private_chat
    data = read_private_chat(limit=20, max_scrolls=60)
"""

import argparse
import json
import sys

from errors import (
    MessageListUnavailableError,
    NotPrivateChatError,
    UIAUnavailableError,
    WeChatUIAError,
)
from get_chat import chat_info_from_snapshot
from get_chat_type import PRIVATE, classify_snapshot
from message_history import collect_messages
from snapshot import scan_window
from uia_utils import enable_dpi_awareness


def read_private_chat(
    hwnd: int | None = None,
    *,
    limit: int = 20,
    max_scrolls: int = 60,
    scroll_step: int = 1,
    settle: float = 0.0,
    timeout: float = 20.0,
    restore: bool = False,
    wake_minimized: bool = False,
    wake_tray: bool = False,
    scroll: bool = True,
) -> dict:
    """读取当前私聊会话信息与消息记录。

    窗口不可用时抛 WindowUnavailableError；UIA 不可用时抛
    UIAUnavailableError；当前不是私聊时抛 NotPrivateChatError。

    wake_minimized / wake_tray 默认关闭；显式开启后分别允许自动还原
    最小化窗口、通过微信原生托盘回调唤起托盘隐藏窗口。
    """
    snapshot = scan_window(
        hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
    )

    if not snapshot.uia_available:
        raise UIAUnavailableError(
            "微信 UI Automation 控件树不可用（可能未物化，或微信版本不匹配）"
        )

    chat = classify_snapshot(snapshot)
    if chat["type"] != PRIVATE:
        raise NotPrivateChatError(
            f"当前不是私聊会话：{chat.get('chat_name')!r}"
            f"（{chat.get('label')} / {chat.get('type')}）"
        )

    chat_info = chat_info_from_snapshot(snapshot)
    if chat_info is None:
        raise MessageListUnavailableError("未找到私聊聊天区，无法读取消息")

    history = collect_messages(
        snapshot,
        limit=limit,
        max_scrolls=max_scrolls,
        scroll_step=scroll_step,
        settle=settle,
        timeout=timeout,
        restore=restore,
        scroll=scroll,
    )

    # 滚动期间用户可能切换会话；直接读标题控件名称，避免再次全树扫描
    try:
        current_name = (
            snapshot.chat_name_element.Name.strip()
            if snapshot.chat_name_element is not None
            else None
        )
        if current_name != snapshot.chat_name:
            history["scroll"]["warnings"].append(
                f"滚动期间会话已切换：{snapshot.chat_name!r} -> {current_name!r}"
            )
    except Exception as exc:
        history["scroll"]["warnings"].append(f"滚动后校验会话失败：{exc}")

    return {
        "hwnd": snapshot.hwnd,
        "window_state": snapshot.window_state,
        "chat_name": chat["chat_name"],
        "type": chat["type"],
        "label": chat["label"],
        "member_count": chat["member_count"],
        "can_send": chat["can_send"],
        "chat_info": chat_info,
        "count": history["count"],
        "messages": history["messages"],
        "scroll": history["scroll"],
    }


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("不能小于 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="读取当前私聊会话信息与聊天记录（可选向上滚动）"
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=20,
        help="最多返回多少条消息（默认 20）",
    )
    parser.add_argument(
        "--max-scrolls",
        type=_non_negative_int,
        default=60,
        help="最多滚动多少次（默认 60；0 表示不滚动）",
    )
    parser.add_argument(
        "--scroll-step",
        type=_positive_int,
        default=1,
        help="每次滚动的鼠标滚轮格数（默认 1）",
    )
    parser.add_argument(
        "--settle",
        type=_non_negative_float,
        default=0.0,
        help="滚动变化并稳定后额外等待的秒数（默认 0，使用轮询等待）",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=20.0,
        help="滚动总超时（秒，默认 20）",
    )
    parser.add_argument(
        "--wake-minimized",
        action="store_true",
        help="允许自动还原最小化的微信窗口（默认不允许）",
    )
    parser.add_argument(
        "--wake-tray",
        action="store_true",
        help=(
            "允许通过微信原生托盘回调唤起隐藏到托盘的微信"
            "（默认不允许；不使用 ShowWindow）"
        ),
    )
    restore_group = parser.add_mutually_exclusive_group()
    restore_group.add_argument(
        "--restore",
        dest="restore",
        action="store_true",
        help="读取后恢复原始滚动位置",
    )
    restore_group.add_argument(
        "--no-restore",
        dest="restore",
        action="store_false",
        help="不恢复原始滚动位置（默认）",
    )
    parser.set_defaults(restore=False)
    parser.add_argument(
        "--no-scroll",
        action="store_true",
        help="只读当前视口，不滚动",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出完整结果",
    )
    return parser


def print_result(data: dict) -> None:
    scroll = data["scroll"]
    reached_top = scroll["reached_top"]
    restored = scroll["restored"]
    print(f"会话     : {data['chat_name']!r} ({data['label']} / {data['type']})")
    print(f"窗口状态 : {data['window_state']}")
    print(f"消息条数 : {data['count']}")
    print(
        "滚动     : "
        f"{'开启' if scroll['enabled'] else '关闭'}"
        f" | 步数 {scroll['steps']}"
        f" | 到达顶部: {'未知' if reached_top is None else ('是' if reached_top else '否')}"
        f" | 截断: {'是' if scroll['truncated'] else '否'}"
        f" | 恢复位置: {'跳过' if restored is None else ('是' if restored else '否')}"
    )
    for message in data["messages"]:
        print(f"[{message['index']:>3}] {message['kind']:<6} {message['text']!r}")
    for warning in scroll["warnings"]:
        print(f"警告: {warning}", file=sys.stderr)


def main() -> int:
    args = build_parser().parse_args()
    enable_dpi_awareness()

    try:
        data = read_private_chat(
            limit=args.limit,
            max_scrolls=args.max_scrolls,
            scroll_step=args.scroll_step,
            settle=args.settle,
            timeout=args.timeout,
            restore=args.restore,
            wake_minimized=args.wake_minimized,
            wake_tray=args.wake_tray,
            scroll=not args.no_scroll,
        )
    except WeChatUIAError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_result(data)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
