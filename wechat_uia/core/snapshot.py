"""一次遍历生成当前微信窗口的控件快照。

`scan_window()` 是唯一会完整遍历控件树的入口；类型判定、输入框读取、
消息读取都消费同一份快照，避免同一次逻辑读取里多次遍历导致结果不一致。

本模块是项目里唯一导入 uiautomation 的模块。
"""

import time
from dataclasses import dataclass

import uiautomation as auto

from .get_hwnd import (
    close_window,
    collect_windows,
    describe_window_state,
    require_wechat_main_window,
    require_window_ready,
    window_kind,
    window_kind_from_class,
)
from .uia_utils import (
    BACKWARD_VIEW_CLASS,
    BRAND_DISORDER_CLASS,
    CHAT_DETAIL_CLASS,
    CHAT_INFO_NAME,
    CHAT_INFO_PANEL_TEXT,
    CHAT_NAME_SUFFIX,
    CONTACT_HEAD_CLASS,
    INPUT_AUTOMATION_ID,
    INPUT_CLASS,
    INPUT_CLASSES,
    MAIN_SESSION_LIST_NAME,
    MEMBER_CELL_ID,
    MESSAGE_LIST_ID,
    PROFILE_LABEL_CLASS,
    PROFILE_POPUP_CLASS,
    PROFILE_VALUE_CLASS,
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
    send_button: object | None = None
    window_kind: str = "unknown"
    chat_info_button: object | None = None
    member_cell: object | None = None
    chat_info_open: bool = False


# 一次扫描的兜底上限：控件树异常时不让脚本无限等下去。
SCAN_TIMEOUT = 10.0
MAX_TREE_DEPTH = 60


def selected_session_item(session_list):
    """返回会话列表中当前选中的列表项；没有选中项时返回 None。"""
    if session_list is None:
        return None
    for item in session_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue
        try:
            pattern = item.GetPattern(auto.PatternId.SelectionItemPattern)
        except Exception:
            continue
        if pattern is None:
            continue
        try:
            if not pattern.IsSelected:
                continue
        except Exception:
            continue
        return item
    return None


def selected_session_identity(session_list) -> dict | None:
    """返回选中会话的轻量身份：名称、AutomationId、RuntimeId。

    RuntimeId 是 UIA 运行时标识，在同一次微信运行期间通常稳定；微信重启
    或控件树重建后可能变化，调用方应把它当作“本次运行内”的会话指纹。
    """
    item = selected_session_item(session_list)
    if item is None:
        return None
    try:
        runtime_id = tuple(item.GetRuntimeId())
    except Exception:
        runtime_id = None
    try:
        automation_id = item.AutomationId
    except Exception:
        automation_id = None
    name = (item.Name or "").split("\n")[0].strip()
    return {
        "name": name or None,
        "automation_id": automation_id,
        "runtime_id": runtime_id,
    }


def selected_session_name(session_list) -> str | None:
    """返回会话列表中当前选中的会话名。

    微信 4.x 的 mmui::ChatSessionCell 支持 SelectionItemPattern.IsSelected；
    不支持该模式时返回 None，调用方应退回标题文字判断。
    """
    identity = selected_session_identity(session_list)
    return identity["name"] if identity else None


def session_name_counts(session_list) -> dict[str, int]:
    """统计会话列表中每个显示名出现次数。"""
    counts: dict[str, int] = {}
    if session_list is None:
        return counts
    for item in session_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue
        name = (item.Name or "").split("\n")[0].strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def session_name_is_unique(session_list, name: str) -> bool | None:
    """会话名是否在会话列表中唯一出现。

    - True：唯一；
    - False：重名，不能仅凭名称确定 session；
    - None：会话列表不可见（例如小窗口布局），无法验证重名。

    调用方应把 None 当作“未验证”，而不是“重名”。
    """
    if session_list is None:
        return None
    name = (name or "").strip()
    if not name:
        return False
    return session_name_counts(session_list).get(name, 0) == 1


def read_value_pattern(control) -> str | None:
    """读取 ValuePattern.Value；控件不支持或读取失败时返回 None。"""
    try:
        pattern = control.GetPattern(auto.PatternId.ValuePattern)
    except Exception:
        return None
    if pattern is None:
        return None
    value = getattr(pattern, "Value", None)
    return value if isinstance(value, str) else None


def set_value_pattern(control, text: str) -> bool:
    """通过 ValuePattern.SetValue 写入文本；不支持或失败时返回 False。"""
    try:
        pattern = control.GetPattern(auto.PatternId.ValuePattern)
    except Exception:
        return False
    if pattern is None:
        return False
    try:
        pattern.SetValue(text)
    except Exception:
        return False
    return True


def read_text_pattern(control) -> str | None:
    """读取 TextPattern 的完整文档文本；不支持或失败时返回 None。"""
    try:
        pattern = control.GetPattern(auto.PatternId.TextPattern)
    except Exception:
        return None
    if pattern is None:
        return None
    try:
        return pattern.DocumentRange.GetText(-1)
    except Exception:
        return None


def read_control_text(control) -> str | None:
    """读取控件文本：优先 ValuePattern，再退回 TextPattern。

    这是输入框读取的唯一实现；fill_input、send_message、get_input 都复用它，
    避免三处各写一遍读取顺序。
    """
    for reader in (read_value_pattern, read_text_pattern):
        try:
            value = reader(control)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def scan_window(
    hwnd: int | None = None,
    *,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> ChatSnapshot:
    """校验窗口并一次遍历收集所有需要的锚点。

    返回 ChatSnapshot；窗口不可用时抛 WindowUnavailableError。
    wake_minimized / wake_tray 为真时，分别允许自动还原最小化窗口、
    通过托盘回调唤起托盘隐藏窗口。
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
    # UIA 根节点的 ClassName 才是 mmui::MainWindow / ChatSingleWindow /
    # SearchMsgUniqueChatWindow；Win32 类名通常只是 Qt51514QWindowIcon。
    window_role = window_kind_from_class(window.ClassName) or window_kind(hwnd)

    chat_name_element = None
    chat_detail = None
    session_list = None
    message_list = None
    input_preferred = None
    input_by_class = None
    input_any = None
    backward_view = None
    send_button = None
    chat_info_button = None
    member_cell = None
    chat_info_open = False
    is_brand_page = False

    for el in iter_descendants(
        window,
        max_depth=MAX_TREE_DEPTH,
        deadline=time.monotonic() + SCAN_TIMEOUT,
    ):
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
        if send_button is None and el.ControlTypeName == "ButtonControl":
            name = (el.Name or "").strip()
            if name == "发送" and (
                cls == "mmui::XOutlineButton" or "Button" in cls
            ):
                send_button = el
        if (
            chat_info_button is None
            and el.ControlTypeName == "ButtonControl"
            and (el.Name or "").strip() == CHAT_INFO_NAME
        ):
            chat_info_button = el
        if member_cell is None and aid == MEMBER_CELL_ID:
            member_cell = el
        if (el.Name or "").strip() == CHAT_INFO_PANEL_TEXT:
            chat_info_open = True
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
        send_button=send_button,
        window_kind=window_role,
        chat_info_button=chat_info_button,
        member_cell=member_cell,
        chat_info_open=chat_info_open or member_cell is not None,
    )


PROFILE_FIELD_LABELS = (
    "备注",
    "标签",
    "朋友圈",
    "共同群聊",
    "个性签名",
    "来源",
    "添加时间",
)


def find_profile_popup(main_hwnd: int | None = None):
    """返回当前可见的联系人资料弹窗 UIA 根控件；没有时返回 None。

    资料页是微信进程里的独立顶层窗口，类名为
    ``mmui::ProfileUniquePop``。只查找可见窗口，避免误用隐藏缓存。
    """
    for info in collect_windows():
        hwnd = int(info.get("hwnd") or 0)
        if not hwnd or not info.get("visible"):
            continue
        if main_hwnd and hwnd == main_hwnd:
            continue
        try:
            root = auto.ControlFromHandle(hwnd)
        except Exception:
            continue
        try:
            if root.ClassName == PROFILE_POPUP_CLASS:
                return root
        except Exception:
            continue
    return None


def read_profile_contact(root) -> str | None:
    """返回资料页顶部显示的联系人名；读不到时返回 None。"""
    for el in iter_descendants(
        root,
        max_depth=MAX_TREE_DEPTH,
        deadline=time.monotonic() + SCAN_TIMEOUT,
    ):
        try:
            if el.ClassName == CONTACT_HEAD_CLASS:
                name = (el.Name or "").strip()
                if name:
                    return name
        except Exception:
            continue
    return None


def read_profile_fields(root) -> dict[str, list[str]]:
    """读取资料页里的字段文本，返回 ``{标签名: [值, ...]}``。

    微信把多个标签放在同一个 ``ProfileTextView`` 里，用逗号分隔；这里
    只负责原样收集，拆分由调用方处理。
    """
    labels = []
    values = []
    for el in iter_descendants(
        root,
        max_depth=MAX_TREE_DEPTH,
        deadline=time.monotonic() + SCAN_TIMEOUT,
    ):
        try:
            name = (el.Name or "").strip()
            if not name:
                continue
            cls = el.ClassName
            rect = el.BoundingRectangle
        except Exception:
            continue
        if cls == PROFILE_LABEL_CLASS and name in PROFILE_FIELD_LABELS:
            labels.append((name, rect))
        elif cls == PROFILE_VALUE_CLASS:
            values.append((name, rect))

    if not labels:
        return {}

    labels.sort(key=lambda item: (item[1].top, item[1].left))
    values.sort(key=lambda item: (item[1].top, item[1].left))

    fields: dict[str, list[str]] = {}
    for index, (name, rect) in enumerate(labels):
        next_top = (
            labels[index + 1][1].top
            if index + 1 < len(labels)
            else None
        )
        selected = []
        for value, value_rect in values:
            if value_rect.top < rect.top - 2:
                continue
            if next_top is not None and value_rect.top >= next_top - 2:
                continue
            if value_rect.left < rect.left:
                continue
            selected.append(value)
        fields[name] = selected
    return fields


def close_profile_popup(root) -> bool:
    """关闭联系人资料弹窗；成功时返回 True。"""
    if root is None:
        return False
    try:
        pattern = root.GetPattern(auto.PatternId.WindowPattern)
        if pattern is not None:
            pattern.Close()
            return True
    except Exception:
        pass
    try:
        hwnd = int(getattr(root, "NativeWindowHandle", 0) or 0)
    except Exception:
        hwnd = 0
    return close_window(hwnd) if hwnd else False
