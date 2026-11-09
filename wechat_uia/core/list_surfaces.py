"""诊断：列出当前微信会话窗口。

用法::

    python -m wechat_uia.core.list_surfaces
    python -m wechat_uia.core.list_surfaces --json
"""

import json
import sys

from .surface import list_surfaces
from .uia_utils import enable_dpi_awareness


def main() -> int:
    enable_dpi_awareness()
    surfaces = list_surfaces()
    if "--json" in sys.argv:
        print(
            json.dumps(
                [surface.__dict__ for surface in surfaces],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not surfaces:
        print("未找到微信会话窗口，请确认微信已启动。")
        return 1

    for surface in surfaces:
        print(
            f"[{surface.role}] hwnd=0x{surface.hwnd:X} ({surface.hwnd}) "
            f"state={surface.window_state}"
        )
        print(f"    标题    : {surface.title!r}")
        print(f"    会话    : {surface.chat_name!r}")
        print(f"    窗口 id : {surface.window_automation_id!r}")
        print("-" * 50)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
