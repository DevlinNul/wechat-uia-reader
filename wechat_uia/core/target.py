"""会话目标的进程内缓存：准备一次，后续复用。

前台应用会连续操作同一个窗口和会话，每次重新选窗口、遍历控件树是纯浪费。
这里缓存只读信息：窗口选择结果、快照、类型判定，以及输入框、消息列表、
发送按钮、会话名控件的引用。

复用前必须复核：

- 同一个线程；
- 窗口还存在且状态正常；
- 会话名控件仍是同一个控件、文本没变；
- 输入框、消息列表、发送按钮的 RuntimeId 没变；
- 请求的会话名与缓存的一致。

任何一项不满足就重新选窗口并完整扫描，不猜。会话身份（选中项 id、成员数
等）仍由 ``fill_snapshot`` / ``assert_session`` 在每次调用时校验。

默认选择（不传 hwnd）时跟随前台：前台切到另一个微信窗口就重新选择。
``strict=True`` 或带 window/session 身份的 ``session`` 不走缓存，避免跳过
多窗口歧义判断。
"""

import threading
from dataclasses import dataclass

import win32gui

from .classify import classify_snapshot
from .element_ref import ElementRef, capture, still_valid, unchanged
from .get_hwnd import is_wechat_window, require_window_ready, window_state
from .snapshot import ChatSnapshot, scan_window
from .surface import ResolvedSurface, resolve_surface

MAX_CACHED = 4

_entries: dict[int, "Target"] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Target:
    """一次准备的结果：选窗口、快照、判定和用于复核的控件引用。"""

    hwnd: int
    resolved: ResolvedSurface
    snapshot: ChatSnapshot
    classification: dict
    chat_label: ElementRef | None
    input_ref: ElementRef | None
    message_list_ref: ElementRef | None
    send_button_ref: ElementRef | None
    thread_id: int


def reset_cache() -> None:
    """清空缓存；换会话或测试时使用。"""
    with _lock:
        _entries.clear()


def _resolve(**kwargs):
    """选窗口的薄包装，便于测试替换。"""
    return resolve_surface(**kwargs)


def _scan(hwnd: int):
    return scan_window(hwnd)


def _ensure_ready(hwnd: int, **kwargs) -> int:
    return require_window_ready(hwnd, **kwargs)


def _window_state(hwnd: int) -> str:
    return window_state(hwnd)


def _window_exists(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


def _window_still_target(hwnd: int) -> bool:
    """默认选择时，前台切到别的微信窗口就重新选。"""
    foreground = win32gui.GetForegroundWindow()
    if foreground == hwnd:
        return True
    try:
        return not is_wechat_window(foreground)
    except Exception:
        return True


def _build(resolved: ResolvedSurface, snapshot: ChatSnapshot) -> Target:
    return Target(
        hwnd=snapshot.hwnd,
        resolved=resolved,
        snapshot=snapshot,
        classification=classify_snapshot(snapshot),
        chat_label=capture(snapshot.chat_name_element),
        input_ref=capture(snapshot.input_field),
        message_list_ref=capture(snapshot.message_list),
        send_button_ref=capture(snapshot.send_button),
        thread_id=threading.get_ident(),
    )


def _usable(target: Target, chat_name: str | None) -> bool:
    """缓存是否还能代表同一个窗口里的同一个会话。"""
    if target.thread_id != threading.get_ident():
        return False
    if not _window_exists(target.hwnd):
        return False
    try:
        if _window_state(target.hwnd) != "normal":
            return False
    except Exception:
        return False

    current_name = target.snapshot.chat_name or ""
    if not unchanged(target.chat_label, current_name):
        return False
    if chat_name is not None and current_name != chat_name:
        return False

    if target.snapshot.input_field is not None and not still_valid(
        target.input_ref
    ):
        return False
    if target.snapshot.message_list is not None and not still_valid(
        target.message_list_ref
    ):
        return False
    if target.snapshot.send_button is not None and not still_valid(
        target.send_button_ref
    ):
        return False
    return True


def _session_matches(target: Target, session: dict) -> bool:
    """缓存的窗口是否满足 session 里的窗口身份字段。"""
    detached_id = session.get("window_automation_id")
    if detached_id:
        return (
            target.snapshot.window_automation_id or ""
        ) == detached_id
    role = session.get("window_role")
    if role:
        return (target.snapshot.window_kind or "") == role
    if any(
        session.get(key)
        for key in ("session_automation_id", "session_runtime_id")
    ):
        return (target.snapshot.window_kind or "") == "main"
    return True


def _candidates(
    hwnd: int | None, chat_name: str | None, session: dict | None
) -> list[Target]:
    with _lock:
        if hwnd is not None:
            target = _entries.get(hwnd)
            return [target] if target is not None else []
        items = list(_entries.values())
    if chat_name is not None:
        items = [
            item
            for item in items
            if (item.snapshot.chat_name or "") == chat_name
        ]
    if session is not None:
        items = [item for item in items if _session_matches(item, session)]
    return items


def _lookup(
    hwnd: int | None, chat_name: str | None, session: dict | None
) -> Target | None:
    """找到唯一可复用的目标；有歧义时返回 None，交给重新选择。"""
    items = _candidates(hwnd, chat_name, session)
    if not items:
        return None
    if hwnd is not None or chat_name is not None:
        return items[0] if len(items) == 1 else None
    defaults = [item for item in items if _window_still_target(item.hwnd)]
    return defaults[0] if len(defaults) == 1 else None


def _store(target: Target) -> None:
    with _lock:
        _entries[target.hwnd] = target
        while len(_entries) > MAX_CACHED:
            oldest = next(iter(_entries))
            _entries.pop(oldest, None)


def _cache_allowed(hwnd: int | None, strict: bool) -> bool:
    """strict 且没有指定 hwnd 时不复用，保留多窗口歧义判断。"""
    return not (strict and hwnd is None)


def acquire(
    hwnd: int | None = None,
    chat_name: str | None = None,
    session: dict | None = None,
    *,
    strict: bool = False,
    wake_minimized: bool = False,
    wake_tray: bool = False,
    force: bool = False,
) -> Target:
    """返回可直接使用的 Target；缓存不可用时重新选择并扫描。"""
    cached = None
    if not force and _cache_allowed(hwnd, strict):
        cached = _lookup(hwnd, chat_name, session)
        if cached is not None and _usable(cached, chat_name):
            return cached

    resolved = _resolve(
        hwnd=hwnd,
        chat_name=chat_name,
        session=session,
        strict=strict,
    )
    _ensure_ready(
        resolved.hwnd,
        window_role=resolved.role,
        wake_minimized=wake_minimized,
        wake_tray=wake_tray,
    )
    snapshot = _scan(resolved.hwnd)
    target = _build(resolved, snapshot)
    _store(target)
    return target
