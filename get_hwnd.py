"""获取微信窗口句柄。

支持微信 3.x (WeChat.exe) 与微信 4.x (Weixin.exe)。
不使用硬编码类名，而是按进程 + 窗口样式特征识别主窗口。

用法: python get_hwnd.py
"""

import ctypes
import sys
from ctypes import wintypes

import win32con
import win32gui
import win32process

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_NAMES = {"wechat.exe", "weixin.exe"}

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
    if user32.IsIconic(hwnd):
        return "minimized"
    if not user32.IsWindowVisible(hwnd):
        return "hidden"

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    if right <= vx or left >= vx + vw or bottom <= vy or top >= vy + vh:
        return "offscreen"
    return "normal"


STATE_HINTS = {
    "minimized": "微信窗口已最小化，请手动还原后再运行（本脚本不会主动唤起窗口）",
    "hidden": "微信窗口已隐藏到托盘，请手动还原后再运行（本脚本不会主动唤起窗口）",
    "offscreen": "微信窗口位于屏幕外，请手动还原后再运行（本脚本不会主动唤起窗口）",
}


def describe_window_state(hwnd: int) -> tuple[str, str | None]:
    """返回 (状态, 提示)。状态为 normal 时提示为 None。"""
    state = window_state(hwnd)
    return state, STATE_HINTS.get(state)


def window_area(hwnd: int) -> int:
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


def main() -> int:
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
    if hwnd:
        state, hint = describe_window_state(hwnd)
        print(f"\n主窗口句柄: {hex(hwnd)} ({hwnd})")
        print(f"窗口状态  : {state}")
        if hint:
            print(f"提示      : {hint}")
    else:
        print("\n未能确定主窗口句柄。")
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
