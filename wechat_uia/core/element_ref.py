"""UIA 元素引用的缓存与失效判断。

快速路径会保存上一次用到的控件引用，下次先用 RuntimeId 和文本确认它还
是同一个控件，再决定要不要重新读取。这里只处理引用本身，不导入
uiautomation；元素可以是 uiautomation 的 ``Control``，也可以是原始 COM
元素。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ElementRef:
    """一个控件在某一时刻的引用、RuntimeId 和名称。"""

    element: object
    runtime_id: tuple | None
    name: str


def _name_of(element) -> str | None:
    """读取元素名称：原始 COM 元素用 CurrentName，Control 用 Name。"""
    try:
        return element.CurrentName
    except Exception:
        pass
    try:
        return element.Name
    except Exception:
        return None


def capture(element) -> ElementRef | None:
    """记录元素当前的 RuntimeId 和名称；元素为空时返回 None。"""
    if not element:
        return None
    try:
        runtime_id = tuple(element.GetRuntimeId())
    except Exception:
        runtime_id = None
    return ElementRef(
        element=element,
        runtime_id=runtime_id,
        name=(_name_of(element) or "").strip(),
    )


def still_valid(ref: ElementRef | None) -> bool:
    """元素仍然存在，且 RuntimeId 与记录时一致。"""
    if ref is None or not ref.element:
        return False
    try:
        return tuple(ref.element.GetRuntimeId()) == ref.runtime_id
    except Exception:
        return False


def current_name(ref: ElementRef | None) -> str | None:
    """读取元素当前名称；引用为空或读取失败时返回 None。"""
    if ref is None or not ref.element:
        return None
    return _name_of(ref.element)


def unchanged(ref: ElementRef | None, name: str) -> bool:
    """元素仍有效，且名称与给定值一致。"""
    if not still_valid(ref):
        return False
    current = current_name(ref)
    if current is None:
        return False
    return current.strip() == (name or "").strip()
