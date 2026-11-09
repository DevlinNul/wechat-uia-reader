"""四个公开入口的窗口唤醒参数约定。"""

import importlib
import inspect
import unittest

PUBLIC_MODULES = (
    "wechat_uia.api.read_private_chat",
    "wechat_uia.api.read_private_tags",
    "wechat_uia.api.fill_input",
    "wechat_uia.api.send_message",
)


class WakeParameterTest(unittest.TestCase):
    def test_all_public_functions_expose_both_wake_flags(self):
        for module_name in PUBLIC_MODULES:
            module = importlib.import_module(module_name)
            function_name = module_name.rsplit(".", 1)[-1]
            function = getattr(module, function_name)
            parameters = inspect.signature(function).parameters

            for name in ("wake_minimized", "wake_tray"):
                self.assertIn(
                    name,
                    parameters,
                    f"{module_name}.{function_name} 缺少 {name}",
                )
                parameter = parameters[name]
                self.assertEqual(
                    parameter.kind,
                    inspect.Parameter.KEYWORD_ONLY,
                    f"{name} 必须是仅关键字参数",
                )
                self.assertIs(
                    parameter.default,
                    False,
                    f"{name} 默认值必须是 False",
                )

    def test_all_public_clis_expose_both_wake_flags(self):
        for module_name in PUBLIC_MODULES:
            module = importlib.import_module(module_name)
            parser = module.build_parser()
            actions = parser._option_string_actions

            for option in ("--wake-minimized", "--wake-tray"):
                self.assertIn(
                    option,
                    actions,
                    f"{module_name} 的命令行缺少 {option}",
                )
                self.assertIs(
                    actions[option].default,
                    False,
                    f"{option} 默认值必须是 False",
                )


class DirectionSwitchTest(unittest.TestCase):
    def test_read_private_chat_cli_exposes_direction(self):
        module = importlib.import_module(
            "wechat_uia.api.read_private_chat"
        )
        parser = module.build_parser()
        self.assertIn("--direction", parser._option_string_actions)


if __name__ == "__main__":
    unittest.main()
