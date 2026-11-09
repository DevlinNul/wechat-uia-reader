"""用截图判断消息是“我”还是“其他人”发送的。

只处理 ``mmui::ChatTextItemView`` 文本消息；图片、语音、文件、卡片、
时间分隔项一律跳过，不点击、不聚焦。

判定只看几何位置，不使用气泡颜色：

- 非背景内容整体贴左 -> other
- 非背景内容整体贴右 -> self
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

RUN_THRESHOLD = 12
COLUMN_RATIO = 0.10
MIN_COLUMN_COUNT = 2
MIN_RUN_PIXELS = 24
MIN_VISIBLE_HEIGHT = 16
EDGE_MARGIN = 20


def capture_region(rect):
    """截取屏幕区域，返回 ``(bgra_bytes, width, height)``；失败返回 None。

    ``rect`` 使用项目统一的 ``(x, y, width, height)`` 格式。
    """
    left, top, width, height = (int(v) for v in rect)
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


def sample_background(bits, width, height):
    """从消息列表左边缘采样背景色；整页复用一个背景色。"""
    if not bits or width <= 0 or height <= 0:
        return None
    stride = width * 4
    samples = []
    for row in range(0, height, 2):
        base = row * stride
        for column in range(0, min(4, width)):
            offset = base + column * 4
            samples.append(
                (bits[offset + 2], bits[offset + 1], bits[offset])
            )
    if not samples:
        return None
    return tuple(_median([sample[i] for sample in samples]) for i in range(3))


def _runs_from_columns(columns):
    """把连续列下标合并为区间。"""
    if not columns:
        return []
    runs = []
    start = prev = columns[0]
    for column in columns[1:]:
        if column == prev + 1:
            prev = column
            continue
        runs.append((start, prev))
        start = prev = column
    runs.append((start, prev))
    return runs


def analyze_row(bits, width, height, row_rect, background=None):
    """分析一条文本消息行，返回 ``(direction, left_gap, right_gap)``。

    ``row_rect`` 是相对截图左上角的 ``(x, y, w, h)``。
    ``background`` 可以传整页消息列表背景色；不传时从行左边缘估计。

    判定使用“所有有效非背景列”的整体跨度，而不是最长单段。这样即使
    消息里有引用块、多个文本段，也不会只看到中间一段就误判。
    """
    x, y, w, h = (int(v) for v in row_rect)
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + w)
    y1 = min(height, y + h)
    if x1 - x0 < 8 or y1 - y0 < MIN_VISIBLE_HEIGHT:
        return DIRECTION_UNKNOWN, None, None

    stride = width * 4
    if background is None:
        samples = []
        for row in range(y0, y1, 2):
            base = row * stride
            for column in range(x0, min(x0 + 4, x1)):
                offset = base + column * 4
                samples.append(
                    (bits[offset + 2], bits[offset + 1], bits[offset])
                )
        if not samples:
            return DIRECTION_UNKNOWN, None, None
        background = tuple(
            _median([sample[i] for sample in samples]) for i in range(3)
        )

    # 按列统计非背景像素；横向隔列采样，避免逐像素循环太慢。
    column_count = (x1 - x0 + 1) // 2
    counts = [0] * column_count
    for row in range(y0, y1, 2):
        base = row * stride
        index = 0
        for column in range(x0, x1, 2):
            offset = base + column * 4
            if (
                max(
                    abs(bits[offset] - background[0]),
                    abs(bits[offset + 1] - background[1]),
                    abs(bits[offset + 2] - background[2]),
                )
                > RUN_THRESHOLD
            ):
                counts[index] += 1
            index += 1

    sampled_rows = max(1, (y1 - y0) // 2)
    threshold = max(
        MIN_COLUMN_COUNT, int(sampled_rows * COLUMN_RATIO)
    )
    columns = [
        index for index, count in enumerate(counts) if count >= threshold
    ]
    runs = [
        run
        for run in _runs_from_columns(columns)
        if (run[1] - run[0] + 1) * 2 >= MIN_RUN_PIXELS
    ]
    if not runs:
        return DIRECTION_UNKNOWN, None, None

    # 用所有有效区间的整体跨度，而不是最长的一段。
    union_left = x0 + runs[0][0] * 2
    union_right = x0 + runs[-1][1] * 2
    left_gap = union_left - x0
    right_gap = (x1 - 1) - union_right

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
    background = sample_background(bits, width, height)

    for record in text_records:
        x, y, w, h = record["rect"]
        direction, _, _ = analyze_row(
            bits,
            width,
            height,
            (x - list_x, y - list_y, w, h),
            background=background,
        )
        record["direction"] = direction
        record["direction_source"] = (
            "screenshot" if direction != DIRECTION_UNKNOWN else None
        )
