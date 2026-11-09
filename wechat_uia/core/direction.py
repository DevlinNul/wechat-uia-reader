"""用截图判断消息是“我”还是“其他人”发送的。

只处理 ``mmui::ChatTextItemView`` 文本消息；图片、语音、文件、卡片、
时间分隔项一律跳过，不点击、不聚焦。

判定依据是气泡在消息行内的水平位置：

- 气泡贴左 -> other
- 气泡贴右 -> self
- 判不出 -> unknown

本模块只依赖 pywin32，不引入 Pillow / NumPy。
"""

import win32con
import win32gui
import win32ui

from .get_hwnd import point_belongs_to_window
from .uia_utils import enable_dpi_awareness, rect_xywh

DIRECTION_SELF = "self"
DIRECTION_OTHER = "other"
DIRECTION_UNKNOWN = "unknown"

TEXT_MESSAGE_CLASS = "mmui::ChatTextItemView"

BAND_TOP = 0.30
BAND_BOTTOM = 0.70
COLOR_THRESHOLD = 25
COLUMN_RATIO = 0.25
EDGE_MARGIN = 20
MIN_RUN_COLUMNS = 4


def capture_region(rect):
    """截取屏幕区域，返回 ``(bgra_bytes, width, height)``；失败返回 None。"""
    left, top, right, bottom = (int(v) for v in rect)
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None

    screen = win32gui.GetDC(0)
    src = None
    mem = None
    bmp = None
    try:
        src = win32ui.CreateDCFromHandle(screen)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, width, height)
        mem.SelectObject(bmp)
        mem.BitBlt(
            (0, 0), (width, height), src, (left, top), win32con.SRCCOPY
        )
        bits = bmp.GetBitmapBits(True)
        if len(bits) < width * height * 4:
            return None
        return bits, width, height
    except Exception:
        return None
    finally:
        try:
            if mem is not None:
                mem.DeleteDC()
        except Exception:
            pass
        try:
            if src is not None:
                src.DeleteDC()
        except Exception:
            pass
        try:
            win32gui.ReleaseDC(0, screen)
        except Exception:
            pass
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _longest_run(columns):
    """返回最长连续列区间 (start, end) 的下标；没有时返回 None。"""
    if not columns:
        return None
    best = (columns[0], columns[0])
    best_len = 1
    start = prev = columns[0]
    for column in columns[1:]:
        if column == prev + 1:
            prev = column
            continue
        length = prev - start + 1
        if length > best_len:
            best_len = length
            best = (start, prev)
        start = prev = column
    length = prev - start + 1
    if length > best_len:
        best = (start, prev)
    return best


def analyze_row(bits, width, height, row_rect):
    """分析一条文本消息行，返回 ``(direction, left_gap, right_gap)``。

    ``row_rect`` 是相对截图左上角的 ``(x, y, w, h)``。
    """
    x, y, w, h = (int(v) for v in row_rect)
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + w)
    y1 = min(height, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return DIRECTION_UNKNOWN, None, None

    stride = width * 4
    band_top = y0 + int((y1 - y0) * BAND_TOP)
    band_bottom = y0 + int((y1 - y0) * BAND_BOTTOM)
    band_bottom = max(band_top + 2, min(band_bottom, y1))

    # 背景色：取行左右边缘像素的中位数，兼容深浅主题。
    samples = []
    for row in range(band_top, band_bottom, 2):
        base = row * stride
        for column in list(range(x0, min(x0 + 4, x1))) + list(
            range(max(x0, x1 - 4), x1)
        ):
            offset = base + column * 4
            samples.append(
                (bits[offset + 2], bits[offset + 1], bits[offset])
            )
    if not samples:
        return DIRECTION_UNKNOWN, None, None
    background = tuple(
        _median([sample[i] for sample in samples]) for i in range(3)
    )

    # 按列统计“与背景明显不同”的像素；横向隔列采样即可。
    column_count = (x1 - x0 + 1) // 2
    counts = [0] * column_count
    for row in range(band_top, band_bottom, 2):
        base = row * stride
        index = 0
        for column in range(x0, x1, 2):
            offset = base + column * 4
            blue = bits[offset]
            green = bits[offset + 1]
            red = bits[offset + 2]
            if (
                max(
                    abs(blue - background[0]),
                    abs(green - background[1]),
                    abs(red - background[2]),
                )
                > COLOR_THRESHOLD
            ):
                counts[index] += 1
            index += 1

    threshold = max(
        1, int(((band_bottom - band_top) // 2) * COLUMN_RATIO)
    )
    columns = [
        index for index, count in enumerate(counts) if count >= threshold
    ]
    run = _longest_run(columns)
    if run is None:
        return DIRECTION_UNKNOWN, None, None
    start, end = run
    if end - start + 1 < MIN_RUN_COLUMNS:
        return DIRECTION_UNKNOWN, None, None

    run_left = x0 + start * 2
    run_right = x0 + end * 2
    left_gap = run_left - x0
    right_gap = (x1 - 1) - run_right

    if left_gap + EDGE_MARGIN < right_gap:
        return DIRECTION_OTHER, left_gap, right_gap
    if right_gap + EDGE_MARGIN < left_gap:
        return DIRECTION_SELF, left_gap, right_gap
    return DIRECTION_UNKNOWN, left_gap, right_gap


def annotate_records(message_list, records, hwnd: int) -> None:
    """给消息记录补上 ``direction`` / ``direction_source``。

    只分析 ``ChatTextItemView``；其他消息一律 unknown。要求消息列表
    当前可见，否则不猜测。
    """
    for record in records:
        record["direction"] = DIRECTION_UNKNOWN
        record["direction_source"] = None

    text_records = [
        record
        for record in records
        if record.get("class") == TEXT_MESSAGE_CLASS
        and record.get("rect") is not None
    ]
    if not text_records or not message_list:
        return

    list_rect = rect_xywh(message_list.BoundingRectangle)
    list_x, list_y, list_w, list_h = list_rect
    center_x = list_x + list_w // 2
    center_y = list_y + list_h // 2
    if not point_belongs_to_window(center_x, center_y, hwnd):
        return

    enable_dpi_awareness()
    captured = capture_region(list_rect)
    if captured is None:
        return
    bits, width, height = captured

    for record in text_records:
        x, y, w, h = record["rect"]
        direction, _, _ = analyze_row(
            bits,
            width,
            height,
            (x - list_x, y - list_y, w, h),
        )
        record["direction"] = direction
        record["direction_source"] = (
            "screenshot" if direction != DIRECTION_UNKNOWN else None
        )
