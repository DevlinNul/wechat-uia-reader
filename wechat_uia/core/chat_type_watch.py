"""会话类型的快速轮询路径。

轮询时绝大多数调用结果没有变化，所以这里分三层：

1. 缓存命中：只读上一次保存的会话名控件，比较 RuntimeId 和文本，
   通常不到 1 ms；
2. 缓存失效但结构正常：走 :func:`wechat_uia.core.snapshot.read_chat_header`
   的批量缓存读取，约几十毫秒；
3. 快速路径拿不到可靠结果：退回 ``scan_window`` + ``classify_snapshot``，
   和统一入口完全一致。

快速路径只读，不唤醒、不点击、不改变窗口状态。窗口角色和布局规则与统一
入口共用同一份实现，结果形状也由 ``classify.attach_window_info`` 统一。

注意：缓存在 ``ttl`` 内不会重新读取控件，期间最多滞后一个 ttl。需要立即
刷新时传 ``force=True``。
"""

import time
from dataclasses import dataclass

import win32gui

from .classify import (
    attach_window_info,
    classify_snapshot,
    classify_title_bar,
    parse_member_count_texts,
)
from .errors import WindowUnavailableError
from .get_hwnd import is_wechat_window
from .snapshot import read_chat_header, scan_window
from .surface import assert_target_role, resolve_surface

DEFAULT_TTL = 0.5


@dataclass
class _Cached:
    """上一次成功读取的结果和用于判断失效的引用。"""

    request_hwnd: int | None
    hwnd: int
    chat_name: str | None
    result: dict
    label: object | None
    runtime_id: tuple | None
    checked_at: float
    ttl: float


_cache: _Cached | None = None


def reset_cache() -> None:
    """清空缓存；测试和显式重置时使用。"""
    global _cache
    _cache = None


def _resolve_hwnd(hwnd: int | None, chat_name: str | None) -> int:
    """选择目标窗口；只在缓存失效时调用。

    显式 hwnd 不再做 probe_surface，角色校验交给 read_chat_header 按根
    类名完成，避免每次缓存过期都多花 100 ms 以上。
    """
    if hwnd is not None:
        return hwnd
    return resolve_surface(chat_name=chat_name).hwnd


def _read_header(hwnd: int):
    """返回 ``(result, label, runtime_id)``；快速路径不可用时返回 None。"""
    header = read_chat_header(hwnd)
    if header is None:
        return None

    assert_target_role(hwnd, header.window_kind)

    member_count = parse_member_count_texts(
        header.title_bar_texts, header.chat_name
    )
    result = classify_title_bar(
        has_chat_name=header.chat_name is not None,
        chat_name=header.chat_name,
        has_input=header.has_input,
        is_brand_page=header.is_brand_page,
        list_title=header.list_title,
        class_names=header.title_bar_classes,
        member_count=member_count,
    )
    result = attach_window_info(
        result,
        hwnd=header.hwnd,
        window_state=header.window_state,
        window_kind=header.window_kind,
        window_automation_id=header.window_automation_id,
        layout=header.layout,
    )

    label = header.chat_name_element
    runtime_id = None
    if label is not None:
        try:
            runtime_id = tuple(label.GetRuntimeId())
        except Exception:
            runtime_id = None
    return result, label, runtime_id


def _read_full(hwnd: int) -> dict:
    """完整扫描兜底，结果与 ``get_current_chat_type`` 一致。"""
    snapshot = scan_window(hwnd)
    assert_target_role(hwnd, getattr(snapshot, "window_kind", "unknown"))
    result = classify_snapshot(snapshot)
    return attach_window_info(
        result,
        hwnd=snapshot.hwnd,
        window_state=snapshot.window_state,
        window_kind=getattr(snapshot, "window_kind", None),
        window_automation_id=getattr(snapshot, "window_automation_id", None),
        layout=getattr(snapshot, "layout", None),
    )


def _label_unchanged(cached: _Cached) -> bool:
    """会话名控件还在、RuntimeId 和文本都没变时返回 True。"""
    label = cached.label
    if label is None or cached.runtime_id is None:
        return False
    try:
        if not label:
            return False
        if tuple(label.GetRuntimeId()) != cached.runtime_id:
            return False
        name = (label.CurrentName or "").strip()
    except Exception:
        return False
    return name == (cached.result.get("chat_name") or "")


def _window_still_target(cached: _Cached) -> bool:
    """默认选择窗口时，前台切到别的微信窗口就重新选择。

    显式 hwnd / chat_name 由调用参数比对负责，不走这里。
    """
    foreground = win32gui.GetForegroundWindow()
    if foreground == cached.hwnd:
        return True
    try:
        return not is_wechat_window(foreground)
    except Exception:
        return True


def get_chat_type_fast(
    hwnd: int | None = None,
    chat_name: str | None = None,
    *,
    force: bool = False,
    ttl: float = DEFAULT_TTL,
) -> dict:
    """快速返回当前会话/视图类型，适合高频轮询。

    与 ``get_current_chat_type`` 返回同样的字段。窗口不可用时抛
    ``WindowUnavailableError``，不会为了轮询去唤醒窗口。
    """
    global _cache

    now = time.monotonic()
    cached = _cache
    same_request = (
        cached is not None
        and hwnd == cached.request_hwnd
        and chat_name == cached.chat_name
    )
    window_ok = same_request and (
        hwnd is not None
        or chat_name is not None
        or _window_still_target(cached)
    )
    if (
        not force
        and window_ok
        and now - cached.checked_at < cached.ttl
        and _label_unchanged(cached)
    ):
        return dict(cached.result)

    # ttl 到期只重新读取头部；窗口选择沿用上一次的结果，避免每次轮询
    # 都重新做一次窗口发现。
    target = cached.hwnd if window_ok else _resolve_hwnd(hwnd, chat_name)
    try:
        header = _read_header(target)
    except WindowUnavailableError:
        # 缓存的窗口可能已经关闭或被最小化；重新选一次，仍不可用就照常报错。
        if cached is not None and cached.hwnd == target:
            target = _resolve_hwnd(hwnd, chat_name)
        header = _read_header(target)

    if header is None:
        result = _read_full(target)
        label = None
        runtime_id = None
    else:
        result, label, runtime_id = header

    _cache = _Cached(
        request_hwnd=hwnd,
        hwnd=target,
        chat_name=chat_name,
        result=dict(result),
        label=label,
        runtime_id=runtime_id,
        checked_at=time.monotonic(),
        ttl=ttl,
    )
    return dict(result)
