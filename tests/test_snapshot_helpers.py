"""UIA 会话选中态与 ValuePattern 的纯逻辑测试。"""

import unittest

from get_chat import list_sessions
from snapshot import read_value_pattern, selected_session_name


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

    def __init__(self, name, selected=None, value=None, supported=True):
        self.Name = name
        self.BoundingRectangle = FakeRect()
        self._pattern = (
            FakePattern(selected=selected, value=value) if supported else None
        )

    def GetPattern(self, pattern_id):
        return self._pattern

    def GetChildren(self):
        return []


class FakeList:
    def __init__(self, items):
        self._items = items

    def GetChildren(self):
        return list(self._items)


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


if __name__ == "__main__":
    unittest.main()
