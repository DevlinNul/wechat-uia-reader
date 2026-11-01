"""读取私聊会话当前可获得的聊天记录（不滚动）。

- 仅支持私聊（private）会话；其他类型抛 UnsupportedChatError
- 不滚动消息列表：只读取 UI Automation 当前已暴露的消息项，
  返回结果可能不包含完整历史，也不保证与屏幕可见区域完全一致
- 只读：不发送消息、不点击、不切换会话、不主动唤起窗口

用法:
    python get_messages.py

作为库使用:
    from get_messages import UnsupportedChatError, get_messages
    data = get_messages()   # 非私聊时抛 UnsupportedChatError
    data["messages"], data["count"]
"""

import sys

import uiautomation as auto

from get_chat import enable_dpi_awareness, iter_descendants
from get_chat_type import PRIVATE, classify_chat
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


class UnsupportedChatError(RuntimeError):
    """当前会话不是私聊，或无法读取消息列表。"""


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
    """读取私聊会话当前可获得的聊天记录。

    窗口不可用时抛 WindowUnavailableError；非私聊时抛 UnsupportedChatError。
    返回: {hwnd, chat_name, type, label, count, messages}
    """
    hwnd = hwnd or require_wechat_main_window()
    require_window_ready(hwnd)

    window = auto.ControlFromHandle(hwnd)
    info = classify_chat(window)
    if info["type"] != PRIVATE:
        raise UnsupportedChatError(
            f"仅支持私聊会话，当前为 {info['label']}（{info['type']}）："
            f"{info['chat_name']!r}"
        )

    message_list = find_message_list(window)
    if message_list is None:
        raise UnsupportedChatError(
            f"未找到消息列表，无法读取聊天记录：{info['chat_name']!r}"
        )

    messages = []
    for item in message_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue
        messages.append(read_item(item, len(messages)))

    return {
        "hwnd": hwnd,
        "chat_name": info["chat_name"],
        "type": info["type"],
        "label": info["label"],
        "count": len(messages),
        "messages": messages,
    }


def print_messages(data: dict) -> None:
    print(f"会话     : {data['chat_name']!r}")
    print(f"类型     : {data['label']} ({data['type']})")
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
    except UnsupportedChatError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_messages(data)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
