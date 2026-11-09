"""读取当前私聊联系人的标签（core 能力）。

路径：

1. 校验当前会话是私聊；
2. 打开“聊天信息”侧栏；
3. 点击联系人头像，打开独立的资料页弹窗；
4. 读取“标签”字段，按逗号拆分；
5. 关闭资料页和侧栏，恢复原来的前台窗口。

这个模块只读取资料页，不发送消息、不修改输入框。打开侧栏和资料页会
临时改变界面，操作结束后会尽量恢复。
"""

import argparse
import json
import sys
import time

import win32gui

from .errors import (
    ChatMismatchError,
    ContactProfileUnavailableError,
    NotPrivateChatError,
    WeChatUIAError,
    WindowUnavailableError,
)
from .classify import classify_snapshot
from .get_hwnd import activate_window, click_point
from .snapshot import (
    close_profile_popup,
    find_profile_popup,
    has_chat_info_panel,
    read_profile_contact,
    read_profile_fields,
    scan_window,
)
from .surface import probe_surface, resolve_surface
from .uia_utils import (
    CONTACT_HEAD_CLASS,
    enable_dpi_awareness,
    iter_descendants,
)

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


def _wait_for_panel(hwnd: int):
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


def _wait_for_profile(hwnd: int):
    """等待联系人资料弹窗出现；超时返回 None。"""
    deadline = time.monotonic() + PROFILE_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        root = find_profile_popup(hwnd)
        if root is not None:
            return root
        time.sleep(POLL_INTERVAL)
    return None


def _click_chat_info(snapshot) -> None:
    """点击“聊天信息”按钮。"""
    button = getattr(snapshot, "chat_info_button", None)
    if button is None:
        raise ContactProfileUnavailableError(
            "找不到“聊天信息”按钮；当前私聊可能不支持联系人资料页"
        )
    x, y = _center(button)
    click_point(x, y, snapshot.hwnd, what="聊天信息按钮")


def _click_member_avatar(snapshot) -> None:
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


def _close_chat_info(hwnd: int, button=None) -> bool:
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


def read_private_tags(
    hwnd: int | None = None, chat_name: str | None = None
) -> dict:
    """读取当前私聊联系人的标签。

    返回::

        {
            "chat_name": "张三",
            "contact": "张三",
            "tags": ["大学同学"],
            "raw_tags": ["大学同学"],
            "hwnd": 123456,
            "window_state": "normal",
        }

    没有标签时 ``tags`` 和 ``raw_tags`` 都是空列表。当前不是私聊，或
    私聊没有资料页入口时，抛出 ``NotPrivateChatError`` /
    ``ContactProfileUnavailableError``。
    """
    resolved = resolve_surface(hwnd=hwnd, chat_name=chat_name)
    snapshot = scan_window(resolved.hwnd)
    result = classify_snapshot(snapshot)
    if result.get("type") != "private":
        raise NotPrivateChatError(
            f"当前会话不是私聊：{result.get('chat_name')!r}"
            f"（{result.get('label')} / {result.get('type')}）"
        )

    chat_name = (snapshot.chat_name or "").strip()
    if not chat_name:
        raise NotPrivateChatError("无法读取当前私聊名称")

    if find_profile_popup(snapshot.hwnd) is not None:
        raise ContactProfileUnavailableError(
            "检测到已打开的联系人资料页；请先关闭后再读取标签"
        )

    original_foreground = 0
    try:
        original_foreground = int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        pass

    panel_clicked = False
    panel_snapshot = None
    profile = None
    contact = chat_name
    raw_tags = []
    tags = []
    try:
        if not activate_window(snapshot.hwnd):
            raise WindowUnavailableError("无法激活微信窗口")

        # 用户可能已经把聊天信息侧栏打开了。此时直接复用，不要先点一次
        # 把它关掉；结束后也保持它原来的打开状态。
        if getattr(snapshot, "chat_info_open", False):
            panel_snapshot = snapshot
        else:
            _click_chat_info(snapshot)
            panel_clicked = True
            panel_snapshot = _wait_for_panel(snapshot.hwnd)
            if panel_snapshot is None:
                raise ContactProfileUnavailableError(
                    "聊天信息侧栏没有打开；无法读取联系人标签"
                )

        cell = getattr(panel_snapshot, "member_cell", None)
        if cell is None:
            raise ContactProfileUnavailableError(
                "当前私聊没有联系人资料入口；无法读取标签"
            )
        contact = (cell.Name or "").strip() or chat_name

        _click_member_avatar(panel_snapshot)
        profile = _wait_for_profile(snapshot.hwnd)
        if profile is None:
            raise ContactProfileUnavailableError(
                "联系人资料页没有打开；无法读取标签"
            )

        profile_contact = read_profile_contact(profile)
        if profile_contact and contact and profile_contact != contact:
            raise ChatMismatchError(
                f"资料页联系人 {profile_contact!r} 与当前会话 "
                f"{contact!r} 不一致"
            )

        fields = read_profile_fields(profile)
        raw_tags = list(fields.get("标签", []))
        tags = split_tags(raw_tags)
    finally:
        if profile is not None:
            try:
                close_profile_popup(profile)
            except Exception:
                pass
        if panel_clicked:
            try:
                _close_chat_info(
                    snapshot.hwnd,
                    button=getattr(panel_snapshot, "chat_info_button", None),
                )
            except Exception:
                pass
        if original_foreground:
            try:
                if win32gui.GetForegroundWindow() != original_foreground:
                    win32gui.SetForegroundWindow(original_foreground)
            except Exception:
                pass

    after_name = None
    after_surface = probe_surface(snapshot.hwnd)
    if after_surface is not None:
        after_name = (after_surface.chat_name or "").strip()
    if after_name != chat_name:
        raise ChatMismatchError(
            f"读取标签期间会话已切换：{chat_name!r} -> "
            f"{after_name!r}"
        )

    return {
        "chat_name": chat_name,
        "contact": contact,
        "tags": tags,
        "raw_tags": raw_tags,
        "hwnd": snapshot.hwnd,
        "window_state": snapshot.window_state,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="读取当前私聊联系人的标签（会临时打开聊天信息和资料页）"
    )
    parser.add_argument(
        "--chat",
        help="可选：目标会话名；只用于选择窗口，不会切换会话",
    )
    parser.add_argument(
        "--hwnd",
        type=lambda value: int(value, 0),
        help="可选：目标窗口句柄；支持十进制或 0x 十六进制",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser


def main() -> int:
    """命令行入口。"""
    args = build_parser().parse_args()
    enable_dpi_awareness()
    try:
        result = read_private_tags(hwnd=args.hwnd, chat_name=args.chat)
    except WeChatUIAError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"会话   : {result['chat_name']!r}")
        print(f"联系人 : {result['contact']!r}")
        tags = ", ".join(result["tags"])
        print(f"标签   : {tags or '（无）'}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
