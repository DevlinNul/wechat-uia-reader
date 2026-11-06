"""获取微信窗口句柄。

支持微信 3.x (WeChat.exe) 与微信 4.x (Weixin.exe)。
不使用硬编码类名，而是按进程 + 窗口样式特征识别主窗口。

用法:
    python get_hwnd.py
"""

import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

from errors import ClickTargetBlockedError, WindowUnavailableError

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_NAMES = {"wechat.exe", "weixin.exe"}

# 微信 4.x 的三种顶层窗口。主窗口才能完整操作；独立聊天窗也能发送；
# 聊天记录窗口是只读的，不能把它的搜索框当成聊天输入框。
WECHAT_MAIN_CLASSES = ("mmui::MainWindow",)
WECHAT_CHAT_WINDOW_CLASSES = ("mmui::ChatSingleWindow",)
WECHAT_HISTORY_WINDOW_CLASSES = ("mmui::SearchMsgUniqueChatWindow",)

# --- 托盘唤起 ---------------------------------------------------------------
# 首选读取 Explorer 工具栏按钮的 (hwnd, uID, callbackMessage)，向微信
# 托盘消息窗口投递鼠标消息，不移动鼠标、不模拟点击。失败时才退回
# Shell_NotifyIconGetRect + 鼠标双击的备选路径。
# 不能对托盘隐藏的窗口调用 ShowWindow，否则只会恢复 Win32 可见性，
# Qt 渲染循环不恢复，客户区会变成纯灰色。
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04

TB_GETBUTTON = 0x0400 + 23
TB_BUTTONCOUNT = 0x0400 + 24

WAKE_TIMEOUT = 3.0
WAKE_POLL_INTERVAL = 0.05
TRAY_EVENT_DELAY = 0.05

_POINTER_SIZE = ctypes.sizeof(ctypes.c_void_p)
_RESERVED_BYTES = 6 if _POINTER_SIZE == 8 else 2

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
_kernel32.VirtualAllocEx.restype = ctypes.c_void_p
_kernel32.VirtualFreeEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
]
_kernel32.VirtualFreeEx.restype = wintypes.BOOL
_kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.ReadProcessMemory.restype = wintypes.BOOL

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.FindWindowExW.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
]
_user32.FindWindowExW.restype = wintypes.HWND


class _TBBUTTON(ctypes.Structure):
    """Explorer 工具栏按钮结构，按当前进程指针宽度布局。"""

    _fields_ = [
        ("iBitmap", ctypes.c_int),
        ("idCommand", ctypes.c_int),
        ("fsState", ctypes.c_ubyte),
        ("fsStyle", ctypes.c_ubyte),
        ("bReserved", ctypes.c_ubyte * _RESERVED_BYTES),
        ("dwData", ctypes.c_void_p),
        ("iString", ctypes.c_void_p),
    ]


class _GUID(ctypes.Structure):
    """Shell_NotifyIconGetRect 使用的 GUID 结构。"""

    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _NOTIFYICONIDENTIFIER(ctypes.Structure):
    """NOTIFYICONIDENTIFIER：用于查询托盘图标位置。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", _GUID),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.Shell_NotifyIconGetRect.argtypes = [
    ctypes.POINTER(_NOTIFYICONIDENTIFIER),
    ctypes.POINTER(wintypes.RECT),
]
_shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long


# 主窗口通常具备的样式：可最小化/最大化/可调整大小（允许 Qt 自绘无边框，故为“或”）
APP_STYLE_BITS = (
    win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX | win32con.WS_THICKFRAME
)


def get_process_name(pid: int) -> str:
    """通过进程 ID 获取可执行文件名。"""
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        ):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def get_wechat_pids() -> set[int]:
    """枚举系统中所有微信进程 PID。"""
    pids = set()
    for pid in win32process.EnumProcesses():
        if get_process_name(pid).lower() in PROCESS_NAMES:
            pids.add(pid)
    return pids


def window_state(hwnd: int) -> str:
    """返回窗口状态：normal / minimized / hidden / offscreen。

    仅做只读判断，不调用 ShowWindow 等会改变窗口状态的接口。
    """
    user32 = ctypes.windll.user32
    # 托盘隐藏的过渡态可能同时带 iconic 标志；先判断可见性，避免把
    # 托盘状态误判成普通最小化。
    if not user32.IsWindowVisible(hwnd):
        return "hidden"
    if user32.IsIconic(hwnd):
        return "minimized"

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    if right <= vx or left >= vx + vw or bottom <= vy or top >= vy + vh:
        return "offscreen"
    return "normal"


STATE_HINTS = {
    "minimized": (
        "微信窗口已最小化，请手动还原后再运行"
        "（read_private_chat 可用 --wake-minimized 允许自动还原）"
    ),
    "hidden": (
        "微信窗口已隐藏到托盘，请手动双击托盘图标还原后再运行"
        "（read_private_chat 可用 --wake-tray 允许自动唤起）"
    ),
    "offscreen": "微信窗口位于屏幕外，请手动还原后再运行（本脚本不会主动唤起窗口）",
}


def describe_window_state(hwnd: int) -> tuple[str, str | None]:
    """返回 (状态, 提示)。状态为 normal 时提示为 None。"""
    state = window_state(hwnd)
    return state, STATE_HINTS.get(state)


def _window_exists(hwnd: int) -> bool:
    """句柄非零且对应窗口仍然存在。"""
    return bool(hwnd) and bool(ctypes.windll.user32.IsWindow(hwnd))


def get_window_pid(hwnd: int) -> int:
    """返回窗口所属进程 ID；失败时返回 0。"""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid)
    except Exception:
        return 0


def hit_test_window(x: int, y: int) -> int:
    """系统命中测试：返回屏幕坐标处实际会接收鼠标的窗口句柄。"""
    try:
        return int(win32gui.WindowFromPoint((int(x), int(y))) or 0)
    except Exception:
        return 0


def window_root(hwnd: int) -> int:
    """返回窗口所属的顶层窗口句柄。"""
    if not hwnd:
        return 0
    try:
        root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        return int(root or hwnd)
    except Exception:
        return hwnd


def point_belongs_to_window(x: int, y: int, target_hwnd: int) -> bool:
    """屏幕坐标处的系统命中窗口是否属于目标顶层窗口。

    鼠标点击之前调用。如果坐标上压着其他窗口，返回 False；调用方应
    放弃点击，而不是把鼠标事件发给无关窗口。
    """
    if not target_hwnd:
        return False
    hit = hit_test_window(x, y)
    if not hit:
        return False
    return window_root(hit) == window_root(target_hwnd)


def describe_hit_window(x: int, y: int) -> str:
    """给错误信息用的简短命中描述：句柄 + 类名。"""
    hit = hit_test_window(x, y)
    if not hit:
        return "桌面或未知窗口"
    root = window_root(hit)
    try:
        class_name = win32gui.GetClassName(root)
    except Exception:
        class_name = "?"
    return f"0x{root:X} ({class_name})"


def is_wechat_window(hwnd: int) -> bool:
    """句柄是否属于微信进程（WeChat.exe / Weixin.exe）。

    正常情况下按进程名判断。权限不足、读不到进程名时，不直接拒绝，
    再看窗口类名是不是微信 4.x 的 Qt/mmui 窗口；两者都不像才拒绝。
    """
    if not _window_exists(hwnd):
        return False
    pid = get_window_pid(hwnd)
    if not pid:
        return False

    process_name = get_process_name(pid).lower()
    if process_name:
        return process_name in PROCESS_NAMES

    try:
        class_name = win32gui.GetClassName(hwnd)
    except Exception:
        return False
    return class_name.startswith("mmui::") or class_name == "Qt51514QWindowIcon"


def window_kind_from_class(class_name: str) -> str | None:
    """按微信 4.x 的 UIA/窗口类名判断角色；不认识时返回 None。"""
    if class_name in WECHAT_MAIN_CLASSES:
        return "main"
    if class_name in WECHAT_CHAT_WINDOW_CLASSES:
        return "chat"
    if class_name in WECHAT_HISTORY_WINDOW_CLASSES:
        return "history"
    return None


def window_kind(hwnd: int) -> str:
    """返回窗口角色：main / chat / history / other / foreign / missing。

    - main：微信主窗口；
    - chat：独立聊天窗口，可发送；
    - history：聊天记录窗口，只读，禁止发送；
    - other：微信进程里的其他窗口；
    - foreign：句柄不属于微信；
    - missing：窗口已经不存在。

    优先看窗口类名；拿不到 mmui 类名时，再用窗口样式判断是否像主窗口。
    """
    if not _window_exists(hwnd):
        return "missing"
    if not is_wechat_window(hwnd):
        return "foreign"

    try:
        class_name = win32gui.GetClassName(hwnd)
    except Exception:
        class_name = ""

    kind = window_kind_from_class(class_name)
    if kind is not None:
        return kind
    if is_main_window(hwnd):
        return "main"
    return "other"


def _open_process(pid: int):
    """以读取/写入远程内存的权限打开进程；失败时返回 0。"""
    return _kernel32.OpenProcess(
        PROCESS_VM_OPERATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )


def _read_remote(handle, address: int, size: int) -> bytes | None:
    """读取远程进程内存；失败时返回 None。"""
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = _kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(read),
    )
    if not ok:
        return None
    return buffer.raw[: read.value]


def _parse_tray_data(data: bytes) -> dict | None:
    """从 TRAYDATA 中解析 (hwnd, uID, callbackMessage)。

    不同 shell 版本可能有不同的前置字段，因此同时尝试两个对齐位置；
    最终结果还会由调用方按进程和类名再校验一次。
    """
    for offset in (0, _POINTER_SIZE):
        end = offset + _POINTER_SIZE + 8
        if len(data) < end:
            continue
        pointer_format = "<Q" if _POINTER_SIZE == 8 else "<I"
        tray_hwnd = struct.unpack_from(pointer_format, data, offset)[0]
        uid = struct.unpack_from("<I", data, offset + _POINTER_SIZE)[0]
        callback = struct.unpack_from(
            "<I", data, offset + _POINTER_SIZE + 4
        )[0]
        if tray_hwnd and callback >= 0x0400 and win32gui.IsWindow(tray_hwnd):
            return {"hwnd": tray_hwnd, "uid": uid, "callback": callback}
    return None


def _tray_toolbars():
    """枚举主任务栏和溢出区里的 ToolbarWindow32。"""
    roots = []
    for class_name in ("Shell_TrayWnd", "NotifyIconOverflowWindow"):
        try:
            root = win32gui.FindWindow(class_name, None)
        except Exception:
            root = 0
        if root:
            roots.append(root)

    seen = set()
    for root in roots:
        children = []
        try:
            win32gui.EnumChildWindows(
                root, lambda child, _: children.append(child) or True, None
            )
        except Exception:
            continue
        for child in children:
            if child in seen:
                continue
            try:
                if win32gui.GetClassName(child) != "ToolbarWindow32":
                    continue
            except Exception:
                continue
            seen.add(child)
            yield child


def _toolbar_buttons(toolbar: int):
    """读取一个工具栏里的按钮，返回含托盘回调信息的字典。"""
    _, pid = win32process.GetWindowThreadProcessId(toolbar)
    handle = _open_process(pid)
    if not handle:
        return

    remote = _kernel32.VirtualAllocEx(
        handle, None, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    )
    if not remote:
        _kernel32.CloseHandle(handle)
        return

    try:
        count = int(_user32.SendMessageW(toolbar, TB_BUTTONCOUNT, 0, 0))
        for index in range(count):
            _user32.SendMessageW(toolbar, TB_GETBUTTON, index, remote)
            raw = _read_remote(handle, remote, ctypes.sizeof(_TBBUTTON))
            if not raw or len(raw) < ctypes.sizeof(_TBBUTTON):
                continue

            button = _TBBUTTON.from_buffer_copy(raw)
            if not button.dwData:
                continue

            data = _read_remote(handle, button.dwData, 64)
            if not data:
                continue
            entry = _parse_tray_data(data)
            if entry:
                entry["index"] = index
                yield entry
    finally:
        _kernel32.VirtualFreeEx(handle, remote, 0, MEM_RELEASE)
        _kernel32.CloseHandle(handle)


def _find_wechat_tray_icon_via_explorer(hwnd: int) -> dict | None:
    """找到属于该微信主窗口的托盘按钮信息。

    返回 {hwnd, uid, callback, index, toolbar, class, title}，找不到时返回
    None。Explorer 权限不足或托盘实现变化时也会返回 None。
    """
    if not hwnd:
        return None
    _, target_pid = win32process.GetWindowThreadProcessId(hwnd)
    if not target_pid:
        return None

    candidates = []
    for toolbar in _tray_toolbars():
        for entry in _toolbar_buttons(toolbar):
            _, pid = win32process.GetWindowThreadProcessId(entry["hwnd"])
            if pid != target_pid:
                continue
            try:
                class_name = win32gui.GetClassName(entry["hwnd"]) or ""
                title = win32gui.GetWindowText(entry["hwnd"]) or ""
            except Exception:
                class_name = ""
                title = ""
            entry = dict(entry)
            entry["toolbar"] = toolbar
            entry["class"] = class_name
            entry["title"] = title
            candidates.append(entry)

    if not candidates:
        return None

    for entry in candidates:
        text = f"{entry['class']} {entry['title']}".lower()
        if "tray" in text:
            return entry
    return candidates[0]


def find_wechat_tray_message_window(hwnd: int) -> int | None:
    """直接定位微信自己的托盘消息窗口，不读取 Explorer 内存。

    微信 4.x 的托盘消息窗口是主窗口同进程的隐藏顶层窗口，类名通常包含
    TrayIconMessageWindow；部分版本可能把它放到 message-only 窗口里。
    """
    if not hwnd:
        return None
    _, target_pid = win32process.GetWindowThreadProcessId(hwnd)
    if not target_pid:
        return None

    def matches(candidate: int) -> bool:
        """类名或标题是否像微信的托盘消息窗口。"""
        try:
            class_name = win32gui.GetClassName(candidate) or ""
            title = win32gui.GetWindowText(candidate) or ""
        except Exception:
            return False
        text = f"{class_name} {title}".lower()
        return "tray" in text and ("icon" in text or "message" in text)

    found = []

    def callback(candidate, _):
        """收集同进程的托盘消息窗口。"""
        _, pid = win32process.GetWindowThreadProcessId(candidate)
        if pid == target_pid and matches(candidate):
            found.append(candidate)
        return True

    win32gui.EnumWindows(callback, None)
    if found:
        return found[0]

    # EnumWindows 不包含 message-only 窗口，再补查一遍。
    message_parent = wintypes.HWND(-3)  # HWND_MESSAGE
    child = _user32.FindWindowExW(message_parent, None, None, None)
    while child:
        _, pid = win32process.GetWindowThreadProcessId(child)
        if pid == target_pid and matches(child):
            return child
        child = _user32.FindWindowExW(message_parent, child, None, None)
    return None


def _notify_icon_rect(
    tray_hwnd: int, uid: int
) -> tuple[int, tuple[int, int, int, int] | None]:
    """查询托盘图标的屏幕矩形。返回 (HRESULT, rect)。

    HRESULT == 0 表示图标可见且 rect 有效；HRESULT == 1（S_FALSE）
    表示图标已注册但当前被折叠在溢出区里。
    """
    identifier = _NOTIFYICONIDENTIFIER()
    identifier.cbSize = ctypes.sizeof(identifier)
    identifier.hWnd = tray_hwnd
    identifier.uID = uid
    rect = wintypes.RECT()
    hr = int(
        _shell32.Shell_NotifyIconGetRect(
            ctypes.byref(identifier), ctypes.byref(rect)
        )
    )
    if hr == 0:
        return hr, (rect.left, rect.top, rect.right, rect.bottom)
    return hr, None


def _probe_tray_icon_rect(
    tray_hwnd: int,
) -> tuple[tuple[int, int, int, int] | None, bool]:
    """返回 (图标矩形, 是否被折叠到溢出区)。"""
    hidden = False
    for uid in range(16):
        hr, rect = _notify_icon_rect(tray_hwnd, uid)
        if hr == 0 and rect:
            return rect, False
        if hr == 1:
            hidden = True
    return None, hidden


TRAY_ROOT_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland",
}


def _click_screen(
    x: int,
    y: int,
    count: int = 2,
    *,
    expected_classes: set[str] | None = None,
) -> None:
    """在屏幕坐标处点击，结束后恢复鼠标位置。

    expected_classes 不为空时，点击前后都会做系统命中测试；如果坐标上
    压着其他窗口，直接抛 ClickTargetBlockedError，不发送任何鼠标按键。
    """
    def require_target() -> None:
        """确认当前坐标仍然命中允许的窗口。"""
        if expected_classes is None:
            return
        hit = hit_test_window(x, y)
        root = window_root(hit)
        try:
            class_name = win32gui.GetClassName(root) if root else ""
        except Exception:
            class_name = ""
        if class_name not in expected_classes:
            raise ClickTargetBlockedError(
                f"点击位置 {x},{y} 命中了 {describe_hit_window(x, y)}，"
                "不是预期的托盘区域；已取消点击"
            )

    require_target()
    original = win32api.GetCursorPos()
    try:
        win32api.SetCursorPos((x, y))
        time.sleep(0.08)
        require_target()
        for _ in range(count):
            require_target()
            win32api.mouse_event(
                win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0
            )
            win32api.mouse_event(
                win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0
            )
            time.sleep(0.08)
    finally:
        win32api.SetCursorPos(original)


def _restore_tray_by_click(hwnd: int, timeout: float) -> bool:
    """备选：用 Shell_NotifyIconGetRect + 鼠标双击唤起托盘图标。

    不读取 Explorer 的工具栏内存，但会移动鼠标并模拟点击。图标折叠在
    溢出区时，临时显示 NotifyIconOverflowWindow；点击后恢复原来的
    溢出区状态。
    """
    tray_hwnd = find_wechat_tray_message_window(hwnd)
    if not tray_hwnd:
        return False

    rect, hidden = _probe_tray_icon_rect(tray_hwnd)
    overflow = None
    overflow_was_visible = True

    try:
        if rect is None and hidden:
            overflow = win32gui.FindWindow("NotifyIconOverflowWindow", None)
            if not overflow:
                return False
            overflow_was_visible = bool(win32gui.IsWindowVisible(overflow))
            win32gui.ShowWindow(overflow, win32con.SW_SHOW)
            try:
                win32gui.SetForegroundWindow(overflow)
            except Exception:
                pass
            time.sleep(0.4)
            for _ in range(10):
                rect, _ = _probe_tray_icon_rect(tray_hwnd)
                if rect is not None:
                    break
                time.sleep(0.05)

        if rect is None:
            return False

        x = (rect[0] + rect[2]) // 2
        y = (rect[1] + rect[3]) // 2
        try:
            _click_screen(
                x, y, expected_classes=TRAY_ROOT_CLASSES
            )
        except ClickTargetBlockedError:
            return False
        time.sleep(0.15)
    finally:
        if overflow is not None and not overflow_was_visible:
            try:
                win32gui.ShowWindow(overflow, win32con.SW_HIDE)
            except Exception:
                pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window_state(hwnd) == "normal":
            return True
        time.sleep(WAKE_POLL_INTERVAL)
    return False


def restore_minimized_window(hwnd: int, timeout: float = WAKE_TIMEOUT) -> bool:
    """还原普通最小化的窗口。

    只对 minimized 状态使用 ShowWindow(SW_RESTORE)；这个方法对真正的
    Win32 最小化是安全的。托盘隐藏（hidden）不能走这里。
    """
    state = window_state(hwnd)
    if state == "normal":
        return True
    if state != "minimized":
        return False

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window_state(hwnd) == "normal":
            return True
        time.sleep(WAKE_POLL_INTERVAL)
    return False


def _restore_tray_by_message(hwnd: int, timeout: float = WAKE_TIMEOUT) -> bool:
    """首选：从 Explorer 工具栏读取托盘回调并投递消息。

    只向微信托盘消息窗口投递托盘回调消息，不移动鼠标、不模拟点击。
    需要读取 Explorer 进程内存；失败时由上层退回模拟点击路径。
    """
    state = window_state(hwnd)
    if state == "normal":
        return True
    if state != "hidden":
        return False

    entry = _find_wechat_tray_icon_via_explorer(hwnd)
    if entry is None:
        return False

    for message in (
        win32con.WM_LBUTTONDOWN,
        win32con.WM_LBUTTONUP,
        win32con.WM_LBUTTONDBLCLK,
    ):
        try:
            win32gui.PostMessage(
                entry["hwnd"], entry["callback"], entry["uid"], message
            )
        except Exception:
            return False
        time.sleep(TRAY_EVENT_DELAY)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window_state(hwnd) == "normal":
            return True
        time.sleep(WAKE_POLL_INTERVAL)
    return False


def restore_tray_window(hwnd: int, timeout: float = WAKE_TIMEOUT) -> bool:
    """唤起隐藏到托盘的窗口。

    首选向微信托盘消息窗口投递原生托盘回调消息，不移动鼠标、不模拟
    点击。只有该方式失败时，才退回 Shell_NotifyIconGetRect + 鼠标双击
    的备选路径。

    不使用 ShowWindow：对托盘隐藏的 Qt 窗口强制显示会只恢复 Win32
    可见性，Qt 渲染循环不恢复，客户区会变成纯灰色。
    """
    state = window_state(hwnd)
    if state == "normal":
        return True
    if state != "hidden":
        return False

    if _restore_tray_by_message(hwnd, timeout):
        return True
    return _restore_tray_by_click(hwnd, timeout)


def require_window_ready(
    hwnd: int,
    *,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> int:
    """校验窗口可用，不可用时立即抛错（early panic）。

    默认只做只读判断。显式开启 wake_minimized 时，对普通最小化窗口
    调用 ShowWindow(SW_RESTORE)；显式开启 wake_tray 时，对托盘隐藏
    窗口走微信原生托盘回调。两者都不会在未授权时改变窗口状态。
    """
    if not _window_exists(hwnd):
        raise WindowUnavailableError("未找到微信主窗口，请确认微信已启动")

    if not is_wechat_window(hwnd):
        raise WindowUnavailableError(
            f"句柄 {hwnd} 不属于微信进程，拒绝操作"
        )

    state = window_state(hwnd)
    if state == "normal":
        return hwnd

    woke = False

    if state == "minimized" and wake_minimized:
        woke = restore_minimized_window(hwnd)
    elif state == "hidden" and wake_tray:
        woke = restore_tray_window(hwnd)

    if woke and window_state(hwnd) == "normal":
        return hwnd

    if state == "minimized" and wake_minimized:
        raise WindowUnavailableError(
            "尝试还原最小化的微信窗口失败，请手动还原后再运行"
        )
    if state == "hidden" and wake_tray:
        raise WindowUnavailableError(
            "尝试从托盘唤起微信失败，请手动双击托盘图标后再运行"
        )

    hint = STATE_HINTS.get(state)
    raise WindowUnavailableError(hint or f"微信窗口不可用（{state}）")


def require_wechat_main_window(
    *,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> int:
    """定位并校验微信主窗口；不可用时抛 WindowUnavailableError。"""
    hwnd = find_wechat_main_window()
    if not hwnd:
        raise WindowUnavailableError("未找到微信窗口，请确认微信已启动")
    return require_window_ready(
        hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
    )


def window_area(hwnd: int) -> int:
    """返回窗口面积（宽 × 高），用于挑选主窗口。"""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return max(0, right - left) * max(0, bottom - top)


def describe(hwnd: int) -> dict:
    """采集窗口基础信息。"""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return {
        "hwnd": hwnd,
        "hex": hex(hwnd),
        "title": win32gui.GetWindowText(hwnd),
        "class": win32gui.GetClassName(hwnd),
        "pid": pid,
        "process": get_process_name(pid),
        "visible": bool(win32gui.IsWindowVisible(hwnd)),
        "rect": (left, top, right - left, bottom - top),
        "area": window_area(hwnd),
    }


def window_traits(hwnd: int) -> dict:
    """主窗口判定所需的样式特征。"""
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    return {
        "owned": bool(
            win32gui.GetParent(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        ),
        "tool_window": bool(ex_style & win32con.WS_EX_TOOLWINDOW),
        "caption": bool(style & win32con.WS_CAPTION),
        "app_style": bool(style & APP_STYLE_BITS),
        "titled": bool(win32gui.GetWindowText(hwnd)),
    }


def is_main_window(hwnd: int) -> bool:
    """按通用窗口特征判断是否为主窗口（与类名无关）。"""
    t = window_traits(hwnd)
    return (
        not t["owned"]
        and not t["tool_window"]
        and t["caption"]
        and t["app_style"]
        and t["titled"]
    )


def collect_windows() -> list[dict]:
    """枚举微信进程的所有顶层窗口（包含隐藏窗口），主窗口排在最前。"""
    pids = get_wechat_pids()
    results = []

    def callback(hwnd, _):
        """收集微信进程的顶层窗口信息。"""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids:
            info = describe(hwnd)
            info["main"] = is_main_window(hwnd)
            info["traits"] = window_traits(hwnd)
            results.append(info)
        return True

    win32gui.EnumWindows(callback, None)
    results.sort(key=lambda w: (not w["main"], not w["visible"], -w["area"]))
    return results


def find_wechat_main_window() -> int | None:
    """返回微信主窗口句柄，找不到时返回 None。"""
    windows = collect_windows()
    mains = [w for w in windows if w["main"]]
    if mains:
        return max(mains, key=lambda w: (w["visible"], w["area"]))["hwnd"]

    # 兜底：无符合样式的窗口时，取体积最大的非工具窗口/无主窗口
    fallback = [
        w
        for w in windows
        if not w["traits"]["tool_window"]
        and not w["traits"]["owned"]
        and w["area"] > 0
    ]
    if fallback:
        return max(fallback, key=lambda w: w["area"])["hwnd"]
    return None


# 这些窗口是桌面/任务栏/输入法的一部分，不应该被“清理遮挡”误伤。
SKIP_OCCLUDER_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "NotifyIconOverflowWindow",
    "MSCTFIME UI",
    "IME",
}


def minimize_overlapping_windows(hwnd: int) -> list[int]:
    """最小化与微信窗口重叠的其他窗口，返回真正被最小化的句柄。

    这是可选能力，默认不开启。只在用户明确要求“清理遮挡窗口”时调用；
    微信自己的窗口、当前 Python 进程、任务栏/输入法等系统窗口不会被处理。
    """
    try:
        wx_left, wx_top, wx_right, wx_bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return []

    wechat_pid = get_window_pid(hwnd)
    own_pid = os.getpid()
    targets: list[int] = []

    def callback(other, _):
        """收集可见、与微信重叠、且不属于微信/本进程的顶层窗口。"""
        try:
            if other == hwnd:
                return True
            if not win32gui.IsWindowVisible(other) or win32gui.IsIconic(other):
                return True
            pid = get_window_pid(other)
            if pid in (wechat_pid, own_pid):
                return True

            class_name = win32gui.GetClassName(other)
            if (
                class_name in SKIP_OCCLUDER_CLASSES
                or class_name.startswith("Windows.UI.Core")
            ):
                return True

            title = win32gui.GetWindowText(other).strip()
            # 无标题窗口通常是内部辅助窗口；Chrome 等浏览器主窗仍要处理。
            if not title and class_name != "Chrome_WidgetWin_1":
                return True

            left, top, right, bottom = win32gui.GetWindowRect(other)
            overlaps = (
                right > wx_left
                and left < wx_right
                and bottom > wx_top
                and top < wx_bottom
            )
            if overlaps:
                targets.append(other)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return []

    minimized = []
    for other in targets:
        try:
            win32gui.ShowWindow(other, win32con.SW_MINIMIZE)
            minimized.append(other)
        except Exception:
            pass
    return minimized


def restore_minimized_windows(handles: list[int]) -> int:
    """还原由 minimize_overlapping_windows 最小化的窗口，返回还原个数。

    使用 SW_SHOWNOACTIVATE：窗口恢复显示，但不会抢走微信的前台焦点。
    """
    restored = 0
    for hwnd in handles:
        try:
            if win32gui.IsWindow(hwnd) and win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                restored += 1
        except Exception:
            pass
    return restored


def main() -> int:
    """枚举窗口并打印主窗口句柄与状态。"""
    windows = collect_windows()
    if not windows:
        print("未找到微信窗口，请确认微信已启动。")
        return 1

    mains = [w for w in windows if w["main"]]
    print(f"找到 {len(windows)} 个微信窗口（主窗口 {len(mains)} 个）:\n")
    for w in windows:
        x, y, width, height = w["rect"]
        flag = "主窗口" if w["main"] else "其他"
        print(f"[{flag}] 句柄: {w['hex']} ({w['hwnd']})")
        print(f"    标题: {w['title']!r}  类名: {w['class']}")
        print(f"    进程: {w['process']} (PID {w['pid']})  可见: {w['visible']}")
        print(f"    位置: x={x}, y={y}, w={width}, h={height}")
        print("-" * 50)

    hwnd = find_wechat_main_window()
    if not hwnd:
        print("错误: 未找到微信窗口，请确认微信已启动", file=sys.stderr)
        return 1

    try:
        require_window_ready(hwnd)
    except WindowUnavailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    state, _ = describe_window_state(hwnd)
    print(f"\n主窗口句柄: {hex(hwnd)} ({hwnd})")
    print(f"窗口状态  : {state}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
