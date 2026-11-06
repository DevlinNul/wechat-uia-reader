"""读取微信当前会话的输入框内容并判断是否为空。

- 对不可发送消息的会话（公众号推送、服务通知、列表页等）抛出 CannotSendError
- 对可发送消息的会话（私聊/群聊/公众号会话）读取输入框文本，并判断是否为空

本脚本只读输入框内容，不发送消息、不切换会话、不主动唤起窗口。

用法:
    python get_input.py

作为库使用:
    from errors import CannotSendError, WindowUnavailableError
    from get_input import get_input_state

    state = get_input_state()   # 窗口不可用抛 WindowUnavailableError；不可发送抛 CannotSendError
    state["text"], state["is_empty"]
"""

import sys

from .errors import CannotSendError, WindowUnavailableError
from .get_chat_type import classify_snapshot
from .snapshot import read_control_text, scan_window
from .uia_utils import enable_dpi_awareness


def read_input_text(input_field) -> str:
    """读取输入框文本：优先 Value/TextPattern，取不到时退回 LegacyIAccessible。"""
    value = read_control_text(input_field)
    if value is not None:
        return value

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
    snapshot = scan_window(hwnd)
    result = classify_snapshot(snapshot)

    if not result.get("can_send"):
        chat = result.get("chat_name")
        label = result.get("label", "未知")
        raise CannotSendError(
            f"当前会话不可发送消息：{chat!r}（{label} / {result.get('type')}）"
        )

    if snapshot.input_field is None:
        raise CannotSendError(f"未找到聊天输入框：{result.get('chat_name')!r}")

    text = read_input_text(snapshot.input_field)
    result = dict(result)
    result["text"] = text
    result["is_empty"] = is_empty_text(text)
    result["hwnd"] = snapshot.hwnd
    result["window_state"] = snapshot.window_state
    return result


def print_state(state: dict) -> None:
    """按 CLI 格式打印会话、类型与输入框状态。"""
    print(f"会话     : {state['chat_name']!r}")
    print(f"类型     : {state['label']} ({state['type']})")
    print(f"可发送   : {'是' if state['can_send'] else '否'}")
    print(f"输入内容 : {state['text']!r}")
    print(f"是否为空 : {'是' if state['is_empty'] else '否'}")


def main() -> int:
    """命令行入口：读取并打印当前输入框内容。"""
    enable_dpi_awareness()
    try:
        state = get_input_state()
    except CannotSendError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_state(state)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
