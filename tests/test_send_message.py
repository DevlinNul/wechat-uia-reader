"""send_message 的发送流程与原子性测试（不发送真实消息）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

import send_message as module
from errors import ChatMismatchError, SendMessageError


class SendMessageTest(unittest.TestCase):
    def _snapshot(self):
        return SimpleNamespace(hwnd=123, input_field=object())

    def test_empty_text_is_rejected(self):
        with mock.patch.object(module, "fill_input") as fill:
            with self.assertRaises(ValueError):
                module.send_message("   ", chat_name="会话 A")
            fill.assert_not_called()

    def test_sends_after_successful_fill(self):
        with (
            mock.patch.object(
                module, "fill_input", return_value={"method": "value_pattern"}
            ) as fill,
            mock.patch.object(
                module, "scan_window", return_value=self._snapshot()
            ),
            mock.patch.object(
                module,
                "session_identity_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(module, "session_mismatches", return_value=[]),
            mock.patch.object(module, "activate_window", return_value=True),
            mock.patch.object(module, "focus_input", return_value=True),
            mock.patch.object(module, "press_enter") as enter,
            mock.patch.object(
                module, "_wait_input_empty", return_value=True
            ),
            mock.patch.object(
                module.win32gui, "GetForegroundWindow", return_value=999
            ),
            mock.patch.object(module.win32gui, "SetForegroundWindow"),
        ):
            result = module.send_message("你好", chat_name="会话 A")

        self.assertEqual(result["fill_method"], "value_pattern")
        self.assertTrue(result["verified"])
        fill.assert_called_once()
        enter.assert_called_once()

    def test_session_change_after_fill_does_not_send(self):
        with (
            mock.patch.object(
                module, "fill_input", return_value={"method": "value_pattern"}
            ),
            mock.patch.object(
                module, "scan_window", return_value=self._snapshot()
            ),
            mock.patch.object(
                module,
                "session_identity_from_snapshot",
                return_value={"chat_name": "会话 B"},
            ),
            mock.patch.object(
                module, "session_mismatches", return_value=["会话名不一致"]
            ),
            mock.patch.object(module, "clear_input") as clear,
            mock.patch.object(module, "press_enter") as enter,
            mock.patch.object(
                module.win32gui, "GetForegroundWindow", return_value=999
            ),
        ):
            with self.assertRaises(ChatMismatchError):
                module.send_message("你好", chat_name="会话 A")

        enter.assert_not_called()
        clear.assert_not_called()

    def test_focus_failure_clears_and_raises(self):
        with (
            mock.patch.object(
                module, "fill_input", return_value={"method": "value_pattern"}
            ),
            mock.patch.object(
                module, "scan_window", return_value=self._snapshot()
            ),
            mock.patch.object(
                module,
                "session_identity_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(module, "session_mismatches", return_value=[]),
            mock.patch.object(module, "activate_window", return_value=True),
            mock.patch.object(module, "focus_input", return_value=False),
            mock.patch.object(module, "clear_input") as clear,
            mock.patch.object(module, "press_enter") as enter,
            mock.patch.object(
                module.win32gui, "GetForegroundWindow", return_value=999
            ),
            mock.patch.object(module.win32gui, "SetForegroundWindow"),
        ):
            with self.assertRaises(SendMessageError):
                module.send_message("你好", chat_name="会话 A")

        clear.assert_called_once()
        enter.assert_not_called()

    def test_enter_failure_clears_and_raises(self):
        with (
            mock.patch.object(
                module, "fill_input", return_value={"method": "value_pattern"}
            ),
            mock.patch.object(
                module, "scan_window", return_value=self._snapshot()
            ),
            mock.patch.object(
                module,
                "session_identity_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(module, "session_mismatches", return_value=[]),
            mock.patch.object(module, "activate_window", return_value=True),
            mock.patch.object(module, "focus_input", return_value=True),
            mock.patch.object(
                module, "press_enter", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(module, "clear_input") as clear,
            mock.patch.object(
                module.win32gui, "GetForegroundWindow", return_value=999
            ),
            mock.patch.object(module.win32gui, "SetForegroundWindow"),
        ):
            with self.assertRaises(SendMessageError):
                module.send_message("你好", chat_name="会话 A")

        clear.assert_called_once()

    def test_input_not_empty_after_enter_clears_and_raises(self):
        with (
            mock.patch.object(
                module, "fill_input", return_value={"method": "value_pattern"}
            ),
            mock.patch.object(
                module, "scan_window", return_value=self._snapshot()
            ),
            mock.patch.object(
                module,
                "session_identity_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(module, "session_mismatches", return_value=[]),
            mock.patch.object(module, "activate_window", return_value=True),
            mock.patch.object(module, "focus_input", return_value=True),
            mock.patch.object(module, "press_enter"),
            mock.patch.object(
                module, "_wait_input_empty", return_value=False
            ),
            mock.patch.object(module, "clear_input") as clear,
            mock.patch.object(
                module.win32gui, "GetForegroundWindow", return_value=999
            ),
            mock.patch.object(module.win32gui, "SetForegroundWindow"),
        ):
            with self.assertRaises(SendMessageError):
                module.send_message("你好", chat_name="会话 A")

        clear.assert_called_once()

    def test_fill_error_propagates(self):
        with (
            mock.patch.object(
                module,
                "fill_input",
                side_effect=ChatMismatchError("会话不匹配"),
            ),
            mock.patch.object(module, "scan_window") as scan,
        ):
            with self.assertRaises(ChatMismatchError):
                module.send_message("你好", chat_name="会话 A")

        scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
