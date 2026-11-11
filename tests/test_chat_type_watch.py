"""快速轮询路径的缓存与兜底测试（不依赖真实微信窗口）。"""

import unittest
from unittest import mock

from wechat_uia.core import chat_type_watch

RESULT = {
    "type": "private",
    "label": "私聊",
    "chat_name": "张三",
    "member_count": None,
    "can_send": True,
    "reason": "测试",
    "hwnd": 100,
    "window_state": "normal",
    "window_role": "main",
    "window_automation_id": None,
    "layout": "wide",
}


class FastPathTest(unittest.TestCase):
    def setUp(self):
        chat_type_watch.reset_cache()
        self.header_calls = []
        self.full_calls = []

    def tearDown(self):
        chat_type_watch.reset_cache()

    def fake_header(self, hwnd):
        self.header_calls.append(hwnd)
        return dict(RESULT), None

    def fake_full(self, hwnd):
        self.full_calls.append(hwnd)
        return dict(RESULT)

    def run_fast(self, **kwargs):
        kwargs.setdefault("hwnd", 100)
        with mock.patch.object(
            chat_type_watch, "_resolve_hwnd", return_value=100
        ), mock.patch.object(
            chat_type_watch, "_read_header", side_effect=self.fake_header
        ), mock.patch.object(
            chat_type_watch, "_read_full", side_effect=self.fake_full
        ):
            return chat_type_watch.get_chat_type_fast(**kwargs)

    def test_first_call_reads_header(self):
        result = self.run_fast()

        self.assertEqual(result["type"], "private")
        self.assertEqual(self.header_calls, [100])
        self.assertEqual(self.full_calls, [])

    def test_cache_hit_skips_reading(self):
        self.run_fast()
        with mock.patch.object(
            chat_type_watch, "_label_unchanged", return_value=True
        ):
            self.run_fast()

        self.assertEqual(self.header_calls, [100])

    def test_label_change_forces_reread(self):
        self.run_fast()
        with mock.patch.object(
            chat_type_watch, "_label_unchanged", return_value=False
        ):
            self.run_fast()

        self.assertEqual(self.header_calls, [100, 100])

    def test_expired_ttl_forces_reread(self):
        self.run_fast(ttl=0.0)
        with mock.patch.object(
            chat_type_watch, "_label_unchanged", return_value=True
        ):
            self.run_fast(ttl=0.0)

        self.assertEqual(self.header_calls, [100, 100])

    def test_force_forces_reread(self):
        self.run_fast()
        with mock.patch.object(
            chat_type_watch, "_label_unchanged", return_value=True
        ):
            self.run_fast(force=True)

        self.assertEqual(self.header_calls, [100, 100])

    def test_other_chat_name_forces_reread(self):
        self.run_fast(chat_name="张三")
        with mock.patch.object(
            chat_type_watch, "_label_unchanged", return_value=True
        ):
            self.run_fast(chat_name="李四")

        self.assertEqual(self.header_calls, [100, 100])

    def test_header_unavailable_falls_back_to_full_scan(self):
        with mock.patch.object(
            chat_type_watch, "_resolve_hwnd", return_value=100
        ), mock.patch.object(
            chat_type_watch, "_read_header", return_value=None
        ), mock.patch.object(
            chat_type_watch, "_read_full", side_effect=self.fake_full
        ):
            result = chat_type_watch.get_chat_type_fast()

        self.assertEqual(result["type"], "private")
        self.assertEqual(self.full_calls, [100])

    def test_returned_dict_is_a_copy(self):
        first = self.run_fast()
        first["type"] = "group"
        with mock.patch.object(
            chat_type_watch, "_label_unchanged", return_value=True
        ):
            second = self.run_fast()

        self.assertEqual(second["type"], "private")


if __name__ == "__main__":
    unittest.main()
