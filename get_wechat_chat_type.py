"""判定当前微信会话/视图的类型。

在 get_wechat_chat.py 获取控件树的基础上：
- 普通会话读取聊天标题栏：群聊会额外显示成员数 "(N)"
- 列表视图（折叠的聊天、服务号）没有聊天标题栏，而是带“返回”按钮的列表页

本脚本只读，不点击、不切换会话、不主动唤起窗口。

用法:
    python get_wechat_chat_type.py
"""

import re
import sys

import uiautomation as auto

from get_wechat_chat import detect_list_view, enable_dpi_awareness, iter_descendants
from get_wechat_hwnd import describe_window_state, find_wechat_main_window

CHAT_NAME_SUFFIX = "current_chat_name_label"
TITLE_BAR_CLASS_SUFFIX = "ChatTitleBarMasterView"

# 群成员数，如 "(26)"
MEMBER_COUNT_RE = re.compile(r"^[（(]\s*(\d+)\s*[)）]$")
# 名称 + 成员数，如 "温暖的大家庭(26)"
NAME_COUNT_RE = r"^(?P<name>.+?)\s*[（(]\s*(?P<count>\d+)\s*[)）]$"

PRIVATE = "private"
GROUP = "group"
OFFICIAL = "official"
FOLDED = "folded"
SERVICE_LIST = "service_list"
LIST_VIEW = "list_view"
UNKNOWN = "unknown"

TYPE_LABELS = {
    PRIVATE: "私聊",
    GROUP: "群聊",
    OFFICIAL: "公众号/服务号",
    FOLDED: "折叠的聊天",
    SERVICE_LIST: "服务号列表",
    LIST_VIEW: "列表视图",
    UNKNOWN: "未知",
}

LIST_TYPES = {FOLDED, SERVICE_LIST, LIST_VIEW}


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


def collect_title_bar_texts(title_bar) -> list[str]:
    """收集标题栏内所有非空文本。"""
    if title_bar is None:
        return []
    texts = []
    for el in [title_bar, *iter_descendants(title_bar)]:
        if el.ControlTypeName in ("TextControl", "ButtonControl") and el.Name.strip():
            texts.append(el.Name.strip())
    return texts


def parse_member_count(texts: list[str], chat_name: str) -> int | None:
    """从标题栏文本中解析群成员数。"""
    for text in texts:
        match = MEMBER_COUNT_RE.match(text)
        if match:
            return int(match.group(1))
        match = re.match(NAME_COUNT_RE, text)
        if match and match.group("name").strip() == chat_name:
            return int(match.group("count"))
    return None


def collect_class_names(control) -> list[str]:
    """收集控件及其后代的类名，用于视图类型兜底判断。"""
    if control is None:
        return []
    names = [control.ClassName]
    names.extend(el.ClassName for el in iter_descendants(control))
    return names


def classify_list_view(title: str) -> tuple[str, str]:
    """按列表标题区分折叠的聊天 / 服务号列表 / 其他列表。"""
    if "折叠" in title:
        return FOLDED, f"带返回按钮的折叠列表页（{title}）"
    if title in ("服务号", "订阅号") or title.endswith("服务号") or title.endswith("订阅号"):
        return SERVICE_LIST, f"带返回按钮的服务号列表页（{title}）"
    return LIST_VIEW, f"带返回按钮的列表页（{title}）"


def classify_chat(window) -> dict:
    """判定窗口当前会话/视图的类型。

    返回: {type, label, chat_name, member_count, reason}
    """
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
                "reason": reason,
            }
        return {
            "type": UNKNOWN,
            "label": TYPE_LABELS[UNKNOWN],
            "chat_name": None,
            "member_count": None,
            "reason": "未找到会话名称控件（可能不在聊天页）",
        }

    chat_name = label.Name.strip()
    title_bar = find_title_bar(label)
    texts = collect_title_bar_texts(title_bar)
    class_names = collect_class_names(title_bar)
    member_count = parse_member_count(texts, chat_name)

    if member_count is not None:
        return {
            "type": GROUP,
            "label": TYPE_LABELS[GROUP],
            "chat_name": chat_name,
            "member_count": member_count,
            "reason": f"标题栏显示群成员数 ({member_count})",
        }

    joined = " ".join(class_names)
    if "ChatRoom" in joined:
        result, reason = GROUP, "标题栏为群聊视图（未显示成员数）"
    elif "ChatBrand" in joined or "Official" in joined:
        result, reason = OFFICIAL, "标题栏为公众号/服务号视图"
    elif "ChatSingle" in joined:
        result, reason = PRIVATE, "标题栏为单聊视图"
    else:
        result, reason = UNKNOWN, "标题栏无明确类型特征"

    return {
        "type": result,
        "label": TYPE_LABELS[result],
        "chat_name": chat_name,
        "member_count": None,
        "reason": reason,
    }


def get_current_chat_type(hwnd: int | None = None) -> dict | None:
    """对外接口：返回当前会话/视图类型信息。"""
    hwnd = hwnd or find_wechat_main_window()
    if not hwnd:
        return None
    window = auto.ControlFromHandle(hwnd)
    result = classify_chat(window)
    result["hwnd"] = hwnd
    result["window_state"] = describe_window_state(hwnd)[0]
    return result


def print_result(result: dict) -> None:
    title = "当前视图" if result["type"] in LIST_TYPES else "当前会话"
    print(f"{title} : {result['chat_name']!r}")
    print(f"类型     : {result['label']} ({result['type']})")
    if result.get("member_count") is not None:
        print(f"成员数   : {result['member_count']}")
    print(f"判定依据 : {result['reason']}")


def main() -> int:
    enable_dpi_awareness()
    hwnd = find_wechat_main_window()
    if not hwnd:
        print("未找到微信窗口，请确认微信已启动。")
        return 1

    _, hint = describe_window_state(hwnd)
    if hint:
        print(f"提示: {hint}\n")

    window = auto.ControlFromHandle(hwnd)

    result = classify_chat(window)
    print_result(result)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
