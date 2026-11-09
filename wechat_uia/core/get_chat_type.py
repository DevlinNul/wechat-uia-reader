"""会话类型判定与身份提取的兼容入口。

实现已拆分到：

- :mod:`wechat_uia.core.classify`：类型与可发送性判定；
- :mod:`wechat_uia.core.session`：身份提取与匹配。

本模块保留原有导入路径和命令行用法，不复制实现。
"""

import json
import sys

from .classify import (
    BROADCAST,
    FEED,
    GROUP,
    LIST,
    NOTICE,
    OFFICIAL,
    PRIVATE,
    TYPE_LABELS,
    UNKNOWN,
    VIEW_TYPES,
    classify_snapshot,
    collect_class_names,
    iter_leaf_text_controls,
    parse_member_count,
)
from .errors import WindowUnavailableError
from .session import (
    assert_session,
    normalize_expected_session,
    session_identity_from_snapshot,
    session_mismatches,
)
from .snapshot import scan_window
from .surface import resolve_surface
from .uia_utils import enable_dpi_awareness


def get_session_identity(hwnd: int | None = None) -> dict:
    """返回当前会话的完整身份信息，供 fill_input 等下游做一致性校验。"""
    resolved = resolve_surface(hwnd=hwnd)
    return session_identity_from_snapshot(scan_window(resolved.hwnd))


def get_current_chat_type(hwnd: int | None = None) -> dict:
    """对外接口：返回当前会话/视图类型信息。

    窗口不可用时抛 WindowUnavailableError。
    """
    resolved = resolve_surface(hwnd=hwnd)
    snapshot = scan_window(resolved.hwnd)
    result = classify_snapshot(snapshot)
    result["hwnd"] = snapshot.hwnd
    result["window_state"] = snapshot.window_state
    result["window_role"] = snapshot.window_kind
    result["layout"] = snapshot.layout
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

    if "--identity" in sys.argv:
        try:
            identity = get_session_identity()
        except WindowUnavailableError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(identity, ensure_ascii=False, indent=2))
        return 0

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
