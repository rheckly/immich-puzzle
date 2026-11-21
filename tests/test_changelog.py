"""Changelog (Entscheid 43): app/CHANGELOG.md mit Typ je Eintrag, parse_changelog und read_changelog in app/server.py."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "app"), os.path.join(ROOT, "tests")]

import server  # noqa: E402

SAMPLE = """# Änderungen

## 0.2.0

Erster Absatz.

- `NEW` Neue Funktion mit `code`
  - Unterpunkt eins
  - Unterpunkt zwei
- `FIX` Fehler behoben
- Hinweis ohne Typ

Zweiter Absatz.

- `LNG` Texte

## 0.1.0 (Prototyp)

- `CHG` Geändert
"""

# Hinweise ohne Typ, die bewusst so bleiben (Anfang des Textes). In Puzzle hat jeder Eintrag einen Typ.
UNTYPED = {}


class ParseTest(unittest.TestCase):
    def test_sample(self):
        new, old = server.parse_changelog(SAMPLE)
        self.assertEqual((new["version"], new["label"]), ("0.2.0", ""))
        self.assertEqual((old["version"], old["label"]), ("0.1.0", "Prototyp"))
        self.assertEqual(new["note"], "Erster Absatz. Zweiter Absatz.")
        self.assertEqual([c["type"] for c in new["changes"]], ["NEW", "FIX", None, "LNG"])
        self.assertEqual(new["changes"][0]["text"], "Neue Funktion mit `code`")
        self.assertEqual(new["changes"][0]["sub"], ["Unterpunkt eins", "Unterpunkt zwei"])
        self.assertEqual(new["changes"][2]["text"], "Hinweis ohne Typ")
        self.assertEqual(old["changes"], [{"type": "CHG", "text": "Geändert", "sub": []}])

    def test_unknown_type_stays_text(self):
        (rel,) = server.parse_changelog("## 1.0\n\n- `XYZ` Etwas\n")
        self.assertEqual(rel["changes"][0]["type"], None)
        self.assertEqual(rel["changes"][0]["text"], "`XYZ` Etwas")

    def test_missing_file(self):
        self.assertEqual(server.read_changelog(os.path.join(tempfile.gettempdir(), "fehlt-changelog.md")), [])


class FileTest(unittest.TestCase):
    def test_every_entry_has_a_type(self):
        releases = server.read_changelog()
        self.assertEqual(releases[0]["version"], server.VERSION)
        untyped = {}
        for rel in releases:
            self.assertTrue(rel["changes"], rel["version"])
            for change in rel["changes"]:
                if change["type"] is None:
                    untyped.setdefault(rel["version"], change["text"])
                for sub in change["sub"]:
                    self.assertNotRegex(sub, r"^`(NEW|CHG|FIX|LNG)` ", rel["version"])  # Unterpunkte ohne Typ
        self.assertEqual(set(untyped), set(UNTYPED))
        for version, start in UNTYPED.items():
            self.assertTrue(untyped[version].startswith(start), version)


if __name__ == "__main__":
    unittest.main()
