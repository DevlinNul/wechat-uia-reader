"""文本规范化：只做纯字符串处理，不涉及窗口或输入法。"""


def normalize_text(text: str) -> str:
    """统一换行符；微信输入框内部按 \\n 处理。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")
