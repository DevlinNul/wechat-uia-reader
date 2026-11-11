"""条件等待的行为测试（纯逻辑，不依赖真实微信）。"""

import time
import unittest

from wechat_uia.core.timing import wait_until


class WaitUntilTest(unittest.TestCase):
    def test_returns_true_immediately(self):
        start = time.monotonic()

        self.assertTrue(wait_until(lambda: True, 0.5))
        self.assertLess(time.monotonic() - start, 0.1)

    def test_returns_false_after_timeout(self):
        start = time.monotonic()
        result = wait_until(lambda: False, 0.1, 0.01)
        elapsed = time.monotonic() - start

        self.assertFalse(result)
        self.assertGreaterEqual(elapsed, 0.09)
        self.assertLess(elapsed, 0.5)

    def test_checks_once_even_with_zero_timeout(self):
        calls = []

        self.assertFalse(wait_until(lambda: calls.append(1) or False, 0.0))
        self.assertEqual(len(calls), 1)

    def test_true_after_several_attempts(self):
        state = {"n": 0}

        def predicate():
            state["n"] += 1
            return state["n"] >= 3

        self.assertTrue(wait_until(predicate, 1.0, 0.01))
        self.assertEqual(state["n"], 3)

    def test_exception_counts_as_not_ready(self):
        state = {"n": 0}

        def predicate():
            state["n"] += 1
            if state["n"] < 2:
                raise RuntimeError("还没好")
            return True

        self.assertTrue(wait_until(predicate, 1.0, 0.01))


if __name__ == "__main__":
    unittest.main()
