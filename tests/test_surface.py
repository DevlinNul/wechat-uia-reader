"""窗口选择策略测试（不接触真实微信）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core import surface
from wechat_uia.core.errors import (
    AmbiguousConversationError,
    ConversationNotOpenError,
    WindowUnavailableError,
)
from wechat_uia.core.surface import (
    WindowSurface,
    choose_candidate,
    find_candidates,
    resolve_surface,
)


def make_surface(hwnd, role, chat_name, automation_id=None):
    return WindowSurface(
        hwnd=hwnd,
        role=role,
        title=chat_name or "",
        window_automation_id=automation_id,
        chat_name=chat_name,
        window_state="normal",
        visible=True,
    )


class ChooseCandidateTest(unittest.TestCase):
    def test_empty_candidates_raise(self):
        with self.assertRaises(ConversationNotOpenError):
            choose_candidate([])

    def test_single_candidate(self):
        result = choose_candidate([make_surface(1, "main", "会话 A")])
        self.assertEqual(result.hwnd, 1)
        self.assertFalse(result.ambiguous)

    def test_multiple_detached_windows_are_ambiguous(self):
        candidates = [
            make_surface(1, "chat", "会话 A", "ChatSingleWindowa"),
            make_surface(2, "chat", "会话 A", "ChatSingleWindowb"),
        ]
        with self.assertRaises(AmbiguousConversationError):
            choose_candidate(candidates)

    def test_main_preferred_without_foreground(self):
        candidates = [
            make_surface(1, "main", "会话 A"),
            make_surface(2, "chat", "会话 A", "ChatSingleWindowa"),
        ]
        with mock.patch.object(
            surface.win32gui, "GetForegroundWindow", return_value=999
        ):
            result = choose_candidate(candidates)
        self.assertEqual(result.hwnd, 1)
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.selection_reason, "main_preferred")

    def test_foreground_wins(self):
        candidates = [
            make_surface(1, "main", "会话 A"),
            make_surface(2, "chat", "会话 A", "ChatSingleWindowa"),
        ]
        with mock.patch.object(
            surface.win32gui, "GetForegroundWindow", return_value=2
        ):
            result = choose_candidate(candidates)
        self.assertEqual(result.hwnd, 2)
        self.assertEqual(result.selection_reason, "foreground")

    def test_strict_rejects_multiple(self):
        candidates = [
            make_surface(1, "main", "会话 A"),
            make_surface(2, "chat", "会话 A", "ChatSingleWindowa"),
        ]
        with self.assertRaises(AmbiguousConversationError):
            choose_candidate(candidates, strict=True)


class FindCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.surfaces = [
            make_surface(1, "main", "会话 A"),
            make_surface(2, "chat", "会话 A", "ChatSingleWindowa"),
            make_surface(3, "chat", "会话 B", "ChatSingleWindowb"),
            make_surface(4, "history", "会话 A", "HistoryA"),
        ]

    def test_filters_by_chat_name(self):
        result = find_candidates("会话 A", surfaces=self.surfaces)
        self.assertEqual([s.hwnd for s in result], [1, 2])

    def test_excludes_history_by_default(self):
        result = find_candidates("会话 A", surfaces=self.surfaces)
        self.assertNotIn(4, [s.hwnd for s in result])

    def test_session_window_id_wins(self):
        result = find_candidates(
            session={
                "chat_name": "会话 A",
                "window_automation_id": "ChatSingleWindowa",
            },
            surfaces=self.surfaces,
        )
        self.assertEqual([s.hwnd for s in result], [2])

    def test_main_session_filters_main_window(self):
        result = find_candidates(
            session={"chat_name": "会话 A", "session_runtime_id": [1, 2]},
            surfaces=self.surfaces,
        )
        self.assertEqual([s.hwnd for s in result], [1])


class ResolveSurfaceTest(unittest.TestCase):
    def test_explicit_hwnd(self):
        target = make_surface(1, "chat", "会话 A", "ChatSingleWindowa")
        with mock.patch.object(
            surface, "probe_surface", return_value=target
        ):
            result = resolve_surface(hwnd=1)
        self.assertEqual(result.hwnd, 1)
        self.assertEqual(result.selection_reason, "explicit_hwnd")

    def test_explicit_hwnd_must_be_conversation(self):
        with mock.patch.object(surface, "probe_surface", return_value=None):
            with self.assertRaises(WindowUnavailableError):
                resolve_surface(hwnd=1)

    def test_default_main(self):
        main = make_surface(1, "main", "会话 A")
        with (
            mock.patch.object(surface, "list_surfaces", return_value=[main]),
            mock.patch.object(
                surface.win32gui, "GetForegroundWindow", return_value=999
            ),
        ):
            result = resolve_surface()
        self.assertEqual(result.hwnd, 1)
        self.assertEqual(result.selection_reason, "default_main")

    def test_only_detached_surface(self):
        chat = make_surface(2, "chat", "会话 A", "ChatSingleWindowa")
        with mock.patch.object(
            surface, "list_surfaces", return_value=[chat]
        ):
            result = resolve_surface()
        self.assertEqual(result.hwnd, 2)
        self.assertEqual(result.selection_reason, "only_surface")

    def test_no_surfaces_raise(self):
        with mock.patch.object(surface, "list_surfaces", return_value=[]):
            with self.assertRaises(WindowUnavailableError):
                resolve_surface()

    def test_resolve_by_chat_name(self):
        main = make_surface(1, "main", "会话 A")
        other = make_surface(2, "chat", "会话 B", "ChatSingleWindowb")
        with (
            mock.patch.object(
                surface, "list_surfaces", return_value=[main, other]
            ),
            mock.patch.object(
                surface.win32gui, "GetForegroundWindow", return_value=999
            ),
        ):
            result = resolve_surface(chat_name="会话 A")
        self.assertEqual(result.hwnd, 1)
        self.assertFalse(result.ambiguous)


if __name__ == "__main__":
    unittest.main()
