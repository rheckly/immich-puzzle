"""Die Sprachdateien sind vollständig und passen zueinander (tests/check_texts.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_texts  # noqa: E402


class TextsTest(unittest.TestCase):
    def test_all_languages_have_the_same_keys(self):
        self.assertEqual(check_texts.problems(), [])


if __name__ == "__main__":
    unittest.main()
