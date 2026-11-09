"""下游公开入口：读取当前私聊联系人的标签。

实现位于 ``wechat_uia/api/read_private_tags.py``。保留这个文件是为了让
``from read_private_tags import read_private_tags`` 和
``python read_private_tags.py`` 可用。
"""

import sys

from wechat_uia.api.read_private_tags import main, read_private_tags

__all__ = ["read_private_tags"]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
