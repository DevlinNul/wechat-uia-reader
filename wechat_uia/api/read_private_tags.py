"""读取当前私聊联系人的标签（公开原子操作）。

API 层只负责编排：

1. 选择目标窗口并显式唤醒；
2. 扫描一次并校验当前是私聊；
3. 调用 core 的侧栏/资料页原语；
4. 读取标签；
5. 关闭资料页和侧栏，恢复前台窗口。

具体点击、等待和标签拆分在 :mod:`wechat_uia.core.read_private_tags`。
"""

import argparse
import json
import sys

import win32gui

from ..cli_common import add_surface_arguments
from ..core.classify import classify_snapshot
from ..core.errors import (
    ChatMismatchError,
    ContactProfileUnavailableError,
    NotPrivateChatError,
    WeChatUIAError,
    WindowUnavailableError,
)
from ..core.get_hwnd import activate_window
from ..core.read_private_tags import (
    click_chat_info,
    click_member_avatar,
    close_chat_info,
    split_tags,
    wait_for_panel,
    wait_for_profile,
)
from ..core.session import normalize_expected_session
from ..core.snapshot import (
    close_profile_popup,
    find_profile_popup,
    read_profile_contact,
    read_profile_fields,
    scan_window,
)
from ..core.surface import probe_surface, resolve_surface, selection_metadata
from ..core.ui_actions import ensure_ready
from ..core.uia_utils import enable_dpi_awareness

__all__ = ["read_private_tags"]


def read_private_tags(
    hwnd: int | None = None,
    chat_name: str | None = None,
    *,
    session: dict | None = None,
    strict: bool = False,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> dict:
    """读取当前私聊联系人的标签。

    可传入 ``chat_name`` 按名称选择窗口，或传入 ``session`` 锁定主窗口 /
    独立窗口。``strict=True`` 时，多个窗口匹配目标会话会直接报错。

    返回::

        {
            "chat_name": "张三",
            "contact": "张三",
            "tags": ["大学同学"],
            "raw_tags": ["大学同学"],
            "hwnd": 123456,
            "window_state": "normal",
            "window_role": "main",
            "window_automation_id": null,
            "layout": "wide",
            "selection_reason": "unique_chat_name",
            "ambiguous": false,
            "candidate_hwnds": [123456],
        }

    没有标签时 ``tags`` 和 ``raw_tags`` 都是空列表。当前不是私聊，或
    私聊没有资料页入口时，抛出 ``NotPrivateChatError`` /
    ``ContactProfileUnavailableError``。
    """
    expected = None
    if chat_name is not None or session is not None:
        expected = normalize_expected_session(chat_name, session)

    resolved = resolve_surface(
        hwnd=hwnd,
        chat_name=expected["chat_name"] if expected else None,
        session=session,
        strict=strict,
    )
    ensure_ready(
        resolved.hwnd,
        window_role=resolved.role,
        wake_minimized=wake_minimized,
        wake_tray=wake_tray,
    )
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
            click_chat_info(snapshot)
            panel_clicked = True
            panel_snapshot = wait_for_panel(snapshot.hwnd)
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

        click_member_avatar(panel_snapshot)
        profile = wait_for_profile(snapshot.hwnd)
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
                close_chat_info(
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
        "window_role": getattr(snapshot, "window_kind", None),
        "window_automation_id": getattr(
            snapshot, "window_automation_id", None
        ),
        "layout": getattr(snapshot, "layout", None),
        **selection_metadata(resolved),
    }


def print_result(result: dict) -> None:
    """按 CLI 格式打印联系人标签。"""
    print(f"会话   : {result['chat_name']!r}")
    print(f"联系人 : {result['contact']!r}")
    tags = ", ".join(result["tags"])
    print(f"标签   : {tags or '（无）'}")
    window_role = result.get("window_role") or "unknown"
    window_id = result.get("window_automation_id")
    print(
        f"窗口   : {window_role}"
        + (f" ({window_id})" if window_id else "")
    )


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="读取当前私聊联系人的标签（会临时打开聊天信息和资料页）"
    )
    add_surface_arguments(parser)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="多个窗口匹配目标会话时直接报错，不自动选择",
    )
    parser.add_argument(
        "--wake-minimized",
        action="store_true",
        help="允许自动还原最小化的微信窗口（默认不允许）",
    )
    parser.add_argument(
        "--wake-tray",
        action="store_true",
        help=(
            "允许通过微信原生托盘回调唤起隐藏到托盘的主窗口"
            "（默认不允许；独立窗口请手动还原）"
        ),
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser


def main() -> int:
    """命令行入口。"""
    args = build_parser().parse_args()
    enable_dpi_awareness()
    try:
        result = read_private_tags(
            hwnd=args.hwnd,
            chat_name=args.chat,
            strict=args.strict,
            wake_minimized=args.wake_minimized,
            wake_tray=args.wake_tray,
        )
    except WeChatUIAError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
