// Kiosk-Modus, Gäste (Port 8080, Konzept docs/konzept-kiosk.md). Läuft vor dem Skript von puzzle.html:
// - fetch: /api/puzzles/… der App geht an /api/kiosk/puzzles/… mit dem Bearbeitungsschlüssel (X-Edit-Key)
// - Bearbeitungslink /edit/<id>#<schlüssel>: Schlüssel merken, Adresse auf /?id=<id>
// Nach dem Laden (DOMContentLoaded) wird die App gekürzt: keine Details, Listen, Einstellungen, kein Upload eigener Musik,
// Startseite mit Datenschutz-Haken, Website-Titel vor dem Link, Bearbeitungslink und grosser QR-Code beim Teilen.
// Aller Kiosk-Code steht hier, in kiosk.css und kiosk.py: die App selbst kennt nur den Haken {{MODE}}.
(() => {
  "use strict";
  // Seite erst zeigen, wenn klar ist, ob der Kiosk offen ist (sonst blitzt bei Pause die App kurz auf)
  document.documentElement.classList.add("kiosk", "kWait");
  const reveal = () => document.documentElement.classList.remove("kWait");
  setTimeout(reveal, 4000);
  const KEY = id => "kiosk-key-" + id;
  const keyOf = id => { try { return localStorage.getItem(KEY(id)) || ""; } catch { return ""; } };
  const setKey = (id, k) => { try { k ? localStorage.setItem(KEY(id), k) : localStorage.removeItem(KEY(id)); } catch { /* privat: nur diese Sitzung */ } };
  const OK = "kiosk-ok";  // Datenschutz bestätigt (pro Tab)
  const accepted = () => { try { return sessionStorage.getItem(OK) === "1"; } catch { return false; } };
  const accept = on => { try { on ? sessionStorage.setItem(OK, "1") : sessionStorage.removeItem(OK); } catch { /* egal */ } };
  const LAST = "kiosk-last";  // zuletzt bearbeitetes Puzzle auf diesem Gerät: «Weiter an deinem letzten Puzzle»
  const lastId = () => { try { return localStorage.getItem(LAST) || ""; } catch { return ""; } };
  const setLast = id => { try { id ? localStorage.setItem(LAST, id) : localStorage.removeItem(LAST); } catch { /* egal */ } };

  // ---------- Bearbeitungslink ----------
  const m = location.pathname.match(/^\/edit\/([A-Za-z0-9_-]+)\/?$/);
  if (m) {
    const k = decodeURIComponent(location.hash.slice(1));
    if (k) setKey(m[1], k);
    accept(true);
    history.replaceState(null, "", "/?id=" + m[1]);
  }

  // ---------- API umleiten ----------
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts = {}) => {
    if (typeof url !== "string") return realFetch(url, opts);
    const method = (opts.method || "GET").toUpperCase();
    if (url === "/api/puzzles" && method === "POST") {
      const res = await realFetch("/api/kiosk/puzzles", opts);
      if (res.ok) { const d = await res.clone().json().catch(() => ({})); if (d.id && d.key) setKey(d.id, d.key); }
      return res;
    }
    const p = url.match(/^\/api\/puzzles\/([A-Za-z0-9_-]+)(\/link)?$/);
    if (p) {
      const headers = { ...(opts.headers || {}), "X-Edit-Key": keyOf(p[1]) };
      let body = opts.body;
      if (p[2] && method === "POST" && body && !(st && st.link)) {  // neuer Link: Titel aus dem Feld vor «Link erstellen»
        try { const b = JSON.parse(body); if (!("title" in b) && kTitle) { b.title = kTitle.value.trim(); body = JSON.stringify(b); } } catch { /* unverändert */ }
      }
      const res = await realFetch("/api/kiosk/puzzles/" + p[1] + (p[2] || ""), { ...opts, headers, body });
      if (res.ok && method === "DELETE" && !p[2]) { setKey(p[1], ""); if (lastId() === p[1]) setLast(""); }
      if (res.status === 404 && !p[2] && method === "GET") { gone = true; if (lastId() === p[1]) setLast(""); if (showGone) showGone(); }
      return res;
    }
    return realFetch(url, opts);
  };

  let status = null, kTitle = null, gone = false, showGone = null;
  const statusLoad = realFetch("/api/kiosk").then(r => r.json()).then(s => { status = s; }).catch(() => { status = null; });

  // ---------- QR-Code (Byte-Modus, Stufe M, Version 1–6, Maske 0; wie docs/design/mockups/puzzle/src/qr.py) ----------
  const QM = { 1: [16, 10, 1], 2: [28, 16, 1], 3: [44, 26, 1], 4: [64, 18, 2], 5: [86, 24, 2], 6: [108, 16, 4] };
  const QA = { 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34] };
  const mul = (x, y) => { let z = 0; for (let i = 7; i >= 0; i--) { z = (z << 1) ^ ((z >>> 7) * 0x11D); z ^= ((y >>> i) & 1) * x; } return z & 0xFF; };
  function divisor(deg) {
    const r = new Array(deg - 1).fill(0).concat([1]); let root = 1;
    for (let n = 0; n < deg; n++) {
      for (let j = 0; j < deg; j++) { r[j] = mul(r[j], root); if (j + 1 < deg) r[j] ^= r[j + 1]; }
      root = mul(root, 2);
    }
    return r;
  }
  function remainder(data, div) {
    const r = new Array(div.length).fill(0);
    for (const b of data) { const f = b ^ r.shift(); r.push(0); div.forEach((c, i) => { r[i] ^= mul(c, f); }); }
    return r;
  }
  function qrMatrix(text) {
    const raw = [...new TextEncoder().encode(text)];
    const ver = Object.keys(QM).map(Number).find(v => raw.length + 2 <= QM[v][0]);
    if (!ver) return null;
    const [ndata, necc, nblk] = QM[ver];
    let bits = [0, 1, 0, 0]; for (let i = 7; i >= 0; i--) bits.push((raw.length >>> i) & 1);
    for (const b of raw) for (let i = 7; i >= 0; i--) bits.push((b >>> i) & 1);
    bits = bits.concat(new Array(Math.min(4, ndata * 8 - bits.length)).fill(0));
    while (bits.length % 8) bits.push(0);
    const data = []; for (let i = 0; i < bits.length; i += 8) data.push(parseInt(bits.slice(i, i + 8).join(""), 2));
    for (let pad = 0xEC; data.length < ndata; pad ^= 0xEC ^ 0x11) data.push(pad);
    const blen = ndata / nblk, div = divisor(necc);
    const blocks = Array.from({ length: nblk }, (_, i) => data.slice(i * blen, (i + 1) * blen)), eccs = blocks.map(b => remainder(b, div));
    const cw = [];
    for (let i = 0; i < blen; i++) for (const b of blocks) cw.push(b[i]);
    for (let i = 0; i < necc; i++) for (const e of eccs) cw.push(e[i]);
    const n = ver * 4 + 17, mx = Array.from({ length: n }, () => new Array(n).fill(0)), fn = Array.from({ length: n }, () => new Array(n).fill(false));
    const put = (x, y, d) => { mx[y][x] = d ? 1 : 0; fn[y][x] = true; };
    for (let i = 0; i < n; i++) { put(6, i, i % 2 === 0); put(i, 6, i % 2 === 0); }
    for (const [cx, cy] of [[3, 3], [n - 4, 3], [3, n - 4]])
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx, y = cy + dy, d = Math.max(Math.abs(dx), Math.abs(dy));
        if (x >= 0 && x < n && y >= 0 && y < n) put(x, y, d !== 2 && d !== 4);
      }
    const al = QA[ver];
    al.forEach((ax, i) => al.forEach((ay, j) => {
      if ((i === 0 && j === 0) || (i === 0 && j === al.length - 1) || (i === al.length - 1 && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) put(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }));
    let rem = 0; for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const f = ((0 << 10) | rem) ^ 0x5412, bit = i => (f >>> i) & 1;
    for (let i = 0; i < 6; i++) put(8, i, bit(i));
    put(8, 7, bit(6)); put(8, 8, bit(7)); put(7, 8, bit(8));
    for (let i = 9; i < 15; i++) put(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) put(n - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) put(8, n - 15 + i, bit(i));
    put(8, n - 8, true);
    let k = 0;
    for (let right = n - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < n; vert++) for (let j = 0; j < 2; j++) {
        const x = right - j, up = ((right + 1) & 2) === 0, y = up ? n - 1 - vert : vert;
        if (!fn[y][x] && k < cw.length * 8) { mx[y][x] = (cw[k >>> 3] >>> (7 - (k & 7))) & 1; k++; }
      }
    }
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (!fn[y][x] && (x + y) % 2 === 0) mx[y][x] ^= 1;
    return mx;
  }
  function qrSvg(text) {
    const mx = qrMatrix(text); if (!mx) return null;
    const n = mx.length, q = 4, ns = "http://www.w3.org/2000/svg";
    let d = ""; mx.forEach((row, y) => row.forEach((v, x) => { if (v) d += `M${x + q},${y + q}h1v1h-1z`; }));
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${n + 2 * q} ${n + 2 * q}`); svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("role", "img"); svg.setAttribute("class", "kQr");
    const bg = document.createElementNS(ns, "rect"); bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", "#fff");
    const path = document.createElementNS(ns, "path"); path.setAttribute("d", d); path.setAttribute("fill", "#000");
    svg.append(bg, path);
    return svg;
  }
  window.kioskQr = qrMatrix;  // für Tests im Browser

  // ---------- Oberfläche kürzen, sobald puzzle.html fertig ist ----------
  const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const hide = (...ids) => ids.forEach(id => { const e = document.getElementById(id); if (e) e.classList.add("kOff"); });
  const boxOf = pno => { const b = document.querySelector(`.left .paneBtn[data-go="${pno}"]`); return b && b.closest(".box"); };
  const editUrl = () => st.id && keyOf(st.id) ? `${location.origin}/edit/${st.id}#${keyOf(st.id)}` : "";

  document.addEventListener("DOMContentLoaded", async () => {
    // Was Gäste nicht brauchen: Details, Listen, Protokoll, Einstellungen, Über, Profil, Immich, eigene Musik, Link-Optionen
    ["P27", "P22", "P9", "P30", "P11", "P12"].forEach(p => { const b = boxOf(p); if (b) b.classList.add("kOff"); });
    hide("familyBtn", "profile", "musicUpBtn", "imBtn", "mkDirect", "pwRow", "lkFrom", "lkUntil", "lkNone", "delRow");
    ["lkActive", "lkDl", "lkMeta", "lkPw"].forEach(id => { const r = $(id) && $(id).closest(".swRow"); if (r) r.classList.add("kOff"); });
    const fromLbl = document.querySelector('label[for="lkFrom"]'); if (fromLbl) fromLbl.classList.add("kOff");
    const tag = document.querySelector(".brandTag"); if (tag) { tag.dataset.i18n = "k.tag"; tag.textContent = t("k.tag"); }
    document.querySelector(".brand").removeAttribute("title");

    // Schritte: ohne «Puzzle Details»; alle Schritte stellt der Gast selbst ein
    STEP_ORDER.splice(STEP_ORDER.indexOf("P27"), 1);
    const wrapDefaults = window.applyDefaults;
    window.applyDefaults = function () { wrapDefaults(); st.steps = null; drawSteps(); };
    st.steps = null; drawSteps();

    // Link nur heute, bis morgen oder 7 Tage
    SHARE_DAYS.splice(3); $("lkDays").replaceChildren();

    await statusLoad;
    const s = status || { status: "open", music: false, limits: { photoMb: 16 }, titleDefault: "" };
    if (!s.music) {  // ohne Musik-Bibliothek kein Schritt Musik, keine Dauer «Wie die Musik»
      STEP_ORDER.splice(STEP_ORDER.indexOf("P26"), 1);
      const b = boxOf("P26"); if (b) b.classList.add("kOff");
      hide("durTabs", "musicCut"); document.querySelectorAll(".mkMusicSel").forEach(e => e.classList.add("kOff"));
      const dur = document.querySelector('#durTabs [data-d="own"]'); if (dur && dur.getAttribute("aria-selected") !== "true") dur.click();
    }
    // Schritte neu nummerieren (links und im Panelkopf)
    STEP_ORDER.forEach((pno, i) => {
      const b = boxOf(pno), p = panelOf(pno);
      [b && b.querySelector(".num"), p && p.querySelector(".paneHead .num")].forEach(e => { if (e) e.textContent = String(i + 1); });
    });

    // Foto: Grösse prüfen, bevor es geladen wird
    $("file").addEventListener("change", e => {
      const f = e.target.files[0];
      if (f && f.size > s.limits.photoMb * 1024 * 1024) { e.stopImmediatePropagation(); e.target.value = ""; toast(t("k.tooBig", { mb: s.limits.photoMb }), true); }
    }, true);

    // Teilen: Website-Titel vor «Link erstellen» (Vorgabe aus dem Admin), danach Bearbeitungslink und QR-Code
    const before = $("shareBefore");
    const lbl = mk("label", "lbl", t("u.webTitle")); lbl.htmlFor = "kTitle"; lbl.dataset.i18n = "u.webTitle"; lbl.style.marginTop = "0";
    kTitle = mk("input"); kTitle.id = "kTitle"; kTitle.type = "text"; kTitle.maxLength = 120; kTitle.autocomplete = "off"; kTitle.value = s.titleDefault || "";
    const tnote = mk("p", "muted mkNote", t("k.titleNote")); tnote.dataset.i18n = "k.titleNote";
    before.prepend(lbl, kTitle, tnote);
    // Hinweise der App, die im Kiosk nicht stimmen (keine Box «Puzzle-Details», Musik nur mit Bibliothek)
    document.querySelectorAll('[data-i18n="u.webTitleNote"]').forEach(e => { e.dataset.i18n = "k.titleNote"; e.textContent = t("k.titleNote"); });
    if (!s.music) document.querySelectorAll('[data-i18n="p.shareIntro"]').forEach(e => { e.dataset.i18n = "k.shareIntro"; e.textContent = t("k.shareIntro"); });
    const after = $("shareAfter"), qrBox = mk("div", "kQrBox"), ed = mk("div", "kEdit");
    const qrHint = mk("p", "muted mkNote", t("k.qrHint")); qrHint.dataset.i18n = "k.qrHint";
    qrBox.append(mk("div", "kQrImg"), qrHint);
    const edHead = mk("h3", "", t("k.editHead")); edHead.dataset.i18n = "k.editHead";
    const edNote = mk("p", "muted mkNote", t("k.editNote")); edNote.dataset.i18n = "k.editNote";
    const edRow = mk("div", "linkRow"), edTxt = mk("code"); edTxt.id = "kEditTxt";
    const edCpy = mk("button", "", t("k.copy")); edCpy.type = "button"; edCpy.dataset.i18n = "k.copy";
    edCpy.onclick = () => copyText(editUrl());
    edRow.append(edTxt, edCpy);
    const edDel = mk("button", "danger", t("k.delete")); edDel.type = "button"; edDel.dataset.i18n = "k.delete";
    edDel.onclick = () => deletePuzzle(st.id);
    const edBtns = mk("div", "btnRow"); edBtns.append(edDel);
    ed.append(edHead, edNote, edRow, edBtns);
    after.prepend(qrBox);
    after.append(ed);
    // Löschen auch ohne Link (Entwurf)
    const draftDel = mk("div", "btnRow kDraftDel"), dd = mk("button", "danger", t("k.delete")); dd.type = "button"; dd.dataset.i18n = "k.delete";
    dd.onclick = () => deletePuzzle(st.id); draftDel.append(dd); before.append(draftDel);

    let lastQr = "", flowHook = () => {};
    const drawKiosk = () => {
      const url = linkUrl();
      if (url !== lastQr) {
        lastQr = url; const holder = qrBox.querySelector(".kQrImg"), svg = url ? qrSvg(new URL(url, location.href).href) : null;
        holder.replaceChildren(...(svg ? [svg] : [])); qrBox.classList.toggle("kOff", !svg);
      }
      edTxt.textContent = editUrl();
      ed.classList.toggle("kOff", !editUrl());
      draftDel.classList.toggle("kOff", !st.id);
      if (st.id && keyOf(st.id)) setLast(st.id);
      flowHook();
    };
    const wrapLink = window.drawLink, wrapState = window.drawState;
    window.drawLink = function () { wrapLink(); drawKiosk(); };
    window.drawState = function () { wrapState(); drawKiosk(); };

    // Gelöschtes Puzzle: nach Löschen zurück zur Startseite
    const wrapDelete = window.deletePuzzle;
    window.deletePuzzle = async function (id) { await wrapDelete(id); if (!st.id) { go("HOME"); } };

    // Neues Puzzle: gleich zum Motiv (ohne Details)
    $("newBtn").onclick = async () => { await save().catch(fail); resetEditor(); go(accepted() ? "P2" : "HOME"); };

    // Startseite P28: Willkommen, Datenschutz, Start
    const pane = $("welcomePane");
    [...pane.children].forEach(c => { if (!c.classList.contains("hmHero")) c.classList.add("kOff"); });
    const heroT = pane.querySelector(".hmHero h3"), heroP = pane.querySelector(".hmHero p");
    heroT.dataset.i18n = "k.welcome"; heroT.textContent = t("k.welcome");
    heroP.dataset.i18n = "k.welcomeText"; heroP.textContent = t("k.welcomeText");
    const start = mk("div", "kStart");
    const steps = mk("ol", "kSteps");
    ["k.s1", "k.s2", "k.s3"].forEach(key => { const li = mk("li", "", t(key)); li.dataset.i18n = key; steps.append(li); });
    const notice = mk("p", "kNotice kOff");
    const ok = mk("label", "kOk"), box = mk("input"); box.type = "checkbox"; box.id = "kOkBox"; box.checked = accepted();
    const okText = mk("span"); okText.dataset.i18nHtml = "k.privacyOk"; okText.innerHTML = t("k.privacyOk");
    ok.append(box, okText);
    const go2 = mk("button", "primary kGo", t("k.start")); go2.type = "button"; go2.dataset.i18n = "k.start";
    const facts = mk("p", "muted mkNote kFacts");
    const drawFacts = () => { facts.textContent = t("k.facts", { mb: s.limits.photoMb }); };
    drawFacts(); onLang(drawFacts);
    const foot = mk("p", "muted kFoot"), pl = mk("a", "", t("k.privacy")); pl.href = "/datenschutz"; pl.dataset.i18n = "k.privacy"; foot.append(pl);
    start.append(steps, notice, ok, go2, facts, foot);
    pane.append(start);
    const closed = s.status !== "open";
    // Kiosk aus (S2) oder voll (S3): eigene Überschrift und Text statt Schritte, Häkchen und Knopf (Mockup kiosk.html)
    const ver = ((document.querySelector('script[src*="kiosk.js"]') || {}).src || "").split("v=")[1] || "";
    // Karte mitten auf der Seite, ohne Kopfzeile (Mockup kiosk.html S1–S3): Logo, «Puzzle», Inhalt
    const gateCard = (cls, rows) => {
      const wrap = mk("div", "kGate " + cls), card = mk("div", "kGateCard");
      card.innerHTML = '<div class="kGateLogo" aria-hidden="true"><img class="lt" src="/brand/puzzle-logo-light.svg" alt="">'
        + '<img class="dk" src="/brand/puzzle-logo-dark.svg" alt=""></div>';
      const kick = mk("p", "kicker", t("k.pauseKicker")); kick.dataset.i18n = "k.pauseKicker"; card.append(kick);
      rows.forEach(r => {
        if (!Array.isArray(r)) { card.append(r); return; }
        const e = mk(r[0], r[1], t(r[2])); e.dataset.i18n = r[2]; card.append(e);
      });
      // Fusszeile: Version · Datenschutz · andere Sprache
      const foot = mk("p", "kGateFoot");
      const priv = mk("a", "", t("k.privacy")); priv.href = "/datenschutz"; priv.dataset.i18n = "k.privacy";
      const lang = mk("a", "", t("k.otherLang")); lang.href = "#"; lang.dataset.i18n = "k.otherLang";
      lang.onclick = e => { e.preventDefault(); setLang(t("k.otherLangCode")); };
      foot.append(`Puzzle ${ver} · `, priv, " · ", lang);
      wrap.append(card, foot); document.body.append(wrap);
      return { wrap, card };
    };
    if (closed) {
      // Kiosk aus (S2) oder voll (S3)
      const k = s.status === "full" ? "k.full" : "k.paused";
      gateCard("kPause", [["h1", "", k + "Head"], ["p", "s", k], ["p", "h", k + "More"]]);
      document.documentElement.classList.add("kClosed");
    }
    // Start (S1): bis der Datenschutz bestätigt ist, nur diese Karte; danach die App
    const demo = mk("div", "kDemo"); demo.setAttribute("aria-hidden", "true");
    for (let r = 0, n = 0; r < 4; r++) for (let c = 0; c < 5; c++, n++) {
      const i = mk("i"), h = (n * 7919) % 997;
      i.style.cssText = `--x:${c * 25}%;--y:${r * 33.333}%;--dx:${(h % 320) - 160}px;--dy:${(h * 3 % 240) - 120}px;`
        + `--r:${(h * 7 % 180) - 90}deg;--d:${(n * .11 + (h % 25) / 100).toFixed(2)}s`;
      demo.append(i);
    }
    const chips = mk("ul", "kChips");
    ["k.chip1", "k.chip2", "k.chip3"].forEach(key => { const li = mk("li", "", t(key)); li.dataset.i18n = key; chips.append(li); });
    const gNotice = mk("p", "kErr kOff");
    const gOk = mk("label", "kConsent"), gBox = mk("input"); gBox.type = "checkbox";
    const gText = mk("span"); gText.dataset.i18nHtml = "k.consent"; gText.innerHTML = t("k.consent");
    gOk.append(gBox, gText);
    const gGo = mk("button", "primary kGo", t("k.upload")); gGo.type = "button"; gGo.dataset.i18n = "k.upload"; gGo.disabled = true;
    const gNote = mk("p", "h kSmall");
    const drawNote = () => { gNote.textContent = t("k.uploadNote", { mb: s.limits.photoMb }); };
    drawNote(); onLang(drawNote);
    // Weiter am letzten Puzzle dieses Geräts (Bearbeitungsschlüssel liegt schon im Browser, V6/F13)
    const cont = mk("a", "kCont", t("k.continue")); cont.dataset.i18n = "k.continue";
    const lid = lastId(); if (lid && keyOf(lid)) cont.href = `/edit/${lid}#${encodeURIComponent(keyOf(lid))}`; else cont.classList.add("kOff");
    gateCard("kStartGate", [["h1", "", "k.tag"], demo, ["p", "s", "k.intro"], chips, gNotice, gOk, gGo, gNote, cont]);
    const drawGate = () => { document.documentElement.classList.toggle("kStart", !closed && !accepted() && !st.id); };
    gBox.onchange = () => { gGo.disabled = !gBox.checked; };
    gGo.onclick = () => { if (!gBox.checked) return; accept(true); box.checked = true; drawGo(); drawGate(); go("P2"); if (!st.img) $("file").click(); };
    drawGate();
    reveal();
    showGone = () => {
      notice.textContent = t("k.gone"); notice.dataset.i18n = "k.gone"; notice.classList.remove("kOff");
      gNotice.textContent = t("k.gone"); gNotice.dataset.i18n = "k.gone"; gNotice.classList.remove("kOff");
    };
    if (gone) showGone();
    const drawGo = () => { go2.disabled = closed || !box.checked; };
    box.onchange = () => { accept(box.checked); drawGo(); if (!box.checked) gBox.checked = false, gGo.disabled = true, drawGate(); };
    drawGo();
    // Zurück aus dem bfcache: Häkchen bleibt gesetzt, Knöpfe nachziehen
    window.addEventListener("pageshow", () => { gGo.disabled = !gBox.checked; drawGo(); });
    go2.onclick = () => { if (!box.checked) return; accept(true); go("P2"); };

    // ---------- Drei Schritte: Foto · Gestalten · Teilen (Roger 23.11.2026, Mockup kiosk.html «V») ----------
    // Keine Spalte mit neun Kästen mehr: immer genau ein Schritt-Panel, oben «Schritt n von 3», unten Zurück/Weiter.
    // Gestalten zeigt die Vorschau mit Teilen, Gesamtzeit und Gruss; der Rest liegt unter «Erweitert». Weiter erstellt den Link.
    const FLOW = ["P2", "P3", "P7"], FLOW_KEYS = ["k.stepPhoto", "k.stepDesign", "k.stepShare"], IN_FLOW = ["P2", "P19", "P26", "P3", "P7"];
    const html = document.documentElement;
    STEP_ORDER.splice(0, STEP_ORDER.length, ...FLOW);
    const flow = () => !!st.id || (!closed && accepted());
    const curStep = () => !st.img ? "P2" : st.link ? "P7" : "P3";
    const okStack = () => stack.length > 0 && FLOW.includes(stack[0]) && stack.every(x => IN_FLOW.includes(x));
    const label = (e, key) => { if (e) { e.dataset.i18n = key; e.textContent = t(key); } return e; };
    const button = (key, cls) => { const b = mk("button", cls || "", t(key)); b.type = "button"; b.dataset.i18n = key; return b; };
    st.tx = "text";
    // Kopf: Titel des Schritts und Fortschritt
    const progs = [];
    FLOW.forEach((pno, i) => {
      const head = panelOf(pno).querySelector(".paneHead");
      label(head.querySelector("h2 [data-i18n]:not(.num):not(.tick)"), FLOW_KEYS[i]);
      const prog = mk("nav", "kProg"), n = mk("span", "kProgN"), ol = mk("ol");
      FLOW.forEach((q, j) => { const li = mk("li"), b = button(FLOW_KEYS[j]); b.dataset.go = q; li.append(b); ol.append(li); });
      prog.append(n, ol); head.after(prog); progs.push({ n, ol, i });
    });
    const drawProg = () => progs.forEach(({ n, ol, i }) => {
      n.textContent = t("k.stepOf", { n: i + 1, m: FLOW.length });
      const done = [!!st.img, !!st.link, false];
      [...ol.children].forEach((li, j) => {
        li.classList.toggle("on", j === i); li.classList.toggle("done", done[j] && j !== i);
        li.firstChild.disabled = j > 0 && !st.img;
      });
    });
    onLang(drawProg);

    // 1 Foto: gewähltes Foto gross, «Foto wählen» / «Anderes Foto», «Ausschnitt anpassen»
    $("motifBody").prepend($("motifNow"));
    const cropBtn = button("k.cropBtn"); cropBtn.dataset.go = "P19"; cropBtn.dataset.stack = "push";
    $("uploadBtn").after(cropBtn);
    const wrapAfter = window.afterMotif;
    window.afterMotif = function () { if (st.quick || !flow()) return wrapAfter(); st.cropDone = true; go("P2"); drawFlow(); };

    // 2 Gestalten: Vorschau, Anzahl Teile, Gesamtzeit, Gruss; «Erweitert» eingeklappt
    const p3 = panelOf("P3"), oldBody = p3.querySelector(".mkBody");
    const gest = mk("div", "mkBody kGest"), pv = mk("div", "kPv"), side = mk("div", "kSide"), ctl = mk("div", "kCtl");
    pv.append($("pWrap"), panelOf("P8").querySelector(".player"));
    ctl.append(label(oldBody.querySelector('[data-i18n="p.count"]'), "k.count"), $("counts"), $("gridInfo"));
    if (s.music) ctl.append(...document.querySelectorAll("#sideTempo .mkMusicSel"), $("durTabs"), $("musicCut"));
    label($("ownDur").querySelector('label[for="durSec"]'), "k.dur"); ctl.append($("ownDur"));
    const gL = mk("label", "lbl"); gL.htmlFor = "kGreet"; label(gL, "k.greet");
    const gT = mk("textarea"); gT.id = "kGreet"; gT.rows = 2; gT.maxLength = 300; gT.dataset.i18nPlaceholder = "k.greetPh"; gT.placeholder = t("k.greetPh");
    gT.oninput = () => { st.tx = "text"; $("txt").value = gT.value; $("txt").oninput(); };
    ctl.append(gL, gT, label(mk("p", "muted mkNote"), "k.greetNote"));
    const more = mk("details", "kMore"), sec = key => label(mk("h3", "paneSection"), key);
    const ramp = $("mkRamp"), rampR = ramp.nextElementSibling;
    const fonts = mk("div", "mkFont kFonts"); fontButtons(fonts, ["hand", "elegant", "sans"]);
    const ghostRow = $("optGhost").closest(".swRow"); label(ghostRow.querySelector("span"), "k.ghost");
    more.append(label(mk("summary"), "k.more"),
      sec("k.tempo"), document.querySelector('label[for="mkLead"]'), $("mkLead"), $("mkFrameOnly"), document.querySelector('label[for="mkRamp"]'), ramp, rampR,
      oldBody.querySelector('[data-i18n="p.startLbl"]'), $("startPick"), oldBody.querySelector('[data-i18n="p.edgesLbl"]'), $("edgePick"), $("optFrame").closest(".swRow"),
      sec("k.font"), fonts, $("txtCol").previousElementSibling, $("txtCol"), ghostRow, $("ghostOpts"));
    side.append(ctl, more); gest.append(side, pv);
    oldBody.before(gest); oldBody.classList.add("kOff");
    const enterP3 = ENTER.P3;
    ENTER.P3 = () => {
      enterP3(); st.visited.P4 = true; gT.value = (st.T.text && st.T.text.text) || ""; drawTextPanel();
      requestAnimationFrame(() => { if (!P.cfg) return; P.resize(); if (!playing) { P.t = 0; $("pPlay").click(); } });
    };
    LEAVE.P3 = () => pausePreview();
    addEventListener("resize", () => { if (P.cfg && shown.includes("P3")) P.resize(); });
    // Neues Puzzle: 30 s statt 45 s, ausser der Admin hat einen Standard gesetzt
    const kioskDefaults = window.applyDefaults;
    window.applyDefaults = function () {
      kioskDefaults();
      if (!st.img && !(st.defaults && st.defaults.config && "T" in st.defaults.config)) { $("durSec").value = 30; drawControls(); }
    };
    if (!st.img && !st.id) window.applyDefaults();

    // 3 Teilen: «Dein Puzzle ist bereit», Link, Teilen, QR, Bearbeitungslink; Titel und Gültigkeit unter «Erweitert»
    const ready = mk("div", "kReady"), readyP = mk("p", "muted");
    ready.append(label(mk("h3"), "k.ready"), readyP);
    const wTitle = $("mkWebTitle"), wNote = wTitle.nextElementSibling;
    const sMore = mk("details", "kMore");
    sMore.append(label(mk("summary"), "k.more"), document.querySelector('label[for="mkWebTitle"]'), wTitle, wNote,
      document.querySelector('label[for="lkDays"]'), $("lkDays"), $("linkState"));
    const saved = mk("label", "kSaved"), sBox = mk("input"); sBox.type = "checkbox"; sBox.id = "kSaved";
    saved.append(sBox, label(mk("span"), "k.saved"));
    edBtns.replaceWith(saved);
    const danger = mk("div", "btnRow kDanger"); danger.append($("rmLink"), edDel);
    after.prepend(ready, $("linkTxt").closest(".linkRow"), $("shareBtn").closest(".btnRow"), qrBox, ed);
    after.append(sMore, danger);
    // «Download erlauben (Video)» (0.1.17): nur wenn der Admin den Video-Download eingeschaltet hat; Foto bleibt gesperrt
    if (s.video === true) { const vr = $("lkVidRow"); vr.classList.remove("hidden"); $("shareBtn").closest(".btnRow").after(vr); }
    after.querySelector(".ogCard").classList.add("kOff");
    // «Neues Puzzle»: Rückfrage, solange der Bearbeitungslink nicht gesichert ist
    const doneRow = panelOf("P7").querySelector(":scope > .doneRow");
    const kNew = button("k.new"), ask = mk("div", "kAsk kOff"), askBtns = mk("div", "btnRow");
    const askYes = button("k.newYes", "danger"), askNo = button("u.cancel");
    askBtns.append(askNo, askYes); ask.append(label(mk("p"), "k.newAsk"), askBtns);
    doneRow.append(kNew); doneRow.before(ask);
    kNew.onclick = () => { if (editUrl() && !sBox.checked) { ask.classList.remove("kOff"); askNo.focus(); return; } $("newBtn").click(); };
    askNo.onclick = () => ask.classList.add("kOff");
    askYes.onclick = () => { ask.classList.add("kOff"); $("newBtn").click(); };

    let savedFor = null;
    const drawFlow = () => {
      const has = !!st.img, up = $("uploadBtn");
      label(up, has ? "k.otherPhoto" : "k.pickPhoto"); up.classList.toggle("primary", !has);
      cropBtn.classList.toggle("kOff", !has);
      readyP.textContent = st.link && st.link.expires_at ? t("k.validUntil", { date: fmtDate(st.link.expires_at * 1000) }) : "";
      if (st.id !== savedFor) { savedFor = st.id; sBox.checked = false; ask.classList.add("kOff"); }
      drawProg();
    };
    flowHook = drawFlow; onLang(drawFlow);

    // Unten: Zurück / Weiter; bei Gestalten heisst Weiter «Puzzle fertigstellen», bei Teilen Zurück «Ändern»
    const wrapNav = window.stepNav;
    window.stepNav = function () {
      wrapNav();
      document.querySelectorAll(".mkP").forEach(p => {  // alte Schrittknöpfe (P19, P26 …) weg, dort gilt wieder «Fertig»
        if (FLOW.includes(p.dataset.pno)) return;
        const row = p.querySelector(":scope > .doneRow.stepRow"); if (!row) return;
        row.querySelectorAll(".stepPrev, .stepNext").forEach(b => b.remove()); row.classList.remove("stepRow");
      });
      const nav = (pno, cls) => panelOf(pno).querySelector(`:scope > .doneRow ${cls}`);
      const lg = (b, key) => { const e = b && b.querySelector(".lg"); if (e) e.textContent = t(key); };
      FLOW.forEach(p => { const b = nav(p, ".stepNext"); if (b) b.classList.add("primary"); });
      const n2 = nav("P2", ".stepNext"); if (n2) n2.disabled = !st.img;
      lg(nav("P3", ".stepNext"), st.link ? "k.toShare" : "k.finish");
      lg(nav("P7", ".stepPrev"), "k.change");
    };
    // Immer ein Schritt offen: leerer oder fremder Stapel (Adresse, Zurück im Browser) → aktueller Schritt
    const wrapPanels = window.drawPanels;
    window.drawPanels = function () {
      const on = flow(); html.classList.toggle("kFlow", on);
      if (on && navReady && !okStack()) {
        stack = [curStep()];
        const h = "#" + stack.join("/"); if (location.hash !== h) history.replaceState(null, "", location.pathname + location.search + h);
      }
      wrapPanels();
      if (on) drawFlow();
    };
    const navWait = setInterval(() => { if (!navReady) return; clearInterval(navWait); drawPanels(); }, 100);

    // Ohne Datenschutz-Haken bleibt man auf der Startseite
    const wrapGo = window.go;
    window.go = function (target, from) {
      if (closed && !st.id && !["HOME", "CLOSE", "P28"].includes(target)) { toast(t(s.status === "full" ? "k.fullHead" : "k.pausedHead"), true); return wrapGo("HOME"); }
      if (!accepted() && !st.id && !["HOME", "CLOSE", "P28"].includes(target)) { toast(t("k.needOk"), true); box.focus(); return wrapGo("HOME"); }
      let mkNow = false;
      if (flow()) {
        if (target === "HOME") target = curStep();
        else if (target === "CLOSE" && stack.length <= 1) return;
        if ((target === "P3" || target === "P7") && !st.img) { toast(t("p.needMotif"), true); target = "P2"; }
        mkNow = target === "P7" && !!st.img && !st.link && !!(from && from.classList && from.classList.contains("stepNext"));
      }
      const r = wrapGo(target, from); drawGate();
      if (mkNow) $("mkLink").click();  // «Puzzle fertigstellen» erstellt den Link
      return r;
    };

    stepNav(); drawPanels(); drawKiosk();
  });
})();
