"""读取当前会话 UI Automation 已暴露的消息记录（不滚动）。

- 不判断会话类型；只读取消息列表中当前可获得的消息项
- 不滚动消息列表：返回结果可能不包含完整历史，也不保证与屏幕可见区域完全一致
- 只读：不发送消息、不点击、不切换会话、不主动唤起窗口

用法:
    python get_messages.py

作为库使用:
    from get_messages import get_messages
    data = get_messages()
    data["messages"], data["count"]
"""

import sys

import uiautomation as auto

from get_chat import enable_dpi_awareness, iter_descendants
from get_hwnd import (
    WindowUnavailableError,
    require_wechat_main_window,
    require_window_ready,
)

MESSAGE_LIST_ID = "chat_message_list"

# 消息项类型
KIND_BY_CLASS = {
    "mmui::ChatItemView": "time",
    "mmui::ChatTextItemView": "text",
    "mmui::ChatBubbleItemView": "bubble",
    "mmui::ChatImageItemView": "image",
    "mmui::ChatVoiceItemView": "voice",
    "mmui::ChatAppReaderItemView": "app",
}


def find_message_list(window):
    """定位当前会话的消息列表控件。"""
    for el in iter_descendants(window):
        if el.AutomationId == MESSAGE_LIST_ID:
            return el
    return None


def item_kind(item) -> str:
    return KIND_BY_CLASS.get(item.ClassName, "other")


def read_item(item, index: int) -> dict:
    """把消息项转换为可序列化的记录。"""
    text = (item.Name or "").strip()
    parts = []
    for el in iter_descendants(item):
        if el.ControlTypeName == "TextControl" and el.Name.strip():
            part = el.Name.strip()
            if part != text and part not in parts:
                parts.append(part)

    handle = item.BoundingRectangle
    return {
        "index": index,
        "kind": item_kind(item),
        "class": item.ClassName,
        "text": text,
        "parts": parts,
        "rect": (
            handle.left,
            handle.top,
            handle.right - handle.left,
            handle.bottom - handle.top,
        ),
    }


def get_messages(hwnd: int | None = None) -> dict:
    """读取当前消息列表中可获得的消息项。

    窗口不可用时抛 WindowUnavailableError；找不到消息列表时抛 RuntimeError。
    返回: {hwnd, count, messages}；消息列表为空时 count 为 0。
    """
    hwnd = hwnd or require_wechat_main_window()
    require_window_ready(hwnd)

    window = auto.ControlFromHandle(hwnd)
    message_list = find_message_list(window)
    if message_list is None:
        raise RuntimeError("未找到消息列表，无法读取聊天记录")

    messages = []
    for item in message_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue
        messages.append(read_item(item, len(messages)))

    return {
        "hwnd": hwnd,
        "count": len(messages),
        "messages": messages,
    }


def print_messages(data: dict) -> None:
    print(f"消息条数 : {data['count']}（未滚动，仅当前可获得）")
    for msg in data["messages"]:
        print(f"[{msg['index']:>2}] {msg['kind']:<6} {msg['text']!r}")


def main() -> int:
    enable_dpi_awareness()
    try:
        data = get_messages()
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_messages(data)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
