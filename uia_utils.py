"""UI Automation 基础能力与锚点常量。

只放与具体业务无关的通用能力：DPI、控件遍历、坐标换算、标题栏定位，
以及微信 4.x 的 UI Automation 锚点。微信版本升级时优先只改本文件。

本模块不导入 uiautomation；控件对象由调用方传入。
"""

import ctypes

# ---------------------------------------------------------------- 锚点（微信 4.x）
SESSION_LIST_ID = "session_list"
MESSAGE_LIST_ID = "chat_message_list"
CHAT_NAME_SUFFIX = "current_chat_name_label"
CHAT_DETAIL_CLASS = "mmui::ChatDetailView"
BACKWARD_VIEW_CLASS = "mmui::ChatBackwardView"
MAIN_SESSION_LIST_NAME = "会话"
TITLE_BAR_CLASS_SUFFIX = "ChatTitleBarMasterView"

INPUT_AUTOMATION_ID = "chat_input_field"
INPUT_CLASS = "mmui::ChatInputField"
INPUT_CLASSES = frozenset({"mmui::ChatInputView", "mmui::ChatInputField"})
BRAND_DISORDER_CLASS = "mmui::ChatBrandDisorderPage"

GROUP_TITLE_FEATURE = "ChatRoom"
BRAND_TITLE_FEATURES = ("ChatBrand", "Official")
SINGLE_TITLE_FEATURE = "ChatSingle"

DPI_AWARENESS_PER_MONITOR = 2


# ---------------------------------------------------------------- 通用能力
def enable_dpi_awareness() -> None:
    """让本进程感知高 DPI，使 UIA 坐标与窗口坐标一致。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(DPI_AWARENESS_PER_MONITOR)
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


def first_text(control) -> str:
    """返回控件子树中第一个非空文本，没有则返回空字符串。"""
    for el in iter_descendants(control):
        if el.ControlTypeName == "TextControl" and el.Name.strip():
            return el.Name.strip()
    return ""


def find_title_bar(label):
    """从会话名称控件向上找到标题栏容器。"""
    el = label
    for _ in range(12):
        el = el.GetParentControl()
        if el is None:
            break
        if el.ClassName.endswith(TITLE_BAR_CLASS_SUFFIX):
            return el
    return label.GetParentControl()


def rect_xywh(rect) -> tuple[int, int, int, int]:
    """把 UIA 的 BoundingRectangle 统一转换为 (x, y, width, height)。"""
    return (
        rect.left,
        rect.top,
        rect.right - rect.left,
        rect.bottom - rect.top,
    )


def to_logical(rect, hwnd: int) -> tuple[int, int, int, int]:
    """把 UIA 的物理像素矩形换算为逻辑坐标，格式同样为 (x, y, width, height)。"""
    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) or 96
    scale = dpi / 96
    x, y, width, height = rect_xywh(rect)
    return tuple(round(v / scale) for v in (x, y, width, height))
