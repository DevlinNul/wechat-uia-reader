"""向当前微信会话发送一条文本消息（原子操作）。

流程：
1. 校验 text 非空；
2. 校验当前会话与给定 session / chat_name 一致；
3. 校验输入框为空，并用 fill_input() 写入；
4. 写入后再次校验会话没有切换；
5. 默认激活窗口、聚焦输入框并按 Enter；
6. 轮询输入框是否清空，确认发送。

默认使用 Enter；可通过 send_method="button" 点击发送按钮，或
send_method="auto" 在 Enter 无法执行时回退按钮。
"""

import argparse
import json
import sys
import time

import win32gui

from errors import ChatMismatchError, SendMessageError, WeChatUIAError
from fill_input import (
    activate_window,
    clear_input,
    click_control,
    fill_input,
    focus_input,
    normalize_text,
    press_enter,
)
from get_chat_type import (
    normalize_expected_session,
    session_identity_from_snapshot,
    session_mismatches,
)
from snapshot import (
    read_text_pattern,
    read_value_pattern,
    scan_window,
)
from uia_utils import enable_dpi_awareness

SEND_CONFIRM_TIMEOUT = 2.0
SEND_POLL_INTERVAL = 0.05
SEND_METHODS = ("enter", "button", "auto")


def _input_is_empty(input_field) -> bool | None:
    """返回输入框是否为空；读取失败时返回 None。"""
    for reader in (read_value_pattern, read_text_pattern):
        try:
            value = reader(input_field)
        except Exception:
            continue
        if value is not None:
            return not value.strip()
    return None


def _wait_input_empty(input_field, timeout: float) -> bool:
    """轮询输入框，直到 strip() 后为空。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _input_is_empty(input_field) is True:
            return True
        time.sleep(SEND_POLL_INTERVAL)
    return False


def _assert_session(expected: dict, snapshot, phase: str) -> None:
    """校验快照仍然是期望会话；不一致时抛 ChatMismatchError。"""
    actual = session_identity_from_snapshot(snapshot)
    mismatches = session_mismatches(expected, actual)
    if mismatches:
        raise ChatMismatchError(f"{phase}：" + "；".join(mismatches))


def send_message(
    text: str,
    chat_name: str | None = None,
    hwnd: int | None = None,
    *,
    session: dict | None = None,
    send_method: str = "enter",
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> dict:
    """发送一条文本消息，返回发送结果。

    会话匹配规则与 fill_input 一致：可传 chat_name，也可传完整 session。
    输入框必须为空；text 必须非空。发送前会再次校验会话，避免切换到
    其他会话后误发。

    send_method:
    - enter（默认）：聚焦输入框后按 Enter，不移动鼠标；
    - button：激活窗口后点击发送按钮；
    - auto：优先 Enter，无法聚焦/执行时回退点击发送按钮。
    """
    if send_method not in SEND_METHODS:
        raise ValueError(
            f"send_method 必须是 {', '.join(SEND_METHODS)} 之一"
        )

    normalized = normalize_text(text)
    if not normalized.strip():
        raise ValueError("text 不能为空")

    expected = normalize_expected_session(chat_name, session)

    fill_result = fill_input(
        normalized,
        session=expected,
        hwnd=hwnd,
        wake_minimized=wake_minimized,
        wake_tray=wake_tray,
    )

    # fill_input 可能耗时；发送前重新确认仍然是同一个会话
    snapshot = scan_window(
        hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
    )
    _assert_session(expected, snapshot, "填写后会话已切换，未发送")

    input_field = snapshot.input_field
    if input_field is None:
        raise SendMessageError(
            f"未找到聊天输入框：{expected['chat_name']!r}"
        )

    used_method = None
    original_foreground = win32gui.GetForegroundWindow()

    try:
        if send_method in ("enter", "auto"):
            if activate_window(snapshot.hwnd):
                # 激活后窗口位置/DPI 可能变化，重新扫描输入框
                time.sleep(0.2)
                fresh = scan_window(
                    hwnd,
                    wake_minimized=wake_minimized,
                    wake_tray=wake_tray,
                )
                _assert_session(expected, fresh, "激活后会话已切换，未发送")
                input_field = fresh.input_field or input_field
                snapshot = fresh
                if focus_input(input_field):
                    try:
                        press_enter()
                    except Exception as exc:
                        clear_input(snapshot.hwnd, input_field)
                        raise SendMessageError(
                            f"发送 Enter 失败：{exc}"
                        ) from exc
                    used_method = "enter"
                elif send_method == "enter":
                    clear_input(snapshot.hwnd, input_field)
                    raise SendMessageError(
                        "无法让聊天输入框获得焦点，未发送"
                    )
            elif send_method == "enter":
                clear_input(snapshot.hwnd, input_field)
                raise SendMessageError("无法将微信置于前台，未发送")

        if used_method is None and send_method in ("button", "auto"):
            if not activate_window(snapshot.hwnd):
                clear_input(snapshot.hwnd, input_field)
                raise SendMessageError("无法将微信置于前台，未发送")
            # 激活后窗口位置/DPI 可能变化，必须重新扫描按钮位置
            time.sleep(0.2)
            fresh = scan_window(
                hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
            )
            _assert_session(expected, fresh, "激活后会话已切换，未发送")
            input_field = fresh.input_field or input_field
            button = getattr(fresh, "send_button", None)
            if button is not None and click_control(button):
                used_method = "button"
                snapshot = fresh
            else:
                clear_input(fresh.hwnd, input_field)
                raise SendMessageError(
                    "未找到可用的发送按钮，或点击发送按钮失败，未发送"
                )

        if not _wait_input_empty(input_field, SEND_CONFIRM_TIMEOUT):
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(
                f"使用 {used_method} 发送后输入框未清空，无法确认消息已发送"
            )
    finally:
        if original_foreground:
            try:
                win32gui.SetForegroundWindow(original_foreground)
            except Exception:
                pass

    return {
        "hwnd": snapshot.hwnd,
        "chat_name": expected["chat_name"],
        "text": normalized,
        "fill_method": fill_result["method"],
        "send_method": used_method,
        "verified": True,
    }


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def _runtime_id(value: str) -> tuple[int, ...]:
    try:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError
        return tuple(int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "RuntimeId 必须是逗号分隔的整数"
        ) from exc


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
        "--send-method",
        choices=SEND_METHODS,
        default="enter",
        help="发送方式：enter（默认，优先键盘）、button、auto",
    )
    parser.add_argument(
        "--chat-type",
        help="可选：期望的会话类型，如 private / group / official",
    )
    parser.add_argument(
        "--member-count",
        type=_positive_int,
        help="可选：期望的群成员数",
    )
    parser.add_argument(
        "--session-automation-id",
        help="可选：期望的选中会话项 AutomationId",
    )
    parser.add_argument(
        "--session-runtime-id",
        type=_runtime_id,
        help="可选：期望的选中会话项 RuntimeId，逗号分隔的整数",
    )
    parser.add_argument(
        "--wake-minimized",
        action="store_true",
        help="允许自动还原最小化的微信窗口（默认不允许）",
    )
    parser.add_argument(
        "--wake-tray",
        action="store_true",
        help="允许通过微信原生托盘回调唤起隐藏到托盘的微信（默认不允许）",
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

    try:
        result = send_message(
            args.text,
            session=session,
            send_method=args.send_method,
            wake_minimized=args.wake_minimized,
            wake_tray=args.wake_tray,
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
        print(f"会话     : {result['chat_name']!r}")
        print("发送结果 : 成功")
        print(f"发送方式 : {result['send_method']}")
        print(f"填充方式 : {result['fill_method']}")
        print(f"文本长度 : {len(result['text'])}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
