// Profil-Kreis rechts in der Kopfzeile, immer da (Familienregel, Entscheid 20 vom 15.11.2026). Er ersetzt den Sonne/Mond-Knopf
// und den Sprachschalter EN|DE: Darstellung und Sprache stehen im Menü.
// Platz: <span id="profile" data-letter="…" data-service="…"></span> in der Kopfzeile. data-letter ist der
// Dienstbuchstabe, data-service der Dienst unter «Angemeldet als …». Jede Seite setzt beide; fehlt data-service, fällt die
// Zeile mit dem Dienst weg (kein Rückfall auf einen Markennamen).
// - Mit Login (LOGIN_PASSWORD): Initialen aus LOGIN_NAME («Roger Heckly» → RH), sonst der erste Buchstabe von LOGIN_USER.
//   Menü: Name, «Angemeldet als …», Dienst, dann Darstellung, Sprache, Trennlinie, «Abmelden».
// - Ohne Login: der Dienstbuchstabe auf der Akzentfläche, Menü nur mit Darstellung und Sprache.
// Darstellung und Sprache sind Segment-Zeilen: aktive Wahl gefüllt mit aria-pressed, ein Klick wirkt sofort, das Menü bleibt offen.
// Gespeichert: localStorage "theme", danach applyTheme() aus dem Kopf der Seite. Fehlt applyTheme, setzt profile.js data-theme
// am <html> selbst; drawTheme() wird aufgerufen, wenn es sie gibt. Sprache über setLang() aus i18n.js.
// Ohne eigene Wahl gilt das System, dann ist keine Darstellung markiert.
// Elemente mit der Klasse .themeLang blendet es aus (ältere Seiten mit eigenem Schalter).
// Optionen am Platz, ohne sie gilt der Standard:
// - data-session="off": keine Abfrage von /api/auth/session, gilt direkt als «ohne Login». Für Seiten, auf denen die
//   Sitzung nicht zum Dienst gehört (z. B. ein Freigabelink).
// - data-logout="/pfad": Abmelden per POST an diesen Pfad, Standard /api/auth/logout. Eine Weiterleitung als Antwort
//   (z. B. 303) wird nicht verfolgt, danach geht es wie immer auf /login.
// Farben aus den App-Tokens (--card, --line, --text, --muted, --accent, --bg; Design-Entscheid 2), nur der Schatten ist eigen.
// Braucht t(), onLang(), setLang(), lang, LANGS und TEXTS aus i18n.js
(() => {
  const css = `
  #profile { position:relative; display:flex; flex:none; margin-left:auto; }
  #profileBtn { width:40px; height:40px; min-width:0; border-radius:50%; display:grid; place-items:center; padding:0; margin:0;
    font:600 14px system-ui, sans-serif; color:var(--on-accent); background:var(--accent);
    border:2px solid transparent; cursor:pointer; }
  #profileBtn:hover { background:var(--accent); }
  #profileBtn[aria-expanded="true"], #profileBtn:focus-visible { outline:none; box-shadow:0 0 0 2px var(--card), 0 0 0 4px var(--accent); }
  #profileMenu { position:absolute; right:0; top:calc(100% + 10px); width:280px; max-width:calc(100vw - 16px);
    background:var(--card); color:var(--text); border:1px solid var(--line); border-radius:12px;
    box-shadow:0 12px 40px var(--pm-shadow); padding:8px; z-index:50; text-align:left; font:15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  #profileMenu[hidden] { display:none; }
  #profileMenu .pm-who { display:flex; gap:12px; align-items:center; padding:10px 10px 14px; border-bottom:1px solid var(--line); margin-bottom:4px; }
  #profileMenu .pm-who .pm-circle { width:44px; height:44px; flex:none; border-radius:50%; display:grid; place-items:center;
    font:600 15px system-ui, sans-serif; color:var(--on-accent); background:var(--accent); }
  #profileMenu .pm-name { font-weight:700; font-size:16px; overflow-wrap:anywhere; }
  #profileMenu .pm-sub { font-size:13px; color:var(--muted); line-height:1.4; overflow-wrap:anywhere; }
  #profileMenu .pm-row { padding:6px 10px 8px; }
  #profileMenu .pm-row .pm-label { display:block; font-size:13px; color:var(--muted); margin-bottom:4px; }
  #profileMenu .pm-seg { display:flex; height:30px; border:1px solid var(--line); border-radius:6px; overflow:hidden; }
  #profileMenu .pm-seg button { flex:1 1 0; min-width:0; height:auto; margin:0; padding:0 8px; border:0; border-radius:0;
    font:13px system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--text); background:var(--bg); cursor:pointer; }
  #profileMenu .pm-seg button + button { border-left:1px solid var(--line); }
  #profileMenu .pm-seg button:hover { background:color-mix(in srgb, var(--accent) 12%, var(--bg)); }
  #profileMenu .pm-seg button[aria-pressed="true"] { background:var(--accent); color:var(--on-accent); font-weight:600; }
  #profileMenu .pm-seg button:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
  #profileMenu .pm-seg button[aria-pressed="true"]:focus-visible { outline-color:var(--on-accent); outline-offset:-4px; }
  #profileMenu .pm-item { display:flex; align-items:center; gap:10px; width:100%; min-width:0; height:auto; margin:0; padding:10px 12px; border-radius:8px;
    font:15px system-ui, -apple-system, "Segoe UI", sans-serif; color:var(--text); background:none;
    border:2px solid transparent; cursor:pointer; text-align:left; }
  #profileMenu .pm-item:hover, #profileMenu .pm-item:focus { outline:none; background:color-mix(in srgb, var(--accent) 10%, transparent); border-color:var(--accent); }
  #profileMenu .pm-item svg { width:18px; height:18px; flex:none; stroke:currentColor; fill:none; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
  #profileMenu .pm-line { border-top:1px solid var(--line); margin:6px 0; }
  .themeLang { display:none !important; }
  :root { --pm-shadow:rgba(0,0,0,.18); }
  :root[data-theme=dark] { --pm-shadow:rgba(0,0,0,.6); }
  @media (prefers-color-scheme:dark) { :root:not([data-theme=light]) { --pm-shadow:rgba(0,0,0,.6); } }
  `;
  const LOGOUT_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>';
  const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };

  // «Roger Heckly» → RH, «Anna» → A; ohne Anzeigename der erste Buchstabe des Benutzernamens
  function initials(name, user) {
    const words = String(name || "").trim().split(/\s+/).filter(Boolean);
    const first = s => (Array.from(s)[0] || "").toUpperCase();
    if (words.length > 1) return first(words[0]) + first(words[words.length - 1]);
    if (words.length === 1) return first(words[0]);
    return first(String(user || "")) || "?";
  }

  async function logout(path) {
    try { await fetch(path, { method: "POST", redirect: "manual" }); } catch { /* Die Login-Seite kommt trotzdem */ }
    location.replace("/login");  // ersetzt den Eintrag: «Zurück» führt nicht in die abgemeldete App (Entscheid 42)
  }

  // localStorage kann fehlen oder werfen (privater Modus): dann gilt die Wahl nur bis zum Neuladen
  function savedTheme() { try { return localStorage.getItem("theme"); } catch { return null; } }
  function saveTheme(value) {
    try { localStorage.setItem("theme", value); } catch { /* nur für diese Seite */ }
    if (typeof applyTheme === "function") applyTheme(); else document.documentElement.dataset.theme = value;
    if (typeof drawTheme === "function") drawTheme();
  }

  // Segment-Zeile: Beschriftung und Knöpfe über die volle Breite. options: [{ value, text() }], current() und pick(value)
  function segRow(id, options, current, pick) {
    const row = el("div", "pm-row"), label = el("span", "pm-label"), seg = el("div", "pm-seg");
    label.id = id;
    seg.setAttribute("role", "group"); seg.setAttribute("aria-labelledby", id);
    const buttons = options.map(o => {
      const b = el("button"); b.type = "button";
      b.addEventListener("click", () => { pick(o.value); draw(); });
      return b;
    });
    seg.append(...buttons); row.append(label, seg);
    function draw(text) {
      if (text !== undefined) label.textContent = text;
      const now = current();
      options.forEach((o, i) => { buttons[i].textContent = o.text(); buttons[i].setAttribute("aria-pressed", String(o.value === now)); });
    }
    return { row, draw };
  }

  function build(slot) {
    const style = el("style");
    style.textContent = css;
    document.head.append(style);

    const btn = el("button"), menu = el("div");
    btn.id = "profileBtn"; btn.type = "button"; btn.textContent = slot.dataset.letter || "M";
    btn.setAttribute("aria-haspopup", "true"); btn.setAttribute("aria-expanded", "false"); btn.setAttribute("aria-controls", "profileMenu");
    menu.id = "profileMenu"; menu.hidden = true;

    let picked = null;  // Falls localStorage nicht geht: die Wahl dieser Seite
    const theme = segRow("pmTheme",
      [{ value: "light", text: () => t("profile.light") }, { value: "dark", text: () => t("profile.dark") }],
      () => savedTheme() || picked,  // Ohne eigene Wahl (System) ist nichts markiert
      value => { picked = value; saveTheme(value); });
    const language = segRow("pmLang",
      LANGS.map(l => ({ value: l, text: () => (TEXTS[l] && TEXTS[l][`lang.${l}`]) || l.toUpperCase() })),  // Jede Sprache in sich selbst
      () => lang,
      value => setLang(value));
    menu.append(theme.row, language.row);
    slot.append(btn, menu);

    let session = null, sub, outText;
    function signedIn(s) {
      session = s;
      const label = initials(s.name, s.user);
      btn.textContent = label;
      const who = el("div", "pm-who"), circle = el("span", "pm-circle"), text = el("div"), nameEl = el("div", "pm-name");
      sub = el("div", "pm-sub");
      circle.textContent = label; circle.setAttribute("aria-hidden", "true");
      nameEl.textContent = s.name || s.user;
      text.append(nameEl, sub); who.append(circle, text);
      const out = el("button", "pm-item");
      out.type = "button"; out.id = "logout"; out.innerHTML = LOGOUT_ICON;
      outText = el("span"); out.append(outText);
      out.addEventListener("click", () => logout(slot.dataset.logout || "/api/auth/logout"));
      menu.prepend(who);
      menu.append(el("div", "pm-line"), out);
    }

    function draw() {
      const what = t(session ? "profile.menu" : "profile.settings");
      btn.title = what;
      btn.setAttribute("aria-label", session ? `${what}: ${session.name || session.user}` : what);
      menu.setAttribute("aria-label", what);
      theme.draw(t("profile.appearance"));
      language.draw(t("lang.label"));
      if (session) {
        const service = slot.dataset.service || "";
        sub.replaceChildren(t("profile.signedInAs", { user: session.user }), ...(service ? [el("br"), service] : []));
        outText.textContent = t("auth.logout");
      }
    }

    const items = () => Array.from(menu.querySelectorAll("button"));
    const focusFirst = () => { const all = items(); (all.find(b => b.getAttribute("aria-pressed") === "true") || all[0]).focus(); };
    const open = () => { theme.draw(); language.draw(); menu.hidden = false; btn.setAttribute("aria-expanded", "true"); };
    const close = focus => {
      if (menu.hidden) return;
      menu.hidden = true; btn.setAttribute("aria-expanded", "false");
      if (focus) btn.focus();
    };
    btn.addEventListener("click", () => { if (menu.hidden) { open(); focusFirst(); } else close(false); });
    btn.addEventListener("keydown", e => { if (e.key === "ArrowDown") { e.preventDefault(); open(); focusFirst(); } });
    // Pfeiltasten: links/rechts im Segment, hoch/runter durch alle Knöpfe des Menüs, Pos1/Ende
    menu.addEventListener("keydown", e => {
      const all = items(), now = document.activeElement, i = all.indexOf(now);
      if (i < 0) return;
      let next = null;
      if (e.key === "ArrowDown") next = all[(i + 1) % all.length];
      else if (e.key === "ArrowUp") next = all[(i - 1 + all.length) % all.length];
      else if (e.key === "ArrowRight") next = now.nextElementSibling;
      else if (e.key === "ArrowLeft") next = now.previousElementSibling;
      else if (e.key === "Home") next = all[0];
      else if (e.key === "End") next = all[all.length - 1];
      if (next && next.tagName === "BUTTON") { e.preventDefault(); next.focus(); }
    });
    document.addEventListener("click", e => { if (!slot.contains(e.target)) close(false); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !menu.hidden) { e.stopPropagation(); close(true); } }, true);
    slot.addEventListener("focusout", e => { if (e.relatedTarget && !slot.contains(e.relatedTarget)) close(false); });

    draw();
    onLang(draw);
    // Bis die Sitzung da ist, bleibt der Platz reserviert, aber leer: so springt der Kreis nicht vom Buchstaben auf die Initialen
    if (slot.dataset.session === "off") return;
    slot.style.visibility = "hidden";
    fetch("/api/auth/session").then(r => r.json()).then(s => {
      if (s && s.enabled && s.user) { signedIn(s); draw(); }
    }).catch(() => { /* Ohne Antwort wie ohne Login */ }).finally(() => { slot.style.visibility = ""; });
  }

  function start() {
    const slot = document.getElementById("profile");
    if (slot && !slot.firstChild) build(slot);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
