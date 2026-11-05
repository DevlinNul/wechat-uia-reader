"""fill_input 的会话校验、首选/回退路径测试。"""

import unittest
from types import SimpleNamespace
from unittest import mock

import fill_input
from errors import (
    CannotSendError,
    ChatMismatchError,
    InputFillError,
    UIAUnavailableError,
)


_UNSET = object()


def make_snapshot(chat_name="会话 A", input_field=_UNSET, uia_available=True):
    if input_field is _UNSET:
        input_field = object()
    return SimpleNamespace(
        hwnd=123,
        chat_name=chat_name,
        input_field=input_field,
        uia_available=uia_available,
    )


class FillInputTest(unittest.TestCase):
    def test_empty_chat_name_is_rejected(self):
        with mock.patch.object(fill_input, "scan_window") as scan:
            with self.assertRaises(ValueError):
                fill_input.fill_input("hi", "  ")
            scan.assert_not_called()

    def test_chat_mismatch_raises_before_writing(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot("会话 A")
            ),
            mock.patch.object(fill_input, "_fill_with_value_pattern") as value_fill,
            mock.patch.object(fill_input, "_fill_with_sendinput") as send_fill,
        ):
            with self.assertRaises(ChatMismatchError):
                fill_input.fill_input("hi", "会话 B")
            value_fill.assert_not_called()
            send_fill.assert_not_called()

    def test_uia_unavailable_raises(self):
        with mock.patch.object(
            fill_input,
            "scan_window",
            return_value=make_snapshot(uia_available=False),
        ):
            with self.assertRaises(UIAUnavailableError):
                fill_input.fill_input("hi", "会话 A")

    def test_cannot_send_raises(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot()
            ),
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": False,
                    "label": "列表",
                    "type": "list",
                },
            ),
        ):
            with self.assertRaises(CannotSendError):
                fill_input.fill_input("hi", "会话 A")

    def test_missing_input_field_raises(self):
        snapshot = make_snapshot(input_field=None)
        with (
            mock.patch.object(fill_input, "scan_window", return_value=snapshot),
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": True,
                    "label": "私聊",
                    "type": "private",
                },
            ),
        ):
            with self.assertRaises(CannotSendError):
                fill_input.fill_input("hi", "会话 A")

    def test_prefers_value_pattern(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot()
            ),
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": True,
                    "label": "私聊",
                    "type": "private",
                },
            ),
            mock.patch.object(
                fill_input, "_fill_with_value_pattern", return_value=True
            ) as value_fill,
            mock.patch.object(fill_input, "_fill_with_sendinput") as send_fill,
        ):
            result = fill_input.fill_input("hi", "会话 A")

        self.assertEqual(result["method"], "value_pattern")
        self.assertTrue(result["verified"])
        value_fill.assert_called_once()
        send_fill.assert_not_called()

    def test_falls_back_to_sendinput(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot()
            ),
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": True,
                    "label": "私聊",
                    "type": "private",
                },
            ),
            mock.patch.object(
                fill_input, "_fill_with_value_pattern", return_value=False
            ),
            mock.patch.object(
                fill_input, "_fill_with_sendinput", return_value=True
            ) as send_fill,
        ):
            result = fill_input.fill_input("hi", "会话 A")

        self.assertEqual(result["method"], "sendinput")
        send_fill.assert_called_once()

    def test_both_methods_failing_raises(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot()
            ),
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": True,
                    "label": "私聊",
                    "type": "private",
                },
            ),
            mock.patch.object(
                fill_input, "_fill_with_value_pattern", return_value=False
            ),
            mock.patch.object(
                fill_input, "_fill_with_sendinput", return_value=False
            ),
        ):
            with self.assertRaises(InputFillError):
                fill_input.fill_input("hi", "会话 A")

    def test_normalizes_newlines(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot()
            ),
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": True,
                    "label": "私聊",
                    "type": "private",
                },
            ),
            mock.patch.object(
                fill_input, "_fill_with_value_pattern", return_value=True
            ) as value_fill,
        ):
            result = fill_input.fill_input("a\r\nb\rc", "会话 A")

        self.assertEqual(result["text"], "a\nb\nc")
        self.assertEqual(value_fill.call_args.args[1], "a\nb\nc")

    def test_passes_wake_flags_to_scan_window(self):
        with (
            mock.patch.object(
                fill_input, "scan_window", return_value=make_snapshot()
            ) as scan,
            mock.patch.object(
                fill_input,
                "classify_snapshot",
                return_value={
                    "can_send": True,
                    "label": "私聊",
                    "type": "private",
                },
            ),
            mock.patch.object(
                fill_input, "_fill_with_value_pattern", return_value=True
            ),
        ):
            fill_input.fill_input(
                "hi", "会话 A", wake_minimized=True, wake_tray=True
            )

        scan.assert_called_once_with(
            None, wake_minimized=True, wake_tray=True
        )


if __name__ == "__main__":
    unittest.main()
