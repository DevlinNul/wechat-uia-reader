"""send_message 编排测试与 core.send 发送原语测试（不发送真实消息）。"""

import importlib
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core.errors import (
    ChatMismatchError,
    ClickTargetBlockedError,
    SendMessageError,
)

module = importlib.import_module("wechat_uia.api.send_message")
send_core = importlib.import_module("wechat_uia.core.send")


class FakeMessageItem:
    ControlTypeName = "ListItemControl"

    def __init__(self, name, runtime_id):
        self.Name = name
        self.ClassName = "mmui::ChatBubbleItemView"
        self._runtime_id = runtime_id

    def GetRuntimeId(self):
        return list(self._runtime_id)

    def GetChildren(self):
        return []


class FakeMessageList:
    def __init__(self, items):
        self.items = list(items)

    def GetChildren(self):
        return list(self.items)


class MessageConfirmationTest(unittest.TestCase):
    def test_new_message_is_seen(self):
        before = FakeMessageList([FakeMessageItem("旧消息", (1,))])
        marker = send_core.message_marker(before)
        after = FakeMessageList(
            [
                FakeMessageItem("旧消息", (1,)),
                FakeMessageItem("新消息", (2,)),
            ]
        )
        self.assertTrue(send_core.message_seen(after, marker, "新消息"))

    def test_same_old_message_alone_is_not_seen(self):
        messages = FakeMessageList([FakeMessageItem("同一句话", (1,))])
        marker = send_core.message_marker(messages)
        self.assertFalse(send_core.message_seen(messages, marker, "同一句话"))

    def test_identical_text_appended_is_seen(self):
        first = FakeMessageItem("同一句话", (1,))
        before = FakeMessageList([first])
        marker = send_core.message_marker(before)
        after = FakeMessageList(
            [first, FakeMessageItem("同一句话", (2,))]
        )
        self.assertTrue(send_core.message_seen(after, marker, "同一句话"))

    def test_unrelated_new_message_is_not_seen(self):
        before = FakeMessageList([FakeMessageItem("旧消息", (1,))])
        marker = send_core.message_marker(before)
        after = FakeMessageList(
            [
                FakeMessageItem("旧消息", (1,)),
                FakeMessageItem("别的新消息", (2,)),
            ]
        )
        self.assertFalse(send_core.message_seen(after, marker, "我要发的"))

    def test_missing_message_list_is_not_confirmed(self):
        seen, reason = send_core.confirm_message_seen(None, {}, "你好")
        self.assertFalse(seen)
        self.assertIn("未找到消息列表", reason)


class CoreSendTest(unittest.TestCase):
    def test_pre_submit_content_mismatch_does_not_send(self):
        with (
            mock.patch.object(
                send_core, "read_input_text", return_value="被改过的内容"
            ),
            mock.patch.object(send_core, "clear_input") as clear,
            mock.patch.object(send_core, "press_alt_s") as press,
        ):
            with self.assertRaises(SendMessageError) as ctx:
                send_core.assert_input_text(
                    object(), "你好", 123, "提交前"
                )
        self.assertIn("输入框内容已变化", str(ctx.exception))
        clear.assert_called_once()
        press.assert_not_called()

    def test_unreadable_input_before_send_does_not_send(self):
        with (
            mock.patch.object(send_core, "read_input_text", return_value=None),
            mock.patch.object(send_core, "clear_input") as clear,
        ):
            with self.assertRaises(SendMessageError):
                send_core.assert_input_text(
                    object(), "你好", 123, "提交前"
                )
        clear.assert_called_once()

    def test_focus_click_blocked_clears_and_raises(self):
        snapshot = SimpleNamespace(hwnd=123, send_button=object())
        with (
            mock.patch.object(
                send_core,
                "focus_input",
                side_effect=ClickTargetBlockedError("被挡住"),
            ),
            mock.patch.object(send_core, "clear_input") as clear,
            mock.patch.object(send_core, "press_alt_s") as press,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                send_core.submit("alt_s", snapshot, object(), "你好")
        clear.assert_called_once()
        press.assert_not_called()

    def test_alt_s_failure_clears_and_raises(self):
        snapshot = SimpleNamespace(hwnd=123)
        with (
            mock.patch.object(send_core, "focus_input", return_value=True),
            mock.patch.object(send_core, "assert_input_text"),
            mock.patch.object(
                send_core, "press_alt_s", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(send_core, "clear_input") as clear,
        ):
            with self.assertRaises(SendMessageError):
                send_core.submit("alt_s", snapshot, object(), "你好")
        clear.assert_called_once()

    def test_enter_failure_clears_and_raises(self):
        snapshot = SimpleNamespace(hwnd=123)
        with (
            mock.patch.object(send_core, "focus_input", return_value=True),
            mock.patch.object(send_core, "assert_input_text"),
            mock.patch.object(
                send_core, "press_enter", side_effect=RuntimeError("boom")
            ),
            mock.patch.object(send_core, "clear_input") as clear,
        ):
            with self.assertRaises(SendMessageError):
                send_core.submit("enter", snapshot, object(), "你好")
        clear.assert_called_once()

    def test_focus_failure_returns_false(self):
        snapshot = SimpleNamespace(hwnd=123)
        with mock.patch.object(send_core, "focus_input", return_value=False):
            self.assertFalse(
                send_core.submit("enter", snapshot, object(), "你好")
            )

    def test_button_unavailable_returns_false(self):
        snapshot = SimpleNamespace(hwnd=123, send_button=None)
        self.assertFalse(
            send_core.submit("button", snapshot, object(), "你好")
        )

    def test_button_click_blocked_clears_and_raises(self):
        snapshot = SimpleNamespace(hwnd=123, send_button=object())
        with (
            mock.patch.object(send_core, "assert_input_text"),
            mock.patch.object(
                send_core,
                "click_control",
                side_effect=ClickTargetBlockedError("被挡住"),
            ),
            mock.patch.object(send_core, "clear_input") as clear,
        ):
            with self.assertRaises(ClickTargetBlockedError):
                send_core.submit("button", snapshot, object(), "你好")
        clear.assert_called_once()


class SendMessageTest(unittest.TestCase):
    def _snapshot(self, send_button=object(), window_kind="main"):
        return SimpleNamespace(
            hwnd=123,
            input_field=object(),
            send_button=send_button,
            message_list=None,
            window_kind=window_kind,
            window_automation_id=None,
            layout="wide",
        )

    @contextmanager
    def send_env(self, snapshot=None, **overrides):
        snapshot = snapshot or self._snapshot()
        resolved = SimpleNamespace(hwnd=123, role="main")
        defaults = {
            "resolve_surface": {"return_value": resolved},
            "ensure_ready": {},
            "activate": {"return_value": True},
            "scan_window": {"return_value": snapshot},
            "classify_snapshot": {
                "return_value": {
                    "type": "private",
                    "label": "私聊",
                    "member_count": None,
                    "can_send": True,
                    "chat_name": "会话 A",
                    "reason": "测试",
                }
            },
            "assert_session": {"return_value": {}},
            "assert_sendable_window": {},
            "fill_snapshot": {
                "return_value": {
                    "method": "value_pattern",
                    "session_check": "strong",
                }
            },
            "message_marker": {"return_value": {}},
            "submit": {"return_value": True},
            "wait_input_empty": {"return_value": True},
            "confirm_message_seen": {
                "return_value": (True, "消息列表已出现新消息")
            },
            "clear_input": {},
            "restore_foreground": {},
            "minimize_overlapping_windows": {"return_value": []},
            "restore_minimized_windows": {"return_value": 0},
        }
        defaults.update(overrides)

        mocks = {}
        with ExitStack() as stack:
            for name, kwargs in defaults.items():
                mocks[name] = stack.enter_context(
                    mock.patch.object(module, name, **kwargs)
                )
            mocks["GetForegroundWindow"] = stack.enter_context(
                mock.patch.object(
                    module.win32gui, "GetForegroundWindow", return_value=999
                )
            )
            yield mocks

    def test_empty_text_is_rejected(self):
        with (
            mock.patch.object(module, "resolve_surface") as resolve,
            mock.patch.object(module, "fill_snapshot") as fill,
        ):
            with self.assertRaises(ValueError):
                module.send_message("   ", chat_name="会话 A")
            resolve.assert_not_called()
            fill.assert_not_called()

    def test_invalid_send_method_is_rejected(self):
        with (
            mock.patch.object(module, "resolve_surface") as resolve,
            mock.patch.object(module, "fill_snapshot") as fill,
        ):
            with self.assertRaises(ValueError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="bad"
                )
            resolve.assert_not_called()
            fill.assert_not_called()

    def test_default_uses_alt_s(self):
        with self.send_env() as mocks:
            result = module.send_message("你好", chat_name="会话 A")

        self.assertEqual(result["send_method"], "alt_s")
        self.assertTrue(result["verified"])
        self.assertTrue(result["confirm"]["message_seen"])
        self.assertEqual(result["session_check"], "strong")
        mocks["submit"].assert_called_once()
        self.assertEqual(mocks["submit"].call_args.args[0], "alt_s")
        mocks["wait_input_empty"].assert_called_once()

    def test_name_only_session_check_is_reported(self):
        with self.send_env(
            fill_snapshot={
                "return_value": {
                    "method": "value_pattern",
                    "session_check": "name_only",
                }
            }
        ):
            result = module.send_message("你好", chat_name="会话 A")

        self.assertEqual(result["session_check"], "name_only")
        self.assertTrue(result["verified"])

    def test_enter_method(self):
        with self.send_env() as mocks:
            result = module.send_message(
                "你好", chat_name="会话 A", send_method="enter"
            )

        self.assertEqual(result["send_method"], "enter")
        self.assertEqual(mocks["submit"].call_args.args[0], "enter")

    def test_button_method(self):
        with self.send_env() as mocks:
            result = module.send_message(
                "你好", chat_name="会话 A", send_method="button"
            )

        self.assertEqual(result["send_method"], "button")
        self.assertEqual(mocks["submit"].call_args.args[0], "button")

    def test_auto_falls_back_to_button(self):
        def choose(method, *args, **kwargs):
            return method == "button"

        with self.send_env(submit={"side_effect": choose}) as mocks:
            result = module.send_message(
                "你好", chat_name="会话 A", send_method="auto"
            )

        self.assertEqual(result["send_method"], "button")
        self.assertEqual(
            [call.args[0] for call in mocks["submit"].call_args_list],
            ["alt_s", "enter", "button"],
        )

    def test_no_send_method_available_clears_and_raises(self):
        with self.send_env(submit={"return_value": False}) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="auto"
                )
        mocks["clear_input"].assert_called_once()

    def test_unconfirmed_message_returns_verified_false(self):
        with self.send_env(
            confirm_message_seen={
                "return_value": (False, "消息列表未在限定时间内出现新消息")
            }
        ):
            result = module.send_message("你好", chat_name="会话 A")

        self.assertFalse(result["verified"])
        self.assertFalse(result["confirm"]["message_seen"])
        self.assertIn("未在限定时间", result["confirm"]["reason"])

    def test_history_window_is_rejected_before_sending(self):
        with self.send_env(
            assert_sendable_window={
                "side_effect": SendMessageError("聊天记录窗口只读")
            }
        ) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message("你好", chat_name="会话 A")

        mocks["submit"].assert_not_called()
        mocks["fill_snapshot"].assert_not_called()

    def test_session_change_after_activation_does_not_send(self):
        with self.send_env(
            assert_session={"side_effect": ChatMismatchError("会话不一致")}
        ) as mocks:
            with self.assertRaises(ChatMismatchError):
                module.send_message("你好", chat_name="会话 A")

        mocks["submit"].assert_not_called()
        mocks["fill_snapshot"].assert_not_called()

    def test_input_not_empty_after_send_clears_and_raises(self):
        with self.send_env(
            wait_input_empty={"return_value": False}
        ) as mocks:
            with self.assertRaises(SendMessageError) as ctx:
                module.send_message(
                    "你好", chat_name="会话 A", send_method="button"
                )
        self.assertIn("不要直接重试", str(ctx.exception))
        mocks["clear_input"].assert_called_once()

    def test_clear_occluders_is_off_by_default(self):
        with self.send_env() as mocks:
            module.send_message("你好", chat_name="会话 A")

        mocks["minimize_overlapping_windows"].assert_not_called()
        mocks["restore_minimized_windows"].assert_not_called()

    def test_wake_flags_are_forwarded(self):
        with self.send_env() as mocks:
            module.send_message(
                "你好",
                chat_name="会话 A",
                wake_minimized=True,
                wake_tray=True,
            )

        mocks["ensure_ready"].assert_called_once_with(
            123,
            window_role="main",
            wake_minimized=True,
            wake_tray=True,
        )

    def test_clear_occluders_minimizes_and_restores(self):
        with self.send_env(
            minimize_overlapping_windows={"return_value": [11, 22]}
        ) as mocks:
            module.send_message(
                "你好", chat_name="会话 A", clear_occluders=True
            )

        mocks["minimize_overlapping_windows"].assert_called_once_with(123)
        mocks["restore_minimized_windows"].assert_called_once_with([11, 22])

    def test_fill_error_propagates(self):
        with self.send_env(
            fill_snapshot={"side_effect": ChatMismatchError("会话不匹配")}
        ) as mocks:
            with self.assertRaises(ChatMismatchError):
                module.send_message("你好", chat_name="会话 A")

        mocks["submit"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
