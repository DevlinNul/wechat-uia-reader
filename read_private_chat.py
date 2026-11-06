"""下游公开入口：读取当前私聊。

实现位于 ``wechat_uia/api/read_private_chat.py``。保留这个文件是为了让
``from read_private_chat import read_private_chat`` 和
``python read_private_chat.py`` 继续可用。
"""

import sys

from wechat_uia.api.read_private_chat import main, read_private_chat

__all__ = ["read_private_chat"]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
