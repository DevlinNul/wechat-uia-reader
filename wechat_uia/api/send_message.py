"""向当前微信会话发送一条文本消息（原子操作）。

流程：
1. 校验 text 非空；
2. 校验当前会话与给定 session / chat_name 一致；
3. 校验输入框为空，并用 fill_input() 写入；
4. 写入后再次校验会话没有切换；
5. 激活窗口、重新扫描，确认输入框里仍然是准备发送的文本；
6. 按顺序尝试发送方式（默认 Alt+S，其次 Enter，最后点击发送按钮）；
7. 轮询输入框是否清空，再在消息列表里确认出现了这条新消息。

默认使用 Alt+S：它是键盘操作，不依赖微信的“Enter 发送 / Ctrl+Enter 发送”
设置，也不需要移动鼠标。send_method="enter" 使用 Enter；send_method="button"
点击发送按钮；send_method="auto" 按 Alt+S -> Enter -> 按钮的顺序尝试。

这里说的“尝试”只发生在真正提交之前。一旦某个发送动作已经执行，即使
没有确认成功，也不会再换一种方式重发，避免同一条消息发两遍。

verified=True 表示“输入框已清空，并且消息列表里看到了这条新消息”。
verified=False 表示“已经提交，但没能独立确认”，不代表一定没发出去，
调用方不要仅凭这个字段直接重试。
"""

import argparse
import json
import sys
import time

import win32gui

from ..cli_common import positive_int, runtime_id
from ..core.errors import (
    ChatMismatchError,
    ClickTargetBlockedError,
    SendMessageError,
    WeChatUIAError,
)
from .fill_input import (
    clear_input,
    click_control,
    fill_input,
    focus_input,
    normalize_text,
    press_alt_s,
    press_enter,
)
from ..core.get_chat_type import (
    normalize_expected_session,
    session_identity_from_snapshot,
    session_mismatches,
)
from ..core.get_hwnd import (
    activate_window,
    minimize_overlapping_windows,
    restore_minimized_windows,
    window_kind,
)
from ..core.snapshot import read_control_text, scan_window
from ..core.uia_utils import enable_dpi_awareness, iter_descendants

SEND_CONFIRM_TIMEOUT = 2.0
MESSAGE_CONFIRM_TIMEOUT = 3.0
SEND_POLL_INTERVAL = 0.05

SEND_METHODS = ("alt_s", "enter", "button", "auto")
SEND_ORDER = {
    "alt_s": ("alt_s",),
    "enter": ("enter",),
    "button": ("button",),
    "auto": ("alt_s", "enter", "button"),
}


def _read_input_text(input_field) -> str | None:
    """读取输入框当前文本；读取顺序由 core.snapshot 统一实现。"""
    return read_control_text(input_field)


def _input_is_empty(input_field) -> bool | None:
    """返回输入框是否为空；读取失败时返回 None。"""
    value = _read_input_text(input_field)
    if value is None:
        return None
    return not value.strip()


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


def _snapshot_window_kind(snapshot) -> str:
    """读取快照里的窗口角色；老快照没有该字段时再查一次。"""
    kind = getattr(snapshot, "window_kind", None)
    if kind:
        return kind
    try:
        return window_kind(snapshot.hwnd)
    except Exception:
        return "unknown"


def _assert_sendable_window(snapshot) -> None:
    """聊天记录窗口只读，不能发送；不属于微信的句柄也拒绝。"""
    kind = _snapshot_window_kind(snapshot)
    if kind == "history":
        raise SendMessageError(
            "当前是聊天记录窗口，它是只读的，不能发送消息"
        )
    if kind == "foreign":
        raise SendMessageError("当前句柄不属于微信，不能发送消息")


def _assert_input_text(
    input_field, expected: str, hwnd: int, phase: str
) -> None:
    """提交前确认输入框里仍然是准备发送的文本。

    填写完成到真正发送之间，输入框可能被清空、被输入法改写，或者被用户
    手动改动。这里再读一次；只要内容变了就不发送，并尽量清掉脏内容。
    """
    actual = _read_input_text(input_field)
    if actual is None:
        clear_input(hwnd, input_field)
        raise SendMessageError(
            f"{phase}：无法读取输入框内容，未发送"
        )
    if actual == expected:
        return

    clear_input(hwnd, input_field)
    raise SendMessageError(
        f"{phase}：输入框内容已变化，未发送"
        f"（期望 {expected!r}，实际 {actual!r}）"
    )


def _message_items(message_list) -> list:
    """返回消息列表里的消息项；读不到时返回空列表。"""
    if message_list is None:
        return []
    try:
        return [
            item
            for item in message_list.GetChildren()
            if item.ControlTypeName == "ListItemControl"
        ]
    except Exception:
        return []


def _message_marker(message_list) -> dict:
    """记录发送前的消息列表状态，用来区分“新消息”和“旧消息”。

    只记录条数和最后一项的身份，不读取整页消息，开销很小。
    """
    items = _message_items(message_list)
    if not items:
        return {"count": 0, "last": None}

    last = items[-1]
    try:
        runtime_id = tuple(last.GetRuntimeId())
    except Exception:
        runtime_id = None
    return {
        "count": len(items),
        "last": (runtime_id, (last.Name or "").strip(), last.ClassName),
    }


def _message_text(item) -> str:
    """把一条消息项的标题和子文本拼成一个字符串。"""
    text = (item.Name or "").strip()
    parts = []
    try:
        for el in iter_descendants(item):
            if el.ControlTypeName == "TextControl" and el.Name.strip():
                parts.append(el.Name.strip())
    except Exception:
        pass
    return "\n".join([text] + parts)


def _message_seen(message_list, before: dict, text: str) -> bool:
    """消息列表里是否出现了一条新的、内容匹配的消息。

    不能只看最后一条的文本：同一条消息发两次时文本完全一样。所以还要
    确认列表条数或最后一项的身份发生了变化。
    """
    items = _message_items(message_list)
    if not items:
        return False

    # 时间分隔项、撤回提示等偶尔会排在最后，所以看最后三项。
    matched = False
    for item in reversed(items[-3:]):
        combined = _message_text(item)
        if text in combined:
            matched = True
            break
    if not matched:
        return False

    after = _message_marker(message_list)
    return (
        after["count"] != before["count"]
        or after["last"] != before["last"]
    )


def _confirm_message_seen(
    message_list, before: dict, text: str
) -> tuple[bool, str]:
    """在限定时间内轮询消息列表，确认新消息出现。"""
    if message_list is None:
        return False, "未找到消息列表，无法独立确认"

    deadline = time.monotonic() + MESSAGE_CONFIRM_TIMEOUT
    while True:
        if _message_seen(message_list, before, text):
            return True, "消息列表已出现新消息"
        if time.monotonic() >= deadline:
            break
        time.sleep(SEND_POLL_INTERVAL)
    return (
        False,
        f"消息列表在 {MESSAGE_CONFIRM_TIMEOUT:g} 秒内未出现新消息",
    )


def _submit(
    method: str,
    snapshot,
    input_field,
    expected_text: str,
) -> bool:
    """执行一种发送方式；无法执行时返回 False，已执行时返回 True。

    返回 False 只代表“还没提交”，调用方可以安全地尝试下一种方式。
    已经提交但结果不确定的情况一律抛异常，不允许继续换方式重发。
    """
    def focus_or_clear() -> bool:
        """聚焦输入框；如果点击焦点被其他窗口挡住，清掉草稿并报错。"""
        try:
            return focus_input(input_field, snapshot.hwnd)
        except ClickTargetBlockedError:
            try:
                clear_input(snapshot.hwnd, input_field)
            except Exception:
                pass
            raise

    if method == "alt_s":
        if not focus_or_clear():
            return False
        _assert_input_text(
            input_field, expected_text, snapshot.hwnd, "Alt+S 发送前"
        )
        try:
            press_alt_s()
        except Exception as exc:
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(f"发送 Alt+S 失败：{exc}") from exc
        return True

    if method == "enter":
        if not focus_or_clear():
            return False
        _assert_input_text(
            input_field, expected_text, snapshot.hwnd, "Enter 发送前"
        )
        try:
            press_enter()
        except Exception as exc:
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(f"发送 Enter 失败：{exc}") from exc
        return True

    if method == "button":
        button = getattr(snapshot, "send_button", None)
        if button is None:
            return False
        _assert_input_text(
            input_field, expected_text, snapshot.hwnd, "点击发送前"
        )
        try:
            return click_control(button, snapshot.hwnd)
        except ClickTargetBlockedError:
            try:
                clear_input(snapshot.hwnd, input_field)
            except Exception:
                pass
            raise

    return False


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
) -> dict:
    """发送一条文本消息，返回发送结果。

    会话匹配规则与 fill_input 一致：可传 chat_name，也可传完整 session。
    输入框必须为空；text 必须非空。发送前会再次校验会话，避免切换到
    其他会话后误发。

    send_method:
    - alt_s（默认）：Alt+S 快捷键，键盘发送，不依赖 Enter 设置；
    - enter：聚焦输入框后按 Enter；
    - button：激活窗口后点击“发送”按钮；
    - auto：按 alt_s -> enter -> button 的顺序尝试。

    clear_occluders=True 时，发送前会把挡住微信的其他窗口最小化，发送后
    再还原。默认关闭。
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
    _assert_sendable_window(snapshot)

    input_field = snapshot.input_field
    if input_field is None:
        raise SendMessageError(
            f"未找到聊天输入框：{expected['chat_name']!r}"
        )

    used_method = None
    message_seen = False
    confirm_reason = "未执行发送确认"
    blocked: list[int] = []
    original_foreground = win32gui.GetForegroundWindow()

    try:
        if clear_occluders:
            blocked = minimize_overlapping_windows(snapshot.hwnd)
            if blocked:
                time.sleep(0.2)

        if not activate_window(snapshot.hwnd):
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError("无法将微信置于前台，未发送")

        # 激活后窗口位置/DPI 可能变化，必须重新扫描
        time.sleep(0.2)
        fresh = scan_window(
            hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
        )
        _assert_session(expected, fresh, "激活后会话已切换，未发送")
        _assert_sendable_window(fresh)

        input_field = fresh.input_field or input_field
        snapshot = fresh
        _assert_input_text(
            input_field, normalized, snapshot.hwnd, "提交前"
        )
        before = _message_marker(snapshot.message_list)

        for method in SEND_ORDER[send_method]:
            if _submit(method, snapshot, input_field, normalized):
                used_method = method
                break

        if used_method is None:
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(
                f"没有可用的发送方式（{send_method}），未发送"
            )

        if not _wait_input_empty(input_field, SEND_CONFIRM_TIMEOUT):
            clear_input(snapshot.hwnd, input_field)
            raise SendMessageError(
                f"使用 {used_method} 发送后输入框未清空；"
                "消息可能已发送，请先人工确认，不要直接重试"
            )

        message_seen, confirm_reason = _confirm_message_seen(
            snapshot.message_list, before, normalized
        )
    finally:
        if blocked:
            restore_minimized_windows(blocked)
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
        "session_check": fill_result.get("session_check", "strong"),
        "window_kind": _snapshot_window_kind(snapshot),
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
            clear_occluders=args.clear_occluders,
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
