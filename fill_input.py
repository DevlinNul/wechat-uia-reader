"""下游公开入口：把文本填入当前聊天输入框（不发送）。

实现位于 ``wechat_uia/api/fill_input.py``。保留这个文件是为了让
``from fill_input import fill_input`` 和 ``python fill_input.py`` 继续可用。
"""

import sys

from wechat_uia.api.fill_input import fill_input, main

__all__ = ["fill_input"]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
