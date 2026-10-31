"""判定当前微信会话/视图的类型与可发送性。

在 get_chat.py 获取控件树的基础上：
- 私聊/群聊：有聊天输入框，可发送消息
- 公众号/服务号会话（如微信支付）：单向，不可发送
- 服务通知：单聊样式但没有输入框，不可发送
- 列表页（折叠的聊天、服务号、公众号消息聚合页等）：不是会话，不可发送

本脚本只读，不点击、不切换会话、不主动唤起窗口。

用法:
    python get_chat_type.py
"""

import re
import sys

import uiautomation as auto

from get_chat import detect_list_view, enable_dpi_awareness, iter_descendants
from get_hwnd import (
    WindowUnavailableError,
    describe_window_state,
    require_wechat_main_window,
    require_window_ready,
)

CHAT_NAME_SUFFIX = "current_chat_name_label"
TITLE_BAR_CLASS_SUFFIX = "ChatTitleBarMasterView"

MEMBER_COUNT_RE = re.compile(r"^[（(]\s*(\d+)\s*[)）]$")

PRIVATE = "private"
GROUP = "group"
OFFICIAL = "official"
NOTICE = "notice"
LIST = "list"
UNKNOWN = "unknown"

TYPE_LABELS = {
    PRIVATE: "私聊",
    GROUP: "群聊",
    OFFICIAL: "公众号/服务号",
    NOTICE: "服务通知",
    LIST: "列表",
    UNKNOWN: "未知",
}

LIST_TYPES = {LIST}

# 有聊天输入框才允许发送消息
INPUT_CLASSES = {"mmui::ChatInputView", "mmui::ChatInputField"}
# 公众号消息聚合页（文章列表，不是会话）
BRAND_DISORDER_CLASS = "mmui::ChatBrandDisorderPage"


def find_chat_name_element(window):
    """定位标题栏中的当前会话名称控件。"""
    for el in iter_descendants(window):
        if el.AutomationId.endswith(CHAT_NAME_SUFFIX) and el.Name:
            return el
    return None


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


def iter_leaf_text_controls(control):
    """遍历叶文本控件：不再包含其他 TextControl 的 TextControl。

    标题栏里复合容器（如 XHBoxView 的“名称(N)”）会包含名称、人数两个子控件，
    属于容器而非独立信息，因此只取叶子节点。
    """
    for el in iter_descendants(control):
        if el.ControlTypeName != "TextControl" or not el.Name.strip():
            continue
        if any(d.ControlTypeName == "TextControl" for d in iter_descendants(el)):
            continue
        yield el


def parse_member_count(title_bar, chat_name: str) -> int | None:
    """从标题栏中解析群成员数。

    依据结构：群聊标题栏有“名称”“(N)”两个独立文本控件；
    私聊只有“名称”一个控件。名称本身可能是 "(1)"、"(2)"，因此必须
    排除会话名控件后，再找纯成员数控件，不能按文本模式猜。
    """
    if title_bar is None:
        return None
    for el in iter_leaf_text_controls(title_bar):
        text = el.Name.strip()
        if text == chat_name or el.AutomationId.endswith(CHAT_NAME_SUFFIX):
            continue
        match = MEMBER_COUNT_RE.match(text)
        if match:
            return int(match.group(1))
    return None


def collect_class_names(control) -> list[str]:
    """收集控件及其后代的类名，用于视图类型兜底判断。"""
    if control is None:
        return []
    names = [control.ClassName]
    names.extend(el.ClassName for el in iter_descendants(control))
    return names


def has_chat_input(window) -> bool:
    """是否存在聊天输入框；有输入框的会话才能发送消息。"""
    return any(el.ClassName in INPUT_CLASSES for el in iter_descendants(window))


def is_brand_disorder_page(window) -> bool:
    """是否为公众号消息聚合页（列表，不是会话）。"""
    return any(el.ClassName == BRAND_DISORDER_CLASS for el in iter_descendants(window))


def classify_list_view(title: str) -> tuple[str, str]:
    """带返回按钮的列表页统一归为“列表”类型。"""
    return LIST, f"带返回按钮的列表页（{title}）"


def classify_chat(window) -> dict:
    """判定窗口当前会话/视图的类型与可发送性。

    窗口不可用时在入口处直接抛 WindowUnavailableError，不返回半成品结果。
    返回: {type, label, chat_name, member_count, can_send, reason}
    """
    hwnd = getattr(window, "NativeWindowHandle", 0)
    if hwnd:
        require_window_ready(hwnd)

    has_input = has_chat_input(window)
    label = find_chat_name_element(window)
    if label is None:
        list_title = detect_list_view(window)
        if list_title is not None:
            result, reason = classify_list_view(list_title)
            return {
                "type": result,
                "label": TYPE_LABELS[result],
                "chat_name": list_title,
                "member_count": None,
                "can_send": False,
                "reason": reason,
            }
        return {
            "type": UNKNOWN,
            "label": TYPE_LABELS[UNKNOWN],
            "chat_name": None,
            "member_count": None,
            "can_send": False,
            "reason": "未找到会话名称控件（可能不在聊天页）",
        }

    chat_name = label.Name.strip()

    # 公众号消息聚合页：有名称标签，但展示的是文章列表，不是会话
    if is_brand_disorder_page(window):
        return {
            "type": LIST,
            "label": TYPE_LABELS[LIST],
            "chat_name": chat_name,
            "member_count": None,
            "can_send": False,
            "reason": "公众号消息聚合页（文章列表，不可发送）",
        }

    title_bar = find_title_bar(label)
    class_names = collect_class_names(title_bar)
    member_count = parse_member_count(title_bar, chat_name)

    if member_count is not None:
        return {
            "type": GROUP,
            "label": TYPE_LABELS[GROUP],
            "chat_name": chat_name,
            "member_count": member_count,
            "can_send": has_input,
            "reason": f"标题栏显示群成员数 ({member_count})",
        }

    joined = " ".join(class_names)
    if "ChatRoom" in joined:
        result, reason = GROUP, "标题栏为群聊视图（未显示成员数）"
    elif "ChatBrand" in joined or "Official" in joined:
        result, reason = OFFICIAL, "标题栏为公众号/服务号视图（单向，不可发送）"
    elif "ChatSingle" in joined:
        if has_input:
            result, reason = PRIVATE, "标题栏为单聊视图，且有聊天输入框"
        else:
            result, reason = NOTICE, "标题栏为单聊视图，但无聊天输入框（不可发送）"
    else:
        result, reason = UNKNOWN, "标题栏无明确类型特征"

    return {
        "type": result,
        "label": TYPE_LABELS[result],
        "chat_name": chat_name,
        "member_count": None,
        "can_send": has_input,
        "reason": reason,
    }


def get_current_chat_type(hwnd: int | None = None) -> dict:
    """对外接口：返回当前会话/视图类型信息。

    窗口不可用时抛 WindowUnavailableError。
    """
    hwnd = hwnd or require_wechat_main_window()
    require_window_ready(hwnd)
    window = auto.ControlFromHandle(hwnd)
    result = classify_chat(window)
    result["hwnd"] = hwnd
    result["window_state"] = describe_window_state(hwnd)[0]
    return result


def print_result(result: dict) -> None:
    title = "当前视图" if result["type"] in LIST_TYPES else "当前会话"
    print(f"{title} : {result['chat_name']!r}")
    print(f"类型     : {result['label']} ({result['type']})")
    print(f"可发送   : {'是' if result.get('can_send') else '否'}")
    if result.get("member_count") is not None:
        print(f"成员数   : {result['member_count']}")
    print(f"判定依据 : {result['reason']}")


def main() -> int:
    enable_dpi_awareness()
    try:
        hwnd = require_wechat_main_window()
        window = auto.ControlFromHandle(hwnd)
        result = classify_chat(window)
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_result(result)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
