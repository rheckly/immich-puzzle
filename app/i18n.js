// Mehrsprachigkeit für Puzzle, aus Collage übernommen (Muster von Face-Timelapse).
// Jede Sprache hat eine Datei texts.<sprache>.js mit festen Schlüsseln. Der Server bindet alle Dateien vor dieser ein.
// Neue Sprache: Datei texts.fr.js anlegen (Kopie von texts.en.js, mit _locale und _label) und hier in LANGS eintragen.
//   t("schlüssel", { name: "…", n: 3 })  Text in der gewählten Sprache, {name} wird ersetzt. Zahlen kommen formatiert hinein.
//   Mehrzahl: der Text ist ein Objekt { one: "…", other: "…" }, gewählt wird nach vars.n über Intl.PluralRules.
//   Fehlt ein Text, gilt Englisch (mit Warnung in der Konsole), sonst der Schlüssel selbst.
//   Statisches HTML: data-i18n="schlüssel" (Text), data-i18n-html (mit <b> usw.), data-i18n-title, data-i18n-placeholder, data-i18n-aria-label, data-i18n-alt.
//   onLang(fn) ruft fn nach jedem Sprachwechsel, damit die Seite gezeichnete Teile neu zeichnet.
const LANGS = ["de", "en"];
const TEXTS = window.TEXTS || {};
const i18nWarned = new Set(), i18nListeners = [];
let lang = (() => {
  const saved = localStorage.getItem("lang");
  if (LANGS.includes(saved)) return saved;
  const browser = (navigator.languages || [navigator.language || ""]).map(l => String(l).slice(0, 2).toLowerCase());
  return browser.find(l => LANGS.includes(l)) || "en";
})();
document.documentElement.lang = lang;

const locale = () => (TEXTS[lang] && TEXTS[lang]._locale) || "en-US";
const fmt = n => Number(n).toLocaleString(locale());
function t(key, vars) {
  let text = TEXTS[lang] && TEXTS[lang][key];
  if (text === undefined) {
    text = TEXTS.en && TEXTS.en[key];
    if (!i18nWarned.has(lang + key)) { i18nWarned.add(lang + key); console.warn(`Missing text: ${key} (${lang})`); }
    if (text === undefined) return key;
  }
  if (typeof text === "object") {
    const n = Number(vars && vars.n);
    text = text[new Intl.PluralRules(locale()).select(n)] ?? text.other;
  }
  return String(text).replace(/\{(\w+)\}/g, (all, name) => {
    if (!vars || vars[name] === undefined || vars[name] === null) return all;
    return typeof vars[name] === "number" ? fmt(vars[name]) : String(vars[name]);
  });
}
// Datum und Zeit in der gewählten Sprache. options wie bei Intl.DateTimeFormat
function fmtDate(value, options) {
  const d = value instanceof Date ? value : new Date(value);
  return isNaN(d) ? "" : d.toLocaleString(locale(), options || { dateStyle: "medium" });
}
// Meldung vom Server: mit Code übersetzt, sonst der Text, wie er kam. field_code und field_vars stehen neben field
function srvText(obj, field = "message") {
  if (!obj) return "";
  const code = obj[`${field}_code`];
  if (!code) return obj[field] || "";
  // Werte können selbst eine Meldung mit Code tragen (z.B. error_code und error_vars für {error}), die wird mit übersetzt
  const vars = { ...(obj[`${field}_vars`] || {}) };
  for (const key of Object.keys(vars)) {
    const inner = key.match(/^(\w+)_code$/);
    if (inner && vars[key]) vars[inner[1]] = srvText(vars, inner[1]);
  }
  return t(`srv.${code}`, vars);
}
function applyI18n(root = document) {
  const each = (attr, set) => root.querySelectorAll(`[${attr}]`).forEach(el => set(el, t(el.getAttribute(attr))));
  each("data-i18n", (el, s) => { el.textContent = s; });
  each("data-i18n-html", (el, s) => { el.innerHTML = s; });
  each("data-i18n-title", (el, s) => { el.title = s; });
  each("data-i18n-placeholder", (el, s) => { el.placeholder = s; });
  each("data-i18n-aria-label", (el, s) => { el.setAttribute("aria-label", s); });
  each("data-i18n-alt", (el, s) => { el.alt = s; });
}
function onLang(fn) { i18nListeners.push(fn); }
function setLang(next) {
  if (!LANGS.includes(next) || next === lang) return;
  lang = next;
  localStorage.setItem("lang", lang);
  document.documentElement.lang = lang;
  applyI18n();
  drawLangSwitch();
  i18nListeners.forEach(fn => fn(lang));
}
// Schalter EN|DE in <nav id="langSwitch">, nur noch auf dem Freigabelink der Zoom-Seite.
// In Puzzle steht die Sprache im Profil-Menü (profile.js)
function drawLangSwitch() {
  const nav = document.getElementById("langSwitch");
  if (!nav) return;
  nav.setAttribute("aria-label", t("lang.label"));
  nav.replaceChildren(...LANGS.map(l => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = (TEXTS[l] && TEXTS[l]._label) || l.toUpperCase();
    b.title = t(`lang.${l}`);
    b.setAttribute("aria-pressed", l === lang);
    b.addEventListener("click", () => setLang(l));
    return b;
  }));
}
document.addEventListener("DOMContentLoaded", () => { applyI18n(); drawLangSwitch(); });
