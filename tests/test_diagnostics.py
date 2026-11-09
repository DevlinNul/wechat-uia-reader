"""内部诊断模块的独立窗口参数与结果字段测试。"""

import importlib
import unittest
from types import SimpleNamespace
from unittest import mock

DIAGNOSTIC_MODULES = (
    "wechat_uia.core.get_chat",
    "wechat_uia.core.get_chat_type",
    "wechat_uia.core.get_input",
    "wechat_uia.core.get_messages",
)


class DiagnosticParserTest(unittest.TestCase):
    def test_all_diagnostics_expose_window_arguments(self):
        for name in DIAGNOSTIC_MODULES:
            module = importlib.import_module(name)
            parser = module.build_parser()
            actions = parser._option_string_actions
            for option in ("--hwnd", "--chat"):
                self.assertIn(
                    option,
                    actions,
                    f"{name} 的命令行缺少 {option}",
                )


class DiagnosticWindowFieldsTest(unittest.TestCase):
    def test_chat_type_reports_detached_window(self):
        module = importlib.import_module("wechat_uia.core.get_chat_type")
        snapshot = SimpleNamespace(
            hwnd=123,
            window_state="normal",
            window_kind="chat",
            window_automation_id="ChatSingleWindowa",
            layout="single",
        )
        resolved = SimpleNamespace(hwnd=123, role="chat")
        with (
            mock.patch.object(
                module, "resolve_surface", return_value=resolved
            ) as resolve,
            mock.patch.object(module, "scan_window", return_value=snapshot),
            mock.patch.object(
                module,
                "classify_snapshot",
                return_value={
                    "type": "private",
                    "label": "私聊",
                    "chat_name": "会话 A",
                    "member_count": None,
                    "can_send": True,
                    "reason": "测试",
                },
            ),
        ):
            result = module.get_current_chat_type(chat_name="会话 A")

        resolve.assert_called_once_with(hwnd=None, chat_name="会话 A")
        self.assertEqual(result["window_role"], "chat")
        self.assertEqual(
            result["window_automation_id"], "ChatSingleWindowa"
        )
        self.assertEqual(result["layout"], "single")

    def test_messages_report_detached_window(self):
        module = importlib.import_module("wechat_uia.core.get_messages")
        snapshot = SimpleNamespace(
            hwnd=123,
            message_list=SimpleNamespace(GetChildren=lambda: []),
            window_kind="chat",
            window_automation_id="ChatSingleWindowa",
            layout="single",
        )
        result = module.messages_from_snapshot(snapshot)
        self.assertEqual(result["window_role"], "chat")
        self.assertEqual(
            result["window_automation_id"], "ChatSingleWindowa"
        )
        self.assertEqual(result["layout"], "single")


if __name__ == "__main__":
    unittest.main()
