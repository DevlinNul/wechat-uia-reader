"""会话身份提取与匹配。

只做数据比较，不访问窗口、不点击、不发送。

主窗口和独立窗口的身份来源不同：

- 主窗口宽布局：会话列表选中项的 AutomationId / RuntimeId；
- 主窗口窄布局：没有强身份，只能比较名称、类型、成员数；
- 独立窗口：根 AutomationId，例如 ``ChatSingleWindowfilehelper``。

本模块不负责选窗口；选窗口由 :mod:`wechat_uia.core.surface` 负责。
"""

from .classify import classify_snapshot
from .errors import ChatMismatchError
from .snapshot import selected_session_identity


def session_identity_from_snapshot(snapshot, classification: dict | None = None) -> dict:
    """从快照提取可用于会话匹配的身份信息。

    包含名称、类型、成员数、窗口角色、独立窗口根 id，以及主窗口选中
    会话项的 AutomationId / RuntimeId。RuntimeId 是本次微信运行内的
    UIA 标识，不是持久 ID。
    """
    result = classification or classify_snapshot(snapshot)
    selected = selected_session_identity(getattr(snapshot, "session_list", None))
    return {
        "chat_name": getattr(snapshot, "chat_name", None),
        "type": result["type"],
        "label": result["label"],
        "member_count": result["member_count"],
        "can_send": result["can_send"],
        "window_role": getattr(snapshot, "window_kind", None),
        "window_automation_id": getattr(snapshot, "window_automation_id", None),
        "layout": getattr(snapshot, "layout", None),
        "selected_session_name": selected["name"] if selected else None,
        "session_automation_id": selected["automation_id"] if selected else None,
        "session_runtime_id": selected["runtime_id"] if selected else None,
        "reason": result["reason"],
    }


def normalize_expected_session(
    chat_name: str | None, session: dict | None
) -> dict:
    """合并 chat_name 与 session，返回统一期望身份。

    session 优先；chat_name 若同时提供，必须与 session['chat_name'] 一致。
    最终结果必须包含非空 chat_name。
    """
    expected: dict = dict(session or {})
    if chat_name is not None:
        name = chat_name.strip()
        if not name:
            raise ValueError("chat_name 不能为空")
        existing = (expected.get("chat_name") or "").strip()
        if existing and existing != name:
            raise ValueError("chat_name 与 session['chat_name'] 不一致")
        expected["chat_name"] = name
    else:
        name = (expected.get("chat_name") or "").strip()
        if not name:
            raise ValueError("chat_name 或 session['chat_name'] 不能为空")
        expected["chat_name"] = name
    return expected


def _normalize_identity_value(value):
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return value


def _has_main_identity(expected: dict) -> bool:
    """session 是否带有主窗口特有的强身份。"""
    return any(
        expected.get(key)
        for key in ("session_automation_id", "session_runtime_id")
    )


def _has_detached_identity(expected: dict) -> bool:
    """session 是否带有独立窗口特有的强身份。"""
    return bool(expected.get("window_automation_id"))


def session_mismatches(
    expected: dict,
    actual: dict,
    *,
    allow_layout_change: bool = False,
    allow_window_change: bool = False,
) -> list[str]:
    """比较期望与实际会话身份，返回不一致描述列表。

    默认拒绝跨窗口类型使用强身份：

    - 主窗口 session 拿到独立窗口用；
    - 独立窗口 session 拿到主窗口用；
    - 宽布局主窗口 session 拿到窄布局主窗口用。

    只有显式传入 ``allow_layout_change`` / ``allow_window_change`` 时才
    允许降级比较。
    """
    mismatch: list[str] = []

    chat_name = expected.get("chat_name")
    if chat_name is not None and str(chat_name).strip():
        actual_name = (actual.get("chat_name") or "").strip()
        if actual_name != str(chat_name).strip():
            mismatch.append(
                f"会话名: 期望 {chat_name!r}，实际 {actual.get('chat_name')!r}"
            )

    for key, label in (("type", "会话类型"), ("member_count", "成员数")):
        if key not in expected:
            continue
        want = expected[key]
        if want is None:
            continue
        got = actual.get(key)
        if _normalize_identity_value(want) != _normalize_identity_value(got):
            mismatch.append(f"{label}: 期望 {want!r}，实际 {got!r}")

    actual_role = actual.get("window_role")
    expected_role = expected.get("window_role")
    actual_layout = actual.get("layout")
    actual_detached_id = actual.get("window_automation_id")
    expected_detached_id = expected.get("window_automation_id")

    # 独立窗口强身份
    if expected_detached_id:
        if actual_role != "chat" and not allow_window_change:
            mismatch.append(
                "窗口身份: session 来自独立窗口，当前目标不是独立窗口"
            )
        elif actual_detached_id != expected_detached_id:
            mismatch.append(
                f"窗口身份: 期望 {expected_detached_id!r}，"
                f"实际 {actual_detached_id!r}"
            )
    elif actual_detached_id and not allow_window_change:
        # 实际是独立窗口，但 session 没有独立窗口 id：
        # 如果 session 来自主窗口，也属于跨窗口。
        if _has_main_identity(expected) or expected_role == "main":
            mismatch.append(
                "窗口身份: session 来自主窗口，不能用于独立窗口"
            )

    # 主窗口强身份
    if _has_main_identity(expected):
        if actual_role != "main" and not allow_window_change:
            mismatch.append(
                "窗口身份: session 来自主窗口，当前目标不是主窗口"
            )
        elif (
            actual_layout == "narrow"
            and not allow_layout_change
        ):
            mismatch.append(
                "窗口布局: 主窗口已进入窄布局，选中项强身份不可用"
            )
        else:
            for key, label in (
                ("selected_session_name", "选中会话名"),
                ("session_automation_id", "会话 AutomationId"),
                ("session_runtime_id", "会话 RuntimeId"),
            ):
                want = expected.get(key)
                if want is None:
                    continue
                got = actual.get(key)
                if _normalize_identity_value(want) != _normalize_identity_value(got):
                    mismatch.append(
                        f"{label}: 期望 {want!r}，实际 {got!r}"
                    )

    return mismatch


def assert_session(
    expected: dict,
    snapshot,
    phase: str = "",
    *,
    classification: dict | None = None,
    allow_layout_change: bool = False,
    allow_window_change: bool = False,
) -> dict:
    """校验快照仍然是期望会话；不一致时抛 ChatMismatchError。

    返回实际身份，方便调用方复用。
    """
    actual = session_identity_from_snapshot(
        snapshot, classification=classification
    )
    mismatches = session_mismatches(
        expected,
        actual,
        allow_layout_change=allow_layout_change,
        allow_window_change=allow_window_change,
    )
    if mismatches:
        prefix = f"{phase}：" if phase else ""
        raise ChatMismatchError(prefix + "；".join(mismatches))
    return actual
