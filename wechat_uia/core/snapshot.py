"""一次遍历生成当前微信窗口的控件快照。

`scan_window()` 是唯一会完整遍历控件树的入口；类型判定、输入框读取、
消息读取都消费同一份快照，避免同一次逻辑读取里多次遍历导致结果不一致。

本模块是项目里唯一导入 uiautomation 的模块。
"""

import time
import warnings
from dataclasses import dataclass

# Python 3.15 上 comtypes 会提示枚举行为可能不同；实测本项目使用的 UIA
# 调用正常，这里只屏蔽这一条已知警告，避免每次命令行都刷屏。
warnings.filterwarnings(
    "ignore",
    message=r"You are running 'comtypes' on Python 3\.15.*",
    category=FutureWarning,
)

import uiautomation as auto

from .get_hwnd import (
    close_window,
    collect_windows,
    describe_window_state,
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
    CHAT_MESSAGE_PAGE_CLASS,
    CHAT_MESSAGE_PAGE_ID,
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
    TITLE_BAR_CLASS,
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
    window_automation_id: str | None = None
    layout: str | None = None
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


def get_root_control(hwnd: int):
    """返回窗口的 UIA 根控件。

    这是本模块之外唯一的 UIA 根入口；surface 等模块通过它读根类名和
    AutomationId，不直接导入 uiautomation。
    """
    return auto.ControlFromHandle(hwnd)


def has_chat_info_panel(hwnd: int, timeout: float = 1.0) -> bool:
    """轻量判断聊天信息侧栏是否可见；找到就停，不做完整快照。"""
    try:
        root = get_root_control(hwnd)
    except Exception:
        return False
    deadline = time.monotonic() + timeout
    for el in iter_descendants(root, max_depth=MAX_TREE_DEPTH, deadline=deadline):
        try:
            aid = el.AutomationId or ""
            name = (el.Name or "").strip()
        except Exception:
            continue
        if aid == MEMBER_CELL_ID or name == CHAT_INFO_PANEL_TEXT:
            return True
    return False


def scan_window(hwnd: int) -> ChatSnapshot:
    """校验窗口并一次遍历收集所有需要的锚点。

    这是唯一的完整控件树遍历入口。它只读，不唤醒、不激活窗口。
    窗口不可用时抛 WindowUnavailableError。
    """
    require_window_ready(hwnd)

    window = get_root_control(hwnd)
    uia_available = window.ClassName.startswith("mmui::")
    # UIA 根节点的 ClassName 才是 mmui::MainWindow / ChatSingleWindow /
    # SearchMsgUniqueChatWindow；Win32 类名通常只是 Qt51514QWindowIcon。
    window_role = window_kind_from_class(window.ClassName) or window_kind(hwnd)
    try:
        window_automation_id = getattr(window, "AutomationId", None) or None
    except Exception:
        window_automation_id = None

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
        elif chat_detail is None and (
            cls == CHAT_MESSAGE_PAGE_CLASS or aid == CHAT_MESSAGE_PAGE_ID
        ):
            # 独立窗口没有 ChatDetailView，聊天区容器是 ChatMessagePage。
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

    list_title = _list_title_from(backward_view, session_list_name)

    chat_name = chat_name_element.Name.strip() if chat_name_element else None

    layout = _layout_for(window_role, session_list is not None)

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
        window_automation_id=window_automation_id,
        layout=layout,
        chat_info_button=chat_info_button,
        member_cell=member_cell,
        chat_info_open=chat_info_open or member_cell is not None,
    )


# ------------------------------------------------------------ 快速读取（轮询）
#
# 轮询只需要会话头部：会话名、标题栏类名、标题栏文本、输入框和会话列表
# 是否存在。用批量查找 + 缓存请求，一次跨进程往返就能拿到这些属性，不必
# 逐个控件构造 uiautomation.Control。
#
# 本模块是唯一导入 uiautomation 的地方，原语放在这里；缓存和失效判断放在
# core/chat_type_watch.py。

TREE_SCOPE_DESCENDANTS = 4
CONTROL_TYPE_TEXT = 50020


@dataclass(frozen=True)
class ChatHeader:
    """一次批量缓存读取得到的会话头部，只在内部层使用。"""

    hwnd: int
    window_state: str
    window_kind: str
    window_automation_id: str | None
    chat_name: str | None
    chat_name_element: object | None
    title_bar_classes: tuple[str, ...]
    title_bar_texts: tuple[tuple[str, str], ...]
    has_input: bool
    is_brand_page: bool
    has_session_list: bool
    list_title: str | None
    layout: str | None


_uia_client = None
_uia_client_failed = False
_cached_request = None
_conditions: dict = {}


def _uia_automation():
    """返回 IUIAutomation 接口；取不到时返回 None，由调用方退回完整扫描。"""
    global _uia_client, _uia_client_failed
    if _uia_client is None and not _uia_client_failed:
        try:
            from uiautomation.uiautomation import _AutomationClient

            _uia_client = _AutomationClient.instance().IUIAutomation
        except Exception:
            _uia_client_failed = True
    return _uia_client


def _cache_request():
    """构造只取判定所需四个属性的缓存请求。"""
    global _cached_request
    if _cached_request is None:
        request = _uia_automation().CreateCacheRequest()
        for property_id in (
            auto.PropertyId.AutomationIdProperty,
            auto.PropertyId.ClassNameProperty,
            auto.PropertyId.NameProperty,
            auto.PropertyId.ControlTypeProperty,
        ):
            request.AddProperty(property_id)
        _cached_request = request
    return _cached_request


def _property_condition(property_id: int, value):
    """按属性等值构造查询条件，并按 (属性, 值) 复用。"""
    key = (property_id, value)
    condition = _conditions.get(key)
    if condition is None:
        condition = _uia_automation().CreatePropertyCondition(
            property_id, value
        )
        _conditions[key] = condition
    return condition


def _true_condition():
    condition = _conditions.get("true")
    if condition is None:
        condition = _uia_automation().CreateTrueCondition()
        _conditions["true"] = condition
    return condition


def _find_first(root, property_id: int, value, cache):
    """在后代里找第一个属性匹配的元素；找不到时返回空指针。"""
    return root.Element.FindFirstBuildCache(
        TREE_SCOPE_DESCENDANTS,
        _property_condition(property_id, value),
        cache,
    )


def _layout_for(window_role: str, has_session_list: bool) -> str | None:
    """按窗口角色和会话列表可见性给出布局标记。"""
    if window_role == "chat":
        return "single"
    if window_role == "main":
        return "wide" if has_session_list else "narrow"
    return None


def _list_title_from(backward_view, session_list_name: str) -> str | None:
    """列表页标题的唯一来源：返回按钮里的文本，退回会话列表名。"""
    if backward_view is not None:
        return first_text(backward_view) or session_list_name
    if session_list_name and session_list_name != MAIN_SESSION_LIST_NAME:
        return session_list_name
    return None


def read_chat_header(hwnd: int) -> ChatHeader | None:
    """批量缓存读取会话头部；结构不符合预期时返回 None。

    只读，不唤醒、不点击、不改变窗口状态。返回 None 表示快速路径无法
    可靠判定（例如标题栏缺失），调用方应退回 :func:`scan_window`。
    """
    require_window_ready(hwnd)

    if _uia_automation() is None:
        return None

    try:
        cache = _cache_request()
        root = get_root_control(hwnd)
        window_class = root.ClassName
    except Exception:
        return None

    window_role = window_kind_from_class(window_class) or window_kind(hwnd)
    try:
        window_automation_id = root.AutomationId or None
    except Exception:
        window_automation_id = None

    try:
        input_element = _find_first(
            root,
            auto.PropertyId.AutomationIdProperty,
            INPUT_AUTOMATION_ID,
            cache,
        )
        if not input_element:
            for class_name in INPUT_CLASSES:
                input_element = _find_first(
                    root,
                    auto.PropertyId.ClassNameProperty,
                    class_name,
                    cache,
                )
                if input_element:
                    break
        session_element = _find_first(
            root, auto.PropertyId.AutomationIdProperty, SESSION_LIST_ID, cache
        )
        brand_element = _find_first(
            root,
            auto.PropertyId.ClassNameProperty,
            BRAND_DISORDER_CLASS,
            cache,
        )
        title_element = _find_first(
            root, auto.PropertyId.ClassNameProperty, TITLE_BAR_CLASS, cache
        )
    except Exception:
        return None

    window_state = describe_window_state(hwnd)[0]
    has_session_list = bool(session_element)
    session_list_name = ""
    if has_session_list:
        session_list_name = (session_element.CachedName or "").strip()

    if not title_element:
        # 列表页或其它非会话页：标题只取返回按钮文本，不枚举整棵树。
        try:
            backward = root.Element.FindFirst(
                TREE_SCOPE_DESCENDANTS,
                _property_condition(
                    auto.PropertyId.ClassNameProperty, BACKWARD_VIEW_CLASS
                ),
            )
        except Exception:
            return None
        if not backward:
            # 既没有标题栏也没有返回按钮：多半是窗口正在重建或被遮挡。
            # 这里不猜成列表页，交给完整扫描判断，避免误报 unknown。
            return None
        backward_view = auto.Control.CreateControlFromElement(backward)
        return ChatHeader(
            hwnd=hwnd,
            window_state=window_state,
            window_kind=window_role,
            window_automation_id=window_automation_id,
            chat_name=None,
            chat_name_element=None,
            title_bar_classes=(),
            title_bar_texts=(),
            has_input=bool(input_element),
            is_brand_page=bool(brand_element),
            has_session_list=has_session_list,
            list_title=_list_title_from(backward_view, session_list_name),
            layout=_layout_for(window_role, has_session_list),
        )

    title_classes = [title_element.CachedClassName or ""]
    title_texts = []
    chat_name = None
    chat_name_element = None
    try:
        elements = title_element.FindAllBuildCache(
            TREE_SCOPE_DESCENDANTS, _true_condition(), cache
        )
        for index in range(elements.Length):
            element = elements.GetElement(index)
            title_classes.append(element.CachedClassName or "")
            if element.CachedControlType != CONTROL_TYPE_TEXT:
                continue
            name = (element.CachedName or "").strip()
            automation_id = element.CachedAutomationId or ""
            title_texts.append((name, automation_id))
            if (
                chat_name_element is None
                and automation_id.endswith(CHAT_NAME_SUFFIX)
                and name
            ):
                chat_name_element = element
                chat_name = name
    except Exception:
        return None

    if chat_name_element is None:
        return None

    return ChatHeader(
        hwnd=hwnd,
        window_state=window_state,
        window_kind=window_role,
        window_automation_id=window_automation_id,
        chat_name=chat_name,
        chat_name_element=chat_name_element,
        title_bar_classes=tuple(title_classes),
        title_bar_texts=tuple(title_texts),
        has_input=bool(input_element),
        is_brand_page=bool(brand_element),
        has_session_list=has_session_list,
        list_title=None,
        layout=_layout_for(window_role, has_session_list),
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
