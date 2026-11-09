"""read_private_tags 的编排与标签拆分测试（不打开真实微信）。"""

import importlib
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

module = importlib.import_module("wechat_uia.core.read_private_tags")
from wechat_uia.core.errors import (
    ChatMismatchError,
    ContactProfileUnavailableError,
    NotPrivateChatError,
)

_UNSET = object()


def make_snapshot(
    chat_name="张三",
    *,
    member_cell=_UNSET,
    chat_info_open=False,
    chat_info_button=_UNSET,
    window_kind="chat",
    window_automation_id="ChatSingleWindowa",
    layout="single",
):
    if member_cell is _UNSET:
        member_cell = None
    if chat_info_button is _UNSET:
        chat_info_button = object()
    return SimpleNamespace(
        hwnd=123,
        chat_name=chat_name,
        window_state="normal",
        window_kind=window_kind,
        window_automation_id=window_automation_id,
        layout=layout,
        chat_info_button=chat_info_button,
        member_cell=member_cell,
        chat_info_open=chat_info_open,
    )


class SplitTagsTest(unittest.TestCase):
    def test_single_tag(self):
        self.assertEqual(module.split_tags(["大学同学"]), ["大学同学"])

    def test_comma_separated_tags(self):
        self.assertEqual(
            module.split_tags(["高中同学,大学同学"]),
            ["高中同学", "大学同学"],
        )

    def test_full_width_and_other_separators(self):
        self.assertEqual(
            module.split_tags(["A，B、C;D；E"]),
            ["A", "B", "C", "D", "E"],
        )

    def test_empty_values_are_ignored(self):
        self.assertEqual(module.split_tags(["", "  "]), [])

    def test_duplicates_are_removed_in_order(self):
        self.assertEqual(
            module.split_tags(["大学同学,高中同学,大学同学"]),
            ["大学同学", "高中同学"],
        )


class ReadPrivateTagsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            module,
            "resolve_surface",
            return_value=SimpleNamespace(
                hwnd=123,
                role="chat",
                selection_reason="unique_chat_name",
                ambiguous=False,
                candidate_hwnds=(123,),
            ),
        )
        self.resolve = patcher.start()
        self.addCleanup(patcher.stop)

    def _base_patches(self, before, after, panel, **overrides):
        values = {
            "scan_window": {"side_effect": [before, after]},
            "classify_snapshot": {
                "return_value": {
                    "type": "private",
                    "label": "私聊",
                    "chat_name": before.chat_name,
                }
            },
            "find_profile_popup": {"return_value": None},
            "activate_window": {"return_value": True},
            "_click_chat_info": {},
            "_wait_for_panel": {"return_value": panel},
            "_click_member_avatar": {},
            "_wait_for_profile": {"return_value": object()},
            "read_profile_contact": {"return_value": before.chat_name},
            "read_profile_fields": {"return_value": {"标签": ["大学同学"]}},
            "close_profile_popup": {"return_value": True},
            "_close_chat_info": {"return_value": True},
        }
        values.update(overrides)
        return values

    def _run(
        self, before, after, panel, call_kwargs=None, **overrides
    ):
        patches = self._base_patches(before, after, panel, **overrides)
        patches.setdefault(
            "probe_surface",
            {"return_value": SimpleNamespace(chat_name=after.chat_name)},
        )
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    module.win32gui,
                    "GetForegroundWindow",
                    side_effect=[111, 222],
                )
            )
            set_foreground = stack.enter_context(
                mock.patch.object(module.win32gui, "SetForegroundWindow")
            )
            for name, kwargs in patches.items():
                stack.enter_context(
                    mock.patch.object(module, name, **kwargs)
                )
            result = module.read_private_tags(**(call_kwargs or {}))
        return result, set_foreground

    def test_reads_and_splits_tags(self):
        before = make_snapshot()
        panel = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"), chat_info_open=True
        )
        after = make_snapshot()

        result, set_foreground = self._run(
            before,
            after,
            panel,
            read_profile_fields={
                "return_value": {"标签": ["高中同学,大学同学"]}
            },
        )

        self.assertEqual(result["chat_name"], "张三")
        self.assertEqual(result["contact"], "张三")
        self.assertEqual(result["tags"], ["高中同学", "大学同学"])
        self.assertEqual(result["raw_tags"], ["高中同学,大学同学"])
        self.assertEqual(result["hwnd"], 123)
        self.assertEqual(result["window_state"], "normal")
        set_foreground.assert_called_once_with(111)

    def test_session_is_forwarded_and_window_fields_returned(self):
        before = make_snapshot()
        panel = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"), chat_info_open=True
        )
        after = make_snapshot()
        session = {
            "chat_name": "张三",
            "window_automation_id": "ChatSingleWindowa",
        }

        result, _ = self._run(
            before,
            after,
            panel,
            call_kwargs={"session": session},
        )

        self.resolve.assert_called_once_with(
            hwnd=None,
            chat_name="张三",
            session=session,
        )
        self.assertEqual(result["window_role"], "chat")
        self.assertEqual(
            result["window_automation_id"], "ChatSingleWindowa"
        )
        self.assertEqual(result["layout"], "single")
        self.assertEqual(result["selection_reason"], "unique_chat_name")
        self.assertFalse(result["ambiguous"])
        self.assertEqual(result["candidate_hwnds"], [123])

    def test_existing_panel_is_reused_and_left_open(self):
        before = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"),
            chat_info_open=True,
        )
        after = make_snapshot()

        with mock.patch.object(
            module, "scan_window", side_effect=[before, after]
        ), mock.patch.object(
            module,
            "probe_surface",
            return_value=SimpleNamespace(chat_name=after.chat_name),
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "private",
                "label": "私聊",
                "chat_name": "张三",
            },
        ), mock.patch.object(
            module, "find_profile_popup", return_value=None
        ), mock.patch.object(
            module, "activate_window", return_value=True
        ), mock.patch.object(
            module, "_click_chat_info"
        ) as open_click, mock.patch.object(
            module, "_wait_for_panel"
        ) as wait_panel, mock.patch.object(
            module, "_click_member_avatar"
        ), mock.patch.object(
            module, "_wait_for_profile", return_value=object()
        ), mock.patch.object(
            module, "read_profile_contact", return_value="张三"
        ), mock.patch.object(
            module, "read_profile_fields", return_value={"标签": ["大学同学"]}
        ), mock.patch.object(
            module, "close_profile_popup"
        ), mock.patch.object(
            module, "_close_chat_info"
        ) as close_info, mock.patch.object(
            module.win32gui, "GetForegroundWindow", return_value=0
        ):
            result = module.read_private_tags()

        self.assertEqual(result["tags"], ["大学同学"])
        open_click.assert_not_called()
        wait_panel.assert_not_called()
        close_info.assert_not_called()

    def test_no_tag_returns_empty_list(self):
        before = make_snapshot()
        panel = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"), chat_info_open=True
        )
        after = make_snapshot()

        result, _ = self._run(
            before,
            after,
            panel,
            read_profile_fields={"return_value": {}},
        )

        self.assertEqual(result["tags"], [])
        self.assertEqual(result["raw_tags"], [])

    def test_not_private_is_rejected_before_clicking(self):
        before = make_snapshot()

        with mock.patch.object(
            module, "scan_window", return_value=before
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "group",
                "label": "群聊",
                "chat_name": "某群",
            },
        ), mock.patch.object(module, "_click_chat_info") as click:
            with self.assertRaises(NotPrivateChatError):
                module.read_private_tags()
        click.assert_not_called()

    def test_missing_member_cell_closes_panel(self):
        before = make_snapshot(chat_name="文件传输助手")
        panel = make_snapshot(
            chat_name="文件传输助手",
            member_cell=None,
            chat_info_open=True,
        )
        after = make_snapshot(chat_name="文件传输助手")

        with mock.patch.object(
            module, "scan_window", side_effect=[before, after]
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "private",
                "label": "私聊",
                "chat_name": "文件传输助手",
            },
        ), mock.patch.object(
            module, "find_profile_popup", return_value=None
        ), mock.patch.object(
            module, "activate_window", return_value=True
        ), mock.patch.object(
            module, "_click_chat_info"
        ), mock.patch.object(
            module, "_wait_for_panel", return_value=panel
        ), mock.patch.object(
            module, "_close_chat_info", return_value=True
        ) as close_info, mock.patch.object(
            module.win32gui, "GetForegroundWindow", return_value=0
        ):
            with self.assertRaises(ContactProfileUnavailableError):
                module.read_private_tags()
        close_info.assert_called_once_with(before.hwnd, button=mock.ANY)

    def test_missing_profile_closes_panel(self):
        before = make_snapshot()
        panel = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"), chat_info_open=True
        )
        after = make_snapshot()

        with mock.patch.object(
            module, "scan_window", side_effect=[before, after]
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "private",
                "label": "私聊",
                "chat_name": "张三",
            },
        ), mock.patch.object(
            module, "find_profile_popup", return_value=None
        ), mock.patch.object(
            module, "activate_window", return_value=True
        ), mock.patch.object(
            module, "_click_chat_info"
        ), mock.patch.object(
            module, "_wait_for_panel", return_value=panel
        ), mock.patch.object(
            module, "_click_member_avatar"
        ), mock.patch.object(
            module, "_wait_for_profile", return_value=None
        ), mock.patch.object(
            module, "_close_chat_info", return_value=True
        ) as close_info, mock.patch.object(
            module.win32gui, "GetForegroundWindow", return_value=0
        ):
            with self.assertRaises(ContactProfileUnavailableError):
                module.read_private_tags()
        close_info.assert_called_once_with(before.hwnd, button=mock.ANY)

    def test_profile_contact_mismatch_is_rejected(self):
        before = make_snapshot()
        panel = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"), chat_info_open=True
        )
        after = make_snapshot()

        with mock.patch.object(
            module, "scan_window", side_effect=[before, after]
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "private",
                "label": "私聊",
                "chat_name": "张三",
            },
        ), mock.patch.object(
            module, "find_profile_popup", return_value=None
        ), mock.patch.object(
            module, "activate_window", return_value=True
        ), mock.patch.object(
            module, "_click_chat_info"
        ), mock.patch.object(
            module, "_wait_for_panel", return_value=panel
        ), mock.patch.object(
            module, "_click_member_avatar"
        ), mock.patch.object(
            module, "_wait_for_profile", return_value=object()
        ), mock.patch.object(
            module, "read_profile_contact", return_value="别人"
        ), mock.patch.object(
            module, "close_profile_popup"
        ) as close_popup, mock.patch.object(
            module, "_close_chat_info", return_value=True
        ) as close_info, mock.patch.object(
            module.win32gui, "GetForegroundWindow", return_value=0
        ):
            with self.assertRaises(ChatMismatchError):
                module.read_private_tags()
        close_popup.assert_called_once()
        close_info.assert_called_once_with(before.hwnd, button=mock.ANY)

    def test_existing_profile_is_rejected(self):
        before = make_snapshot()

        with mock.patch.object(
            module, "scan_window", return_value=before
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "private",
                "label": "私聊",
                "chat_name": "张三",
            },
        ), mock.patch.object(
            module, "find_profile_popup", return_value=object()
        ), mock.patch.object(
            module, "_click_chat_info"
        ) as click:
            with self.assertRaises(ContactProfileUnavailableError):
                module.read_private_tags()
        click.assert_not_called()

    def test_chat_switch_during_read_is_rejected(self):
        before = make_snapshot()
        panel = make_snapshot(
            member_cell=SimpleNamespace(Name="张三"), chat_info_open=True
        )
        after = make_snapshot(chat_name="Grace")

        with mock.patch.object(
            module, "scan_window", side_effect=[before, after]
        ), mock.patch.object(
            module,
            "probe_surface",
            return_value=SimpleNamespace(chat_name=after.chat_name),
        ), mock.patch.object(
            module,
            "classify_snapshot",
            return_value={
                "type": "private",
                "label": "私聊",
                "chat_name": "张三",
            },
        ), mock.patch.object(
            module, "find_profile_popup", return_value=None
        ), mock.patch.object(
            module, "activate_window", return_value=True
        ), mock.patch.object(
            module, "_click_chat_info"
        ), mock.patch.object(
            module, "_wait_for_panel", return_value=panel
        ), mock.patch.object(
            module, "_click_member_avatar"
        ), mock.patch.object(
            module, "_wait_for_profile", return_value=object()
        ), mock.patch.object(
            module, "read_profile_contact", return_value="张三"
        ), mock.patch.object(
            module,
            "read_profile_fields",
            return_value={"标签": ["大学同学"]},
        ), mock.patch.object(
            module, "close_profile_popup"
        ), mock.patch.object(
            module, "_close_chat_info", return_value=True
        ), mock.patch.object(
            module.win32gui, "GetForegroundWindow", return_value=0
        ):
            with self.assertRaises(ChatMismatchError):
                module.read_private_tags()


if __name__ == "__main__":
    unittest.main()
