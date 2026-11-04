"""窗口唤醒开关的纯逻辑测试（不操作真实微信窗口）。"""

import struct
import unittest
from unittest import mock

import get_hwnd
from errors import WindowUnavailableError


class RequireWindowReadyTest(unittest.TestCase):
    def setUp(self):
        exists = mock.patch.object(get_hwnd, "_window_exists", return_value=True)
        exists.start()
        self.addCleanup(exists.stop)

    def test_normal_needs_no_wake(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="normal"),
            mock.patch.object(get_hwnd, "restore_minimized_window") as restore_min,
            mock.patch.object(get_hwnd, "restore_tray_window") as restore_tray,
        ):
            self.assertEqual(get_hwnd.require_window_ready(1), 1)
            restore_min.assert_not_called()
            restore_tray.assert_not_called()

    def test_minimized_requires_flag(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="minimized"),
            mock.patch.object(get_hwnd, "restore_minimized_window") as restore,
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1)
            self.assertIn("--wake-minimized", str(ctx.exception))
            restore.assert_not_called()

    def test_minimized_flag_restores(self):
        with (
            mock.patch.object(
                get_hwnd,
                "window_state",
                side_effect=["minimized", "normal"],
            ),
            mock.patch.object(
                get_hwnd, "restore_minimized_window", return_value=True
            ) as restore_min,
            mock.patch.object(get_hwnd, "restore_tray_window") as restore_tray,
        ):
            self.assertEqual(
                get_hwnd.require_window_ready(1, wake_minimized=True), 1
            )
            restore_min.assert_called_once_with(1)
            restore_tray.assert_not_called()

    def test_hidden_requires_tray_flag(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="hidden"),
            mock.patch.object(get_hwnd, "restore_tray_window") as restore,
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1, wake_minimized=True)
            self.assertIn("--wake-tray", str(ctx.exception))
            restore.assert_not_called()

    def test_hidden_tray_flag_restores(self):
        with (
            mock.patch.object(
                get_hwnd,
                "window_state",
                side_effect=["hidden", "normal"],
            ),
            mock.patch.object(
                get_hwnd, "restore_tray_window", return_value=True
            ) as restore_tray,
            mock.patch.object(get_hwnd, "restore_minimized_window") as restore_min,
        ):
            self.assertEqual(get_hwnd.require_window_ready(1, wake_tray=True), 1)
            restore_tray.assert_called_once_with(1)
            restore_min.assert_not_called()

    def test_minimized_wake_failure_is_explicit(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="minimized"),
            mock.patch.object(
                get_hwnd, "restore_minimized_window", return_value=False
            ),
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1, wake_minimized=True)
            self.assertIn("尝试还原", str(ctx.exception))

    def test_hidden_wake_failure_is_explicit(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="hidden"),
            mock.patch.object(
                get_hwnd, "restore_tray_window", return_value=False
            ),
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1, wake_tray=True)
            self.assertIn("尝试从托盘唤起", str(ctx.exception))


class TrayDataTest(unittest.TestCase):
    def test_parses_hwnd_uid_and_callback(self):
        pointer_format = "<Q" if get_hwnd._POINTER_SIZE == 8 else "<I"
        data = struct.pack(pointer_format + "II", 0x1234, 0, 0x8385)

        with mock.patch.object(get_hwnd.win32gui, "IsWindow", return_value=True):
            entry = get_hwnd._parse_tray_data(data)

        self.assertEqual(
            entry, {"hwnd": 0x1234, "uid": 0, "callback": 0x8385}
        )

    def test_rejects_invalid_callback(self):
        pointer_format = "<Q" if get_hwnd._POINTER_SIZE == 8 else "<I"
        data = struct.pack(pointer_format + "II", 0x1234, 0, 0)

        with mock.patch.object(get_hwnd.win32gui, "IsWindow", return_value=True):
            self.assertIsNone(get_hwnd._parse_tray_data(data))


if __name__ == "__main__":
    unittest.main()
