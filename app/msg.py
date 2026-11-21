"""Meldungen an die Seite in mehreren Sprachen.

Der Server schreibt seine Meldungen weiter deutsch (Log, Tests, alte Stände). Dazu kommt ein Code mit Werten,
den die Seite in ihrer Sprache zeigt: Schlüssel srv.<code> in app/texts.*.js, Platzhalter {name} wie bei str.format.

    raise ValueError(Msg("noPhotos", "Keine Fotos gefunden."))
    raise ValueError(Msg("tooBig", "Höchstens {max} Kacheln.", max=500))
    job["message"] = Msg(...)  ->  besser put(job, "message", Msg(...)), dann stehen message_code und message_vars daneben

_route schickt bei Fehlern {"error": text, "error_code": code, "error_vars": werte}. Die Seite übersetzt mit
srvText(daten, "error") bzw. srvText(job, "message") aus i18n.js; ohne Code zeigt sie den Text, wie er kam.
"""


class Msg(str):
    """Ein deutscher Text, der seinen Code und seine Werte mitträgt. Verhält sich sonst wie ein gewöhnlicher str."""

    def __new__(cls, code, text, **values):
        s = super().__new__(cls, text.format(**values) if values else text)
        s.code, s.values = code, values
        return s


def fields(field, msg):
    """Die Felder für eine Meldung: {field: text, field_code: code, field_vars: werte}. Ohne Msg nur der Text."""
    out = {field: str(msg)}
    code = getattr(msg, "code", None)
    if code:
        out[f"{field}_code"] = code
        out[f"{field}_vars"] = dict(msg.values)
    return out


def put(target, field, msg):
    """Setzt eine Meldung in ein dict (Job, Speichern, Teilen). Alte Code-Felder fallen weg, wenn msg keinen Code hat."""
    target.pop(f"{field}_code", None)
    target.pop(f"{field}_vars", None)
    target.update(fields(field, msg))
    return target


def of(error):
    """Die Meldung einer Ausnahme, mit Code, wenn sie mit Msg ausgelöst wurde."""
    first = error.args[0] if getattr(error, "args", None) else None
    return first if isinstance(first, Msg) else str(error)


def coded(field, msg):
    """Wie fields, aber Code und Werte stehen immer da (None ohne Code). Für dicts, die ein anderer Thread
    gleichzeitig liest (index_state): So ändert sich die Zahl der Schlüssel nie."""
    return {f"{field}_code": None, f"{field}_vars": None, **fields(field, msg)}


def apart(n, mark="’"):
    """Zahl mit Tausender-Trennzeichen für den deutschen Text: 12’345."""
    return f"{n:,}".replace(",", mark)


def raw(msg, **values):
    """Der deutsche Text bleibt, wie er ist (etwa mit apart), die Seite bekommt diese Werte roh und formatiert selbst."""
    msg.values = {**msg.values, **values}
    return msg
