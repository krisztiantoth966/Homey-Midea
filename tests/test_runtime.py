import unittest

from lib.runtime import (
    LIGHT_QUERY_CLASS_NAMES,
    active_warning,
    filter_dirty,
    normalize_query_mode,
    poll_seconds,
)


class RuntimeTests(unittest.TestCase):
    def test_query_mode(self):
        self.assertEqual(normalize_query_mode(None), "light")
        self.assertEqual(normalize_query_mode("PUSH"), "push")
        self.assertEqual(normalize_query_mode("full"), "full")
        self.assertEqual(normalize_query_mode("bogus"), "light")

    def test_poll_seconds(self):
        self.assertEqual(poll_seconds(None), 15)
        self.assertEqual(poll_seconds("5"), 10)
        self.assertEqual(poll_seconds(30), 30)
        self.assertEqual(poll_seconds(9999), 600)

    def test_light_queries(self):
        self.assertEqual(LIGHT_QUERY_CLASS_NAMES, ("StateQuery", "PowerQuery"))

    def test_filter(self):
        self.assertIsNone(filter_dirty({}))
        self.assertIsNone(filter_dirty({"full_dust": None}))
        self.assertTrue(filter_dirty({"full_dust": True}))
        self.assertFalse(filter_dirty({"full_dust": 0}))

    def test_warning_priority(self):
        self.assertIsNone(active_warning(filter_is_dirty=False, error_code=0))
        self.assertIsNone(active_warning(filter_is_dirty=None, error_code=0))
        self.assertEqual(active_warning(filter_is_dirty=True, error_code=0).key, "warnings.filter")
        w = active_warning(filter_is_dirty=True, error_code=38)
        self.assertEqual((w.key, w.tokens), ("warnings.error_code", {"code": 38}))


if __name__ == "__main__":
    unittest.main()
