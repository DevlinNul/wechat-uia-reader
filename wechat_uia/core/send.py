"""发送动作与独立确认。

不选窗口、不扫描控件树；由 API 层传入快照和已填好的输入框。
"""

import time

from .errors import ClickTargetBlockedError, SendMessageError
from .input import clear_input, read_input_text
from .ui_actions import (
    click_control,
    focus_input,
    press_alt_s,
    press_enter,
)
from .uia_utils import iter_descendants

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


def message_items(message_list) -> list:
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


def message_marker(message_list) -> dict:
    """记录发送前的消息列表状态，用来区分“新消息”和“旧消息”。"""
    items = message_items(message_list)
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


def message_seen(message_list, before: dict, text: str) -> bool:
    """消息列表里是否出现了一条新的、内容匹配的消息。"""
    items = message_items(message_list)
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

    after = message_marker(message_list)
    return (
        after["count"] != before["count"]
        or after["last"] != before["last"]
    )


def confirm_message_seen(
    message_list, before: dict, text: str
) -> tuple[bool, str]:
    """在限定时间内轮询消息列表，确认新消息出现。"""
    if message_list is None:
        return False, "未找到消息列表，无法独立确认"

    deadline = time.monotonic() + MESSAGE_CONFIRM_TIMEOUT
    while True:
        if message_seen(message_list, before, text):
            return True, "消息列表已出现新消息"
        if time.monotonic() >= deadline:
            break
        time.sleep(SEND_POLL_INTERVAL)
    return (
        False,
        f"消息列表在 {MESSAGE_CONFIRM_TIMEOUT:g} 秒内未出现新消息",
    )


def input_is_empty(input_field) -> bool | None:
    """返回输入框是否为空；读取失败时返回 None。"""
    value = read_input_text(input_field)
    if value is None:
        return None
    return not value.strip()


def wait_input_empty(input_field, timeout: float) -> bool:
    """轮询输入框，直到 strip() 后为空。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if input_is_empty(input_field) is True:
            return True
        time.sleep(SEND_POLL_INTERVAL)
    return False


def assert_input_text(
    input_field, expected: str, hwnd: int, phase: str
) -> None:
    """提交前确认输入框里仍然是准备发送的文本。"""
    actual = read_input_text(input_field)
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


def assert_sendable_window(snapshot) -> None:
    """聊天记录窗口只读，不能发送；不属于微信的句柄也拒绝。"""
    kind = getattr(snapshot, "window_kind", None) or "unknown"
    if kind == "history":
        raise SendMessageError(
            "当前是聊天记录窗口，它是只读的，不能发送消息"
        )
    if kind == "foreign":
        raise SendMessageError("当前句柄不属于微信，不能发送消息")


def submit(
    method: str,
    snapshot,
    input_field,
    expected_text: str,
) -> bool:
    """执行一种发送方式；无法执行时返回 False，已执行时返回 True。"""
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
        assert_input_text(
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
        assert_input_text(
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
        assert_input_text(
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
