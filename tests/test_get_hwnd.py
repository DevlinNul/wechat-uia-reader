"""窗口唤醒开关的纯逻辑测试（不操作真实微信窗口）。"""

import struct
import unittest
from unittest import mock

from wechat_uia.core import get_hwnd
from wechat_uia.core.errors import WindowUnavailableError


class RequireWindowReadyTest(unittest.TestCase):
    def setUp(self):
        exists = mock.patch.object(get_hwnd, "_window_exists", return_value=True)
        exists.start()
        self.addCleanup(exists.stop)
        wechat = mock.patch.object(
            get_hwnd, "is_wechat_window", return_value=True
        )
        wechat.start()
        self.addCleanup(wechat.stop)

    def test_foreign_window_is_rejected(self):
        with mock.patch.object(
            get_hwnd, "is_wechat_window", return_value=False
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1)
        self.assertIn("不属于微信进程", str(ctx.exception))

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

    def test_minimized_hint_mentions_all_public_entries(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="minimized"),
            mock.patch.object(get_hwnd, "restore_minimized_window") as restore,
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1)

        message = str(ctx.exception)
        self.assertIn("read_private_chat", message)
        self.assertIn("read_private_tags", message)
        self.assertIn("fill_input", message)
        self.assertIn("send_message", message)
        self.assertIn("--wake-minimized", message)
        restore.assert_not_called()

    def test_hidden_hint_mentions_all_public_entries(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="hidden"),
            mock.patch.object(get_hwnd, "restore_tray_window") as restore,
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(1)

        message = str(ctx.exception)
        self.assertIn("read_private_chat", message)
        self.assertIn("read_private_tags", message)
        self.assertIn("fill_input", message)
        self.assertIn("send_message", message)
        self.assertIn("--wake-tray", message)
        restore.assert_not_called()

    def test_offscreen_hint_does_not_promise_wake(self):
        with mock.patch.object(
            get_hwnd, "window_state", return_value="offscreen"
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(
                    1, wake_minimized=True, wake_tray=True
                )

        message = str(ctx.exception)
        self.assertIn("屏幕外", message)
        self.assertIn("不能移动屏幕外窗口", message)

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

    def test_detached_window_rejects_tray_wake(self):
        with (
            mock.patch.object(
                get_hwnd, "window_state", return_value="hidden"
            ),
            mock.patch.object(
                get_hwnd, "restore_tray_window"
            ) as restore_tray,
        ):
            with self.assertRaises(WindowUnavailableError) as ctx:
                get_hwnd.require_window_ready(
                    1, wake_tray=True, window_role="chat"
                )
        self.assertIn("只有主窗口", str(ctx.exception))
        restore_tray.assert_not_called()

    def test_detached_minimized_only_needs_minimized_wake(self):
        with (
            mock.patch.object(
                get_hwnd,
                "window_state",
                side_effect=["minimized", "normal"],
            ),
            mock.patch.object(
                get_hwnd, "restore_minimized_window", return_value=True
            ) as restore_min,
            mock.patch.object(
                get_hwnd, "restore_tray_window"
            ) as restore_tray,
        ):
            self.assertEqual(
                get_hwnd.require_window_ready(
                    1, wake_minimized=True, window_role="chat"
                ),
                1,
            )
            restore_min.assert_called_once_with(1)
            restore_tray.assert_not_called()

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


class ProcessCheckTest(unittest.TestCase):
    def _check(self, process_name, class_name):
        with (
            mock.patch.object(get_hwnd, "_window_exists", return_value=True),
            mock.patch.object(get_hwnd, "get_window_pid", return_value=10),
            mock.patch.object(
                get_hwnd, "get_process_name", return_value=process_name
            ),
            mock.patch.object(
                get_hwnd.win32gui, "GetClassName", return_value=class_name
            ),
        ):
            return get_hwnd.is_wechat_window(1)

    def test_process_name_wins(self):
        self.assertTrue(self._check("Weixin.exe", "Chrome_WidgetWin_1"))
        self.assertFalse(self._check("chrome.exe", "mmui::MainWindow"))

    def test_unreadable_process_name_falls_back_to_class(self):
        self.assertTrue(self._check("", "mmui::MainWindow"))
        self.assertTrue(self._check("", "Qt51514QWindowIcon"))
        self.assertFalse(self._check("", "Chrome_WidgetWin_1"))


class WindowKindTest(unittest.TestCase):
    def test_uia_class_names_map_to_roles(self):
        self.assertEqual(
            get_hwnd.window_kind_from_class("mmui::MainWindow"), "main"
        )
        self.assertEqual(
            get_hwnd.window_kind_from_class("mmui::ChatSingleWindow"), "chat"
        )
        self.assertEqual(
            get_hwnd.window_kind_from_class(
                "mmui::SearchMsgUniqueChatWindow"
            ),
            "history",
        )
        self.assertIsNone(
            get_hwnd.window_kind_from_class("Qt51514QWindowIcon")
        )

    def test_class_names_map_to_roles(self):
        cases = [
            ("mmui::MainWindow", "main"),
            ("mmui::ChatSingleWindow", "chat"),
            ("mmui::SearchMsgUniqueChatWindow", "history"),
        ]
        for class_name, expected in cases:
            with self.subTest(class_name=class_name):
                with (
                    mock.patch.object(
                        get_hwnd, "_window_exists", return_value=True
                    ),
                    mock.patch.object(
                        get_hwnd, "is_wechat_window", return_value=True
                    ),
                    mock.patch.object(
                        get_hwnd.win32gui,
                        "GetClassName",
                        return_value=class_name,
                    ),
                ):
                    self.assertEqual(get_hwnd.window_kind(1), expected)

    def test_unknown_class_is_other(self):
        for class_name in ("Qt51514QWindowIcon", "SomethingElse"):
            with self.subTest(class_name=class_name):
                with (
                    mock.patch.object(
                        get_hwnd, "_window_exists", return_value=True
                    ),
                    mock.patch.object(
                        get_hwnd, "is_wechat_window", return_value=True
                    ),
                    mock.patch.object(
                        get_hwnd.win32gui,
                        "GetClassName",
                        return_value=class_name,
                    ),
                ):
                    self.assertEqual(get_hwnd.window_kind(1), "other")

    def test_foreign_and_missing_windows(self):
        with mock.patch.object(
            get_hwnd, "_window_exists", return_value=False
        ):
            self.assertEqual(get_hwnd.window_kind(1), "missing")

        with (
            mock.patch.object(get_hwnd, "_window_exists", return_value=True),
            mock.patch.object(
                get_hwnd, "is_wechat_window", return_value=False
            ),
        ):
            self.assertEqual(get_hwnd.window_kind(1), "foreign")


class TrayRestorePathTest(unittest.TestCase):
    def test_probe_returns_visible_rect(self):
        with mock.patch.object(
            get_hwnd,
            "_notify_icon_rect",
            side_effect=[(1, None), (0, (10, 20, 30, 40))],
        ):
            self.assertEqual(
                get_hwnd._probe_tray_icon_rect(1),
                ((10, 20, 30, 40), False),
            )

    def test_probe_reports_hidden_icon(self):
        side_effect = [(1, None)] + [(-2147467259, None)] * 15
        with mock.patch.object(
            get_hwnd, "_notify_icon_rect", side_effect=side_effect
        ):
            self.assertEqual(get_hwnd._probe_tray_icon_rect(1), (None, True))

    def test_prefers_tray_message(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="hidden"),
            mock.patch.object(
                get_hwnd, "_restore_tray_by_message", return_value=True
            ) as message,
            mock.patch.object(get_hwnd, "_restore_tray_by_click") as click,
        ):
            self.assertTrue(get_hwnd.restore_tray_window(1))
            message.assert_called_once_with(1, get_hwnd.WAKE_TIMEOUT)
            click.assert_not_called()

    def test_falls_back_to_shell_click(self):
        with (
            mock.patch.object(get_hwnd, "window_state", return_value="hidden"),
            mock.patch.object(
                get_hwnd, "_restore_tray_by_message", return_value=False
            ),
            mock.patch.object(
                get_hwnd, "_restore_tray_by_click", return_value=True
            ) as click,
        ):
            self.assertTrue(get_hwnd.restore_tray_window(1))
            click.assert_called_once_with(1, get_hwnd.WAKE_TIMEOUT)


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
