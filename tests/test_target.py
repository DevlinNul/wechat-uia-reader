"""会话目标缓存的复用与失效判断（纯逻辑，不依赖真实微信）。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core import target as target_module


class FakeElement:
    def __init__(self, runtime_id=(1, 2, 3), name="会话 A", alive=True):
        self._runtime_id = runtime_id
        self._name = name
        self.alive = alive

    def __bool__(self):
        return self.alive

    def GetRuntimeId(self):
        return list(self._runtime_id)

    @property
    def CurrentName(self):
        return self._name


def make_snapshot(chat_name="会话 A", hwnd=123):
    return SimpleNamespace(
        hwnd=hwnd,
        chat_name=chat_name,
        chat_name_element=FakeElement(name=chat_name),
        input_field=FakeElement(),
        message_list=FakeElement(),
        send_button=FakeElement(),
        window_kind="main",
        window_automation_id=None,
        layout="wide",
    )


class TargetCacheTest(unittest.TestCase):
    def setUp(self):
        target_module.reset_cache()
        self.snapshots = []
        self.resolve_calls = []
        self.scan_calls = []

        def resolve(**kwargs):
            self.resolve_calls.append(kwargs)
            return SimpleNamespace(hwnd=123, role="main")

        def scan(hwnd):
            self.scan_calls.append(hwnd)
            snapshot = make_snapshot()
            self.snapshots.append(snapshot)
            return snapshot

        self.patches = [
            mock.patch.object(target_module, "_resolve", side_effect=resolve),
            mock.patch.object(target_module, "_scan", side_effect=scan),
            mock.patch.object(target_module, "_ensure_ready"),
            mock.patch.object(
                target_module, "_window_exists", return_value=True
            ),
            mock.patch.object(
                target_module, "_window_state", return_value="normal"
            ),
            mock.patch.object(
                target_module, "_window_still_target", return_value=True
            ),
            mock.patch.object(
                target_module, "classify_snapshot", return_value={"ok": True}
            ),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        target_module.reset_cache()

    def test_second_acquire_reuses_target(self):
        first = target_module.acquire(chat_name="会话 A")
        second = target_module.acquire(chat_name="会话 A")

        self.assertIs(first, second)
        self.assertEqual(len(self.resolve_calls), 1)
        self.assertEqual(len(self.scan_calls), 1)

    def test_chat_switch_invalidates(self):
        first = target_module.acquire(chat_name="会话 A")
        first.snapshot.chat_name_element._name = "会话 B"

        target_module.acquire(chat_name="会话 A")

        self.assertEqual(len(self.resolve_calls), 2)
        self.assertEqual(len(self.scan_calls), 2)

    def test_recreated_input_invalidates(self):
        first = target_module.acquire(chat_name="会话 A")
        first.snapshot.input_field._runtime_id = (9, 9, 9)

        target_module.acquire(chat_name="会话 A")

        self.assertEqual(len(self.scan_calls), 2)

    def test_minimized_window_invalidates(self):
        target_module.acquire(chat_name="会话 A")
        with mock.patch.object(
            target_module, "_window_state", return_value="minimized"
        ):
            target_module.acquire(chat_name="会话 A")

        self.assertEqual(len(self.scan_calls), 2)

    def test_strict_bypasses_cache(self):
        target_module.acquire(chat_name="会话 A")
        target_module.acquire(chat_name="会话 A", strict=True)

        self.assertEqual(len(self.resolve_calls), 2)

    def test_other_window_session_bypasses_cache(self):
        target_module.acquire(chat_name="会话 A")
        target_module.acquire(
            chat_name="会话 A",
            session={"window_automation_id": "ChatSingleWindowx"},
        )

        self.assertEqual(len(self.resolve_calls), 2)

    def test_reset_cache_forces_rescan(self):
        target_module.acquire(chat_name="会话 A")
        target_module.reset_cache()
        target_module.acquire(chat_name="会话 A")

        self.assertEqual(len(self.scan_calls), 2)

    def test_selection_metadata_carried(self):
        result = target_module.acquire(chat_name="会话 A")

        self.assertEqual(result.resolved.hwnd, 123)
        self.assertEqual(result.classification, {"ok": True})


if __name__ == "__main__":
    unittest.main()
