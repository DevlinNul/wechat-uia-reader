"""判定当前微信会话/视图的类型与可发送性。

只做判定，不点击、不发送、不改变窗口状态，也不负责选窗口。
消费 :class:`wechat_uia.core.snapshot.ChatSnapshot`：

- 私聊/群聊：有聊天输入框，可发送消息
- 公众号/服务号会话（official）：有输入框，可发送
- 公众号推送（broadcast）：单向推送，无输入框，不可发送
- 服务通知（notice）：单聊样式但没有输入框，不可发送
- 公众号聚合页（feed）：文章列表页，不是会话，不可发送
- 列表页（list）：折叠的聊天、服务号、服务号子列表等，不是会话，不可发送

判定时会通过 ``snapshot.title_bar`` 再读标题栏子树的类名和叶文本，
这是额外的 UIA 调用；标题栏缺失时不做这次读取。
"""

import re

from .uia_utils import (
    BRAND_TITLE_FEATURES,
    CHAT_NAME_SUFFIX,
    GROUP_TITLE_FEATURE,
    SINGLE_TITLE_FEATURE,
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


def parse_member_count_texts(texts, chat_name: str | None) -> int | None:
    """从 ``(文本, AutomationId)`` 序列里解析群成员数。

    名称本身可能是 "(1)"、"(2)"，因此必须排除会话名控件，再找纯成员数
    文本，不能按文本模式猜。快速路径和完整快照共用这一份规则。
    """
    for text, automation_id in texts:
        text = (text or "").strip()
        if not text:
            continue
        if text == chat_name or (automation_id or "").endswith(
            CHAT_NAME_SUFFIX
        ):
            continue
        match = MEMBER_COUNT_RE.match(text)
        if match:
            return int(match.group(1))
    return None


def parse_member_count(title_bar, chat_name: str) -> int | None:
    """从标题栏中解析群成员数。

    依据结构：群聊标题栏有“名称”“(N)”两个独立文本控件；
    私聊只有名称一个控件。
    """
    if title_bar is None:
        return None
    return parse_member_count_texts(
        (
            (el.Name, el.AutomationId)
            for el in iter_leaf_text_controls(title_bar)
        ),
        chat_name,
    )


def collect_class_names(control) -> list[str]:
    """收集控件及其后代的类名，用于视图类型兜底判断。"""
    if control is None:
        return []
    names = [control.ClassName]
    names.extend(el.ClassName for el in iter_descendants(control))
    return names


def classify_title_bar(
    *,
    has_chat_name: bool,
    chat_name: str | None,
    has_input: bool,
    is_brand_page: bool,
    list_title: str | None,
    class_names,
    member_count: int | None,
) -> dict:
    """纯判定：只看已经提取好的字段，不访问 UIA。

    完整快照和快速路径都调用这里，保证同一套规则只有一份实现。
    """
    if not has_chat_name:
        if list_title is not None:
            return _result(
                LIST,
                list_title,
                None,
                False,
                f"带返回按钮的列表页（{list_title}）",
            )
        return _result(
            UNKNOWN,
            None,
            None,
            False,
            "未找到会话名称控件（可能不在聊天页）",
        )

    name = chat_name or ""

    # 公众号消息聚合页：有名称标签，但展示的是文章列表，不是会话
    if is_brand_page:
        return _result(
            FEED,
            name,
            None,
            False,
            "公众号消息聚合页（文章列表，不可发送）",
        )

    if member_count is not None:
        return _result(
            GROUP,
            name,
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

    return _result(result, name, None, has_input, reason)


def attach_window_info(
    result: dict,
    *,
    hwnd: int,
    window_state: str,
    window_kind: str | None,
    window_automation_id: str | None,
    layout: str | None,
) -> dict:
    """给判定结果补上窗口信息，保证各入口返回同一形状。"""
    merged = dict(result)
    merged["hwnd"] = hwnd
    merged["window_state"] = window_state
    merged["window_role"] = window_kind
    merged["window_automation_id"] = window_automation_id
    merged["layout"] = layout
    return merged


def classify_snapshot(snapshot) -> dict:
    """基于控件快照判定类型与可发送性。

    会通过 ``snapshot.title_bar`` 读取标题栏子树的类名和叶文本，属于
    额外 UIA 调用；不点击、不发送、不改变窗口状态。
    """
    if snapshot.chat_name_element is None:
        return classify_title_bar(
            has_chat_name=False,
            chat_name=None,
            has_input=snapshot.has_input,
            is_brand_page=snapshot.is_brand_page,
            list_title=snapshot.list_title,
            class_names=(),
            member_count=None,
        )

    chat_name = snapshot.chat_name or ""
    title_bar = snapshot.title_bar

    if snapshot.is_brand_page:
        return classify_title_bar(
            has_chat_name=True,
            chat_name=chat_name,
            has_input=snapshot.has_input,
            is_brand_page=True,
            list_title=snapshot.list_title,
            class_names=(),
            member_count=None,
        )

    return classify_title_bar(
        has_chat_name=True,
        chat_name=chat_name,
        has_input=snapshot.has_input,
        is_brand_page=False,
        list_title=snapshot.list_title,
        class_names=collect_class_names(title_bar),
        member_count=parse_member_count(title_bar, chat_name),
    )


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
