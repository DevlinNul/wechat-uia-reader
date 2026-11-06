"""按需向上滚动，读取当前会话更早的消息。

该模块只负责“历史采集”这一有副作用的动作：
- 使用鼠标滚轮向上滚动，不使用键盘，避免误触输入框；
- 以 RuntimeId + 类名 + 文本作为消息去重键（Qt 会复用 RuntimeId）；
- 有明确的 limit / max_scrolls / timeout 上限；
- 默认不恢复滚动位置；需要时显式开启，并会把原始视口的第一条消息滚回顶部。

无滚动的单次读取仍由 get_messages.get_messages() 提供。
"""

import ctypes
import time

import win32api
import win32con
import win32gui
import win32process

from .errors import MessageListUnavailableError
from .get_messages import read_item

WHEEL_DELTA = 120
# 无变化时单轮最多等多久。正常滚动会提前返回；只有顶部/图片预览加载
# 阻塞时才会等满，因此这里可以放宽一些。
SCROLL_CHANGE_TIMEOUT = 0.8
# 连续多少次“滚不动且没有新消息”才判定到顶。图片预览加载可能短暂
# 阻塞滚动，2 次容易误判，改为 3 次。
TOP_CONFIRMATIONS = 3
POLL_INTERVAL = 0.01


def visible_signature(message_list) -> tuple:
    """轻量视口签名，只读首尾项，用于判断滚轮是否生效。"""
    items = [
        item
        for item in message_list.GetChildren()
        if item.ControlTypeName == "ListItemControl"
    ]
    if not items:
        return (0, None, None)

    def runtime_id(item):
        """安全读取 RuntimeId；失败时返回 None。"""
        try:
            return tuple(item.GetRuntimeId())
        except Exception:
            return None

    return (len(items), runtime_id(items[0]), runtime_id(items[-1]))


def _wait_for_change(message_list, before_signature, timeout: float) -> bool:
    """轮询视口签名，直到滚动生效并稳定下来，或超时。

    只判断“变化”会在列表仍在滚动时读到中间状态；这里要求变化后
    连续两次签名相同，才认为这一格滚动结束。
    """
    deadline = time.monotonic() + timeout
    changed = False
    last_signature = None
    stable = 0

    while time.monotonic() < deadline:
        signature = visible_signature(message_list)
        if signature != before_signature:
            changed = True

        if changed:
            if signature == last_signature:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
                last_signature = signature
        time.sleep(POLL_INTERVAL)

    return changed


def read_visible(message_list) -> list[dict]:
    """读取消息列表当前视口内的消息项。"""
    records = []
    for item in message_list.GetChildren():
        if item.ControlTypeName != "ListItemControl":
            continue

        record = read_item(item, 0)
        try:
            runtime_id = tuple(item.GetRuntimeId())
        except Exception:
            runtime_id = None

        # RuntimeId 会被 Qt 复用（实测时间分隔项会复用），不能单独作为身份；
        # 必须叠加类名与文本，避免把不同的时间项合并掉。
        identity = (record["class"], record["text"], tuple(record["parts"]))
        record["_key"] = (
            ("runtime", runtime_id, identity) if runtime_id else ("fallback", identity)
        )
        # 恢复位置时不能用 RuntimeId：长距离滚动后控件可能被重建。
        # 内容键只用于恢复锚点，不参与采集去重。
        record["_restore_key"] = identity
        records.append(record)
    return records


def merge_older_page(
    collected: list[dict], page: list[dict], anchor_key
) -> tuple[int, bool]:
    """把新一页中位于锚点上方的消息合并到 collected 前面。

    返回 (新增条数, 是否找到锚点)。锚点是滚动前视口的第一条消息。
    """
    page_keys = [record["_key"] for record in page]
    anchor_index = page_keys.index(anchor_key) if anchor_key in page_keys else None
    candidates = page[:anchor_index] if anchor_index is not None else page

    seen = {record["_key"] for record in collected}
    older = [record for record in candidates if record["_key"] not in seen]
    collected[:0] = older
    return len(older), anchor_index is not None


def trim_to_limit(messages: list[dict], limit: int) -> list[dict]:
    """超出上限时保留较新的消息（列表按时间正序）。"""
    if len(messages) <= limit:
        return messages
    return messages[-limit:]


def anchor_position_matches(
    page: list[dict], anchor_key, anchor_index: int, tolerance: int = 1
) -> bool:
    """锚点是否在原始下标附近（允许一格误差，避免时间分隔项不可见时误判）。"""
    start = max(0, anchor_index - tolerance)
    end = min(len(page), anchor_index + tolerance + 1)
    return any(page[index]["_restore_key"] == anchor_key for index in range(start, end))


def choose_restore_anchor(page: list[dict]) -> tuple[object, int]:
    """选择恢复滚动位置的锚点。

    优先第一条有实际文本的文本消息；图片、语音、时间分隔项的文本往往是
    “图片”“语音”“星期一”等通用值，Qt 又会复用 RuntimeId，不适合作为
    恢复锚点。找不到文本消息时再退回第一条非时间消息。
    """
    for index, record in enumerate(page):
        if record["kind"] == "text" and record["text"].strip():
            return record["_restore_key"], index
    for index, record in enumerate(page):
        if record["kind"] != "time":
            return record["_restore_key"], index
    if page:
        return page[0]["_restore_key"], 0
    return None, 0


def _wheel(notches: int) -> None:
    """正数向上滚，负数向下滚。"""
    win32api.mouse_event(
        win32con.MOUSEEVENTF_WHEEL,
        0,
        0,
        notches * WHEEL_DELTA,
        0,
    )


def _center_of(message_list) -> tuple[int, int]:
    """返回消息列表的屏幕中心点。"""
    rect = message_list.BoundingRectangle
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def _move_cursor(message_list) -> None:
    """把鼠标移到消息列表中心，确保滚轮落在列表上。"""
    win32api.SetCursorPos(_center_of(message_list))


def _activate_window(hwnd: int) -> bool:
    """尽力把微信置于前台，用于让鼠标滚轮落到消息列表上。"""
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


def _restore_foreground(hwnd: int) -> None:
    """尽力把原前台窗口恢复为前台。"""
    if not hwnd or win32gui.GetForegroundWindow() == hwnd:
        return
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _restore_scroll(
    message_list,
    anchor_key,
    anchor_index: int,
    *,
    steps: int,
    scroll_step: int,
    settle: float,
) -> bool:
    """向下滚动，直到锚点消息回到原始视口中的位置。

    时间分隔项会被 Qt 复用或改写，不能作为恢复锚点；调用方应优先传入
    视口中第一条非时间消息及其原始下标。
    """
    if anchor_key is None:
        return False

    def is_exact(page) -> bool:
        """锚点是否正好位于原始下标。"""
        return (
            len(page) > anchor_index
            and page[anchor_index]["_restore_key"] == anchor_key
        )

    def is_close(page) -> bool:
        """锚点是否落在原始下标附近。"""
        return anchor_position_matches(page, anchor_key, anchor_index)

    def has_key(page) -> bool:
        """当前视口是否包含锚点。"""
        return any(record["_restore_key"] == anchor_key for record in page)

    # 粗调：向下回滚。滚轮向下通常比向上"走得少"，因此不能只回滚
    # 与采集相同的次数；多给一倍余量，找到锚点或到底后立即停止。
    last_keys = None
    no_change = 0
    for _ in range(steps * 2 + 4):
        page = read_visible(message_list)
        if is_exact(page):
            return True
        if has_key(page):
            break

        keys = tuple(record["_key"] for record in page)
        if keys == last_keys:
            no_change += 1
            if no_change >= 2:
                break
        else:
            no_change = 0
            last_keys = keys

        before_signature = visible_signature(message_list)
        _move_cursor(message_list)
        _wheel(-scroll_step)
        _wait_for_change(message_list, before_signature, SCROLL_CHANGE_TIMEOUT)
        time.sleep(settle)

    # 细调：根据锚点当前下标决定向上还是向下，直到回到原始下标
    for _ in range(scroll_step * 2 + 8):
        page = read_visible(message_list)
        if is_exact(page):
            return True

        indices = [
            index
            for index, record in enumerate(page)
            if record["_restore_key"] == anchor_key
        ]
        if not indices:
            # 锚点已滚出视口，回退一格
            before_signature = visible_signature(message_list)
            _move_cursor(message_list)
            _wheel(1)
            _wait_for_change(message_list, before_signature, SCROLL_CHANGE_TIMEOUT)
            time.sleep(settle)
            continue

        current_index = indices[0]
        before_signature = visible_signature(message_list)
        _move_cursor(message_list)
        if current_index > anchor_index:
            _wheel(-1)   # 向下：让锚点向顶部移动
        elif current_index < anchor_index:
            _wheel(1)    # 向上：把锚点上方的项拉回视口
        else:
            return True
        _wait_for_change(message_list, before_signature, SCROLL_CHANGE_TIMEOUT)
        time.sleep(settle)

    # 精确位置不可达时，允许一格误差，避免时间分隔项不可见导致整体失败
    return is_close(read_visible(message_list))


def _finalize(messages: list[dict]) -> list[dict]:
    """清理内部字段并重排 index；历史消息的 rect 统一置空。"""
    for index, message in enumerate(messages):
        message["index"] = index
        message.pop("_key", None)
        message.pop("_restore_key", None)
        message.pop("runtime_id", None)
        # 历史消息来自不同滚动位置，屏幕矩形已经无意义
        message["rect"] = None
    return messages


def _scroll_meta(
    *,
    enabled: bool,
    limit: int,
    max_scrolls: int,
    steps: int,
    reached_top: bool | None,
    truncated: bool,
    limit_hit: bool,
    restored: bool | None,
    timed_out: bool,
    warnings: list[str],
    elapsed: float,
) -> dict:
    """组装 scroll 元数据。"""
    return {
        "enabled": enabled,
        "limit": limit,
        "max_scrolls": max_scrolls,
        "steps": steps,
        "reached_top": reached_top,
        "truncated": truncated,
        "limit_hit": limit_hit,
        "restored": restored,
        "timed_out": timed_out,
        "warnings": warnings,
        "elapsed_seconds": round(elapsed, 3),
    }


def collect_messages(
    snapshot,
    *,
    limit: int = 20,
    max_scrolls: int = 60,
    scroll_step: int = 1,
    settle: float = 0.0,
    timeout: float = 20.0,
    restore: bool = False,
    scroll: bool = True,
) -> dict:
    """从当前视口开始向上滚动，尽可能读取更多消息。

    返回 {messages, count, scroll}；messages 按时间正序。
    """
    if limit <= 0:
        raise ValueError("limit 必须大于 0")
    if max_scrolls < 0:
        raise ValueError("max_scrolls 不能小于 0")
    if scroll_step <= 0:
        raise ValueError("scroll_step 必须大于 0")
    if settle < 0:
        raise ValueError("settle 不能小于 0")
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")

    if max_scrolls == 0:
        scroll = False

    message_list = snapshot.message_list
    if message_list is None:
        raise MessageListUnavailableError("未找到消息列表，无法读取聊天记录")

    started = time.monotonic()
    warnings: list[str] = []

    if not scroll:
        messages = read_visible(message_list)
        limit_hit = len(messages) > limit
        messages = trim_to_limit(messages, limit)
        _finalize(messages)
        return {
            "messages": messages,
            "count": len(messages),
            "scroll": _scroll_meta(
                enabled=False,
                limit=limit,
                max_scrolls=max_scrolls,
                steps=0,
                reached_top=None,
                truncated=True,
                limit_hit=limit_hit,
                restored=True,
                timed_out=False,
                warnings=warnings,
                elapsed=time.monotonic() - started,
            ),
        }

    original_foreground = win32gui.GetForegroundWindow()
    original_cursor = win32api.GetCursorPos()

    # 先置前再读初始视口：微信在前台/后台时窗口几何可能变化，
    # 若先读再置前，恢复位置会以错误的视口为基准。
    activated = _activate_window(snapshot.hwnd)
    if not activated:
        warnings.append("未能将微信置于前台，滚动可能无效")
    time.sleep(settle)
    original_page = read_visible(message_list)

    if not original_page:
        return {
            "messages": [],
            "count": 0,
            "scroll": _scroll_meta(
                enabled=True,
                limit=limit,
                max_scrolls=max_scrolls,
                steps=0,
                reached_top=True,
                truncated=False,
                limit_hit=False,
                restored=True,
                timed_out=False,
                warnings=warnings,
                elapsed=time.monotonic() - started,
            ),
        }

    original_top_key = original_page[0]["_key"]
    original_anchor_key, original_anchor_index = choose_restore_anchor(original_page)

    collected = original_page
    top_key = original_top_key

    steps = 0
    stable = 0
    reached_top = False
    timed_out = False
    limit_hit = False
    over_limit = False
    restored: bool | None = True

    try:
        while True:
            if len(collected) >= limit:
                limit_hit = True
                break
            if steps >= max_scrolls:
                break
            if time.monotonic() >= started + timeout:
                timed_out = True
                break

            before_top = top_key
            before_signature = visible_signature(message_list)
            _move_cursor(message_list)
            _wheel(scroll_step)
            steps += 1
            _wait_for_change(message_list, before_signature, SCROLL_CHANGE_TIMEOUT)
            time.sleep(settle)

            try:
                page = read_visible(message_list)
            except Exception as exc:
                warnings.append(f"滚动后读取失败：{type(exc).__name__}: {exc}")
                break

            if not page:
                warnings.append("滚动后未读取到消息项")
                break

            added, anchor_found = merge_older_page(collected, page, before_top)
            if not anchor_found:
                warnings.append("滚动后未找到重叠锚点，去重结果可能不精确")

            top_key = page[0]["_key"]
            if top_key == before_top and added == 0:
                stable += 1
            else:
                stable = 0

            if stable >= TOP_CONFIRMATIONS:
                if activated:
                    reached_top = True
                else:
                    warnings.append("无法确认是否到达顶部（微信未处于前台）")
                break

            if len(collected) >= limit:
                limit_hit = True
                break
    finally:
        over_limit = len(collected) > limit
        if over_limit:
            collected = collected[-limit:]
            limit_hit = True

        if steps > 0:
            if restore:
                restored = _restore_scroll(
                    message_list,
                    original_anchor_key,
                    original_anchor_index,
                    steps=steps,
                    scroll_step=scroll_step,
                    settle=settle,
                )
                if not restored:
                    warnings.append("未能恢复到原始滚动位置")
            else:
                restored = None

        win32api.SetCursorPos(original_cursor)
        _restore_foreground(original_foreground)

    _finalize(collected)
    return {
        "messages": collected,
        "count": len(collected),
        "scroll": _scroll_meta(
            enabled=True,
            limit=limit,
            max_scrolls=max_scrolls,
            steps=steps,
            reached_top=reached_top,
            truncated=(not reached_top) or over_limit,
            limit_hit=limit_hit,
            restored=restored,
            timed_out=timed_out,
            warnings=warnings,
            elapsed=time.monotonic() - started,
        ),
    }
