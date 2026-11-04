"""一次遍历生成当前微信窗口的控件快照。

`scan_window()` 是唯一会完整遍历控件树的入口；类型判定、输入框读取、
消息读取都消费同一份快照，避免同一次逻辑读取里多次遍历导致结果不一致。

本模块是项目里唯一导入 uiautomation 的模块。
"""

from dataclasses import dataclass

import uiautomation as auto

from get_hwnd import (
    describe_window_state,
    require_wechat_main_window,
    require_window_ready,
)
from uia_utils import (
    BACKWARD_VIEW_CLASS,
    BRAND_DISORDER_CLASS,
    CHAT_DETAIL_CLASS,
    CHAT_NAME_SUFFIX,
    INPUT_AUTOMATION_ID,
    INPUT_CLASS,
    INPUT_CLASSES,
    MAIN_SESSION_LIST_NAME,
    MESSAGE_LIST_ID,
    SESSION_LIST_ID,
    find_title_bar,
    first_text,
    iter_descendants,
)


@dataclass(frozen=True)
class ChatSnapshot:
    """当前窗口在某一时刻的只读快照，仅在内部层使用。"""

    hwnd: int
    window_state: str
    chat_name: str | None
    chat_name_element: object | None
    title_bar: object | None
    chat_detail: object | None
    session_list: object | None
    message_list: object | None
    input_field: object | None
    has_input: bool
    is_brand_page: bool
    list_title: str | None
    session_list_name: str
    uia_available: bool = True


def scan_window(
    hwnd: int | None = None,
    *,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> ChatSnapshot:
    """校验窗口并一次遍历收集所有需要的锚点。

    窗口不可用时抛 WindowUnavailableError。wake_minimized / wake_tray
    为真时，分别允许自动还原最小化窗口、通过托盘回调唤起托盘隐藏窗口。
    """
    if hwnd is None:
        hwnd = require_wechat_main_window(
            wake_minimized=wake_minimized, wake_tray=wake_tray
        )
    else:
        require_window_ready(
            hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
        )

    window = auto.ControlFromHandle(hwnd)
    uia_available = window.ClassName.startswith("mmui::")

    chat_name_element = None
    chat_detail = None
    session_list = None
    message_list = None
    input_preferred = None
    input_by_class = None
    input_any = None
    backward_view = None
    is_brand_page = False

    for el in iter_descendants(window):
        aid = el.AutomationId
        cls = el.ClassName

        if cls.startswith("mmui::"):
            uia_available = True
        if chat_name_element is None and aid.endswith(CHAT_NAME_SUFFIX) and el.Name:
            chat_name_element = el
        if cls == CHAT_DETAIL_CLASS:
            chat_detail = el
        if aid == SESSION_LIST_ID:
            session_list = el
        if aid == MESSAGE_LIST_ID:
            message_list = el
        if aid == INPUT_AUTOMATION_ID and input_preferred is None:
            input_preferred = el
        if cls == INPUT_CLASS and input_by_class is None:
            input_by_class = el
        if cls in INPUT_CLASSES and input_any is None:
            input_any = el
        if backward_view is None and cls == BACKWARD_VIEW_CLASS:
            backward_view = el
        if cls == BRAND_DISORDER_CLASS:
            is_brand_page = True

    session_list_name = (session_list.Name or "").strip() if session_list else ""

    if backward_view is not None:
        list_title = first_text(backward_view) or session_list_name
    elif session_list_name and session_list_name != MAIN_SESSION_LIST_NAME:
        list_title = session_list_name
    else:
        list_title = None

    chat_name = chat_name_element.Name.strip() if chat_name_element else None

    return ChatSnapshot(
        hwnd=hwnd,
        window_state=describe_window_state(hwnd)[0],
        chat_name=chat_name,
        chat_name_element=chat_name_element,
        title_bar=find_title_bar(chat_name_element) if chat_name_element else None,
        chat_detail=chat_detail,
        session_list=session_list,
        message_list=message_list,
        input_field=input_preferred or input_by_class or input_any,
        has_input=input_any is not None,
        is_brand_page=is_brand_page,
        list_title=list_title,
        session_list_name=session_list_name,
        uia_available=uia_available,
    )
