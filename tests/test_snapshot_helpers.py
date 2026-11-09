"""UIA 会话选中态与 ValuePattern 的纯逻辑测试。"""

import unittest
from types import SimpleNamespace
from unittest import mock

from wechat_uia.core import get_chat_type, session, snapshot as snapshot_module
from wechat_uia.core.get_chat import list_sessions
from wechat_uia.core.snapshot import (
    has_chat_info_panel,
    read_profile_contact,
    read_profile_fields,
    read_value_pattern,
    selected_session_identity,
    selected_session_name,
    session_name_is_unique,
)


class FakeRect:
    left = 0
    top = 0
    right = 10
    bottom = 10


class FakePattern:
    def __init__(self, selected=None, value=None):
        self.IsSelected = selected
        self.Value = value


class FakeItem:
    ControlTypeName = "ListItemControl"

    def __init__(
        self,
        name,
        selected=None,
        value=None,
        supported=True,
        automation_id="",
        runtime_id=(1, 2, 3),
    ):
        self.Name = name
        self.AutomationId = automation_id
        self.BoundingRectangle = FakeRect()
        self._runtime_id = runtime_id
        self._pattern = (
            FakePattern(selected=selected, value=value) if supported else None
        )

    def GetPattern(self, pattern_id):
        return self._pattern

    def GetRuntimeId(self):
        return self._runtime_id

    def GetChildren(self):
        return []


class FakeList:
    def __init__(self, items):
        self._items = items

    def GetChildren(self):
        return list(self._items)


class ProfileRect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeProfileControl:
    def __init__(self, name="", class_name="", rect=None, children=()):
        self.Name = name
        self.ClassName = class_name
        self.BoundingRectangle = rect or ProfileRect(0, 0, 100, 40)
        self._children = list(children)

    def GetChildren(self):
        return list(self._children)


class SelectedSessionTest(unittest.TestCase):
    def test_returns_selected_item_name(self):
        session_list = FakeList(
            [
                FakeItem("会话 A\n预览", selected=False),
                FakeItem("会话 B\n预览", selected=True),
            ]
        )

        self.assertEqual(selected_session_name(session_list), "会话 B")

    def test_returns_none_when_nothing_selected(self):
        session_list = FakeList([FakeItem("会话 A", selected=False)])

        self.assertIsNone(selected_session_name(session_list))

    def test_returns_none_when_pattern_unsupported(self):
        session_list = FakeList([FakeItem("会话 A", supported=False)])

        self.assertIsNone(selected_session_name(session_list))

    def test_returns_none_without_session_list(self):
        self.assertIsNone(selected_session_name(None))


class SelectedSessionIdentityTest(unittest.TestCase):
    def test_returns_name_automation_and_runtime_id(self):
        session_list = FakeList(
            [
                FakeItem(
                    "会话 B\n预览",
                    selected=True,
                    automation_id="session_item_会话 B",
                    runtime_id=(42, 1, 2, -3),
                )
            ]
        )

        identity = selected_session_identity(session_list)

        self.assertEqual(identity["name"], "会话 B")
        self.assertEqual(identity["automation_id"], "session_item_会话 B")
        self.assertEqual(identity["runtime_id"], (42, 1, 2, -3))

    def test_returns_none_without_selection(self):
        self.assertIsNone(
            selected_session_identity(FakeList([FakeItem("会话 A")]))
        )


class SessionIdentityFromSnapshotTest(unittest.TestCase):
    def test_builds_identity_from_snapshot(self):
        snapshot = SimpleNamespace(
            chat_name="会话 A",
            session_list=object(),
        )
        selected = {
            "name": "会话 A",
            "automation_id": "session_item_会话 A",
            "runtime_id": (1, 2, 3),
        }

        with (
            mock.patch.object(
                session,
                "classify_snapshot",
                return_value={
                    "chat_name": "会话 A",
                    "type": "group",
                    "label": "群聊",
                    "member_count": 26,
                    "can_send": True,
                    "reason": "测试",
                },
            ),
            mock.patch.object(
                session,
                "selected_session_identity",
                return_value=selected,
            ),
        ):
            identity = get_chat_type.session_identity_from_snapshot(snapshot)

        self.assertEqual(identity["chat_name"], "会话 A")
        self.assertEqual(identity["type"], "group")
        self.assertEqual(identity["member_count"], 26)
        self.assertEqual(identity["selected_session_name"], "会话 A")
        self.assertEqual(
            identity["session_automation_id"], "session_item_会话 A"
        )
        self.assertEqual(identity["session_runtime_id"], (1, 2, 3))


class SessionNameUniqueTest(unittest.TestCase):
    def test_unique_name(self):
        session_list = FakeList(
            [FakeItem("会话 A\n预览 A"), FakeItem("会话 B\n预览 B")]
        )

        self.assertTrue(session_name_is_unique(session_list, "会话 A"))
        self.assertFalse(session_name_is_unique(session_list, "会话 C"))

    def test_duplicate_name(self):
        session_list = FakeList(
            [FakeItem("会话 A\n预览 1"), FakeItem("会话 A\n预览 2")]
        )

        self.assertFalse(session_name_is_unique(session_list, "会话 A"))

    def test_none_session_list(self):
        self.assertIsNone(session_name_is_unique(None, "会话 A"))


class FakePanelControl:
    ControlTypeName = "TextControl"
    ClassName = "mmui::XTextView"

    def __init__(self, name="", automation_id="", children=()):
        self.Name = name
        self.AutomationId = automation_id
        self._children = list(children)

    def GetRuntimeId(self):
        return (1, id(self))

    def GetChildren(self):
        return list(self._children)


class ChatInfoPanelProbeTest(unittest.TestCase):
    def _probe(self, root):
        with mock.patch.object(
            snapshot_module, "get_root_control", return_value=root
        ):
            return has_chat_info_panel(1, timeout=0.1)

    def test_detects_panel_text(self):
        root = FakePanelControl(
            children=[FakePanelControl(name="查找聊天内容")]
        )
        self.assertTrue(self._probe(root))

    def test_detects_member_cell(self):
        root = FakePanelControl(
            children=[FakePanelControl(automation_id="single_chat_member_cell")]
        )
        self.assertTrue(self._probe(root))

    def test_returns_false_without_panel(self):
        root = FakePanelControl(children=[FakePanelControl(name="其他内容")])
        self.assertFalse(self._probe(root))


class ValuePatternTest(unittest.TestCase):
    def test_reads_value(self):
        self.assertEqual(read_value_pattern(FakeItem("x", value="你好")), "你好")

    def test_returns_none_when_unsupported(self):
        self.assertIsNone(read_value_pattern(FakeItem("x", supported=False)))


class ListSessionsTest(unittest.TestCase):
    def test_marks_selected_session(self):
        session_list = FakeList(
            [
                FakeItem("会话 A\n预览 A"),
                FakeItem("会话 B\n预览 B"),
            ]
        )

        sessions = list_sessions(session_list, selected_name="会话 B")

        self.assertEqual([s["name"] for s in sessions], ["会话 A", "会话 B"])
        self.assertEqual([s["selected"] for s in sessions], [False, True])
        self.assertEqual(sessions[1]["detail"], ["预览 B"])


class ProfileFieldsTest(unittest.TestCase):
    def test_reads_tag_row_before_next_label(self):
        tag_label = FakeProfileControl(
            "标签",
            "mmui::XTextView",
            ProfileRect(100, 200, 200, 240),
        )
        tag_value = FakeProfileControl(
            "大学同学",
            "mmui::ProfileTextView",
            ProfileRect(220, 200, 340, 240),
        )
        moments_label = FakeProfileControl(
            "朋友圈",
            "mmui::XTextView",
            ProfileRect(100, 300, 200, 340),
        )
        moments_value = FakeProfileControl(
            "不应算进标签",
            "mmui::ProfileTextView",
            ProfileRect(220, 300, 400, 340),
        )
        root = FakeProfileControl(
            children=[tag_label, tag_value, moments_label, moments_value]
        )

        fields = read_profile_fields(root)

        self.assertEqual(fields["标签"], ["大学同学"])
        self.assertEqual(fields["朋友圈"], ["不应算进标签"])

    def test_returns_empty_dict_without_labels(self):
        root = FakeProfileControl(
            children=[
                FakeProfileControl(
                    "大学同学",
                    "mmui::ProfileTextView",
                    ProfileRect(100, 100, 200, 140),
                )
            ]
        )

        self.assertEqual(read_profile_fields(root), {})

    def test_reads_profile_contact_from_head_control(self):
        root = FakeProfileControl(
            children=[
                FakeProfileControl(
                    "张三",
                    "mmui::ContactHeadView",
                    ProfileRect(10, 10, 130, 130),
                )
            ]
        )

        self.assertEqual(read_profile_contact(root), "张三")

    def test_missing_profile_contact_returns_none(self):
        root = FakeProfileControl(children=[])

        self.assertIsNone(read_profile_contact(root))


if __name__ == "__main__":
    unittest.main()
