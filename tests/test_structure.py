"""固化目录与分层规则：根目录只放三个公开入口，内部实现放进包。"""

import ast
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "wechat_uia"

# 包内每个文件允许依赖的项目内模块（写完整模块名）。
ALLOWED_PACKAGE_IMPORTS = {
    "cli_common.py": set(),
    "core/errors.py": set(),
    "core/get_hwnd.py": {"wechat_uia.core.errors"},
    "core/uia_utils.py": set(),
    "core/snapshot.py": {
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.uia_utils",
    },
    "core/classify.py": {"wechat_uia.core.uia_utils"},
    "core/session.py": {
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.snapshot",
    },
    "core/surface.py": {
        "wechat_uia.core.errors",
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.uia_utils",
    },
    "core/ui_actions.py": {
        "wechat_uia.core.errors",
        "wechat_uia.core.get_hwnd",
    },
    "core/input_text.py": set(),
    "core/input.py": {
        "wechat_uia.core.errors",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.ui_actions",
    },
    "core/fill.py": {
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.input",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
    },
    "core/send.py": {
        "wechat_uia.core.errors",
        "wechat_uia.core.input",
        "wechat_uia.core.ui_actions",
        "wechat_uia.core.uia_utils",
    },
    "core/get_chat.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.errors",
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "core/get_chat_type.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "core/get_input.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "core/get_messages.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.errors",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "core/read_private_tags.py": {
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "core/direction.py": {
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.uia_utils",
    },
    "core/message_history.py": {
        "wechat_uia.core.direction",
        "wechat_uia.core.errors",
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.get_messages",
    },
    "core/list_surfaces.py": {
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "api/read_private_chat.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.get_chat",
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.message_history",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.uia_utils",
    },
    "api/fill_input.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.errors",
        "wechat_uia.core.fill",
        "wechat_uia.core.input",
        "wechat_uia.core.input_text",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.ui_actions",
        "wechat_uia.core.uia_utils",
    },
    "api/send_message.py": {
        "wechat_uia.cli_common",
        "wechat_uia.core.classify",
        "wechat_uia.core.errors",
        "wechat_uia.core.fill",
        "wechat_uia.core.get_hwnd",
        "wechat_uia.core.input",
        "wechat_uia.core.input_text",
        "wechat_uia.core.send",
        "wechat_uia.core.session",
        "wechat_uia.core.snapshot",
        "wechat_uia.core.surface",
        "wechat_uia.core.ui_actions",
        "wechat_uia.core.uia_utils",
    },
    "api/__init__.py": set(),
    "__init__.py": {
        "wechat_uia.api.fill_input",
        "wechat_uia.api.read_private_chat",
        "wechat_uia.api.send_message",
    },
    "core/__init__.py": set(),
}

PUBLIC_WRAPPERS = {
    "read_private_chat.py": {"wechat_uia.api.read_private_chat"},
    "fill_input.py": {"wechat_uia.api.fill_input"},
    "send_message.py": {"wechat_uia.api.send_message"},
}

PUBLIC_NAMES = ("read_private_chat", "fill_input", "send_message")


def package_of(path: Path) -> str:
    """返回文件所在包的完整名字；根目录文件返回空字符串。"""
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return ".".join(parts)


def imports_of(path: Path) -> set[str]:
    """提取文件中导入的项目内模块，支持相对导入。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    base_package = package_of(path)
    base_parts = base_package.split(".") if base_package else []
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                trimmed = base_parts[
                    : max(0, len(base_parts) - (node.level - 1))
                ]
                module = ".".join(
                    trimmed + ([node.module] if node.module else [])
                )
            else:
                module = node.module or ""
            if module.startswith("wechat_uia"):
                found.add(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("wechat_uia"):
                    found.add(alias.name)
    return found


def imports_uiautomation(path: Path) -> bool:
    """文件是否真的 import 了 uiautomation（不看说明文字）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".")[0] == "uiautomation"
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "uiautomation":
                return True
    return False


class LayeringTest(unittest.TestCase):
    def test_no_duplicate_python_files(self):
        """同一个文件不能被复制成两份；实现只保留一份。"""
        files = [
            path
            for path in ROOT.rglob("*.py")
            if "__pycache__" not in path.parts
        ]
        by_hash: dict[str, list[Path]] = {}
        for path in files:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            by_hash.setdefault(digest, []).append(path)

        duplicates = [
            [str(path.relative_to(ROOT)) for path in paths]
            for paths in by_hash.values()
            if len(paths) > 1
        ]
        self.assertEqual(duplicates, [], f"发现重复文件：{duplicates}")

    def test_public_wrappers_do_not_copy_implementation(self):
        """根目录三个入口只能是薄包装，不能复制实现。"""
        for name in PUBLIC_NAMES:
            source = (ROOT / f"{name}.py").read_text(encoding="utf-8")
            self.assertNotIn(
                f"def {name}(",
                source,
                f"{name}.py 不应复制实现，只应导入 wechat_uia.api",
            )
            self.assertNotIn("uiautomation", source)
            self.assertNotIn("win32", source)
            self.assertLessEqual(
                len(source.splitlines()),
                30,
                f"{name}.py 应保持为薄包装",
            )

    def test_package_imports_match_allowed_layers(self):
        for name, allowed in ALLOWED_PACKAGE_IMPORTS.items():
            path = PACKAGE / name
            self.assertTrue(path.exists(), f"缺少文件：{path}")
            imported = imports_of(path)
            self.assertLessEqual(
                imported,
                allowed,
                f"{name} 引入了未授权的项目内依赖：{imported - allowed}",
            )

    def test_public_wrappers_only_depend_on_their_api_module(self):
        for name, allowed in PUBLIC_WRAPPERS.items():
            path = ROOT / name
            self.assertTrue(path.exists(), f"缺少公开入口：{path}")
            imported = imports_of(path)
            self.assertLessEqual(
                imported,
                allowed,
                f"{name} 不应依赖内部实现：{imported - allowed}",
            )

    def test_root_has_only_public_entries_and_no_internal_modules(self):
        for name in PUBLIC_NAMES:
            self.assertTrue(
                (ROOT / f"{name}.py").exists(),
                f"根目录缺少公开入口：{name}.py",
            )

        internal = (
            "errors.py",
            "get_hwnd.py",
            "uia_utils.py",
            "snapshot.py",
            "get_chat.py",
            "get_chat_type.py",
            "get_input.py",
            "get_messages.py",
            "message_history.py",
        )
        for name in internal:
            self.assertFalse(
                (ROOT / name).exists(),
                f"{name} 应放在 wechat_uia/core 下",
            )
            self.assertTrue(
                (PACKAGE / "core" / name).exists(),
                f"wechat_uia/core 缺少 {name}",
            )

        self.assertTrue(
            (PACKAGE / "api" / "read_private_chat.py").exists()
        )
        self.assertTrue((PACKAGE / "api" / "fill_input.py").exists())
        self.assertTrue((PACKAGE / "api" / "send_message.py").exists())
        self.assertTrue(
            (PACKAGE / "core" / "read_private_tags.py").exists()
        )

    def test_get_messages_does_not_depend_on_chat_readers(self):
        imported = imports_of(PACKAGE / "core" / "get_messages.py")
        self.assertFalse(
            imported
            & {
                "wechat_uia.core.get_chat",
                "wechat_uia.core.get_chat_type",
                "wechat_uia.core.get_input",
                "wechat_uia.core.message_history",
            },
            "get_messages.py 只应依赖 snapshot / uia_utils / errors",
        )

    def test_only_snapshot_imports_uiautomation(self):
        for path in PACKAGE.rglob("*.py"):
            if path == PACKAGE / "core" / "snapshot.py":
                continue
            self.assertFalse(
                imports_uiautomation(path),
                f"{path} 不应直接导入 uiautomation，请使用 snapshot",
            )

    def test_window_layer_does_not_depend_on_uia(self):
        imported = imports_of(PACKAGE / "core" / "get_hwnd.py")
        self.assertFalse(
            imported
            & {
                "wechat_uia.core.snapshot",
                "wechat_uia.core.get_chat",
                "wechat_uia.core.get_chat_type",
            },
            "get_hwnd.py 应保持纯 win32 窗口层",
        )
        self.assertFalse(
            imports_uiautomation(PACKAGE / "core" / "get_hwnd.py"),
            "get_hwnd.py 不应导入 uiautomation",
        )


if __name__ == "__main__":
    unittest.main()
