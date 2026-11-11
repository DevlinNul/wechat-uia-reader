"""在已有快照上完成“校验会话 + 写入输入框”的原子操作。

不选窗口、不扫描控件树；由 API 层传入一次扫描得到的快照。
"""

from .classify import classify_snapshot
from .errors import (
    CannotSendError,
    ChatMismatchError,
    InputFillError,
    InputNotEmptyError,
    UIAUnavailableError,
)
from .input import (
    fill_with_sendinput,
    fill_with_value_pattern,
    read_input_text,
)
from .session import session_identity_from_snapshot, session_mismatches
from .snapshot import (
    scan_window,
    selected_session_identity,
    session_name_is_unique,
    set_value_pattern,
)


def _session_check_level(expected: dict, snapshot) -> str:
    """决定本次会话校验的强度标签。"""
    if expected.get("window_automation_id"):
        return "window_identity"
    if any(
        expected.get(key)
        for key in ("session_automation_id", "session_runtime_id")
    ):
        return "strong"
    unique = session_name_is_unique(
        getattr(snapshot, "session_list", None), expected["chat_name"]
    )
    if unique is False:
        raise ChatMismatchError(
            f"会话名 {expected['chat_name']!r} 不唯一，"
            "无法仅凭名称确定 session；"
            "请传入 session_automation_id 或 session_runtime_id"
        )
    # None：主窗口窄布局或独立窗口没有会话列表，只能校验名称
    return "name_unique" if unique else "name_only"


def fill_snapshot(
    snapshot,
    expected: dict,
    text: str,
    *,
    classification: dict | None = None,
) -> dict:
    """校验快照并写入输入框，返回写入结果。

    前置条件：输入框在 str.strip() 意义下为空。会话不匹配抛
    ChatMismatchError，输入框已有内容抛 InputNotEmptyError，两种写入
    方式都失败时抛 InputFillError。
    """
    if not snapshot.uia_available:
        raise UIAUnavailableError(
            "微信 UI Automation 控件树不可用（可能未物化，或微信版本不匹配）"
        )

    kind = getattr(snapshot, "window_kind", None) or "unknown"
    if kind == "history":
        raise CannotSendError(
            "当前是聊天记录窗口，它是只读的，不能写入聊天输入框"
        )

    current_name = (snapshot.chat_name or "").strip()
    if current_name != expected["chat_name"]:
        raise ChatMismatchError(
            f"当前会话是 {current_name!r}，不是指定的 {expected['chat_name']!r}"
        )

    session_check = _session_check_level(expected, snapshot)

    chat = classification or classify_snapshot(snapshot)
    actual = {
        "chat_name": current_name,
        "type": chat.get("type"),
        "member_count": chat.get("member_count"),
        "window_role": getattr(snapshot, "window_kind", None),
        "window_automation_id": getattr(snapshot, "window_automation_id", None),
        "layout": getattr(snapshot, "layout", None),
    }
    if any(
        key in expected
        for key in (
            "selected_session_name",
            "session_automation_id",
            "session_runtime_id",
            "window_automation_id",
            "window_role",
        )
    ):
        selected = selected_session_identity(
            getattr(snapshot, "session_list", None)
        )
        actual["selected_session_name"] = selected["name"] if selected else None
        actual["session_automation_id"] = (
            selected["automation_id"] if selected else None
        )
        actual["session_runtime_id"] = (
            selected["runtime_id"] if selected else None
        )

    mismatches = session_mismatches(expected, actual)
    if mismatches:
        raise ChatMismatchError(
            "当前会话与指定会话不一致：" + "；".join(mismatches)
        )

    if not chat.get("can_send"):
        raise CannotSendError(
            f"当前会话不可发送消息：{expected['chat_name']!r}"
            f"（{chat.get('label')} / {chat.get('type')}）"
        )

    input_field = snapshot.input_field
    if input_field is None:
        raise CannotSendError(
            f"未找到聊天输入框：{expected['chat_name']!r}"
        )

    current_text = read_input_text(input_field)
    if current_text is None:
        raise InputFillError("无法读取输入框内容，不能确认输入框为空")
    if current_text.strip():
        raise InputNotEmptyError(
            f"输入框已有内容，拒绝覆盖或追加：{current_text!r}"
        )

    if fill_with_value_pattern(input_field, text):
        method = "value_pattern"
    else:
        # 回退到 SendInput 会激活窗口并模拟键盘输入。写入前重新扫描并
        # 复核会话，避免用户切换会话后把内容敲进另一个会话。
        try:
            latest_snapshot = scan_window(snapshot.hwnd)
            latest_classification = classify_snapshot(latest_snapshot)
            latest_identity = session_identity_from_snapshot(
                latest_snapshot, latest_classification
            )
            latest_mismatches = session_mismatches(expected, latest_identity)
        except Exception as exc:
            raise ChatMismatchError(
                f"无法在回退写入前确认当前会话，已中止：{exc}"
            ) from exc

        if latest_mismatches:
            raise ChatMismatchError(
                "会话在回退写入前已切换，已中止，未执行 SendInput："
                + "；".join(latest_mismatches)
            )

        snapshot = latest_snapshot
        input_field = snapshot.input_field
        if input_field is None:
            raise CannotSendError(
                f"未找到聊天输入框：{expected['chat_name']!r}"
            )

        if fill_with_sendinput(snapshot.hwnd, input_field, text):
            method = "sendinput"
        else:
            try:
                set_value_pattern(input_field, "")
            except Exception:
                pass
            raise InputFillError(
                "无法把文本填入聊天输入框（ValuePattern 和 SendInput 均失败）"
            )

    return {
        "hwnd": snapshot.hwnd,
        "chat_name": expected["chat_name"],
        "text": text,
        "method": method,
        "session_check": session_check,
        "verified": True,
        "window_role": getattr(snapshot, "window_kind", None),
        "window_automation_id": getattr(snapshot, "window_automation_id", None),
        "layout": getattr(snapshot, "layout", None),
    }
