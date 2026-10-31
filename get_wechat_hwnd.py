"""获取微信窗口句柄。

支持微信 3.x (WeChat.exe) 与微信 4.x (Weixin.exe)。
用法: python get_wechat_hwnd.py
"""

import ctypes
import sys
from ctypes import wintypes

import win32con
import win32gui
import win32process

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 已知的微信主窗口类名
KNOWN_CLASSES = {
    "WeChatMainWndForPC",
    "mmui::MainWindow",
    "WeChatLoginWndForPC",
    "Qt51514QWindowIcon",
}

PROCESS_NAMES = {"wechat.exe", "weixin.exe"}


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


def is_wechat_window(hwnd: int, title: str, class_name: str) -> bool:
    """判断窗口是否属于微信。"""
    if class_name in KNOWN_CLASSES:
        return True
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    name = get_process_name(pid).lower()
    if name in PROCESS_NAMES:
        return True
    return "微信" in title or "wechat" in title.lower()


def collect_windows() -> list[dict]:
    """枚举所有顶层窗口并筛选出微信窗口。"""
    results = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        if not is_wechat_window(hwnd, title, class_name):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        results.append(
            {
                "hwnd": hwnd,
                "hex": hex(hwnd),
                "title": title,
                "class": class_name,
                "pid": pid,
                "process": get_process_name(pid),
                "rect": (left, top, right - left, bottom - top),
            }
        )
        return True

    win32gui.EnumWindows(callback, None)
    return results


def main() -> int:
    windows = collect_windows()
    if not windows:
        print("未找到微信窗口，请确认微信已启动。")
        return 1

    print(f"找到 {len(windows)} 个微信相关窗口:\n")
    for w in windows:
        x, y, width, height = w["rect"]
        print(f"句柄    : {w['hex']} ({w['hwnd']})")
        print(f"标题    : {w['title']!r}")
        print(f"类名    : {w['class']}")
        print(f"进程    : {w['process']} (PID {w['pid']})")
        print(f"位置尺寸: x={x}, y={y}, w={width}, h={height}")
        print("-" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
