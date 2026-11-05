"""判定当前微信会话/视图的类型与可发送性。

消费 snapshot 提供的一次性控件快照：
- 私聊/群聊：有聊天输入框，可发送消息
- 公众号/服务号会话（official）：有输入框，可发送
- 公众号推送（broadcast）：单向推送，无输入框，不可发送
- 服务通知（notice）：单聊样式但没有输入框，不可发送
- 公众号聚合页（feed）：文章列表页，不是会话，不可发送
- 列表页（list）：折叠的聊天、服务号、服务号子列表等，不是会话，不可发送

本脚本只读，不点击、不切换会话、不主动唤起窗口。

用法:
    python get_chat_type.py
"""

import re
import sys

from errors import WindowUnavailableError
from snapshot import scan_window
from uia_utils import (
    BRAND_TITLE_FEATURES,
    CHAT_NAME_SUFFIX,
    GROUP_TITLE_FEATURE,
    SINGLE_TITLE_FEATURE,
    enable_dpi_awareness,
    iter_descendants,
)

MEMBER_COUNT_RE = re.compile(r"^[（(]\s*(\d+)\s*[)）]$")

PRIVATE = "private"
GROUP = "group"
OFFICIAL = "official"
BROADCAST = "broadcast"
NOTICE = "notice"
FEED = "feed"
LIST = "list"
UNKNOWN = "unknown"

TYPE_LABELS = {
    PRIVATE: "私聊",
    GROUP: "群聊",
    OFFICIAL: "公众号/服务号会话",
    BROADCAST: "公众号推送",
    NOTICE: "服务通知",
    FEED: "公众号聚合页",
    LIST: "列表",
    UNKNOWN: "未知",
}

VIEW_TYPES = {FEED, LIST}


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


def classify_snapshot(snapshot) -> dict:
    """基于控件快照判定类型与可发送性（纯逻辑，不访问 UIA）。"""
    if snapshot.chat_name_element is None:
        if snapshot.list_title is not None:
            return _result(
                LIST,
                snapshot.list_title,
                None,
                False,
                f"带返回按钮的列表页（{snapshot.list_title}）",
            )
        return _result(
            UNKNOWN,
            None,
            None,
            False,
            "未找到会话名称控件（可能不在聊天页）",
        )

    chat_name = snapshot.chat_name or ""
    has_input = snapshot.has_input

    # 公众号消息聚合页：有名称标签，但展示的是文章列表，不是会话
    if snapshot.is_brand_page:
        return _result(
            FEED,
            chat_name,
            None,
            False,
            "公众号消息聚合页（文章列表，不可发送）",
        )

    title_bar = snapshot.title_bar
    class_names = collect_class_names(title_bar)
    member_count = parse_member_count(title_bar, chat_name)

    if member_count is not None:
        return _result(
            GROUP,
            chat_name,
            member_count,
            has_input,
            f"标题栏显示群成员数 ({member_count})",
        )

    joined = " ".join(class_names)
    if GROUP_TITLE_FEATURE in joined:
        result, reason = GROUP, "标题栏为群聊视图（未显示成员数）"
    elif any(feature in joined for feature in BRAND_TITLE_FEATURES):
        if has_input:
            result, reason = OFFICIAL, "公众号/服务号会话视图，且有聊天输入框（可发送）"
        else:
            result, reason = BROADCAST, "公众号/服务号推送视图，无输入框（不可发送）"
    elif SINGLE_TITLE_FEATURE in joined:
        if has_input:
            result, reason = PRIVATE, "标题栏为单聊视图，且有聊天输入框"
        else:
            result, reason = NOTICE, "单聊视图但没有输入框（不可发送）"
    else:
        result, reason = UNKNOWN, "标题栏无明确类型特征"

    return _result(result, chat_name, None, has_input, reason)


def _result(
    view_type: str,
    chat_name: str | None,
    member_count: int | None,
    can_send: bool,
    reason: str,
) -> dict:
    """组装统一的类型判定结果。"""
    return {
        "type": view_type,
        "label": TYPE_LABELS[view_type],
        "chat_name": chat_name,
        "member_count": member_count,
        "can_send": can_send,
        "reason": reason,
    }


def get_current_chat_type(hwnd: int | None = None) -> dict:
    """对外接口：返回当前会话/视图类型信息。

    窗口不可用时抛 WindowUnavailableError。
    """
    snapshot = scan_window(hwnd)
    result = classify_snapshot(snapshot)
    result["hwnd"] = snapshot.hwnd
    result["window_state"] = snapshot.window_state
    return result


def print_result(result: dict) -> None:
    """打印类型、可发送性与判定依据。"""
    title = "当前视图" if result["type"] in VIEW_TYPES else "当前会话"
    print(f"{title} : {result['chat_name']!r}")
    print(f"类型     : {result['label']} ({result['type']})")
    print(f"可发送   : {'是' if result.get('can_send') else '否'}")
    if result.get("member_count") is not None:
        print(f"成员数   : {result['member_count']}")
    print(f"判定依据 : {result['reason']}")


def main() -> int:
    """命令行入口：判定当前会话/视图类型。"""
    enable_dpi_awareness()
    try:
        result = get_current_chat_type()
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print_result(result)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
