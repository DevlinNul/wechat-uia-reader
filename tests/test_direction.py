"""截图方向判断的纯逻辑测试（不截真实屏幕）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core import direction


WIDTH = 720
HEIGHT = 112
STRIDE = WIDTH * 4


def make_image(left, right, top=30, bottom=80):
    data = bytearray(WIDTH * HEIGHT * 4)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            offset = y * STRIDE + x * 4
            if top <= y <= bottom and left <= x <= right:
                blue = green = red = 100
            else:
                blue = green = red = 240
            data[offset] = blue
            data[offset + 1] = green
            data[offset + 2] = red
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

    def test_empty_row_is_unknown(self):
        bits = make_image(0, 0, top=-1, bottom=-1)
        result, _, _ = direction.analyze_row(
            bits, WIDTH, HEIGHT, (0, 0, WIDTH, HEIGHT)
        )
        self.assertEqual(result, direction.DIRECTION_UNKNOWN)


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
