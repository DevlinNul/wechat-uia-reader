"""向当前微信会话发送一条文本消息（原子操作）。

API 层只负责编排：

1. 选择目标窗口；
2. 显式唤醒/激活；
3. 扫描一次；
4. 校验会话并写入输入框；
5. 提交发送；
6. 在目标窗口的消息列表里独立确认。

具体输入框读写、键盘发送、消息确认分别在
:mod:`wechat_uia.core.fill`、:mod:`wechat_uia.core.input`、
:mod:`wechat_uia.core.send` 中实现。
"""

import argparse
import json
import sys
import time

import win32gui

from ..cli_common import positive_int, runtime_id
from ..core.classify import classify_snapshot
from ..core.errors import (
    SendMessageError,
    WeChatUIAError,
)
from ..core.fill import fill_snapshot
from ..core.get_hwnd import (
    minimize_overlapping_windows,
    restore_minimized_windows,
)
from ..core.input import clear_input
from ..core.input_text import normalize_text
from ..core.send import (
    MESSAGE_CONFIRM_TIMEOUT,
    SEND_CONFIRM_TIMEOUT,
    SEND_METHODS,
    SEND_ORDER,
    assert_sendable_window,
    confirm_message_seen,
    message_marker,
    submit,
    wait_input_empty,
)
from ..core.session import assert_session, normalize_expected_session
from ..core.snapshot import scan_window
from ..core.surface import resolve_surface, selection_metadata
from ..core.ui_actions import activate, ensure_ready, restore_foreground
from ..core.uia_utils import enable_dpi_awareness

def send_message(
    text: str,
    chat_name: str | None = None,
    hwnd: int | None = None,
    *,
    session: dict | None = None,
    send_method: str = "alt_s",
    clear_occluders: bool = False,
    wake_minimized: bool = False,
    wake_tray: bool = False,
    strict: bool = False,
) -> dict:
    """发送一条文本消息，返回发送结果。

    会话匹配规则与 fill_input 一致：可传 chat_name，也可传完整 session。
    输入框必须为空；text 必须非空。

    ``strict=True`` 时，如果多个窗口匹配目标会话，直接报错，不自动选择。
    """
    if send_method not in SEND_METHODS:
        raise ValueError(
            f"send_method 必须是 {', '.join(SEND_METHODS)} 之一"
        )

    normalized = normalize_text(text)
    if not normalized.strip():
        raise ValueError("text 不能为空")

    expected = normalize_expected_session(chat_name, session)

    resolved = resolve_surface(
        hwnd=hwnd,
        chat_name=expected["chat_name"],
        session=session,
        strict=strict,
    )
    ensure_ready(
        resolved.hwnd,
        window_role=resolved.role,
        wake_minimized=wake_minimized,
        wake_tray=wake_tray,
    )

    original_foreground = win32gui.GetForegroundWindow()
    blocked: list[int] = []

    try:
        if clear_occluders:
            blocked = minimize_overlapping_windows(resolved.hwnd)
            if blocked:
                time.sleep(0.2)

        if not activate(resolved.hwnd):
            raise SendMessageError("无法将微信窗口置于前台，未发送")

        # 激活后窗口位置/DPI/会话都可能变化，重新扫描一次
        snapshot = scan_window(resolved.hwnd)
        classification = classify_snapshot(snapshot)
        assert_session(
            expected,
            snapshot,
            "激活后会话已切换，未发送",
            classification=classification,
        )
        assert_sendable_window(snapshot)

        input_field = snapshot.input_field
        if input_field is None:
            raise SendMessageError(
                f"未找到聊天输入框：{expected['chat_name']!r}"
            )

        fill_result = fill_snapshot(
            snapshot,
            expected,
            normalized,
            classification=classification,
        )

        before = message_marker(snapshot.message_list)
        used_method = None
        for method in SEND_ORDER[send_method]:
            if submit(method, snapshot, input_field, normalized):
                used_method = method
                break

        if used_method is None:
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(
                f"没有可用的发送方式（{send_method}），未发送"
            )

        if not wait_input_empty(input_field, SEND_CONFIRM_TIMEOUT):
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(
                f"使用 {used_method} 发送后输入框未清空；"
                "消息可能已发送，请先人工确认，不要直接重试"
            )

        message_seen, confirm_reason = confirm_message_seen(
            snapshot.message_list, before, normalized
        )
    finally:
        if blocked:
            restore_minimized_windows(blocked)
        restore_foreground(original_foreground)

    return {
        "hwnd": snapshot.hwnd,
        "chat_name": expected["chat_name"],
        "text": normalized,
        "fill_method": fill_result["method"],
        "send_method": used_method,
        "session_check": fill_result.get("session_check", "strong"),
        "window_kind": getattr(snapshot, "window_kind", None),
        "window_role": getattr(snapshot, "window_kind", None),
        "window_automation_id": getattr(snapshot, "window_automation_id", None),
        "layout": getattr(snapshot, "layout", None),
        **selection_metadata(resolved),
        "verified": bool(message_seen),
        "confirm": {
            "input_cleared": True,
            "message_seen": bool(message_seen),
            "message_timeout": MESSAGE_CONFIRM_TIMEOUT,
            "reason": confirm_reason,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="向当前微信会话发送一条文本消息"
    )
    parser.add_argument(
        "--chat",
        required=True,
        help="目标会话名；必须与当前会话一致",
    )
    parser.add_argument(
        "--text",
        required=True,
        help="要发送的文本（支持中文和换行）",
    )
    parser.add_argument(
        "--hwnd",
        type=lambda value: int(value, 0),
        help="可选：目标窗口句柄；支持十进制或 0x 十六进制",
    )
    parser.add_argument(
        "--send-method",
        choices=SEND_METHODS,
        default="alt_s",
        help="发送方式：alt_s（默认，Alt+S 快捷键）、enter、button、auto",
    )
    parser.add_argument(
        "--clear-occluders",
        action="store_true",
        help="发送前最小化挡住微信的其他窗口，发送后再还原（默认关闭）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="多个窗口匹配目标会话时直接报错，不自动选择",
    )
    parser.add_argument(
        "--chat-type",
        help="可选：期望的会话类型，如 private / group / official",
    )
    parser.add_argument(
        "--member-count",
        type=positive_int,
        help="可选：期望的群成员数",
    )
    parser.add_argument(
        "--session-automation-id",
        help="可选：期望的选中会话项 AutomationId",
    )
    parser.add_argument(
        "--session-runtime-id",
        type=runtime_id,
        help="可选：期望的选中会话项 RuntimeId，逗号分隔的整数",
    )
    parser.add_argument(
        "--window-automation-id",
        help="可选：独立窗口根 AutomationId，用于精确锁定窗口",
    )
    parser.add_argument(
        "--wake-minimized",
        action="store_true",
        help="允许自动还原最小化的微信窗口（默认不允许）",
    )
    parser.add_argument(
        "--wake-tray",
        action="store_true",
        help="允许通过微信原生托盘回调唤起隐藏到托盘的主窗口（默认不允许）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    enable_dpi_awareness()

    session = {"chat_name": args.chat}
    if args.chat_type:
        session["type"] = args.chat_type
    if args.member_count is not None:
        session["member_count"] = args.member_count
    if args.session_automation_id:
        session["session_automation_id"] = args.session_automation_id
    if args.session_runtime_id is not None:
        session["session_runtime_id"] = args.session_runtime_id
    if args.window_automation_id:
        session["window_automation_id"] = args.window_automation_id

    try:
        result = send_message(
            args.text,
            session=session,
            hwnd=args.hwnd,
            send_method=args.send_method,
            clear_occluders=args.clear_occluders,
            wake_minimized=args.wake_minimized,
            wake_tray=args.wake_tray,
            strict=args.strict,
        )
    except WeChatUIAError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["verified"]:
            print("发送结果 : 成功（已独立确认）")
        else:
            print("发送结果 : 已提交（消息列表未确认）")
        print(f"会话     : {result['chat_name']!r}")
        print(f"发送方式 : {result['send_method']}")
        print(f"填充方式 : {result['fill_method']}")
        print(f"文本长度 : {len(result['text'])}")
        if result.get("session_check") == "name_only":
            print(
                "会话校验 : 只校验当前会话名"
                "（会话列表不可见，未验证重名）"
            )
        else:
            print("会话校验 : 通过")
        print(f"独立确认 : {result['confirm']['reason']}")
        if not result["verified"]:
            print(
                "注意: 消息可能已经发出，但消息列表没能确认；"
                "请不要直接重试，先用微信人工确认。",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
