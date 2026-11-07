"""send_message 的发送流程与原子性测试（不发送真实消息）。"""

import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

import importlib

module = importlib.import_module("wechat_uia.api.send_message")
from wechat_uia.core.errors import (
    ChatMismatchError,
    ClickTargetBlockedError,
    SendMessageError,
)

_UNSET = object()


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
        marker = module._message_marker(before)
        after = FakeMessageList(
            [
                FakeMessageItem("旧消息", (1,)),
                FakeMessageItem("新消息", (2,)),
            ]
        )

        self.assertTrue(module._message_seen(after, marker, "新消息"))

    def test_same_old_message_alone_is_not_seen(self):
        messages = FakeMessageList([FakeMessageItem("同一句话", (1,))])
        marker = module._message_marker(messages)

        self.assertFalse(
            module._message_seen(messages, marker, "同一句话")
        )

    def test_identical_text_appended_is_seen(self):
        first = FakeMessageItem("同一句话", (1,))
        before = FakeMessageList([first])
        marker = module._message_marker(before)
        after = FakeMessageList(
            [first, FakeMessageItem("同一句话", (2,))]
        )

        self.assertTrue(
            module._message_seen(after, marker, "同一句话")
        )

    def test_unrelated_new_message_is_not_seen(self):
        before = FakeMessageList([FakeMessageItem("旧消息", (1,))])
        marker = module._message_marker(before)
        after = FakeMessageList(
            [
                FakeMessageItem("旧消息", (1,)),
                FakeMessageItem("别的新消息", (2,)),
            ]
        )

        self.assertFalse(module._message_seen(after, marker, "我要发的"))

    def test_missing_message_list_is_not_confirmed(self):
        seen, reason = module._confirm_message_seen(None, {}, "你好")

        self.assertFalse(seen)
        self.assertIn("未找到消息列表", reason)


class SendMessageTest(unittest.TestCase):
    def _snapshot(self, send_button=_UNSET, window_kind="main"):
        if send_button is _UNSET:
            send_button = object()
        return SimpleNamespace(
            hwnd=123,
            input_field=object(),
            send_button=send_button,
            message_list=None,
            window_kind=window_kind,
        )

    @contextmanager
    def send_env(self, snapshot=None, **overrides):
        """统一替换发送流程里的外部动作，返回可断言的 mock 字典。"""
        snapshot = snapshot or self._snapshot()
        defaults = {
            "fill_input": {"return_value": {"method": "value_pattern"}},
            "scan_window": {"return_value": snapshot},
            "session_identity_from_snapshot": {
                "return_value": {"chat_name": "会话 A"}
            },
            "session_mismatches": {"return_value": []},
            "activate_window": {"return_value": True},
            "focus_input": {"return_value": True},
            "press_alt_s": {},
            "press_enter": {},
            "click_control": {"return_value": True},
            "clear_input": {},
            "_read_input_text": {"return_value": "你好"},
            "_wait_input_empty": {"return_value": True},
            "_confirm_message_seen": {
                "return_value": (True, "消息列表已出现新消息")
            },
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
            mocks["SetForegroundWindow"] = stack.enter_context(
                mock.patch.object(module.win32gui, "SetForegroundWindow")
            )
            yield mocks

    def test_empty_text_is_rejected(self):
        with mock.patch.object(module, "fill_input") as fill:
            with self.assertRaises(ValueError):
                module.send_message("   ", chat_name="会话 A")
            fill.assert_not_called()

    def test_invalid_send_method_is_rejected(self):
        with mock.patch.object(module, "fill_input") as fill:
            with self.assertRaises(ValueError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="bad"
                )
            fill.assert_not_called()

    def test_default_uses_alt_s(self):
        with self.send_env() as mocks:
            result = module.send_message("你好", chat_name="会话 A")

        self.assertEqual(result["send_method"], "alt_s")
        self.assertTrue(result["verified"])
        self.assertTrue(result["confirm"]["message_seen"])
        self.assertEqual(result["session_check"], "strong")
        mocks["press_alt_s"].assert_called_once()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_not_called()
        self.assertEqual(mocks["focus_input"].call_args.args[1], 123)

    def test_name_only_session_check_is_reported(self):
        with self.send_env(
            fill_input={
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
        mocks["press_alt_s"].assert_not_called()
        mocks["press_enter"].assert_called_once()
        mocks["click_control"].assert_not_called()

    def test_button_success(self):
        with self.send_env() as mocks:
            result = module.send_message(
                "你好", chat_name="会话 A", send_method="button"
            )

        self.assertEqual(result["send_method"], "button")
        self.assertTrue(result["verified"])
        mocks["press_alt_s"].assert_not_called()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_called_once()
        self.assertEqual(mocks["click_control"].call_args.args[1], 123)

    def test_auto_prefers_alt_s(self):
        with self.send_env() as mocks:
            result = module.send_message(
                "你好", chat_name="会话 A", send_method="auto"
            )

        self.assertEqual(result["send_method"], "alt_s")
        mocks["press_alt_s"].assert_called_once()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_not_called()

    def test_auto_falls_back_to_button_when_focus_fails(self):
        with self.send_env(focus_input={"return_value": False}) as mocks:
            result = module.send_message(
                "你好", chat_name="会话 A", send_method="auto"
            )

        self.assertEqual(result["send_method"], "button")
        mocks["press_alt_s"].assert_not_called()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_called_once()

    def test_button_unavailable_raises_and_clears(self):
        snapshot = self._snapshot(send_button=None)
        with self.send_env(snapshot=snapshot) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="button"
                )

        mocks["clear_input"].assert_called_once()
        mocks["press_alt_s"].assert_not_called()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_not_called()

    def test_alt_s_failure_clears_and_raises(self):
        with self.send_env(
            press_alt_s={"side_effect": RuntimeError("boom")}
        ) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message("你好", chat_name="会话 A")

        mocks["clear_input"].assert_called_once()

    def test_enter_failure_clears_and_raises(self):
        with self.send_env(
            press_enter={"side_effect": RuntimeError("boom")}
        ) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="enter"
                )

        mocks["clear_input"].assert_called_once()

    def test_focus_failure_clears_and_raises(self):
        with self.send_env(focus_input={"return_value": False}) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="enter"
                )

        mocks["clear_input"].assert_called_once()
        mocks["press_enter"].assert_not_called()

    def test_pre_submit_content_mismatch_does_not_send(self):
        with self.send_env(
            _read_input_text={"return_value": "被改过的内容"}
        ) as mocks:
            with self.assertRaises(SendMessageError) as ctx:
                module.send_message("你好", chat_name="会话 A")

        self.assertIn("输入框内容已变化", str(ctx.exception))
        mocks["clear_input"].assert_called_once()
        mocks["press_alt_s"].assert_not_called()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_not_called()

    def test_unreadable_input_before_send_does_not_send(self):
        with self.send_env(
            _read_input_text={"return_value": None}
        ) as mocks:
            with self.assertRaises(SendMessageError):
                module.send_message("你好", chat_name="会话 A")

        mocks["clear_input"].assert_called_once()
        mocks["press_alt_s"].assert_not_called()

    def test_click_blocked_clears_and_raises(self):
        with self.send_env(
            click_control={
                "side_effect": ClickTargetBlockedError("被其他窗口挡住")
            }
        ) as mocks:
            with self.assertRaises(ClickTargetBlockedError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="button"
                )

        mocks["clear_input"].assert_called_once()

    def test_focus_click_blocked_clears_and_raises(self):
        with self.send_env(
            focus_input={
                "side_effect": ClickTargetBlockedError("被其他窗口挡住")
            }
        ) as mocks:
            with self.assertRaises(ClickTargetBlockedError):
                module.send_message(
                    "你好", chat_name="会话 A", send_method="alt_s"
                )

        mocks["clear_input"].assert_called_once()
        mocks["press_alt_s"].assert_not_called()

    def test_unconfirmed_message_returns_verified_false(self):
        with self.send_env(
            _confirm_message_seen={
                "return_value": (False, "消息列表未在限定时间内出现新消息")
            }
        ) as mocks:
            result = module.send_message("你好", chat_name="会话 A")

        self.assertFalse(result["verified"])
        self.assertFalse(result["confirm"]["message_seen"])
        self.assertIn("未在限定时间", result["confirm"]["reason"])
        mocks["press_alt_s"].assert_called_once()

    def test_history_window_is_rejected_before_sending(self):
        snapshot = self._snapshot(window_kind="history")
        with self.send_env(snapshot=snapshot) as mocks:
            with self.assertRaises(SendMessageError) as ctx:
                module.send_message("你好", chat_name="会话 A")

        self.assertIn("聊天记录窗口", str(ctx.exception))
        mocks["activate_window"].assert_not_called()
        mocks["press_alt_s"].assert_not_called()

    def test_session_change_after_fill_does_not_send(self):
        with self.send_env(
            session_mismatches={"return_value": ["会话名不一致"]}
        ) as mocks:
            with self.assertRaises(ChatMismatchError):
                module.send_message("你好", chat_name="会话 A")

        mocks["clear_input"].assert_not_called()
        mocks["press_alt_s"].assert_not_called()
        mocks["press_enter"].assert_not_called()
        mocks["click_control"].assert_not_called()

    def test_input_not_empty_after_send_clears_and_raises(self):
        with self.send_env(
            _wait_input_empty={"return_value": False}
        ) as mocks:
            with self.assertRaises(SendMessageError) as ctx:
                module.send_message(
                    "你好", chat_name="会话 A", send_method="button"
                )

        self.assertIn("不要直接重试", str(ctx.exception))
        mocks["clear_input"].assert_called_once()
        mocks["press_alt_s"].assert_not_called()

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

        fill_kwargs = mocks["fill_input"].call_args.kwargs
        self.assertTrue(fill_kwargs["wake_minimized"])
        self.assertTrue(fill_kwargs["wake_tray"])

        self.assertGreaterEqual(mocks["scan_window"].call_count, 1)
        for call in mocks["scan_window"].call_args_list:
            self.assertTrue(call.kwargs["wake_minimized"])
            self.assertTrue(call.kwargs["wake_tray"])

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
            fill_input={"side_effect": ChatMismatchError("会话不匹配")}
        ) as mocks:
            with self.assertRaises(ChatMismatchError):
                module.send_message("你好", chat_name="会话 A")

        mocks["scan_window"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
