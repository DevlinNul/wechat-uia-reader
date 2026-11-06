"""UIA 遍历的防环、深度和超时测试（不连接真实微信）。"""

import time
import unittest

from uia_utils import iter_descendants


class Node:
    def __init__(self, identity, children=None):
        self.identity = identity
        self.children = children or []
        self.Name = f"node-{identity}"
        self.ClassName = "FakeControl"
        self.ControlTypeName = "TextControl"

    def GetRuntimeId(self):
        return [42, self.identity]

    def GetChildren(self):
        return list(self.children)


class IterDescendantsTest(unittest.TestCase):
    def test_cycle_is_visited_once(self):
        root = Node(1)
        child = Node(2)
        root.children = [child]
        child.children = [root]

        seen = [node.identity for node in iter_descendants(root)]

        self.assertEqual(seen, [2])

    def test_duplicate_siblings_are_visited_once(self):
        root = Node(1)
        first = Node(2)
        duplicate = Node(2)
        root.children = [first, duplicate]

        seen = [node.identity for node in iter_descendants(root)]

        self.assertEqual(seen, [2])

    def test_depth_limit_stops_before_grandchildren(self):
        root = Node(1)
        child = Node(2)
        grandchild = Node(3)
        root.children = [child]
        child.children = [grandchild]

        seen = [node.identity for node in iter_descendants(root, max_depth=1)]

        self.assertEqual(seen, [2])

    def test_expired_deadline_yields_nothing(self):
        root = Node(1)
        root.children = [Node(2)]

        seen = list(
            iter_descendants(root, deadline=time.monotonic() - 1)
        )

        self.assertEqual(seen, [])

    def test_missing_runtime_id_still_walks_children(self):
        root = Node(1)
        child = Node(2)
        root.children = [child]

        def no_runtime_id():
            raise RuntimeError("元素已失效")

        root.GetRuntimeId = no_runtime_id
        child.GetRuntimeId = no_runtime_id

        seen = [node.identity for node in iter_descendants(root)]

        self.assertEqual(seen, [2])


if __name__ == "__main__":
    unittest.main()
