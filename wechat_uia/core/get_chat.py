"""获取微信的聊天会话信息。

微信 4.x 是 Qt 单 HWND 渲染：会话列表与聊天区没有独立窗口句柄，
只能通过 UI Automation 定位控件，并以主窗口句柄 + 控件矩形描述。

用法:
    python get_chat.py            # 打印当前会话与聊天区
    python get_chat.py --sessions # 同时列出会话列表
"""

import ctypes
import sys

from .errors import NotAConversationError, WindowUnavailableError
from .get_hwnd import (
    describe_window_state,
    require_wechat_main_window,
)
from .snapshot import scan_window, selected_session_name
from .uia_utils import enable_dpi_awareness, rect_xywh, to_logical


def chat_info_from_snapshot(snapshot) -> dict | None:
    """从已有快照提取聊天区信息，避免重复遍历控件树。

    当前是列表页时抛 NotAConversationError；无法识别聊天区时返回 None。
    """
    if snapshot.message_list is None and snapshot.chat_name_element is None:
        if snapshot.list_title is not None:
            raise NotAConversationError(
                f"{snapshot.list_title!r}（折叠的聊天/服务号等），没有聊天区"
            )
        return None

    pane = snapshot.chat_detail or snapshot.message_list
    if pane is None:
        return None

    pane_rect = pane.BoundingRectangle
    message_rect = (
        snapshot.message_list.BoundingRectangle if snapshot.message_list else None
    )
    return {
        "hwnd": snapshot.hwnd,
        "chat_pane_hwnd": 0,
        "chat_pane_rect": rect_xywh(pane_rect),
        "chat_pane_logical": to_logical(pane_rect, snapshot.hwnd),
        "message_list_rect": rect_xywh(message_rect) if message_rect else None,
        "chat_name": snapshot.chat_name,
    }


def get_chat_info(hwnd: int | None = None) -> dict | None:
    """返回当前聊天会话信息。

    窗口不可用时抛 WindowUnavailableError；当前是列表页时抛
    NotAConversationError；无法识别聊天区时返回 None。
    """
    return chat_info_from_snapshot(scan_window(hwnd))


def list_sessions(
    session_list, selected_name: str | None = None
) -> list[dict]:
    """解析会话列表控件，返回每个会话的名称、预览和位置。

    selected_name 由 SelectionItemPattern.IsSelected 得到；无法判断时
    所有条目都是 selected=False。
    """
    sessions = []
    if session_list is None:
        return sessions
    for item in session_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue
        lines = [ln.strip() for ln in (item.Name or "").split("\n") if ln.strip()]
        name = lines[0] if lines else ""
        sessions.append(
            {
                "name": name,
                "detail": lines[1:],
                "raw": item.Name,
                "rect": rect_xywh(item.BoundingRectangle),
                "selected": bool(selected_name is not None and name == selected_name),
            }
        )
    return sessions


def get_sessions(hwnd: int | None = None) -> list[dict]:
    """返回当前会话列表的纯数据表示；没有会话列表时返回空列表。"""
    snapshot = scan_window(hwnd)
    selected_name = selected_session_name(snapshot.session_list)
    return list_sessions(snapshot.session_list, selected_name=selected_name)


def describe_placement(hwnd: int) -> str:
    """返回窗口的最小化/最大化状态说明。"""
    state, _ = describe_window_state(hwnd)
    if state != "normal":
        return {"minimized": "最小化", "hidden": "隐藏到托盘", "offscreen": "屏幕外"}[state]
    if ctypes.windll.user32.IsZoomed(hwnd):
        return "最大化"
    return "普通"


def main() -> int:
    """命令行入口：打印当前会话、聊天区与会话列表。"""
    enable_dpi_awareness()
    show_sessions = "--sessions" in sys.argv

    try:
        hwnd = require_wechat_main_window()
        info = get_chat_info(hwnd)
    except NotAConversationError as exc:
        print(f"当前处于列表页: {exc}")
        return 0
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    if not info:
        print("未找到微信聊天区，请确认微信已登录并打开过任意会话。")
        return 1

    hwnd = info["hwnd"]
    print(f"主窗口句柄 : {hex(hwnd)} ({hwnd})  状态: {describe_placement(hwnd)}")
    print(f"聊天区句柄 : 无（Qt 子控件，归属于主窗口）")
    print(f"当前会话   : {info['chat_name']!r}")
    print(f"聊天区位置 : {info['chat_pane_rect']}  (物理像素，x,y,w,h)")
    print(f"  → 逻辑坐标: {info['chat_pane_logical']}")
    if info["message_list_rect"]:
        print(f"消息列表   : {info['message_list_rect']}")

    if show_sessions:
        try:
            sessions = get_sessions(hwnd)
        except WindowUnavailableError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        print(f"\n会话列表（{len(sessions)} 个）:")
        for i, s in enumerate(sessions, 1):
            preview = " | ".join(s["detail"])
            marker = "  [当前]" if s.get("selected") else ""
            print(f"{i:>3}. {s['name']}{marker}  {preview}")
            print(f"     位置: {s['rect']}")

    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
