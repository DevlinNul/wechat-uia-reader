"""微信 4.x UIA 读取与发送。

对下游暴露三个原子操作：

- :func:`read_private_chat`
- :func:`fill_input`
- :func:`send_message`

根目录的同名文件是薄包装，保留原来的导入方式和命令行用法。
"""

from importlib import import_module

__all__ = ["read_private_chat", "fill_input", "send_message"]


def __getattr__(name: str):
    """按需加载 API，避免 ``python -m`` 运行时提前导入子模块。"""
    if name in __all__:
        module = import_module(f".api.{name}", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
