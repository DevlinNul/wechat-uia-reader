"""固化模块分层规则，防止再次出现叶子模块依赖业务模块。"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FEATURE_MODULES = {
    "get_chat.py",
    "get_chat_type.py",
    "get_input.py",
    "get_messages.py",
}
ALL_MODULES = FEATURE_MODULES | {
    "errors.py",
    "get_hwnd.py",
    "uia_utils.py",
    "snapshot.py",
}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


class LayeringTest(unittest.TestCase):
    def test_no_feature_module_imports_another_except_input_to_type(self):
        allowed = {("get_input.py", "get_chat_type.py")}
        for name in FEATURE_MODULES:
            imported = imports_of(ROOT / name)
            for other in FEATURE_MODULES:
                other_module = other.removesuffix(".py")
                if other == name or other_module not in imported:
                    continue
                self.assertIn(
                    (name, other),
                    allowed,
                    f"{name} 不应依赖 {other}",
                )

    def test_get_messages_does_not_depend_on_chat_readers(self):
        imported = imports_of(ROOT / "get_messages.py")
        self.assertFalse(
            imported & {"get_chat", "get_chat_type", "get_input"},
            "get_messages.py 只应依赖 snapshot / uia_utils / errors / get_hwnd",
        )

    def test_only_snapshot_imports_uiautomation(self):
        for name in ALL_MODULES - {"snapshot.py"}:
            self.assertNotIn(
                "uiautomation",
                imports_of(ROOT / name),
                f"{name} 不应直接导入 uiautomation，请使用 snapshot",
            )

    def test_window_layer_does_not_depend_on_uia(self):
        imported = imports_of(ROOT / "get_hwnd.py")
        self.assertFalse(
            imported & {"uiautomation", "snapshot", "get_chat", "get_chat_type"},
            "get_hwnd.py 应保持纯 win32 窗口层",
        )


if __name__ == "__main__":
    unittest.main()
