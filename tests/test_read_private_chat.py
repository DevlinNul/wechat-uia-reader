"""read_private_chat 输出会话身份，供 fill_input 原子配对。"""

import unittest
from types import SimpleNamespace
from unittest import mock

import importlib

module = importlib.import_module("wechat_uia.api.read_private_chat")
from wechat_uia.core.get_chat_type import PRIVATE


class ReadPrivateChatSessionTest(unittest.TestCase):
    def test_returns_session_identity(self):
        snapshot = SimpleNamespace(
            hwnd=123,
            window_state="normal",
            window_kind="main",
            chat_name="会话 A",
            chat_name_element=SimpleNamespace(Name="会话 A"),
            uia_available=True,
        )
        session = {
            "chat_name": "会话 A",
            "type": "private",
            "label": "私聊",
            "member_count": None,
            "can_send": True,
            "selected_session_name": "会话 A",
            "session_automation_id": "session_item_会话 A",
            "session_runtime_id": (1, 2, 3),
        }
        history = {
            "count": 0,
            "messages": [],
            "scroll": {"warnings": []},
        }

        resolved = SimpleNamespace(hwnd=123, role="main")
        with (
            mock.patch.object(
                module, "resolve_surface", return_value=resolved
            ),
            mock.patch.object(module, "require_window_ready"),
            mock.patch.object(
                module, "scan_window", return_value=snapshot
            ) as scan,
            mock.patch.object(
                module,
                "classify_snapshot",
                return_value={
                    "type": PRIVATE,
                    "label": "私聊",
                    "chat_name": "会话 A",
                    "member_count": None,
                    "can_send": True,
                },
            ),
            mock.patch.object(
                module,
                "chat_info_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(
                module, "session_identity_from_snapshot", return_value=session
            ),
            mock.patch.object(
                module, "collect_messages", return_value=history
            ),
        ):
            result = module.read_private_chat(scroll=False)

        scan.assert_called_once_with(123)
        self.assertEqual(result["session"], session)
        self.assertEqual(result["chat_name"], "会话 A")
        self.assertEqual(result["window_kind"], "main")

    def test_direction_flag_is_forwarded(self):
        snapshot = SimpleNamespace(
            hwnd=123,
            window_state="normal",
            window_kind="main",
            chat_name="会话 A",
            chat_name_element=SimpleNamespace(Name="会话 A"),
            uia_available=True,
        )
        history = {
            "count": 1,
            "messages": [
                {
                    "index": 0,
                    "kind": "text",
                    "text": "你好",
                    "direction": "self",
                    "direction_source": "screenshot",
                }
            ],
            "scroll": {"warnings": []},
        }
        resolved = SimpleNamespace(hwnd=123, role="main")
        with (
            mock.patch.object(
                module, "resolve_surface", return_value=resolved
            ),
            mock.patch.object(module, "require_window_ready"),
            mock.patch.object(
                module, "scan_window", return_value=snapshot
            ),
            mock.patch.object(
                module,
                "classify_snapshot",
                return_value={
                    "type": PRIVATE,
                    "label": "私聊",
                    "chat_name": "会话 A",
                    "member_count": None,
                    "can_send": True,
                },
            ),
            mock.patch.object(
                module,
                "chat_info_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(
                module,
                "session_identity_from_snapshot",
                return_value={"chat_name": "会话 A"},
            ),
            mock.patch.object(
                module, "collect_messages", return_value=history
            ) as collect,
        ):
            result = module.read_private_chat(
                scroll=False, with_direction=True
            )

        self.assertTrue(result["direction_enabled"])
        self.assertEqual(
            result["messages"][0]["direction"], "self"
        )
        self.assertTrue(collect.call_args.kwargs["with_direction"])


if __name__ == "__main__":
    unittest.main()
