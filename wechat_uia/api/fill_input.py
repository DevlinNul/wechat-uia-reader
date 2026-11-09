"""把文本填入当前微信会话的聊天输入框（不发送）。

API 层只负责编排：解析参数、选择窗口、唤醒、扫描一次，然后调用
:func:`wechat_uia.core.fill.fill_snapshot` 完成校验和写入。

本模块只写输入框，不按 Enter、不点击发送、不切换会话。
"""

import argparse
import json
import sys

from ..cli_common import positive_int, runtime_id
from ..core.errors import WeChatUIAError
from ..core.fill import fill_snapshot
from ..core.input_text import normalize_text
from ..core.session import normalize_expected_session
from ..core.snapshot import scan_window
from ..core.surface import resolve_surface, selection_metadata
from ..core.ui_actions import ensure_ready
from ..core.uia_utils import enable_dpi_awareness

__all__ = ["fill_input"]


def fill_input(
    text: str,
    chat_name: str | None = None,
    hwnd: int | None = None,
    *,
    session: dict | None = None,
    strict: bool = False,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> dict:
    """把 text 填入当前会话输入框，返回写入结果。

    会话匹配支持两种方式：

    - ``chat_name``：只比较会话名，较弱；
    - ``session``：传入 ``get_session_identity()`` 的结果或同结构字典，
      可同时比较会话名、类型、成员数、窗口角色、窗口 id 和选中项 id。

    ``strict=True`` 时，多个窗口匹配目标会话会直接报错，不自动选择。

    输入框在 ``str.strip()`` 意义下必须为空。会话不匹配抛
    ChatMismatchError，输入框已有内容抛 InputNotEmptyError，不覆盖也不
    追加。两种写入方式都失败时抛 InputFillError。
    """
    normalized = normalize_text(text)
    expected = normalize_expected_session(chat_name, session)

    resolved = resolve_surface(
        hwnd=hwnd,
        chat_name=expected["chat_name"],
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
    result = fill_snapshot(snapshot, expected, normalized)
    result.update(selection_metadata(resolved))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把文本填入当前微信会话的聊天输入框（不发送）"
    )
    parser.add_argument(
        "--chat",
        required=True,
        help="目标会话名；必须与当前会话完全一致",
    )
    parser.add_argument(
        "--text",
        required=True,
        help="要填入的文本（支持中文和换行）",
    )
    parser.add_argument(
        "--hwnd",
        type=lambda value: int(value, 0),
        help="可选：目标窗口句柄；支持十进制或 0x 十六进制",
    )
    parser.add_argument(
        "--chat-type",
        help="可选：期望的会话类型，如 private / group / official",
    )
    parser.add_argument(
        "--member-count",
        type=positive_int,
        help="可选：期望的群成员数",
    )
    parser.add_argument(
        "--session-automation-id",
        help="可选：期望的选中会话项 AutomationId",
    )
    parser.add_argument(
        "--session-runtime-id",
        type=runtime_id,
        help="可选：期望的选中会话项 RuntimeId，逗号分隔的整数",
    )
    parser.add_argument(
        "--window-automation-id",
        help="可选：独立窗口根 AutomationId，用于精确锁定窗口",
    )
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
        help="允许通过微信原生托盘回调唤起隐藏到托盘的主窗口（默认不允许）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    enable_dpi_awareness()

    session = {"chat_name": args.chat}
    if args.chat_type:
        session["type"] = args.chat_type
    if args.member_count is not None:
        session["member_count"] = args.member_count
    if args.session_automation_id:
        session["session_automation_id"] = args.session_automation_id
    if args.session_runtime_id is not None:
        session["session_runtime_id"] = args.session_runtime_id
    if args.window_automation_id:
        session["window_automation_id"] = args.window_automation_id

    try:
        result = fill_input(
            args.text,
            session=session,
            hwnd=args.hwnd,
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
        print(f"会话     : {result['chat_name']!r}")
        print(f"填入方式 : {result['method']}")
        print(f"文本长度 : {len(result['text'])}")
        if result.get("session_check") == "name_only":
            print(
                "会话校验 : 只校验当前会话名"
                "（会话列表不可见，未验证重名）"
            )
        else:
            print("会话校验 : 通过")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
