"""模拟鼠标点击前的系统命中测试（不移动真实鼠标）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

import importlib

fill_input = importlib.import_module("wechat_uia.api.fill_input")
from wechat_uia.core import get_hwnd
from wechat_uia.core.errors import ClickTargetBlockedError


class FakeControl:
    IsEnabled = True
    IsOffscreen = False
    NativeWindowHandle = 0

    def __init__(self, rect=(100, 100, 200, 200)):
        left, top, right, bottom = rect
        self.BoundingRectangle = SimpleNamespace(
            left=left, top=top, right=right, bottom=bottom
        )
        self.HasKeyboardFocus = False

    def SetFocus(self):
        pass

    def GetTopLevelControl(self):
        return SimpleNamespace(NativeWindowHandle=123)


class ClickControlTest(unittest.TestCase):
    def test_allows_click_when_target_window_is_hit(self):
        control = FakeControl()
        with (
            mock.patch.object(
                fill_input, "point_belongs_to_window", return_value=True
            ) as hit,
            mock.patch.object(
                fill_input.win32api, "GetCursorPos", return_value=(1, 2)
            ),
            mock.patch.object(
                fill_input.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                fill_input.win32api, "mouse_event"
            ) as mouse,
            mock.patch.object(fill_input.time, "sleep"),
        ):
            result = fill_input.click_control(control, 123)

        self.assertTrue(result)
        self.assertEqual(hit.call_count, 2)
        set_cursor.assert_any_call((150, 150))
        set_cursor.assert_any_call((1, 2))
        self.assertEqual(mouse.call_count, 2)

    def test_rejects_before_moving_when_other_window_is_on_top(self):
        control = FakeControl()
        with (
            mock.patch.object(
                fill_input, "point_belongs_to_window", return_value=False
            ),
            mock.patch.object(
                fill_input.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                fill_input.win32api, "mouse_event"
            ) as mouse,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                fill_input.click_control(control, 123)

        set_cursor.assert_not_called()
        mouse.assert_not_called()

    def test_rechecks_after_moving_and_never_clicks(self):
        control = FakeControl()
        with (
            mock.patch.object(
                fill_input,
                "point_belongs_to_window",
                side_effect=[True, False],
            ),
            mock.patch.object(
                fill_input.win32api, "GetCursorPos", return_value=(1, 2)
            ),
            mock.patch.object(
                fill_input.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                fill_input.win32api, "mouse_event"
            ) as mouse,
            mock.patch.object(fill_input.time, "sleep"),
        ):
            with self.assertRaises(ClickTargetBlockedError):
                fill_input.click_control(control, 123)

        set_cursor.assert_any_call((150, 150))
        set_cursor.assert_any_call((1, 2))
        mouse.assert_not_called()

    def test_unknown_target_window_is_rejected(self):
        control = FakeControl()
        control.GetTopLevelControl = lambda: SimpleNamespace(
            NativeWindowHandle=0
        )
        with (
            mock.patch.object(
                fill_input.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(fill_input.win32api, "mouse_event") as mouse,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                fill_input.click_control(control)

        set_cursor.assert_not_called()
        mouse.assert_not_called()


class FocusInputTest(unittest.TestCase):
    def test_focus_click_is_rejected_when_other_window_is_on_top(self):
        control = FakeControl()
        with (
            mock.patch.object(
                fill_input.time, "monotonic", side_effect=[0.0, 2.0]
            ),
            mock.patch.object(fill_input.time, "sleep"),
            mock.patch.object(
                fill_input, "point_belongs_to_window", return_value=False
            ),
            mock.patch.object(
                fill_input.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                fill_input.win32api, "mouse_event"
            ) as mouse,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                fill_input.focus_input(control, 123)

        set_cursor.assert_not_called()
        mouse.assert_not_called()


class HitTestHelpersTest(unittest.TestCase):
    def test_point_belongs_to_same_root_window(self):
        with (
            mock.patch.object(
                get_hwnd, "hit_test_window", return_value=22
            ),
            mock.patch.object(
                get_hwnd,
                "window_root",
                side_effect=lambda hwnd: 1 if hwnd in (22, 123) else hwnd,
            ),
        ):
            self.assertTrue(
                get_hwnd.point_belongs_to_window(10, 20, 123)
            )

    def test_point_on_other_root_window_is_rejected(self):
        with (
            mock.patch.object(
                get_hwnd, "hit_test_window", return_value=99
            ),
            mock.patch.object(
                get_hwnd,
                "window_root",
                side_effect=lambda hwnd: hwnd,
            ),
        ):
            self.assertFalse(
                get_hwnd.point_belongs_to_window(10, 20, 123)
            )

    def test_tray_click_rejects_covering_window_before_moving(self):
        with (
            mock.patch.object(
                get_hwnd, "hit_test_window", return_value=5
            ),
            mock.patch.object(get_hwnd, "window_root", return_value=5),
            mock.patch.object(
                get_hwnd.win32gui,
                "GetClassName",
                return_value="Chrome_WidgetWin_1",
            ),
            mock.patch.object(
                get_hwnd.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                get_hwnd.win32api, "mouse_event"
            ) as mouse,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                get_hwnd._click_screen(
                    10,
                    20,
                    expected_classes={"Shell_TrayWnd"},
                )

        set_cursor.assert_not_called()
        mouse.assert_not_called()

    def test_tray_click_allows_taskbar(self):
        with (
            mock.patch.object(
                get_hwnd, "hit_test_window", return_value=5
            ),
            mock.patch.object(get_hwnd, "window_root", return_value=5),
            mock.patch.object(
                get_hwnd.win32gui,
                "GetClassName",
                return_value="Shell_TrayWnd",
            ),
            mock.patch.object(
                get_hwnd.win32api, "GetCursorPos", return_value=(1, 2)
            ),
            mock.patch.object(
                get_hwnd.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                get_hwnd.win32api, "mouse_event"
            ) as mouse,
            mock.patch.object(get_hwnd.time, "sleep"),
        ):
            get_hwnd._click_screen(
                10, 20, expected_classes={"Shell_TrayWnd"}
            )

        set_cursor.assert_any_call((10, 20))
        self.assertEqual(mouse.call_count, 4)


class ClickPointTest(unittest.TestCase):
    def test_click_point_clicks_target_window(self):
        with (
            mock.patch.object(
                get_hwnd, "point_belongs_to_window", return_value=True
            ) as hit,
            mock.patch.object(
                get_hwnd.win32api, "GetCursorPos", return_value=(1, 2)
            ),
            mock.patch.object(
                get_hwnd.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                get_hwnd.win32api, "mouse_event"
            ) as mouse,
            mock.patch.object(get_hwnd.time, "sleep"),
        ):
            get_hwnd.click_point(10, 20, 123)

        self.assertEqual(hit.call_count, 2)
        set_cursor.assert_any_call((10, 20))
        set_cursor.assert_any_call((1, 2))
        self.assertEqual(mouse.call_count, 2)

    def test_click_point_rejects_other_window(self):
        with (
            mock.patch.object(
                get_hwnd, "point_belongs_to_window", return_value=False
            ),
            mock.patch.object(
                get_hwnd,
                "describe_hit_window",
                return_value="0x1 (Other)",
            ),
            mock.patch.object(
                get_hwnd.win32api, "SetCursorPos"
            ) as set_cursor,
            mock.patch.object(
                get_hwnd.win32api, "mouse_event"
            ) as mouse,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                get_hwnd.click_point(10, 20, 123)

        set_cursor.assert_not_called()
        mouse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
