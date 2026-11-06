"""会话类型判定与坐标工具的纯逻辑测试（不需要真实微信窗口）。"""

import unittest

from wechat_uia.core.get_chat_type import (
    BROADCAST,
    FEED,
    GROUP,
    LIST,
    NOTICE,
    OFFICIAL,
    PRIVATE,
    UNKNOWN,
    classify_snapshot,
)
from wechat_uia.core.snapshot import ChatSnapshot
from wechat_uia.core.uia_utils import rect_xywh


class FakeControl:
    def __init__(
        self,
        name: str = "",
        class_name: str = "",
        automation_id: str = "",
        control_type: str = "",
        children=None,
    ):
        self.Name = name
        self.ClassName = class_name
        self.AutomationId = automation_id
        self.ControlTypeName = control_type
        self._children = list(children or [])

    def GetChildren(self):
        return list(self._children)


class FakeRect:
    def __init__(self, left: int, top: int, right: int, bottom: int):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


def make_snapshot(
    chat_name=None,
    chat_name_element=None,
    title_bar=None,
    has_input=False,
    list_title=None,
    is_brand_page=False,
):
    return ChatSnapshot(
        hwnd=1,
        window_state="normal",
        chat_name=chat_name,
        chat_name_element=chat_name_element,
        title_bar=title_bar,
        chat_detail=None,
        session_list=None,
        message_list=None,
        input_field=None,
        has_input=has_input,
        is_brand_page=is_brand_page,
        list_title=list_title,
        session_list_name="",
    )


def named_control(name: str, automation_id: str = "") -> FakeControl:
    return FakeControl(
        name=name,
        automation_id=automation_id,
        control_type="TextControl",
    )


class ClassifySnapshotTest(unittest.TestCase):
    def test_private_chat_named_like_member_count(self):
        label = named_control("(1)", "chat_current_chat_name_label")
        title_bar = FakeControl(
            class_name="mmui::ChatTitleBarMasterView",
            children=[label, FakeControl(class_name="mmui::ChatSingleView")],
        )
        snapshot = make_snapshot(
            chat_name="(1)",
            chat_name_element=label,
            title_bar=title_bar,
            has_input=True,
        )

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], PRIVATE)
        self.assertIsNone(result["member_count"])
        self.assertTrue(result["can_send"])

    def test_group_chat_uses_separate_member_count_control(self):
        label = named_control("项目群", "chat_current_chat_name_label")
        count = named_control("(26)")
        title_bar = FakeControl(
            class_name="mmui::ChatTitleBarMasterView",
            children=[label, count],
        )
        snapshot = make_snapshot(
            chat_name="项目群",
            chat_name_element=label,
            title_bar=title_bar,
            has_input=True,
        )

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], GROUP)
        self.assertEqual(result["member_count"], 26)

    def test_list_page(self):
        snapshot = make_snapshot(list_title="聊天")

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], LIST)
        self.assertFalse(result["can_send"])

    def test_unknown_page(self):
        result = classify_snapshot(make_snapshot())

        self.assertEqual(result["type"], UNKNOWN)
        self.assertFalse(result["can_send"])

    def test_brand_page_is_feed(self):
        label = named_control("某公众号", "chat_current_chat_name_label")
        snapshot = make_snapshot(
            chat_name="某公众号",
            chat_name_element=label,
            is_brand_page=True,
            has_input=False,
        )

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], FEED)
        self.assertFalse(result["can_send"])

    def test_single_view_without_input_is_notice(self):
        label = named_control("服务通知", "chat_current_chat_name_label")
        title_bar = FakeControl(class_name="mmui::ChatSingleView")
        snapshot = make_snapshot(
            chat_name="服务通知",
            chat_name_element=label,
            title_bar=title_bar,
            has_input=False,
        )

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], NOTICE)
        self.assertFalse(result["can_send"])

    def test_brand_view_with_input_is_official(self):
        label = named_control("某服务号", "chat_current_chat_name_label")
        title_bar = FakeControl(class_name="mmui::ChatBrandView")
        snapshot = make_snapshot(
            chat_name="某服务号",
            chat_name_element=label,
            title_bar=title_bar,
            has_input=True,
        )

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], OFFICIAL)
        self.assertTrue(result["can_send"])

    def test_brand_view_without_input_is_broadcast(self):
        label = named_control("某公众号", "chat_current_chat_name_label")
        title_bar = FakeControl(class_name="mmui::ChatBrandView")
        snapshot = make_snapshot(
            chat_name="某公众号",
            chat_name_element=label,
            title_bar=title_bar,
            has_input=False,
        )

        result = classify_snapshot(snapshot)

        self.assertEqual(result["type"], BROADCAST)
        self.assertFalse(result["can_send"])


class RectTest(unittest.TestCase):
    def test_rect_is_unified_to_xywh(self):
        self.assertEqual(rect_xywh(FakeRect(10, 20, 40, 60)), (10, 20, 30, 40))


if __name__ == "__main__":
    unittest.main()
