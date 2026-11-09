"""界面动作原语：唤醒、激活、点击、聚焦、键盘。

这里集中所有会改变窗口/鼠标/键盘状态的动作，供 API 层编排复用。
本模块不扫描控件树，不读写输入框。
"""

import ctypes
import time
from ctypes import wintypes

import win32con
import win32gui

from .get_hwnd import (
    activate_window,
    click_point as _click_point,
    require_window_ready,
)
from .errors import ClickTargetBlockedError, InputFillError

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_CONTROL = 0x11
VK_A = 0x41
VK_DELETE = 0x2E
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_MENU = 0x12
VK_S = 0x53


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        ]

    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _INPUT)]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(INPUT),
    ctypes.c_int,
]
_user32.SendInput.restype = wintypes.UINT


def ensure_ready(
    hwnd: int,
    *,
    window_role: str = "main",
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> int:
    """显式检查并可选唤醒目标窗口。唯一会修改窗口状态入口之一。"""
    return require_window_ready(
        hwnd,
        wake_minimized=wake_minimized,
        wake_tray=wake_tray,
        window_role=window_role,
    )


def click_point(x: int, y: int, target_hwnd: int, *, what: str = "控件") -> None:
    """安全点击屏幕坐标；命中其他窗口时抛 ClickTargetBlockedError。"""
    _click_point(x, y, target_hwnd, what=what)


def _key_input(vk: int, flags: int = 0, scan: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        i=INPUT._INPUT(
            ki=KEYBDINPUT(
                wVk=vk,
                wScan=scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def _send_inputs(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise InputFillError(
            f"SendInput 发送失败（{sent}/{len(inputs)}），"
            f"错误码 {ctypes.get_last_error()}"
        )


def _unicode_inputs(text: str) -> list[INPUT]:
    """把文本转成 Unicode 键盘事件；换行用 Shift+Enter，避免触发发送。"""
    inputs = []
    for char in text:
        if char == "\n":
            inputs.extend(
                [
                    _key_input(VK_SHIFT),
                    _key_input(VK_RETURN),
                    _key_input(VK_RETURN, KEYEVENTF_KEYUP),
                    _key_input(VK_SHIFT, KEYEVENTF_KEYUP),
                ]
            )
            continue

        encoded = char.encode("utf-16-le")
        for offset in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[offset : offset + 2], "little")
            inputs.append(_key_input(0, KEYEVENTF_UNICODE, unit))
            inputs.append(
                _key_input(0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, unit)
            )
    return inputs


def clear_via_keyboard() -> None:
    """Ctrl+A 后 Delete，不经过剪贴板。"""
    _send_inputs(
        [
            _key_input(VK_CONTROL),
            _key_input(VK_A),
            _key_input(VK_A, KEYEVENTF_KEYUP),
            _key_input(VK_CONTROL, KEYEVENTF_KEYUP),
            _key_input(VK_DELETE),
            _key_input(VK_DELETE, KEYEVENTF_KEYUP),
        ]
    )


def send_unicode(text: str) -> None:
    """用 SendInput 注入 Unicode 字符。调用前应确保输入框已聚焦。"""
    _send_inputs(_unicode_inputs(text))


def press_enter() -> None:
    """发送一次 Enter 键事件（不点击发送按钮）。"""
    _send_inputs(
        [
            _key_input(VK_RETURN),
            _key_input(VK_RETURN, KEYEVENTF_KEYUP),
        ]
    )


def press_alt_s() -> None:
    """发送微信原生的 Alt+S 快捷键（“发送消息”）。"""
    _send_inputs(
        [
            _key_input(VK_MENU),
            _key_input(VK_S),
            _key_input(VK_S, KEYEVENTF_KEYUP),
            _key_input(VK_MENU, KEYEVENTF_KEYUP),
        ]
    )


def _control_target_hwnd(control) -> int:
    """尽量从控件本身找到所属顶层窗口句柄。"""
    try:
        top = control.GetTopLevelControl()
        hwnd = int(getattr(top, "NativeWindowHandle", 0) or 0)
        if hwnd:
            return hwnd
    except Exception:
        pass
    try:
        return int(getattr(control, "NativeWindowHandle", 0) or 0)
    except Exception:
        return 0


def click_control(control, target_hwnd: int | None = None) -> bool:
    """点击控件中心；点击前做命中测试，结束后恢复鼠标位置。"""
    if control is None:
        return False
    try:
        if not getattr(control, "IsEnabled", True):
            return False
        if getattr(control, "IsOffscreen", False):
            return False
        rect = control.BoundingRectangle
    except Exception:
        return False
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return False

    x = (rect.left + rect.right) // 2
    y = (rect.top + rect.bottom) // 2
    target = int(target_hwnd or 0) or _control_target_hwnd(control)
    click_point(x, y, target, what="控件")
    return True


def focus_input(input_field, target_hwnd: int | None = None) -> bool:
    """让聊天输入框获得键盘焦点；SetFocus 失败时点击输入框中心。"""
    try:
        input_field.SetFocus()
    except Exception:
        pass

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            if input_field.HasKeyboardFocus:
                return True
        except Exception:
            pass
        time.sleep(0.05)

    try:
        rect = input_field.BoundingRectangle
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        target = int(target_hwnd or 0) or _control_target_hwnd(input_field)
        click_point(x, y, target, what="聊天输入框")
        time.sleep(0.15)
        return bool(getattr(input_field, "HasKeyboardFocus", False))
    except ClickTargetBlockedError:
        raise
    except Exception:
        return False


def activate(hwnd: int, settle: float = 0.2) -> bool:
    """把窗口置于前台；唯一的窗口置前入口。"""
    return activate_window(hwnd, settle=settle)


def restore_foreground(hwnd: int) -> None:
    """尽力把窗口恢复为前台。"""
    if not hwnd or win32gui.GetForegroundWindow() == hwnd:
        return
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
