"""微信会话窗口的发现与选择。

只做只读探测，不扫描完整控件树，不唤醒、不点击、不发送。

窗口角色只看 UIA 根类名：

- ``mmui::MainWindow`` -> main
- ``mmui::ChatSingleWindow`` -> chat
- ``mmui::SearchMsgUniqueChatWindow`` -> history

选择策略与扫描分离：

- 本模块只判断“应该操作哪个 HWND”；
- 完整控件树由 :func:`wechat_uia.core.snapshot.scan_window` 负责。
"""

import time
from dataclasses import dataclass

import win32gui

from .errors import (
    AmbiguousConversationError,
    ConversationNotOpenError,
    WindowUnavailableError,
)
from .get_hwnd import (
    collect_windows,
    describe,
    window_kind_from_class,
    window_state,
)
from .session import normalize_expected_session
from .snapshot import get_root_control
from .uia_utils import CHAT_NAME_SUFFIX, iter_descendants

PROBE_TIMEOUT = 1.0
PROBE_MAX_DEPTH = 30

MAIN = "main"
CHAT = "chat"
HISTORY = "history"
SURFACE_ROLES = {MAIN, CHAT, HISTORY}


@dataclass(frozen=True)
class WindowSurface:
    """一个可承载会话的微信顶层窗口。"""

    hwnd: int
    role: str
    title: str
    window_automation_id: str | None
    chat_name: str | None
    window_state: str
    visible: bool


@dataclass(frozen=True)
class ResolvedSurface:
    """选择结果：目标 Surface 与选择原因。"""

    surface: WindowSurface
    selection_reason: str
    ambiguous: bool
    candidate_hwnds: tuple[int, ...]

    @property
    def hwnd(self) -> int:
        return self.surface.hwnd

    @property
    def role(self) -> str:
        return self.surface.role

    @property
    def chat_name(self) -> str | None:
        return self.surface.chat_name

    @property
    def window_automation_id(self) -> str | None:
        return self.surface.window_automation_id


def selection_metadata(resolved) -> dict:
    """把窗口选择结果转成可序列化的公开字段。"""
    return {
        "selection_reason": getattr(resolved, "selection_reason", None),
        "ambiguous": bool(getattr(resolved, "ambiguous", False)),
        "candidate_hwnds": list(
            getattr(resolved, "candidate_hwnds", ()) or ()
        ),
    }


def _find_chat_label(root, timeout: float = PROBE_TIMEOUT) -> str | None:
    """在控件树中查找当前会话名控件；找到就停，不做完整快照。"""
    deadline = time.monotonic() + timeout
    for el in iter_descendants(
        root, max_depth=PROBE_MAX_DEPTH, deadline=deadline
    ):
        try:
            aid = el.AutomationId or ""
            name = (el.Name or "").strip()
        except Exception:
            continue
        if aid.endswith(CHAT_NAME_SUFFIX) and name:
            return name
    return None


def _probe_chat_name(root, role: str, title: str) -> str | None:
    """只读探测当前会话名。

    独立窗口的 Win32 标题就是会话名，最小化时也可靠；主窗口则查找
    标题控件，找到就停。
    """
    clean_title = (title or "").strip()
    if role == CHAT:
        return clean_title or _find_chat_label(root)
    if role == MAIN:
        return _find_chat_label(root)
    if role == HISTORY:
        return clean_title or _find_chat_label(root)
    return None


def probe_surface(hwnd: int) -> WindowSurface | None:
    """轻量探测一个窗口；不是会话窗口时返回 None。"""
    try:
        root = get_root_control(hwnd)
    except Exception:
        return None
    try:
        role = window_kind_from_class(root.ClassName)
    except Exception:
        return None
    if role not in SURFACE_ROLES:
        return None

    try:
        info = describe(hwnd)
    except Exception:
        return None

    try:
        automation_id = getattr(root, "AutomationId", None) or None
    except Exception:
        automation_id = None

    return WindowSurface(
        hwnd=hwnd,
        role=role,
        title=info.get("title") or "",
        window_automation_id=automation_id,
        chat_name=_probe_chat_name(root, role, info.get("title") or ""),
        window_state=window_state(hwnd),
        visible=bool(info.get("visible")),
    )


def list_surfaces() -> list[WindowSurface]:
    """列出所有微信会话窗口；不生成完整控件快照。"""
    surfaces = []
    seen = set()
    for info in collect_windows():
        hwnd = int(info.get("hwnd") or 0)
        if not hwnd or hwnd in seen:
            continue
        seen.add(hwnd)
        surface = probe_surface(hwnd)
        if surface is not None:
            surfaces.append(surface)
    return surfaces


def _matches_session(surface: WindowSurface, expected: dict) -> bool:
    """按 session 中实际提供的窗口字段过滤。"""
    detached_id = expected.get("window_automation_id")
    if detached_id:
        return surface.window_automation_id == detached_id
    role = expected.get("window_role")
    if role:
        return surface.role == role
    if any(
        expected.get(key)
        for key in ("session_automation_id", "session_runtime_id")
    ):
        return surface.role == MAIN
    return True


def find_candidates(
    chat_name: str | None = None,
    session: dict | None = None,
    *,
    surfaces: list[WindowSurface] | None = None,
    allow_history: bool = False,
) -> list[WindowSurface]:
    """找出所有匹配目标会话的窗口。

    只按当前会话名和 session 中的窗口字段过滤；类型、成员数在拿到
    完整快照后再校验。
    """
    expected = None
    if chat_name is not None or session is not None:
        expected = normalize_expected_session(chat_name, session)

    candidates = []
    for surface in surfaces if surfaces is not None else list_surfaces():
        if surface.role == HISTORY and not allow_history:
            continue
        if expected is not None:
            if not _matches_session(surface, expected):
                continue
            wanted_name = (expected.get("chat_name") or "").strip()
            if wanted_name and (surface.chat_name or "").strip() != wanted_name:
                continue
        candidates.append(surface)
    return candidates


def choose_candidate(
    candidates: list[WindowSurface], *, strict: bool = False
) -> ResolvedSurface:
    """从候选窗口里选一个，并说明原因。

    微信不允许两个独立窗口打开同一会话，因此：

    - 多个独立窗口同名：一定是不同会话，必须提供窗口 id 或 hwnd；
    - 主窗口 + 一个独立窗口同名：可能是同一会话，前台优先，其次正常
      主窗口，再其次正常独立窗口。
    """
    if not candidates:
        raise ConversationNotOpenError("目标会话没有在任何微信窗口中打开")

    hwnds = tuple(surface.hwnd for surface in candidates)
    if len(candidates) == 1:
        return ResolvedSurface(
            surface=candidates[0],
            selection_reason="unique_chat_name",
            ambiguous=False,
            candidate_hwnds=hwnds,
        )

    detached = [s for s in candidates if s.role == CHAT]
    mains = [s for s in candidates if s.role == MAIN]

    if len(detached) > 1:
        raise AmbiguousConversationError(
            "多个独立窗口匹配同一会话名，但它们一定是不同会话；"
            "请提供 window_automation_id 或 hwnd"
        )
    if strict:
        raise AmbiguousConversationError(
            "多个窗口匹配目标会话，strict 模式拒绝自动选择；"
            "请提供 hwnd 或完整 session"
        )

    foreground = win32gui.GetForegroundWindow()
    for surface in candidates:
        if surface.hwnd == foreground:
            return ResolvedSurface(
                surface=surface,
                selection_reason="foreground",
                ambiguous=True,
                candidate_hwnds=hwnds,
            )

    # 前台不是候选窗口时，优先可用窗口，避免选中已最小化的主窗口，
    # 却把旁边正常显示的独立窗口忽略掉。
    normal_mains = [s for s in mains if s.window_state == "normal"]
    if normal_mains:
        return ResolvedSurface(
            surface=normal_mains[0],
            selection_reason="main_preferred",
            ambiguous=True,
            candidate_hwnds=hwnds,
        )

    normal_detached = [s for s in detached if s.window_state == "normal"]
    if normal_detached:
        return ResolvedSurface(
            surface=normal_detached[0],
            selection_reason="detached_preferred",
            ambiguous=True,
            candidate_hwnds=hwnds,
        )

    if mains:
        return ResolvedSurface(
            surface=mains[0],
            selection_reason="main_preferred",
            ambiguous=True,
            candidate_hwnds=hwnds,
        )
    if detached:
        return ResolvedSurface(
            surface=detached[0],
            selection_reason="detached_preferred",
            ambiguous=True,
            candidate_hwnds=hwnds,
        )
    raise AmbiguousConversationError(
        "多个窗口匹配目标会话，无法安全选择；请提供 hwnd 或完整 session"
    )


def resolve_surface(
    hwnd: int | None = None,
    chat_name: str | None = None,
    session: dict | None = None,
    *,
    strict: bool = False,
    allow_history: bool = False,
) -> ResolvedSurface:
    """只读选择目标窗口；不唤醒、不扫描完整控件树。

    顺序：显式 hwnd -> session -> chat_name -> 默认窗口。
    默认窗口优先前台微信会话窗口，其次正常主窗口，再其次唯一的正常
    独立窗口；主窗口不可用时不会强行选择已最小化的主窗口。
    """
    if hwnd:
        surface = probe_surface(hwnd)
        if surface is None:
            raise WindowUnavailableError(
                f"句柄 {hwnd} 不是微信会话窗口，拒绝操作"
            )
        if surface.role == HISTORY and not allow_history:
            raise WindowUnavailableError(
                "当前是聊天记录窗口，它只读，不能作为会话操作目标"
            )
        return ResolvedSurface(
            surface=surface,
            selection_reason="explicit_hwnd",
            ambiguous=False,
            candidate_hwnds=(hwnd,),
        )

    if chat_name is not None or session is not None:
        candidates = find_candidates(
            chat_name, session, allow_history=allow_history
        )
        return choose_candidate(candidates, strict=strict)

    surfaces = [
        s for s in list_surfaces() if s.role in (MAIN, CHAT)
    ]
    foreground = win32gui.GetForegroundWindow()
    for surface in surfaces:
        if surface.hwnd == foreground:
            return ResolvedSurface(
                surface=surface,
                selection_reason="foreground",
                ambiguous=False,
                candidate_hwnds=tuple(s.hwnd for s in surfaces),
            )

    mains = [s for s in surfaces if s.role == MAIN]
    chats = [s for s in surfaces if s.role == CHAT]

    if not mains:
        if len(chats) == 1:
            return ResolvedSurface(
                surface=chats[0],
                selection_reason="only_surface",
                ambiguous=False,
                candidate_hwnds=(chats[0].hwnd,),
            )
        if not chats:
            raise WindowUnavailableError(
                "未找到微信主窗口或可用的会话窗口，请确认微信已启动"
            )
        raise AmbiguousConversationError(
            "没有可用的主窗口，且存在多个独立会话窗口；"
            "请用 chat_name 或 hwnd 指定目标"
        )

    normal_mains = [s for s in mains if s.window_state == "normal"]
    if normal_mains:
        return ResolvedSurface(
            surface=normal_mains[0],
            selection_reason="default_main",
            ambiguous=False,
            candidate_hwnds=tuple(s.hwnd for s in mains),
        )

    # 主窗口存在但不可用：优先唯一的正常独立窗口，避免选中一个已经
    # 最小化/隐藏的主窗口。
    normal_chats = [s for s in chats if s.window_state == "normal"]
    if len(normal_chats) == 1:
        return ResolvedSurface(
            surface=normal_chats[0],
            selection_reason="detached_preferred",
            ambiguous=False,
            candidate_hwnds=(normal_chats[0].hwnd,),
        )
    if len(normal_chats) > 1:
        raise AmbiguousConversationError(
            "主窗口不可用，且存在多个正常显示的独立会话窗口；"
            "请用 chat_name 或 hwnd 指定目标"
        )

    # 所有窗口都不可用时退回主窗口，让它按原来的最小化/托盘提示报错。
    return ResolvedSurface(
        surface=mains[0],
        selection_reason="default_main",
        ambiguous=False,
        candidate_hwnds=tuple(s.hwnd for s in mains),
    )
