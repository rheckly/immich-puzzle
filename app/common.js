// Aus Immich Collage übernommen (gekürzt): Hilfen, Login, Spalten, Panels «Über» und «Changelog».
const $ = id => document.getElementById(id);
function show(el, text) { el.textContent = text || ""; el.classList.toggle("hidden", !text); }
// fmt, t, locale, onLang und srvText kommen aus i18n.js
const plain = s => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// ---------- Login ----------
// Ist der Login aktiv (LOGIN_PASSWORD) und die Anmeldung abgelaufen, antwortet die API mit 401.
// Dann auf die Login-Seite und nach dem Anmelden zurück hierher. Gilt für jeden fetch der Seite.
// replace statt assign (Entscheid 42): Die abgelaufene Seite bleibt nicht im Verlauf, «Zurück» führt nach dem Anmelden
// zur Seite davor statt nochmals hierher. Die Login-Seite ersetzt sich nach dem Anmelden ebenfalls (login.html)
function toLogin() {
  location.replace(`/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`);
}
{
  const rawFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const res = await rawFetch(...args);
    if (res.status === 401 && !String(args[0] && args[0].url || args[0]).startsWith("/api/auth/")) toLogin();
    return res;
  };
}
// Abmelden steht im Profil-Button oben rechts (profile.js)

// Hell/Dunkel und Sprache stehen im Profil-Menü (profile.js). applyTheme steht im Kopf der Seite

// ---------- Pfad oben und Spalten ----------
// Die Spalten der Panels passen ganz ins Fenster, auch solange der Kopf oben noch sichtbar ist. Sonst wären die Knöpfe unten
// (Fertig) erst nach dem Scrollen zu sehen. Beim Scrollen rückt die Spalte nach oben und wird höher
function fitPaneHeight() {
  const head = document.querySelector("header"), top = Math.max(12, (head ? head.getBoundingClientRect().bottom : 0) + 16);
  document.documentElement.style.setProperty("--paneTop", `${Math.round(top)}px`);
}
fitPaneHeight();
window.addEventListener("scroll", fitPaneHeight, { passive: true });
window.addEventListener("resize", fitPaneHeight);
if (document.querySelector("header")) new ResizeObserver(fitPaneHeight).observe(document.querySelector("header"));  // Der Pfad oben kann den Kopf höher machen
// stack: die offenen Spalten von links nach rechts, [{ id, label }]. Ein Klick auf einen Teil des Pfads ruft back(id)
function drawCrumbs(stack, back) {
  const nav = $("crumbs");
  nav.classList.toggle("hidden", stack.length < 2);
  nav.replaceChildren(...(stack.length < 2 ? [] : stack.flatMap((p, i) => {
    if (i === stack.length - 1) {
      const now = document.createElement("span"); now.className = "now"; now.textContent = p.label;
      now.setAttribute("aria-current", "location");
      return [now];
    }
    const link = document.createElement("button"); link.textContent = p.label;
    link.addEventListener("click", () => back(p.id));
    const sep = document.createElement("span"); sep.className = "sep"; sep.textContent = ">"; sep.setAttribute("aria-hidden", "true");
    return [link, sep];
  })));
}
// Füllt die Spalten von rechts her auf. Die Spalte ganz rechts bleibt immer sichtbar, was links keinen Platz hat, verschwindet.
// width: Mindestbreite je Spalte, track: Breite im Raster. Gibt die Spalten zurück, die zu sehen sind
function fitColumns(ids, width, track) {
  const page = window.innerWidth, narrow = page <= 860;
  let used = 40 + width[ids[ids.length - 1]], first = ids.length - 1;
  while (first > 0 && used + 16 + width[ids[first - 1]] <= page) {
    first--;
    used += 16 + width[ids[first]];
  }
  ids.forEach((id, i) => $(id).classList.toggle("offLeft", !narrow && i < first));
  document.querySelector("main").style.gridTemplateColumns = narrow ? "" : ids.slice(first).map(id => track[id]).join(" ");
  return ids.slice(first);
}

// ---------- Panel «Über» ----------
// Gleich aufgebaut wie «Über» in Face-Timelapse: Name, Abschnitt Version, Abschnitt Immich mit Status und Version.
// Braucht im Panel <div id="aboutBody">. Fragt bei jedem Öffnen neu nach (GET /api/version und /api/immich)
// Entscheid 43: danach die Abschnitte aus about.sections ([Titel-Schlüssel, Text-Schlüssel], z. B. Herkunft und Lizenz)
// und zuunterst die Box «Changelog» mit «N Änderungen in M Versionen» und «>». Die Box erscheint nur, wenn
// GET /api/changelog antwortet und die Seite about.onChangelog setzt (öffnet rechts das Panel mit <div id="changelogBody">).
const about = { app: null, appFailed: false, immich: { status: "checking" }, sections: [], changelog: null, onChangelog: null, changelogOpen: false };
async function drawImmichStatus() {
  about.immich = { status: "checking" };
  drawAbout();
  if (!about.app) {
    try { about.app = await (await fetch("/api/version")).json(); about.appFailed = false; }
    catch { about.appFailed = true; }
  }
  if (!about.changelog) await loadChangelog();
  let info;
  try { info = await (await fetch("/api/immich")).json(); }
  catch (e) { info = { ok: false, error: String(e) }; }
  about.immich = info.ok ? { status: "connected", version: info.version }
    : info.configured === false ? { status: "off" } : { status: "error", error: srvText(info, "error") };
  drawAbout();
}
function drawAbout() {
  const body = $("aboutBody");
  if (!body) return;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const table = rows => {
    const tb = el("tbody");
    rows.forEach(([head, cell]) => { const tr = el("tr"); tr.append(el("th", "", head)); const td = el("td"); td.append(cell); tr.append(td); tb.append(tr); });
    const tab = el("table", "aboutTable"); tab.append(tb); return tab;
  };
  const parts = [el("p", "aboutName", "Puzzle"), el("h3", "paneSection", t("about.version"))];
  if (about.app) {
    const rows = [[t("about.version"), about.app.version]];
    if (about.app.date) rows.push([t("about.built"), fmtDate(about.app.date)]);
    parts.push(table(rows));
  } else parts.push(el("p", "muted", about.appFailed ? t("about.noVersion") : t("src.loading")));
  const im = about.immich, status = el("span");
  status.append(el("span", `immichDot dot-${im.status}`), t({ connected: "immich.connected", checking: "immich.checking", error: "immich.error", off: "immich.off" }[im.status]));
  const rows = [[t("immich.status"), status]];
  if (im.version) rows.push([t("about.version"), im.version.replace(/^v/, "")]);
  if (im.error) rows.push(["", el("span", "error", im.error)]);
  parts.push(el("h3", "paneSection", t("immich.title")), table(rows));
  if (im.status === "error") {
    const p = el("p"), retry = el("button", "", t("immich.retry"));
    retry.type = "button";
    retry.addEventListener("click", drawImmichStatus);
    p.append(retry); parts.push(p);
  }
  for (const [title, text] of about.sections) parts.push(el("h3", "paneSection", t(title)), el("p", "aboutText", t(text)));
  const log = about.changelog;
  if (log && log.releases.length && about.onChangelog) {
    const head = el("div", "clBoxHead"), open = el("button", "paneBtn" + (about.changelogOpen ? " on" : ""), ">");
    open.type = "button";
    open.id = "changelogBtn";
    open.title = open.ariaLabel = t("about.changelogOpen");
    open.setAttribute("aria-pressed", String(about.changelogOpen));
    open.addEventListener("click", () => about.onChangelog());
    head.append(el("h3", "", t("about.changelog")), open);
    const changes = log.releases.reduce((n, r) => n + r.changes.length, 0);
    const box = el("div", "clBox");
    box.append(head, el("p", "muted", t("about.changelogSum", { changes, versions: log.releases.length })));
    parts.push(box);
  }
  body.replaceChildren(...parts);
}
onLang(drawAbout);

// ---------- Panel «Einstellungen» ----------
// Puzzle: die Live-Links stehen nicht unter «Über» (wie Collage), sondern als erster Abschnitt der Einstellungen.
// Braucht im Panel <div id="settingsBody">. about.share setzt die Seite aus GET /api/shares, Speichern über about.saveShareUrl
function drawSettings() {
  const body = $("settingsBody");
  if (!body) return;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const table = rows => {
    const tb = el("tbody");
    rows.forEach(([head, cell]) => { const tr = el("tr"); tr.append(el("th", "", head)); const td = el("td"); td.append(cell); tr.append(td); tb.append(tr); });
    const tab = el("table", "aboutTable"); tab.append(tb); return tab;
  };
  const parts = [el("h3", "paneSection first", t("about.share"))];
  const s = about.share;
  if (!s) parts.push(el("p", "muted", t("src.loading")));
  else if (!s.enabled) parts.push(el("p", "muted", t("about.shareOff")));
  else {
    parts.push(el("p", "muted", t("settings.shareIntro")));
    parts.push(table(s.url ? [[t("about.shareUrl"), el("span", "", s.url + "/…")]]
                           : [[t("about.sharePath"), el("span", "", s.prefix + "/…")], ["", el("span", "muted", t("about.shareNoUrl"))]]));
    if (about.saveShareUrl) parts.push(shareUrlForm(s, el));
  }
  body.replaceChildren(...parts);
}
onLang(drawSettings);

// ---------- Panel «Changelog» (Entscheid 43, wie Reels) ----------
// GET /api/changelog: {types, releases: [{version, label, note, changes: [{type, text, sub}]}]} aus app/CHANGELOG.md.
// Oben das Auswahlfeld Release (alle oder eine) und die Filter-Chips NEW, CHG, FIX, LNG mit Anzahl, darunter die Versionen.
// Jeder Eintrag hat sein Typ-Abzeichen; Einträge ohne Typ (Hinweise) haben keins, zählen in keinem Chip und erscheinen
// nur, solange alle Chips an sind. Braucht im Panel <div id="changelogBody">
const changelogView = { release: "", types: null };
async function loadChangelog() {
  try {
    const res = await fetch("/api/changelog");
    about.changelog = res.ok ? await res.json() : null;
  } catch { about.changelog = null; }
  if (about.changelog && !Array.isArray(about.changelog.releases)) about.changelog = null;
}
function changeText(text) {  // `Code` als <code>, **fett** als <strong> , sonst reiner Text
  const span = document.createElement("span");
  for (const part of String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (!part) continue;
    const tag = part.startsWith("**") ? "strong" : part.startsWith("`") ? "code" : null;
    if (!tag) { span.append(part); continue; }
    const e = document.createElement(tag); e.textContent = part.slice(tag === "strong" ? 2 : 1, tag === "strong" ? -2 : -1); span.append(e);
  }
  return span;
}
function drawChangelog() {
  const body = $("changelogBody"), log = about.changelog;
  if (!body || !log) return;
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const types = log.types || ["NEW", "CHG", "FIX", "LNG"];
  if (!changelogView.types) changelogView.types = new Set(types);
  const all = changelogView.types.size === types.length;
  const inRelease = log.releases.filter(r => !changelogView.release || r.version === changelogView.release);
  const parts = [el("p", "muted clIntro", t("cl.intro"))];

  const bar = el("div", "clBar"), label = el("label", "clRelease"), select = el("select");
  select.append(new Option(t("cl.allReleases"), ""));
  for (const r of log.releases) select.append(new Option(r.label ? `${r.version} (${r.label})` : r.version, r.version));
  select.value = changelogView.release;
  select.addEventListener("change", () => { changelogView.release = select.value; drawChangelog(); });
  label.append(t("cl.release"), select);
  const chips = el("div", "clChips");
  chips.setAttribute("role", "group");
  chips.ariaLabel = t("cl.types");
  for (const type of types) {
    const on = changelogView.types.has(type), chip = el("button", `clChip clType-${type}${on ? " on" : ""}`, type);
    chip.type = "button";
    chip.title = t(`cl.type.${type}`);
    chip.setAttribute("aria-pressed", String(on));
    chip.append(" ", el("span", "clCount", String(inRelease.reduce((n, r) => n + r.changes.filter(c => c.type === type).length, 0))));
    chip.addEventListener("click", () => {
      if (on) changelogView.types.delete(type); else changelogView.types.add(type);
      drawChangelog();
    });
    chips.append(chip);
  }
  bar.append(label, chips);
  parts.push(bar);

  let shown = 0;
  for (const r of inRelease) {
    const changes = r.changes.filter(c => c.type ? changelogView.types.has(c.type) : all);
    if (!changes.length) continue;
    shown++;
    const sec = el("section", "clRel"), head = el("h3", "clRelHead");
    head.append(el("span", "", r.version));
    if (r.label) head.append(el("span", "muted", r.label));
    sec.append(head);
    if (r.note && all) sec.append(el("p", "muted clNote", r.note));
    const list = el("ul", "clList");
    for (const c of changes) {
      const li = el("li");
      const badge = el("span", c.type ? `clBadge clType-${c.type}` : "clBadge clNone", c.type || "");
      if (c.type) badge.title = t(`cl.type.${c.type}`);
      li.append(badge);
      const text = el("div", "clText");
      text.append(changeText(c.text));
      if (c.sub && c.sub.length) {
        const sub = el("ul", "clSub");
        for (const s of c.sub) { const item = el("li"); item.append(changeText(s)); sub.append(item); }
        text.append(sub);
      }
      li.append(text);
      list.append(li);
    }
    sec.append(list);
    parts.push(sec);
  }
  if (!shown) parts.push(el("p", "muted", t("cl.none")));
  body.replaceChildren(...parts);
}
onLang(drawChangelog);
// Einstellung «Adresse der Live-Links»: SHARE_PUBLIC_URL aus dem Stack ist die Vorgabe, ein eigener Wert gilt davor.
// Leer speichern oder «Vorgabe verwenden» = wieder die Vorgabe. Speichern übernimmt about.saveShareUrl(url) der Seite
function shareUrlForm(s, el) {
  const box = el("div", "shareUrlForm"), id = "shareUrlInput";
  const label = el("label", "", t("about.shareSetting")); label.htmlFor = id;
  const input = el("input"); input.id = id; input.type = "url"; input.maxLength = 300; input.spellcheck = false;
  input.value = s.custom ? s.url : (s.default || "");
  input.placeholder = s.default || "https://links.example.com/puzzles";
  const hint = el("p", "muted", s.custom ? t("about.shareCustom") : (s.default ? t("about.shareDefault") : t("about.shareNoDefault")));
  const err = el("p", "error hidden");
  const save = el("button", "", t("about.shareSave")); save.type = "button";
  const reset = el("button", "", t("about.shareReset")); reset.type = "button"; reset.disabled = !s.custom;
  const run = async url => {
    err.classList.add("hidden");
    save.disabled = reset.disabled = true;
    try { await about.saveShareUrl(url); }
    catch (e) { err.textContent = e.message; err.classList.remove("hidden"); save.disabled = false; reset.disabled = !s.custom; }
  };
  // Gleicher Wert wie die Vorgabe = keine eigene Adresse, sonst bliebe sie hängen, wenn der Stack später ändert
  save.addEventListener("click", () => { const v = input.value.trim().replace(/\/+$/, ""); run(v === (s.default || "") ? "" : v); });
  input.addEventListener("keydown", e => { if (e.key === "Enter") save.click(); });
  reset.addEventListener("click", () => run(""));
  const row = el("div", "btnRow"); row.style.marginTop = "6px"; row.append(save, reset);
  box.append(label, input, hint, row, err);
  return box;
}

