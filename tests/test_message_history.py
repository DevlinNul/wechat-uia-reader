"""历史消息合并与截断的纯逻辑测试。"""

import unittest

from message_history import (
    anchor_position_matches,
    choose_restore_anchor,
    merge_older_page,
    read_visible,
    trim_to_limit,
    visible_signature,
)


def record(key: str, text: str | None = None, kind: str = "text") -> dict:
    return {
        "_key": key,
        "_restore_key": ("restore", key),
        "text": text or key,
        "kind": kind,
    }


def keys(messages: list[dict]) -> list[str]:
    return [message["_key"] for message in messages]


class FakeRect:
    left = 0
    top = 0
    right = 10
    bottom = 10


class FakeItem:
    ControlTypeName = "ListItemControl"

    def __init__(self, name: str, class_name: str, runtime_id):
        self.Name = name
        self.ClassName = class_name
        self.AutomationId = ""
        self.BoundingRectangle = FakeRect()
        self._runtime_id = runtime_id

    def GetChildren(self):
        return []

    def GetRuntimeId(self):
        return self._runtime_id


class FakeList:
    def __init__(self, items):
        self._items = items

    def GetChildren(self):
        return list(self._items)


class VisibleSignatureTest(unittest.TestCase):
    def test_signature_uses_first_and_last_item(self):
        items = [
            FakeItem("a", "mmui::ChatTextItemView", (1,)),
            FakeItem("b", "mmui::ChatTextItemView", (2,)),
        ]

        self.assertEqual(visible_signature(FakeList(items)), (2, (1,), (2,)))

    def test_empty_list(self):
        self.assertEqual(visible_signature(FakeList([])), (0, None, None))


class RuntimeKeyTest(unittest.TestCase):
    def test_reused_runtime_id_with_different_text_is_not_collapsed(self):
        items = [
            FakeItem("9月24日 20:32", "mmui::ChatItemView", (42, 1, 4, -1)),
            FakeItem("12:11", "mmui::ChatItemView", (42, 1, 4, -1)),
        ]

        records = read_visible(FakeList(items))

        self.assertNotEqual(records[0]["_key"], records[1]["_key"])


class AnchorPositionTest(unittest.TestCase):
    def test_allows_one_item_offset(self):
        page = [record("other"), record("anchor"), record("other-2")]

        self.assertTrue(anchor_position_matches(page, ("restore", "anchor"), 0))
        self.assertTrue(anchor_position_matches(page, ("restore", "anchor"), 2))

    def test_rejects_far_offset(self):
        page = [record("other"), record("other-2"), record("other-3"), record("anchor")]

        self.assertFalse(anchor_position_matches(page, ("restore", "anchor"), 1))


class ChooseRestoreAnchorTest(unittest.TestCase):
    def test_skips_time_separator(self):
        page = [record("time-1", kind="time"), record("message-2")]

        key, index = choose_restore_anchor(page)

        self.assertEqual(key, ("restore", "message-2"))
        self.assertEqual(index, 1)

    def test_prefers_text_over_image(self):
        page = [record("image-1", kind="image"), record("message-2")]

        key, index = choose_restore_anchor(page)

        self.assertEqual(key, ("restore", "message-2"))
        self.assertEqual(index, 1)

    def test_uses_first_item_when_not_time(self):
        page = [record("message-1"), record("time-2", kind="time")]

        key, index = choose_restore_anchor(page)

        self.assertEqual(key, ("restore", "message-1"))
        self.assertEqual(index, 0)

    def test_falls_back_to_first_time_item(self):
        page = [record("time-1", kind="time"), record("time-2", kind="time")]

        key, index = choose_restore_anchor(page)

        self.assertEqual(key, ("restore", "time-1"))
        self.assertEqual(index, 0)

    def test_empty_page(self):
        self.assertEqual(choose_restore_anchor([]), (None, 0))


class MergeOlderPageTest(unittest.TestCase):
    def test_prepends_only_items_above_anchor(self):
        collected = [record("b"), record("c")]
        page = [record("a"), record("b"), record("c")]

        added, anchor_found = merge_older_page(collected, page, "b")

        self.assertEqual(added, 1)
        self.assertTrue(anchor_found)
        self.assertEqual(keys(collected), ["a", "b", "c"])

    def test_deduplicates_when_anchor_is_missing(self):
        collected = [record("b"), record("c")]
        page = [record("a"), record("b"), record("x")]

        added, anchor_found = merge_older_page(collected, page, "missing")

        self.assertFalse(anchor_found)
        self.assertEqual(added, 2)
        self.assertEqual(keys(collected), ["a", "x", "b", "c"])

    def test_no_new_items_when_page_starts_at_anchor(self):
        collected = [record("a"), record("b")]
        page = [record("a"), record("b"), record("c")]

        added, anchor_found = merge_older_page(collected, page, "a")

        self.assertTrue(anchor_found)
        self.assertEqual(added, 0)
        self.assertEqual(keys(collected), ["a", "b"])


class TrimToLimitTest(unittest.TestCase):
    def test_keeps_newest_messages(self):
        messages = [record("a"), record("b"), record("c"), record("d")]

        trimmed = trim_to_limit(messages, 2)

        self.assertEqual(keys(trimmed), ["c", "d"])

    def test_no_change_when_under_limit(self):
        messages = [record("a"), record("b")]

        trimmed = trim_to_limit(messages, 5)

        self.assertIs(trimmed, messages)


if __name__ == "__main__":
    unittest.main()
