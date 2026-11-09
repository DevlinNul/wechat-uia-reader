"""联系人标签读取的底层界面原语。

只负责：拆分标签、等待聊天信息侧栏/资料页、点击“聊天信息”和联系人头像、
收起侧栏。选窗口、唤醒、扫描、私聊校验和结果组装由
:mod:`wechat_uia.api.read_private_tags` 负责。

本模块不主动选窗口，也不生成最终结果。
"""

import time

from .errors import ContactProfileUnavailableError
from .get_hwnd import click_point
from .snapshot import find_profile_popup, has_chat_info_panel, scan_window
from .uia_utils import CONTACT_HEAD_CLASS, iter_descendants

PANEL_WAIT_TIMEOUT = 2.0
PROFILE_WAIT_TIMEOUT = 3.0
CLOSE_WAIT_TIMEOUT = 2.0
POLL_INTERVAL = 0.1

TAG_SEPARATORS = (",", "，", "、", ";", "；")


def split_tags(raw_tags) -> list[str]:
    """把资料页里的标签文本拆成去重后的标签列表。

    微信会把多个标签放在同一个文本控件里，例如
    ``"高中同学,大学同学"``。这里同时兼容中英文逗号、顿号和分号。
    """
    tags = []
    for raw in raw_tags or []:
        value = str(raw or "")
        for separator in TAG_SEPARATORS:
            value = value.replace(separator, ",")
        for part in value.split(","):
            part = part.strip()
            if part:
                tags.append(part)
    return list(dict.fromkeys(tags))


def _center(control) -> tuple[int, int]:
    """返回控件中心点。"""
    rect = control.BoundingRectangle
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def _wait_until(predicate, timeout: float) -> bool:
    """轮询直到 predicate 为真；超时返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return False


def wait_for_panel(hwnd: int):
    """等待聊天信息侧栏出现，返回新快照；超时返回 None。

    等待期间只做局部探测，出现后再完整扫描一次。
    """
    if not _wait_until(
        lambda: has_chat_info_panel(hwnd), PANEL_WAIT_TIMEOUT
    ):
        return None
    try:
        return scan_window(hwnd)
    except Exception:
        return None


def wait_for_profile(hwnd: int):
    """等待联系人资料弹窗出现；超时返回 None。"""
    deadline = time.monotonic() + PROFILE_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        root = find_profile_popup(hwnd)
        if root is not None:
            return root
        time.sleep(POLL_INTERVAL)
    return None


def click_chat_info(snapshot) -> None:
    """点击“聊天信息”按钮。"""
    button = getattr(snapshot, "chat_info_button", None)
    if button is None:
        raise ContactProfileUnavailableError(
            "找不到“聊天信息”按钮；当前私聊可能不支持联系人资料页"
        )
    x, y = _center(button)
    click_point(x, y, snapshot.hwnd, what="聊天信息按钮")


def click_member_avatar(snapshot) -> None:
    """点击私聊资料入口里的联系人头像。"""
    cell = getattr(snapshot, "member_cell", None)
    if cell is None:
        raise ContactProfileUnavailableError(
            "聊天信息里没有联系人资料入口；当前会话可能不是普通私聊"
        )
    avatar = None
    for el in iter_descendants(cell):
        try:
            if el.ClassName == CONTACT_HEAD_CLASS:
                avatar = el
                break
        except Exception:
            continue
    target = avatar or cell
    x, y = _center(target)
    click_point(x, y, snapshot.hwnd, what="联系人头像")


def close_chat_info(hwnd: int, button=None) -> bool:
    """如果聊天信息侧栏还开着，就再点一次按钮把它收起来。

    ``button`` 可由调用方从已有快照传入，避免重复整树扫描；关闭后只用
    局部探测确认侧栏是否消失。
    """
    if button is None:
        try:
            snapshot = scan_window(hwnd)
        except Exception:
            return False
        if not getattr(snapshot, "chat_info_open", False):
            return True
        button = getattr(snapshot, "chat_info_button", None)
    if button is None:
        return False
    try:
        x, y = _center(button)
        click_point(x, y, hwnd, what="聊天信息按钮")
    except Exception:
        return False
    return _wait_until(
        lambda: not has_chat_info_panel(hwnd),
        CLOSE_WAIT_TIMEOUT,
    )
