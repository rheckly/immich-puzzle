"""Prüft die Sprachdateien app/texts.*.js (Mehrsprachigkeit, siehe app/i18n.js).

    python tests/check_texts.py      -> listet Probleme, Exit-Code 1 wenn es welche gibt

Geprüft wird:
- jede Sprachdatei hat dieselben Schlüssel wie texts.en.js, mit denselben {platzhaltern}
- Mehrzahl-Texte haben mindestens "other"
- LANGS in app/i18n.js nennt genau die vorhandenen Sprachdateien
- jeder feste Schlüssel aus den Seiten (data-i18n…="…", t("…")) und jeder Code aus Msg("…", …) im Python-Code steht im Wörterbuch
- der deutsche Text zu srv.<code> ist genau die Vorlage aus dem Python-Code
"""
import ast
import glob
import json
import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load(path):
    """Liest texts.xx.js. Die Dateien sind JSON in einer Zuweisung, dazu //-Kommentare und Kommas am Ende."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    body = src[src.index("= {") + 2:src.rindex("};") + 1]
    body = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


def holders(value):
    parts = value.values() if isinstance(value, dict) else [value]
    return set().union(*(set(PLACEHOLDER.findall(str(p))) for p in parts))


def used_keys():
    keys = set()
    for path in glob.glob(os.path.join(APP, "*.html")) + glob.glob(os.path.join(APP, "*.js")):
        if os.path.basename(path).startswith("texts.") or os.path.basename(path) == "share.html":
            continue
        with open(path, encoding="utf-8") as f:
            src = "\n".join(line for line in f.read().splitlines() if not line.strip().startswith("//"))  # Beispiele in Kommentaren zählen nicht
        keys |= set(re.findall(r'data-i18n(?:-html|-title|-placeholder|-aria-label|-alt)?="([^"{}$]+)"', src))
        keys |= set(re.findall(r'\bt\(\s*["\']([\w.]+)["\']', src))
    return keys


def python_codes():
    codes = {}
    for path in glob.glob(os.path.join(APP, "*.py")):
        if os.path.basename(path) == "msg.py":  # Nur Beispiele im Kommentar
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # Auch über mehrere Zeilen zusammengesetzte Texte: Msg("code", "Teil 1 " "Teil 2", …)
        for code, literals in re.findall(r'\bMsg\(\s*"(\w+)",\s*((?:"(?:[^"\\]|\\.)*"\s*)+)', src):
            text = "".join(ast.literal_eval(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', literals))
            codes.setdefault(code, set()).add(text)
    return codes


def problems():
    out = []
    files = {re.match(r"texts\.(\w+)\.js", os.path.basename(p)).group(1): p for p in glob.glob(os.path.join(APP, "texts.*.js"))}
    texts = {}
    for lang, path in files.items():
        try:
            texts[lang] = load(path)
        except (ValueError, json.JSONDecodeError) as e:
            out.append(f"texts.{lang}.js lässt sich nicht lesen: {e}")
    if "en" not in texts:
        return out + ["texts.en.js fehlt (Englisch ist der Ersatz)"]
    with open(os.path.join(APP, "i18n.js"), encoding="utf-8") as f:
        langs = json.loads(re.search(r"const LANGS = (\[[^\]]*\])", f.read()).group(1))
    if sorted(langs) != sorted(files):
        out.append(f"LANGS in i18n.js {langs} passt nicht zu den Sprachdateien {sorted(files)}")
    en = texts["en"]
    for lang, words in texts.items():
        for key in ("_locale", "_label"):
            if key not in words:
                out.append(f"texts.{lang}.js: {key} fehlt")
        for key in sorted(set(en) - set(words)):
            out.append(f"texts.{lang}.js: Schlüssel fehlt: {key}")
        for key in sorted(set(words) - set(en)):
            out.append(f"texts.{lang}.js: Schlüssel zu viel (fehlt in texts.en.js): {key}")
        for key in sorted(set(words) & set(en)):
            if key.startswith("_"):
                continue
            if isinstance(words[key], dict) and "other" not in words[key]:
                out.append(f"texts.{lang}.js: {key}: Mehrzahl ohne \"other\"")
            if holders(words[key]) != holders(en[key]):
                out.append(f"texts.{lang}.js: {key}: Platzhalter {sorted(holders(words[key]))} statt {sorted(holders(en[key]))}")
    for key in sorted(used_keys() - set(en)):
        out.append(f"Schlüssel wird benutzt, steht aber nicht im Wörterbuch: {key}")
    de = texts.get("de", {})
    for code, templates in sorted(python_codes().items()):
        key = f"srv.{code}"
        if key not in en:
            out.append(f"Msg-Code ohne Text: {key}")
        elif len(templates) > 1:
            out.append(f"Msg-Code {code} hat verschiedene deutsche Texte: {sorted(templates)}")
        elif de.get(key) != next(iter(templates)):
            out.append(f"{key}: deutscher Text weicht vom Python-Code ab: {de.get(key)!r} / {next(iter(templates))!r}")
    return out


if __name__ == "__main__":
    found = problems()
    for p in found:
        print(p)
    print(f"{len(found)} Probleme" if found else "Sprachdateien in Ordnung")
    sys.exit(1 if found else 0)
