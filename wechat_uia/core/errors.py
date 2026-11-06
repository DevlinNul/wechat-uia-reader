"""项目统一异常族。

该模块只依赖标准库，可被任意层导入。窗口层与 UIA 层都从这里取异常类型，
避免 `WindowUnavailableError`、`CannotSendError` 分散定义。
"""


class WeChatUIAError(RuntimeError):
    """微信 UIA 读取相关错误的基类。"""


class WindowUnavailableError(WeChatUIAError):
    """微信主窗口不可用：未启动、已关闭、最小化、隐藏到托盘或位于屏幕外。"""


class UIAUnavailableError(WeChatUIAError):
    """微信 UI Automation 控件树不可用（未物化或无法读取）。"""


class NotAConversationError(WeChatUIAError):
    """当前视图不是会话（列表页、公众号聚合页等）。"""


class NotPrivateChatError(WeChatUIAError):
    """当前会话不是私聊。"""


class CannotSendError(WeChatUIAError):
    """当前会话不能发送消息，或找不到可用的输入框。"""


class ChatMismatchError(WeChatUIAError):
    """当前会话与调用方指定的会话不一致。"""


class InputFillError(WeChatUIAError):
    """无法把文本写入聊天输入框。"""


class InputNotEmptyError(WeChatUIAError):
    """聊天输入框已有内容，拒绝覆盖或追加。"""


class SendMessageError(WeChatUIAError):
    """无法确认消息已发送，或发送过程中出现异常。"""


class ClickTargetBlockedError(WeChatUIAError):
    """点击位置被其他窗口占用；为避免误操作，本次点击已取消。"""


class MessageListUnavailableError(WeChatUIAError):
    """无法定位当前会话的消息列表。"""
