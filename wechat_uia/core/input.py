"""聊天输入框的读取、写入和清空。

只操作输入框本身；不判断会话、不选窗口、不发送。
"""

import time

import win32api
import win32gui

from .errors import ClickTargetBlockedError
from .snapshot import (
    read_control_text,
    read_text_pattern,
    read_value_pattern,
    set_value_pattern,
)
from .ui_actions import (
    activate,
    clear_via_keyboard,
    focus_input,
    send_unicode,
)
from .timing import (
    CLEAR_SETTLE,
    FILL_VERIFY_INTERVAL,
    FILL_VERIFY_TIMEOUT,
    KEY_SETTLE,
    wait_until,
)


def read_input_text(input_field) -> str | None:
    """读取输入框当前文本；读取顺序由 core.snapshot 统一实现。"""
    return read_control_text(input_field)


def input_matches(input_field, text: str) -> bool:
    """用 ValuePattern 或 TextPattern 校验输入框内容。"""
    for reader in (read_value_pattern, read_text_pattern):
        try:
            actual = reader(input_field)
        except Exception:
            continue
        if actual == text:
            return True
    return False


def wait_input_matches(
    input_field, text: str, timeout: float = FILL_VERIFY_TIMEOUT
) -> bool:
    """在短时间内轮询输入框，直到内容与预期一致。"""
    return wait_until(
        lambda: input_matches(input_field, text),
        timeout,
        FILL_VERIFY_INTERVAL,
    )


def fill_with_value_pattern(input_field, text: str) -> bool:
    """首选：ValuePattern.SetValue，不需要焦点。"""
    current = read_input_text(input_field)
    if current is None or current.strip():
        return False
    if not set_value_pattern(input_field, text):
        return False
    return wait_input_matches(input_field, text)


def fill_with_sendinput(hwnd: int, input_field, text: str) -> bool:
    """回退：聚焦输入框后用 SendInput 注入 Unicode 字符。"""
    original_foreground = win32gui.GetForegroundWindow()
    original_cursor = win32api.GetCursorPos()

    try:
        if not activate(hwnd):
            return False
        if not focus_input(input_field, hwnd):
            return False

        current = read_input_text(input_field)
        if current is None or current.strip():
            return False

        clear_via_keyboard()
        time.sleep(KEY_SETTLE)
        send_unicode(text)
        if wait_input_matches(input_field, text):
            return True

        # 校验失败时尽量清掉半成品，避免留下脏内容
        clear_via_keyboard()
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


def clear_input(hwnd: int, input_field) -> bool:
    """清空输入框；优先 ValuePattern，失败时用 Ctrl+A/Delete。"""
    if set_value_pattern(input_field, "") and wait_input_matches(
        input_field, ""
    ):
        return True
    if not activate(hwnd):
        return False
    try:
        focused = focus_input(input_field, hwnd)
    except ClickTargetBlockedError:
        # 清理输入框时如果位置被占用，直接放弃，不碰其他窗口
        return False
    if not focused:
        return False
    clear_via_keyboard()
    time.sleep(CLEAR_SETTLE)
    return input_matches(input_field, "")
