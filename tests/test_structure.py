"""固化模块分层规则，防止再次出现叶子模块依赖业务模块。"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 每个模块允许依赖的项目内模块。新增依赖时必须先更新这里。
ALLOWED_IMPORTS = {
    "errors.py": set(),
    "get_hwnd.py": {"errors"},
    "uia_utils.py": set(),
    "snapshot.py": {"get_hwnd", "uia_utils"},
    "get_chat.py": {"errors", "get_hwnd", "snapshot", "uia_utils"},
    "get_chat_type.py": {"errors", "snapshot", "uia_utils"},
    "get_input.py": {"errors", "get_chat_type", "snapshot", "uia_utils"},
    "get_messages.py": {"errors", "snapshot", "uia_utils"},
    "fill_input.py": {
        "errors",
        "get_chat_type",
        "get_hwnd",
        "snapshot",
        "uia_utils",
    },
    "send_message.py": {
        "errors",
        "fill_input",
        "get_chat_type",
        "get_hwnd",
        "snapshot",
        "uia_utils",
    },
    "message_history.py": {"errors", "get_messages"},
    "read_private_chat.py": {
        "errors",
        "get_chat",
        "get_chat_type",
        "message_history",
        "snapshot",
        "uia_utils",
    },
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
    def test_project_imports_match_allowed_layers(self):
        module_names = {Path(name).stem for name in ALLOWED_IMPORTS}
        for name, allowed in ALLOWED_IMPORTS.items():
            imported = imports_of(ROOT / name)
            project_imports = imported & module_names
            self.assertLessEqual(
                project_imports,
                allowed,
                f"{name} 引入了未授权的项目内依赖：{project_imports - allowed}",
            )

    def test_get_messages_does_not_depend_on_chat_readers(self):
        imported = imports_of(ROOT / "get_messages.py")
        self.assertFalse(
            imported & {"get_chat", "get_chat_type", "get_input", "message_history"},
            "get_messages.py 只应依赖 snapshot / uia_utils / errors",
        )

    def test_only_snapshot_imports_uiautomation(self):
        for name in ALLOWED_IMPORTS:
            if name == "snapshot.py":
                continue
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
