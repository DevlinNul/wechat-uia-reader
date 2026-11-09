"""fill_snapshot 的会话校验与写入测试，以及 fill_input 的编排测试。"""

import importlib
import unittest
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core.errors import (
    CannotSendError,
    ChatMismatchError,
    InputFillError,
    InputNotEmptyError,
    UIAUnavailableError,
)

fill_core = importlib.import_module("wechat_uia.core.fill")
fill_api = importlib.import_module("wechat_uia.api.fill_input")
input_core = importlib.import_module("wechat_uia.core.input")
input_text = importlib.import_module("wechat_uia.core.input_text")

_UNSET = object()


class FakeSessionItem:
    ControlTypeName = "ListItemControl"

    def __init__(self, name):
        self.Name = name + "\n预览"


class FakeSessionList:
    def __init__(self, names):
        self._items = [FakeSessionItem(name) for name in names]

    def GetChildren(self):
        return list(self._items)


def make_snapshot(
    chat_name="会话 A",
    input_field=_UNSET,
    uia_available=True,
    session_names=None,
    window_kind="main",
    session_list=_UNSET,
    window_automation_id=None,
    layout="wide",
):
    if input_field is _UNSET:
        input_field = object()
    if session_list is _UNSET:
        if session_names is None:
            session_names = (chat_name,)
        session_list = FakeSessionList(session_names)
    return SimpleNamespace(
        hwnd=123,
        chat_name=chat_name,
        input_field=input_field,
        uia_available=uia_available,
        session_list=session_list,
        window_kind=window_kind,
        window_automation_id=window_automation_id,
        layout=layout,
    )


def classification(can_send=True, type_="private", member_count=None):
    return {
        "chat_name": "会话 A",
        "type": type_,
        "label": "私聊",
        "member_count": member_count,
        "can_send": can_send,
        "reason": "测试",
    }


def run_fill(
    snapshot,
    expected,
    text="hi",
    *,
    cls=None,
    unique=None,
    selected=None,
    input_text="",
    value_result=True,
    send_result=False,
):
    cls = cls or classification(can_send=True)
    with (
        mock.patch.object(fill_core, "classify_snapshot", return_value=cls),
        mock.patch.object(
            fill_core, "session_name_is_unique", return_value=unique
        ),
        mock.patch.object(
            fill_core, "selected_session_identity", return_value=selected
        ),
        mock.patch.object(
            fill_core, "read_input_text", return_value=input_text
        ),
        mock.patch.object(
            fill_core, "fill_with_value_pattern", return_value=value_result
        ) as value_fill,
        mock.patch.object(
            fill_core, "fill_with_sendinput", return_value=send_result
        ) as send_fill,
        mock.patch.object(fill_core, "set_value_pattern", return_value=True),
    ):
        result = fill_core.fill_snapshot(snapshot, expected, text)
    return result, [None, None, None, None, value_fill, send_fill, None]


class FillSnapshotValidationTest(unittest.TestCase):
    def test_chat_mismatch_raises_before_writing(self):
        with self.assertRaises(ChatMismatchError):
            run_fill(
                make_snapshot("会话 A"),
                {"chat_name": "会话 B"},
                value_result=True,
            )

    def test_uia_unavailable_raises(self):
        with self.assertRaises(UIAUnavailableError):
            fill_core.fill_snapshot(
                make_snapshot(uia_available=False), {"chat_name": "会话 A"}, "hi"
            )

    def test_history_window_is_rejected(self):
        with self.assertRaises(CannotSendError) as ctx:
            fill_core.fill_snapshot(
                make_snapshot(window_kind="history"), {"chat_name": "会话 A"}, "hi"
            )
        self.assertIn("聊天记录窗口", str(ctx.exception))

    def test_cannot_send_raises(self):
        with self.assertRaises(CannotSendError):
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A"},
                cls=classification(can_send=False),
            )

    def test_missing_input_field_raises(self):
        with self.assertRaises(CannotSendError):
            run_fill(
                make_snapshot(input_field=None),
                {"chat_name": "会话 A"},
            )

    def test_non_empty_input_raises(self):
        with self.assertRaises(InputNotEmptyError):
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A"},
                input_text="已有内容",
            )

    def test_whitespace_only_input_is_allowed(self):
        result, _ = run_fill(
            make_snapshot(),
            {"chat_name": "会话 A"},
            input_text="  \n\t ",
            value_result=True,
        )
        self.assertEqual(result["method"], "value_pattern")

    def test_unreadable_input_raises(self):
        with self.assertRaises(InputFillError):
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A"},
                input_text=None,
            )

    def test_session_type_and_member_count_match(self):
        result, _ = run_fill(
            make_snapshot(),
            {"chat_name": "会话 A", "type": "group", "member_count": 26},
            cls=classification(type_="group", member_count=26),
            value_result=True,
        )
        self.assertEqual(result["method"], "value_pattern")

    def test_session_type_mismatch_raises(self):
        with self.assertRaises(ChatMismatchError) as ctx:
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A", "type": "group"},
                cls=classification(type_="private"),
            )
        self.assertIn("会话类型", str(ctx.exception))

    def test_session_member_count_mismatch_raises(self):
        with self.assertRaises(ChatMismatchError) as ctx:
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A", "type": "group", "member_count": 26},
                cls=classification(type_="group", member_count=10),
            )
        self.assertIn("成员数", str(ctx.exception))

    def test_session_runtime_id_mismatch_raises(self):
        with self.assertRaises(ChatMismatchError) as ctx:
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A", "session_runtime_id": [9, 9, 9]},
                selected={
                    "name": "会话 A",
                    "automation_id": "session_item_会话 A",
                    "runtime_id": (1, 2, 3),
                },
            )
        self.assertIn("RuntimeId", str(ctx.exception))

    def test_session_runtime_id_match(self):
        result, _ = run_fill(
            make_snapshot(),
            {
                "chat_name": "会话 A",
                "session_automation_id": "session_item_会话 A",
                "session_runtime_id": [1, 2, 3],
            },
            selected={
                "name": "会话 A",
                "automation_id": "session_item_会话 A",
                "runtime_id": (1, 2, 3),
            },
            value_result=True,
        )
        self.assertEqual(result["method"], "value_pattern")
        self.assertEqual(result["session_check"], "strong")

    def test_duplicate_name_without_strong_identity_raises(self):
        with self.assertRaises(ChatMismatchError) as ctx:
            run_fill(
                make_snapshot(session_names=("会话 A", "会话 A")),
                {"chat_name": "会话 A"},
                unique=False,
            )
        self.assertIn("不唯一", str(ctx.exception))

    def test_small_layout_without_session_list_allows_name_only(self):
        result, _ = run_fill(
            make_snapshot(session_list=None, layout="narrow"),
            {"chat_name": "会话 A"},
            unique=None,
            value_result=True,
        )
        self.assertEqual(result["session_check"], "name_only")

    def test_detached_window_name_only(self):
        result, _ = run_fill(
            make_snapshot(
                session_list=None,
                window_kind="chat",
                window_automation_id="ChatSingleWindowfilehelper",
                layout="single",
            ),
            {"chat_name": "会话 A"},
            unique=None,
            value_result=True,
        )
        self.assertEqual(result["session_check"], "name_only")
        self.assertEqual(result["window_role"], "chat")

    def test_detached_window_identity_match(self):
        result, _ = run_fill(
            make_snapshot(
                session_list=None,
                window_kind="chat",
                window_automation_id="ChatSingleWindowfilehelper",
                layout="single",
            ),
            {
                "chat_name": "会话 A",
                "window_automation_id": "ChatSingleWindowfilehelper",
            },
            unique=None,
            value_result=True,
        )
        self.assertEqual(result["session_check"], "window_identity")

    def test_strong_identity_allows_duplicate_name(self):
        result, _ = run_fill(
            make_snapshot(session_names=("会话 A", "会话 A")),
            {"chat_name": "会话 A", "session_runtime_id": (1, 2, 3)},
            selected={
                "name": "会话 A",
                "automation_id": "session_item_会话 A",
                "runtime_id": (1, 2, 3),
            },
            value_result=True,
        )
        self.assertEqual(result["method"], "value_pattern")

    def test_prefers_value_pattern(self):
        result, entered = run_fill(
            make_snapshot(),
            {"chat_name": "会话 A"},
            value_result=True,
        )
        value_fill = entered[4]
        send_fill = entered[5]
        self.assertEqual(result["method"], "value_pattern")
        self.assertTrue(result["verified"])
        value_fill.assert_called_once()
        send_fill.assert_not_called()

    def test_falls_back_to_sendinput(self):
        result, entered = run_fill(
            make_snapshot(),
            {"chat_name": "会话 A"},
            value_result=False,
            send_result=True,
        )
        send_fill = entered[5]
        self.assertEqual(result["method"], "sendinput")
        send_fill.assert_called_once()

    def test_both_methods_failing_raises(self):
        with self.assertRaises(InputFillError):
            run_fill(
                make_snapshot(),
                {"chat_name": "会话 A"},
                value_result=False,
                send_result=False,
            )


class FillInputOrchestrationTest(unittest.TestCase):
    def test_empty_chat_name_is_rejected(self):
        with mock.patch.object(fill_api, "resolve_surface") as resolve:
            with self.assertRaises(ValueError):
                fill_api.fill_input("hi", "  ")
            resolve.assert_not_called()

    def test_conflicting_chat_name_and_session_raises(self):
        with self.assertRaises(ValueError):
            fill_api.fill_input(
                "hi",
                "会话 A",
                session={"chat_name": "会话 B", "type": "private"},
            )

    def test_session_without_chat_name_raises(self):
        with self.assertRaises(ValueError):
            fill_api.fill_input("hi", session={"type": "private"})

    def test_resolves_and_scans_once(self):
        resolved = SimpleNamespace(hwnd=123, role="main")
        snapshot = object()
        with (
            mock.patch.object(
                fill_api, "resolve_surface", return_value=resolved
            ) as resolve,
            mock.patch.object(fill_api, "ensure_ready") as ready,
            mock.patch.object(
                fill_api, "scan_window", return_value=snapshot
            ) as scan,
            mock.patch.object(
                fill_api,
                "fill_snapshot",
                return_value={"method": "value_pattern"},
            ) as fill,
        ):
            result = fill_api.fill_input("hi", "会话 A")

        resolve.assert_called_once_with(
            hwnd=None, chat_name="会话 A", session=None
        )
        ready.assert_called_once_with(
            123,
            window_role="main",
            wake_minimized=False,
            wake_tray=False,
        )
        scan.assert_called_once_with(123)
        fill.assert_called_once()
        self.assertEqual(result["method"], "value_pattern")

    def test_passes_session_and_wake_flags(self):
        resolved = SimpleNamespace(hwnd=123, role="chat")
        session = {
            "chat_name": "会话 A",
            "window_automation_id": "ChatSingleWindowfilehelper",
        }
        with (
            mock.patch.object(
                fill_api, "resolve_surface", return_value=resolved
            ) as resolve,
            mock.patch.object(fill_api, "ensure_ready") as ready,
            mock.patch.object(fill_api, "scan_window", return_value=object()),
            mock.patch.object(
                fill_api,
                "fill_snapshot",
                return_value={"method": "value_pattern"},
            ),
        ):
            fill_api.fill_input(
                "hi",
                session=session,
                wake_minimized=True,
                wake_tray=True,
            )

        resolve.assert_called_once_with(
            hwnd=None, chat_name="会话 A", session=session
        )
        ready.assert_called_once_with(
            123,
            window_role="chat",
            wake_minimized=True,
            wake_tray=True,
        )


class InputPrimitiveTest(unittest.TestCase):
    def test_normalizes_newlines(self):
        self.assertEqual(
            input_text.normalize_text("a\r\nb\rc"), "a\nb\nc"
        )

    def test_wait_input_matches_retries_transient_readback(self):
        with (
            mock.patch.object(
                input_core, "input_matches", side_effect=[False, True]
            ) as matches,
            mock.patch.object(input_core.time, "sleep"),
        ):
            self.assertTrue(
                input_core.wait_input_matches(object(), "hi", timeout=1)
            )
        self.assertEqual(matches.call_count, 2)


if __name__ == "__main__":
    unittest.main()
