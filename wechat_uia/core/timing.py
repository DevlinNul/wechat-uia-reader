"""轮询间隔、超时和条件等待的唯一来源。

延迟优化时先改这里，不要在业务模块里散落固定 sleep。每个常量都可以被
调用方覆盖，模块里的默认值只代表“常规场景”。
"""

import time

# 激活窗口时的等待上限和轮询间隔：切到前台通常几十毫秒内完成，
# 不要用固定 sleep 等满上限。
ACTIVATE_SETTLE = 0.2
ACTIVATE_POLL_INTERVAL = 0.01

# 焦点点击后的等待上限。
FOCUS_CLICK_SETTLE = 0.15

# 清空输入框、键盘输入后的等待上限。
KEY_SETTLE = 0.05
CLEAR_SETTLE = 0.1

# 通用轮询间隔。发送后等输入框清空是热路径上的固定成本，间隔要小；
# 每次读取本身只有亚毫秒级。
POLL_INTERVAL = 0.01
FILL_VERIFY_INTERVAL = 0.03

# 各类等待的超时。
FOCUS_TIMEOUT = 1.0
FILL_VERIFY_TIMEOUT = 0.6
SEND_CONFIRM_TIMEOUT = 2.0
MESSAGE_CONFIRM_TIMEOUT = 3.0


def wait_until(
    predicate,
    timeout: float,
    interval: float = POLL_INTERVAL,
) -> bool:
    """轮询 predicate 直到为真或超时。

    先检查一次再等待，避免 timeout 很短时白白睡一轮。predicate 抛异常
    视为“还没满足”，由调用方决定是否要自己捕获。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            if predicate():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
