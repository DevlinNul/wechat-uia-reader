"""元素引用缓存的失效判断（纯逻辑，不依赖真实微信）。"""

import unittest

from wechat_uia.core.element_ref import (
    capture,
    current_name,
    still_valid,
    unchanged,
)


class FakeElement:
    def __init__(self, runtime_id=(1, 2, 3), name="张三", alive=True):
        self._runtime_id = runtime_id
        self._name = name
        self.alive = alive
        self.use_control_name = False

    def __bool__(self):
        return self.alive

    def GetRuntimeId(self):
        return list(self._runtime_id)

    @property
    def CurrentName(self):
        if self.use_control_name:
            raise AttributeError("no CurrentName")
        if not self.alive:
            raise RuntimeError("元素已失效")
        return self._name

    @property
    def Name(self):
        return self._name


class CaptureTest(unittest.TestCase):
    def test_empty_element_returns_none(self):
        self.assertIsNone(capture(None))
        self.assertIsNone(capture(FakeElement(alive=False)))

    def test_capture_reads_runtime_id_and_name(self):
        ref = capture(FakeElement((7, 8), " 某联系人 "))

        self.assertEqual(ref.runtime_id, (7, 8))
        self.assertEqual(ref.name, "某联系人")

    def test_capture_falls_back_to_control_name(self):
        element = FakeElement(name="李四")
        element.use_control_name = True

        self.assertEqual(capture(element).name, "李四")


class StillValidTest(unittest.TestCase):
    def test_same_runtime_id_is_valid(self):
        ref = capture(FakeElement((1, 2, 3)))

        self.assertTrue(still_valid(ref))

    def test_changed_runtime_id_is_invalid(self):
        element = FakeElement((1, 2, 3))
        ref = capture(element)
        element._runtime_id = (1, 2, 4)

        self.assertFalse(still_valid(ref))

    def test_dead_element_is_invalid(self):
        element = FakeElement()
        ref = capture(element)
        element.alive = False

        self.assertFalse(still_valid(ref))
        self.assertFalse(still_valid(None))


class UnchangedTest(unittest.TestCase):
    def test_same_name_is_unchanged(self):
        element = FakeElement(name="某群")

        self.assertTrue(unchanged(capture(element), "某群"))

    def test_other_name_is_changed(self):
        element = FakeElement(name="某群")
        ref = capture(element)
        element._name = "另一个群"

        self.assertFalse(unchanged(ref, "某群"))

    def test_missing_name_is_not_unchanged(self):
        element = FakeElement(name="张三")
        ref = capture(element)
        element.alive = False

        self.assertFalse(unchanged(ref, "张三"))
        self.assertIsNone(current_name(ref))
        self.assertIsNone(current_name(None))


if __name__ == "__main__":
    unittest.main()
