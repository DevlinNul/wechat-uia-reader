"""获取微信的聊天会话信息。

微信 4.x 是 Qt 单 HWND 渲染：会话列表与聊天区没有独立窗口句柄，
只能通过 UI Automation 定位控件，并以主窗口句柄 + 屏幕坐标描述。

用法:
    python get_wechat_chat.py            # 打印当前会话与聊天区
    python get_wechat_chat.py --sessions # 同时列出会话列表
"""

import ctypes
import sys

import uiautomation as auto

from get_wechat_hwnd import describe_window_state, find_wechat_main_window

# UI Automation 锚点（微信 4.x）
CHAT_DETAIL_CLASS = "mmui::ChatDetailView"
SESSION_LIST_ID = "session_list"
MESSAGE_LIST_ID = "chat_message_list"
CHAT_NAME_SUFFIX = "current_chat_name_label"
BACKWARD_VIEW_CLASS = "mmui::ChatBackwardView"
MAIN_SESSION_LIST_NAME = "会话"


def enable_dpi_awareness() -> None:
    """让本进程感知高 DPI，使 UIA 坐标与窗口坐标一致。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def iter_descendants(control):
    """深度优先遍历所有后代控件。"""
    for child in control.GetChildren():
        yield child
        yield from iter_descendants(child)


def to_logical(rect, hwnd: int):
    """把 UIA 的物理像素坐标换算为逻辑坐标。"""
    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) or 96
    scale = dpi / 96
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    return tuple(round(v / scale) for v in (left, top, right, bottom))


def find_list_view_title(window) -> str | None:
    """列表视图（折叠的聊天/服务号）的标题；没有返回按钮时返回 None。"""
    for el in iter_descendants(window):
        if el.ClassName == BACKWARD_VIEW_CLASS:
            for text in iter_descendants(el):
                if text.ControlTypeName == "TextControl" and text.Name.strip():
                    return text.Name.strip()
            return ""
    return None


def find_session_list_name(window) -> str:
    """当前会话列表的标题（普通会话列表为“会话”）。"""
    for el in iter_descendants(window):
        if el.AutomationId == SESSION_LIST_ID:
            return (el.Name or "").strip()
    return ""


def detect_list_view(window) -> str | None:
    """识别当前是否处于列表视图，返回标题。"""
    title = find_list_view_title(window)
    if title is not None:
        return title or find_session_list_name(window)
    name = find_session_list_name(window)
    if name and name != MAIN_SESSION_LIST_NAME:
        return name
    return None


def get_chat_info(hwnd: int | None = None) -> dict | None:
    """返回当前聊天会话信息，找不到聊天区时返回 None。"""
    hwnd = hwnd or find_wechat_main_window()
    if not hwnd:
        return None

    window = auto.ControlFromHandle(hwnd)
    chat_name = None
    message_list = None
    chat_view = None
    session_list = None

    for el in iter_descendants(window):
        aid = el.AutomationId
        if chat_name is None and aid.endswith(CHAT_NAME_SUFFIX) and el.Name:
            chat_name = el
        elif aid == MESSAGE_LIST_ID:
            message_list = el
        elif aid == SESSION_LIST_ID:
            session_list = el
        elif el.ClassName == CHAT_DETAIL_CLASS:
            chat_view = el

    # 仅有 ChatDetailView 但既无会话名也无消息列表：多为列表视图/非聊天页
    if message_list is None and chat_name is None:
        return None

    pane = chat_view or message_list
    if pane is None:
        return None
    info = {
        "hwnd": hwnd,
        "chat_pane_hwnd": 0,
        "chat_pane_rect": pane.BoundingRectangle,
        "message_list_rect": message_list.BoundingRectangle if message_list else None,
        "chat_name": chat_name.Name if chat_name else None,
        "session_list": session_list,
    }
    return info


def list_sessions(session_list) -> list[dict]:
    """解析会话列表，返回每个会话的名称、预览和位置。"""
    sessions = []
    if session_list is None:
        return sessions
    for item in session_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue
        lines = [ln.strip() for ln in (item.Name or "").split("\n") if ln.strip()]
        sessions.append(
            {
                "name": lines[0] if lines else "",
                "detail": lines[1:],
                "raw": item.Name,
                "rect": item.BoundingRectangle,
            }
        )
    return sessions


def describe_placement(hwnd: int) -> str:
    """返回窗口的最小化/最大化状态说明。"""
    state, _ = describe_window_state(hwnd)
    if state != "normal":
        return {"minimized": "最小化", "hidden": "隐藏到托盘", "offscreen": "屏幕外"}[state]
    if ctypes.windll.user32.IsZoomed(hwnd):
        return "最大化"
    return "普通"


def main() -> int:
    enable_dpi_awareness()
    show_sessions = "--sessions" in sys.argv

    hwnd = find_wechat_main_window()
    if not hwnd:
        print("未找到微信窗口，请确认微信已启动。")
        return 1

    state, hint = describe_window_state(hwnd)
    if hint:
        print(f"提示: {hint}")
        print("     （以下坐标仅供参考，UIA 返回的最小化窗口位置为还原后的位置）\n")

    info = get_chat_info(hwnd)
    if not info:
        window = auto.ControlFromHandle(hwnd)
        list_title = detect_list_view(window)
        if list_title is not None:
            print(f"当前处于列表视图: {list_title!r}（折叠的聊天/服务号等），没有聊天区。")
            return 0
        print("未找到微信聊天区，请确认微信已登录并打开过任意会话。")
        return 1

    hwnd = info["hwnd"]
    print(f"主窗口句柄 : {hex(hwnd)} ({hwnd})  状态: {describe_placement(hwnd)}")
    print(f"聊天区句柄 : 无（Qt 子控件，归属于主窗口）")
    print(f"当前会话   : {info['chat_name']!r}")
    print(f"聊天区位置 : {info['chat_pane_rect']}  (物理像素)")
    print(f"  → 逻辑坐标: {to_logical(info['chat_pane_rect'], hwnd)}")
    if info["message_list_rect"]:
        print(f"消息列表   : {info['message_list_rect']}")

    if show_sessions:
        sessions = list_sessions(info["session_list"])
        print(f"\n会话列表（{len(sessions)} 个）:")
        for i, s in enumerate(sessions, 1):
            preview = " | ".join(s["detail"])
            print(f"{i:>3}. {s['name']}  {preview}")
            print(f"     位置: {s['rect']}")

    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
