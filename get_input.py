"""读取微信当前会话的输入框内容并判断是否为空。

- 对不可发送消息的会话（公众号/服务号、服务通知、列表页等）抛出 CannotSendError
- 对可发送消息的会话（私聊/群聊）读取输入框文本，并判断是否为空

本脚本只读输入框内容，不发送消息、不切换会话、不主动唤起窗口。

用法:
    python get_input.py

作为库使用:
    from get_hwnd import WindowUnavailableError
    from get_input import CannotSendError, get_input_state

    state = get_input_state()   # 窗口不可用抛 WindowUnavailableError；不可发送抛 CannotSendError
    state["text"], state["is_empty"]
"""

import sys

import uiautomation as auto

from get_chat import enable_dpi_awareness, iter_descendants
from get_chat_type import classify_chat
from get_hwnd import (
    WindowUnavailableError,
    describe_window_state,
    require_wechat_main_window,
    require_window_ready,
)

INPUT_AUTOMATION_ID = "chat_input_field"
INPUT_CLASS = "mmui::ChatInputField"


class CannotSendError(RuntimeError):
    """当前会话不能发送消息，或找不到可用的输入框。"""


def find_input_field(window):
    """定位聊天输入框控件。"""
    fallback = None
    for el in iter_descendants(window):
        if el.AutomationId == INPUT_AUTOMATION_ID:
            return el
        if fallback is None and el.ClassName == INPUT_CLASS:
            fallback = el
    return fallback


def read_input_text(input_field) -> str:
    """读取输入框文本（优先 ValuePattern，其次 LegacyIAccessible）。"""
    for getter in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
        method = getattr(input_field, getter, None)
        if method is None:
            continue
        try:
            pattern = method()
        except Exception:
            continue
        if pattern is None:
            continue
        value = getattr(pattern, "Value", None)
        if isinstance(value, str):
            return value
    raise CannotSendError("无法读取输入框内容（输入框不提供 Value 接口）")


def is_empty_text(text: str) -> bool:
    """空白字符也视为空内容。"""
    return not text.strip()


def get_input_state(hwnd: int | None = None) -> dict:
    """返回当前会话输入框状态。

    窗口不可用时抛 WindowUnavailableError；不可发送时抛 CannotSendError。
    返回: {type, label, chat_name, can_send, member_count, text, is_empty, reason}
    """
    hwnd = hwnd or require_wechat_main_window()
    require_window_ready(hwnd)

    window = auto.ControlFromHandle(hwnd)
    result = classify_chat(window)
    if not result.get("can_send"):
        chat = result.get("chat_name")
        label = result.get("label", "未知")
        raise CannotSendError(
            f"当前会话不可发送消息：{chat!r}（{label} / {result.get('type')}）"
        )

    input_field = find_input_field(window)
    if input_field is None:
        raise CannotSendError(f"未找到聊天输入框：{result.get('chat_name')!r}")

    text = read_input_text(input_field)
    result = dict(result)
    result["text"] = text
    result["is_empty"] = is_empty_text(text)
    result["hwnd"] = hwnd
    result["window_state"] = describe_window_state(hwnd)[0]
    return result


def print_state(state: dict) -> None:
    print(f"会话     : {state['chat_name']!r}")
    print(f"类型     : {state['label']} ({state['type']})")
    print(f"可发送   : {'是' if state['can_send'] else '否'}")
    print(f"输入内容 : {state['text']!r}")
    print(f"是否为空 : {'是' if state['is_empty'] else '否'}")


def main() -> int:
    enable_dpi_awareness()
    try:
        hwnd = require_wechat_main_window()
        state = get_input_state(hwnd)
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except CannotSendError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_state(state)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
