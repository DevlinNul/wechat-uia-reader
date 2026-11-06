"""把文本填入当前微信会话的聊天输入框（不发送）。

前置条件：
- 当前会话名必须与调用方给定的 chat_name 完全一致，否则抛 ChatMismatchError；
- 当前会话必须可发送消息，且有聊天输入框；
- 输入框内容在 str.strip() 意义下必须为空；非空时抛 InputNotEmptyError，
  不覆盖也不追加已有内容。

两种写入方式：
1. 首选 ValuePattern.SetValue：不需要焦点，支持中文和换行；
2. 失败时回退到 SendInput + KEYEVENTF_UNICODE：不经过剪贴板，需要输入框焦点。

本模块只写输入框，不按 Enter、不点击发送、不切换会话。
"""

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

from errors import (
    CannotSendError,
    ChatMismatchError,
    InputFillError,
    InputNotEmptyError,
    UIAUnavailableError,
    WeChatUIAError,
)
from get_chat_type import classify_snapshot
from snapshot import (
    read_text_pattern,
    read_value_pattern,
    scan_window,
    selected_session_identity,
    set_value_pattern,
)
from uia_utils import enable_dpi_awareness

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_CONTROL = 0x11
VK_A = 0x41
VK_DELETE = 0x2E
VK_SHIFT = 0x10
VK_RETURN = 0x0D


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


def normalize_text(text: str) -> str:
    """统一换行符；微信输入框内部按 \\n 处理。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


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


def _clear_via_keyboard() -> None:
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


def _read_input_text(input_field) -> str | None:
    """读取输入框当前文本；两个接口都不可用时返回 None。"""
    for reader in (read_value_pattern, read_text_pattern):
        try:
            value = reader(input_field)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _input_matches(input_field, text: str) -> bool:
    """用 ValuePattern 或 TextPattern 校验输入框内容。"""
    for reader in (read_value_pattern, read_text_pattern):
        try:
            actual = reader(input_field)
        except Exception:
            continue
        if actual == text:
            return True
    return False


def _fill_with_value_pattern(input_field, text: str) -> bool:
    """首选：ValuePattern.SetValue，不需要焦点。"""
    current = _read_input_text(input_field)
    if current is None or current.strip():
        return False
    if not set_value_pattern(input_field, text):
        return False
    time.sleep(0.1)
    return _input_matches(input_field, text)


def _activate_window(hwnd: int) -> bool:
    """把微信置于前台；不调用 ShowWindow。"""
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    current_thread = win32api.GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    foreground_thread = (
        win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    )
    attached = False
    try:
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(
                ctypes.windll.user32.AttachThreadInput(
                    current_thread, foreground_thread, True
                )
            )
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            ctypes.windll.user32.AttachThreadInput(
                current_thread, foreground_thread, False
            )

    time.sleep(0.2)
    return win32gui.GetForegroundWindow() == hwnd


def _focus_input(input_field) -> bool:
    """让输入框获得键盘焦点；SetFocus 失败时点击输入框中心。"""
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
        original_cursor = win32api.GetCursorPos()
        try:
            win32api.SetCursorPos((x, y))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        finally:
            win32api.SetCursorPos(original_cursor)
        time.sleep(0.15)
        return bool(getattr(input_field, "HasKeyboardFocus", False))
    except Exception:
        return False


def _fill_with_sendinput(hwnd: int, input_field, text: str) -> bool:
    """回退：聚焦输入框后用 SendInput 注入 Unicode 字符。"""
    original_foreground = win32gui.GetForegroundWindow()
    original_cursor = win32api.GetCursorPos()

    try:
        if not _activate_window(hwnd):
            return False
        if not _focus_input(input_field):
            return False

        current = _read_input_text(input_field)
        if current is None or current.strip():
            return False

        _clear_via_keyboard()
        time.sleep(0.05)
        _send_inputs(_unicode_inputs(text))
        time.sleep(0.15)

        if _input_matches(input_field, text):
            return True

        # 校验失败时尽量清掉半成品，避免留下脏内容
        _clear_via_keyboard()
        return False
    finally:
        try:
            win32api.SetCursorPos(original_cursor)
        except Exception:
            pass
        if original_foreground:
            try:
                win32gui.SetForegroundWindow(original_foreground)
            except Exception:
                pass


def _normalize_identity_value(value):
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return value


def _session_mismatches(expected: dict, actual: dict) -> list[str]:
    """比较调用方给出的会话身份字段，返回不一致描述。"""
    checks = [
        ("chat_name", "会话名"),
        ("type", "会话类型"),
        ("member_count", "成员数"),
        ("selected_session_name", "选中会话名"),
        ("session_automation_id", "会话 AutomationId"),
        ("session_runtime_id", "会话 RuntimeId"),
    ]
    mismatches = []
    for key, label in checks:
        if key not in expected:
            continue
        want = expected[key]
        if want is None:
            continue
        if isinstance(want, str) and not want.strip():
            continue
        got = actual.get(key)
        if _normalize_identity_value(want) != _normalize_identity_value(got):
            mismatches.append(f"{label}: 期望 {want!r}，实际 {got!r}")
    return mismatches


def fill_input(
    text: str,
    chat_name: str | None = None,
    hwnd: int | None = None,
    *,
    session: dict | None = None,
    wake_minimized: bool = False,
    wake_tray: bool = False,
) -> dict:
    """把 text 填入当前会话输入框，返回写入结果。

    会话匹配支持两种方式：
    - chat_name：只比较会话名，较弱；
    - session：传入 get_session_identity() 的结果或同结构字典，可同时比较
      会话名、类型、成员数、选中项 AutomationId/RuntimeId。只比较
      session 中实际提供的非 None 字段。

    输入框在 str.strip() 意义下必须为空。会话不匹配抛
    ChatMismatchError，输入框已有内容抛 InputNotEmptyError，不覆盖也不
    追加。两种写入方式都失败时抛 InputFillError。
    """
    normalized = normalize_text(text)

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

    snapshot = scan_window(
        hwnd, wake_minimized=wake_minimized, wake_tray=wake_tray
    )
    if not snapshot.uia_available:
        raise UIAUnavailableError(
            "微信 UI Automation 控件树不可用（可能未物化，或微信版本不匹配）"
        )

    current_name = (snapshot.chat_name or "").strip()
    if current_name != expected["chat_name"]:
        raise ChatMismatchError(
            f"当前会话是 {current_name!r}，不是指定的 {expected['chat_name']!r}"
        )

    chat = classify_snapshot(snapshot)
    actual = {
        "chat_name": current_name,
        "type": chat.get("type"),
        "member_count": chat.get("member_count"),
    }
    if any(
        key in expected
        for key in (
            "selected_session_name",
            "session_automation_id",
            "session_runtime_id",
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

    mismatches = _session_mismatches(expected, actual)
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

    current_text = _read_input_text(input_field)
    if current_text is None:
        raise InputFillError("无法读取输入框内容，不能确认输入框为空")
    if current_text.strip():
        raise InputNotEmptyError(
            f"输入框已有内容，拒绝覆盖或追加：{current_text!r}"
        )

    if _fill_with_value_pattern(input_field, normalized):
        method = "value_pattern"
    elif _fill_with_sendinput(snapshot.hwnd, input_field, normalized):
        method = "sendinput"
    else:
        # 两种方式都失败时尽量清掉半成品，避免留下脏内容
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
        "text": normalized,
        "method": method,
        "verified": True,
    }


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def _runtime_id(value: str) -> tuple[int, ...]:
    try:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError
        return tuple(int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "RuntimeId 必须是逗号分隔的整数"
        ) from exc


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
        "--chat-type",
        help="可选：期望的会话类型，如 private / group / official",
    )
    parser.add_argument(
        "--member-count",
        type=_positive_int,
        help="可选：期望的群成员数",
    )
    parser.add_argument(
        "--session-automation-id",
        help="可选：期望的选中会话项 AutomationId",
    )
    parser.add_argument(
        "--session-runtime-id",
        type=_runtime_id,
        help="可选：期望的选中会话项 RuntimeId，逗号分隔的整数",
    )
    parser.add_argument(
        "--wake-minimized",
        action="store_true",
        help="允许自动还原最小化的微信窗口（默认不允许）",
    )
    parser.add_argument(
        "--wake-tray",
        action="store_true",
        help="允许通过微信原生托盘回调唤起隐藏到托盘的微信（默认不允许）",
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

    try:
        result = fill_input(
            args.text,
            session=session,
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
        print("校验     : 通过")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
