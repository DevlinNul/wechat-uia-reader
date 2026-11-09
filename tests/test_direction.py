"""截图方向判断的纯逻辑测试（不截真实屏幕）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core import direction


WIDTH = 720
HEIGHT = 112
STRIDE = WIDTH * 4


def make_image(left, right, top=30, bottom=80):
    return make_multi_image([(left, right, top, bottom)])


def make_multi_image(blocks):
    data = bytearray(WIDTH * HEIGHT * 4)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            offset = y * STRIDE + x * 4
            foreground = any(
                top <= y <= bottom and left <= x <= right
                for left, right, top, bottom in blocks
            )
            value = 100 if foreground else 240
            data[offset] = value
            data[offset + 1] = value
            data[offset + 2] = value
            data[offset + 3] = 255
    return bytes(data)


class AnalyzeRowTest(unittest.TestCase):
    def test_bubble_on_left_is_other(self):
        bits = make_image(50, 400)
        result, left_gap, right_gap = direction.analyze_row(
            bits, WIDTH, HEIGHT, (0, 0, WIDTH, HEIGHT)
        )
        self.assertEqual(result, direction.DIRECTION_OTHER)
        self.assertLess(left_gap, right_gap)

    def test_bubble_on_right_is_self(self):
        bits = make_image(320, 680)
        result, left_gap, right_gap = direction.analyze_row(
            bits, WIDTH, HEIGHT, (0, 0, WIDTH, HEIGHT)
        )
        self.assertEqual(result, direction.DIRECTION_SELF)
        self.assertLess(right_gap, left_gap)

    def test_union_uses_outer_span_with_multiple_runs(self):
        # 中间一段比左侧一段更长；只看最长段会判不出，看整体跨度应判 other。
        bits = make_multi_image(
            [
                (50, 100, 30, 80),
                (300, 450, 30, 80),
            ]
        )
        result, left_gap, right_gap = direction.analyze_row(
            bits, WIDTH, HEIGHT, (0, 0, WIDTH, HEIGHT)
        )
        self.assertEqual(result, direction.DIRECTION_OTHER)
        self.assertLess(left_gap, right_gap)

    def test_empty_row_is_unknown(self):
        bits = make_image(0, 0, top=-1, bottom=-1)
        result, _, _ = direction.analyze_row(
            bits, WIDTH, HEIGHT, (0, 0, WIDTH, HEIGHT)
        )
        self.assertEqual(result, direction.DIRECTION_UNKNOWN)


class CaptureRegionTest(unittest.TestCase):
    def test_uses_xywh_coordinates(self):
        src = mock.Mock()
        mem = mock.Mock()
        bmp = mock.Mock()
        src.CreateCompatibleDC.return_value = mem
        src.CreateCompatibleBitmap.return_value = bmp
        bmp.GetBitmapBits.return_value = b"\0" * (10 * 5 * 4)
        with (
            mock.patch.object(direction.win32gui, "GetDC", return_value=1),
            mock.patch.object(
                direction.win32ui, "CreateDCFromHandle", return_value=src
            ),
            mock.patch.object(
                direction.win32ui, "CreateBitmap", return_value=bmp
            ),
            mock.patch.object(direction.win32gui, "ReleaseDC"),
            mock.patch.object(direction.win32gui, "DeleteObject"),
        ):
            captured = direction.capture_region((100, 200, 10, 5))

        self.assertEqual(captured[1:], (10, 5))
        mem.BitBlt.assert_called_once_with(
            (0, 0), (10, 5), src, (100, 200), direction.win32con.SRCCOPY
        )


class AnnotateRecordsTest(unittest.TestCase):
    def test_only_text_records_are_analyzed(self):
        bits = make_image(50, 400)
        message_list = SimpleNamespace(
            BoundingRectangle=SimpleNamespace(
                left=0, top=0, right=WIDTH, bottom=HEIGHT
            )
        )
        records = [
            {
                "class": "mmui::ChatTextItemView",
                "rect": (0, 0, WIDTH, HEIGHT),
                "text": "文本",
            },
            {
                "class": "mmui::ChatImageItemView",
                "rect": (0, 0, WIDTH, HEIGHT),
                "text": "图片",
            },
        ]
        with (
            mock.patch.object(
                direction, "point_belongs_to_window", return_value=True
            ),
            mock.patch.object(
                direction,
                "capture_region",
                return_value=(bits, WIDTH, HEIGHT),
            ),
        ):
            direction.annotate_records(message_list, records, 123)

        self.assertEqual(records[0]["direction"], "other")
        self.assertEqual(records[0]["direction_source"], "screenshot")
        self.assertEqual(records[1]["direction"], "unknown")
        self.assertIsNone(records[1]["direction_source"])

    def test_occluded_list_stays_unknown(self):
        message_list = SimpleNamespace(
            BoundingRectangle=SimpleNamespace(
                left=0, top=0, right=WIDTH, bottom=HEIGHT
            )
        )
        records = [
            {
                "class": "mmui::ChatTextItemView",
                "rect": (0, 0, WIDTH, HEIGHT),
                "text": "文本",
            }
        ]
        with (
            mock.patch.object(
                direction, "point_belongs_to_window", return_value=False
            ),
            mock.patch.object(direction, "capture_region") as capture,
        ):
            direction.annotate_records(message_list, records, 123)

        self.assertEqual(records[0]["direction"], "unknown")
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
