"""下游公开入口：向当前聊天发送一条文本消息。

实现位于 ``wechat_uia/api/send_message.py``。保留这个文件是为了让
``from send_message import send_message`` 和
``python send_message.py`` 继续可用。
"""

import sys

from wechat_uia.api.send_message import main, send_message

__all__ = ["send_message"]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
