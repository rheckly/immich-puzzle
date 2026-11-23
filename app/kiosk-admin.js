// Kiosk-Modus, Admin (Port 8082): Box «Kiosk» links und Panel PK mit Ein/Aus, Zahlen und Aufräumjob; Block «Kiosk» in den
// Einstellungen (P11: Kontaktadresse für die Datenschutzseite, Vorgabe Website-Titel, Datenschutztext); «Über» zeigt «Modus: Kiosk». Läuft vor dem Skript von puzzle.html,
// baut sich nach dem Laden ein (DOMContentLoaded). Daten aus /api/kiosk/admin (kiosk.py).
(() => {
  "use strict";
  document.documentElement.classList.add("kioskAdmin");
  const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const tx = (e, key) => { e.dataset.i18n = key; e.textContent = t(key); return e; };
  let data = null;

  document.addEventListener("DOMContentLoaded", () => {
    about.mode = "kiosk";   // «Über»: Modus statt Immich-Status (common.js)
    const aboutSum = document.querySelector('.left .paneBtn[data-go="P12"]').closest(".box").querySelector("[data-i18n]:not(h2)");
    if (aboutSum) { aboutSum.dataset.i18n = "about.sumKiosk"; aboutSum.textContent = t("about.sumKiosk"); }
    // Box links, vor «Protokoll»
    const logBox = document.querySelector('.left .paneBtn[data-go="P30"]').closest(".box");
    const box = mk("section", "box"), head = mk("div", "boxHead"), h = tx(mk("h2", "boxTitle fixed"), "k.adm.title");
    const btn = mk("button", "paneBtn", ">"); btn.type = "button"; btn.dataset.go = "PK"; btn.title = t("pane.openRight"); btn.dataset.i18nTitle = "pane.openRight";
    head.append(h, btn); const sum = mk("p", "muted"); sum.id = "kSum"; box.append(head, sum);
    logBox.before(box);
    // Box «Kiosk-Status» zuoberst, über «Was möchtest du heute erstellen?» (Roger 22.11.2026): Schalter wie im Panel PK
    const stBox = mk("section", "box"); stBox.id = "kStatusBox";
    const stHead = mk("label", "boxHead kStatusHead"), stSw = mk("input", "sw"); stSw.type = "checkbox"; stSw.setAttribute("role", "switch");
    stSw.setAttribute("aria-label", t("k.adm.on")); stHead.append(tx(mk("h2", "boxTitle fixed"), "k.adm.statusHead"), stSw);
    const stTxt = mk("p", "muted"); stBox.append(stHead, stTxt);
    $("kindBox").before(stBox);

    // Panel
    const pane = mk("aside", "pane mkP kPk"); pane.dataset.pno = "PK"; pane.id = "sideKiosk";
    const ph = mk("div", "paneHead"), x = mk("button", "paneX", "×"); x.type = "button"; x.dataset.go = "CLOSE"; x.title = t("pane.close");
    ph.append(tx(mk("h2"), "k.adm.title"), x);
    const body = mk("div", "mkBody");
    // Ein/Aus
    const sw = mk("label", "swRow"), swIn = mk("input", "sw"); swIn.type = "checkbox"; swIn.setAttribute("role", "switch"); swIn.id = "kOn";
    sw.append(tx(mk("span"), "k.adm.on"), swIn);
    const state = mk("p", "muted mkNote"); state.id = "kState";
    // Zahlen
    const nums = mk("div", "kNums"); nums.id = "kNums";
    const limits = mk("p", "muted mkNote"); limits.id = "kLimits";
    const statsH = tx(mk("h3"), "k.adm.stats"), stats = mk("table", "kStats"); stats.id = "kStats";
    // Aufräumjob
    const jobH = tx(mk("h3"), "k.adm.job"), job = mk("p", "muted mkNote"); job.id = "kJob";
    const jobRow = mk("div", "btnRow"), jobBtn = tx(mk("button"), "k.adm.jobRun"); jobBtn.type = "button"; jobRow.append(jobBtn);
    const runs = mk("table", "kStats"); runs.id = "kRuns";
    // Notbremse: alles der letzten N Stunden löschen (Sicherheitsprüfung 0.1.9)
    const purgeH = tx(mk("h3"), "k.adm.purge"), purgeN = tx(mk("p", "muted mkNote"), "k.adm.purgeNote");
    const purgeRow = mk("div", "btnRow kPurge"), purgeL = tx(mk("label", "lbl"), "k.adm.purgeHours");
    const purgeIn = mk("input"); purgeIn.type = "number"; purgeIn.min = "1"; purgeIn.max = "720"; purgeIn.value = "2"; purgeIn.id = "kPurgeHours"; purgeL.htmlFor = "kPurgeHours";
    const purgeBtn = tx(mk("button", "danger"), "k.adm.purgeRun"); purgeBtn.type = "button";
    purgeRow.append(purgeL, purgeIn, purgeBtn);
    // Block «Kiosk» in den Einstellungen (P11), nach «Erstellt mit»
    const setBox = mk("div"); setBox.id = "kSettings";
    const setH = tx(mk("h3", "paneSection"), "k.adm.title");
    const cL = tx(mk("label", "lbl"), "k.adm.contact"); cL.htmlFor = "kContact";
    const cIn = mk("input"); cIn.id = "kContact"; cIn.type = "text"; cIn.maxLength = 200; cIn.autocomplete = "off";
    const cN = tx(mk("p", "muted mkNote"), "k.adm.contactNote");
    const tL = tx(mk("label", "lbl"), "k.adm.titleLbl"); tL.htmlFor = "kTitleDef";
    const tIn = mk("input"); tIn.id = "kTitleDef"; tIn.type = "text"; tIn.maxLength = 120; tIn.autocomplete = "off";
    const pL = tx(mk("label", "lbl"), "k.adm.privacy"); pL.htmlFor = "kPrivacy";
    const pIn = mk("textarea"); pIn.id = "kPrivacy"; pIn.maxLength = 6000;
    const pN = tx(mk("p", "muted mkNote"), "k.adm.privacyNote");
    const setRow = mk("div", "btnRow"), save = tx(mk("button", "primary"), "k.adm.save"), deflt = tx(mk("button"), "k.adm.privacyDefault");
    save.type = deflt.type = "button"; setRow.append(save, deflt);
    body.append(sw, state, nums, limits, statsH, stats, jobH, job, jobRow, runs, purgeH, purgeN, purgeRow);
    // Meldungen über ntfy (0.1.13): Zustand aus dem Stack, je Art ein Schalter (speichert sofort), Uhrzeit, Test und «Jetzt senden»
    const nH = tx(mk("h3", "paneSection"), "k.adm.ntfy.head"), nState = mk("p", "muted mkNote"); nState.id = "kNtfyState";
    const nSw = {};
    const nRows = ["limits", "errors", "summary"].map(k => {
      const r = mk("label", "swRow"), i = mk("input", "sw"); i.type = "checkbox"; i.setAttribute("role", "switch"); i.dataset.kind = k;
      r.append(tx(mk("span"), `k.adm.ntfy.${k}`), i); nSw[k] = i; return r;
    });
    const nTL = tx(mk("label", "lbl"), "k.adm.ntfy.time"); nTL.htmlFor = "kNtfyTime";
    const nTime = mk("input"); nTime.id = "kNtfyTime"; nTime.type = "time"; nTime.step = "60";
    const nTN = tx(mk("p", "muted mkNote"), "k.adm.ntfy.timeNote");
    const nRow = mk("div", "btnRow"), nTest = tx(mk("button"), "k.adm.ntfy.test"), nNow = tx(mk("button"), "k.adm.ntfy.now");
    nTest.type = nNow.type = "button"; nRow.append(nTest, nNow);
    const nLast = mk("p", "muted mkNote"); nLast.id = "kNtfyLast";
    setBox.append(setH, cL, cIn, cN, tL, tIn, pL, pIn, pN, setRow, nH, nState, ...nRows, nTL, nTime, nTN, nRow, nLast);
    $("settingsMore").after(setBox);
    const done = mk("div", "btnRow doneRow"), db = tx(mk("button", "paneDone"), "pane.done"); db.type = "button"; db.dataset.go = "CLOSE"; done.append(db);
    pane.append(ph, body, done);
    const last = [...document.querySelectorAll("main > .mkP")].pop(); last.after(pane);

    const row = (cells, th) => { const tr = mk("tr"); cells.forEach(c => tr.append(mk(th ? "th" : "td", "", c))); return tr; };
    const fmtN = n => (n || 0).toLocaleString();
    function draw() {
      if (!data) return;
      $("kSum").textContent = t(`k.adm.sum.${data.status}`, { n: data.puzzles, links: data.links });
      swIn.checked = stSw.checked = data.enabled;
      stTxt.textContent = t(`k.adm.state.${data.status}`);
      state.textContent = t(`k.adm.state.${data.status}`);
      nums.replaceChildren(...[["puzzles", data.puzzles], ["drafts", data.drafts], ["links", data.links], ["storage", `${data.storageMb} MB`], ["gone", data.gone]]
        .map(([k, v]) => { const d = mk("div"); d.append(mk("b", "", typeof v === "number" ? fmtN(v) : v), mk("span", "", t(`k.adm.n.${k}`))); return d; }));
      const L = data.limits;
      limits.textContent = t("k.adm.limits", { photo: L.photo_mb, hour: L.per_ip_hour, max: L.max_puzzles, storage: L.storage_mb, draft: L.draft_hours, every: L.clean_minutes });
      stats.replaceChildren(row([t("k.adm.c.day"), t("k.adm.c.created"), t("k.adm.c.links"), t("k.adm.c.views"), t("k.adm.c.rejected")], true),
        ...(data.stats.length ? data.stats.map(s => row([fmtDate(s.day + "T12:00:00"), fmtN(s.created), fmtN(s.links), fmtN(s.views), fmtN(s.rejected)]))
          : [row([t("k.adm.none"), "", "", "", ""])]));
      const rs = (data.job && data.job.runs) || [];
      job.textContent = rs.length ? t("k.adm.jobLast", { when: fmtDate(rs[0].when, { dateStyle: "medium", timeStyle: "short" }), every: L.clean_minutes }) : t("k.adm.jobNever", { every: L.clean_minutes });
      runs.replaceChildren(row([t("k.adm.c.when"), t("k.adm.c.drafts"), t("k.adm.c.expired"), t("k.adm.c.orphans"), t("k.adm.c.by")], true),
        ...rs.slice(0, 8).map(r => row([fmtDate(r.when, { dateStyle: "short", timeStyle: "short" }), fmtN(r.drafts), fmtN(r.expired), fmtN(r.orphans + r.tmp), r.by || ""])));
      if (document.activeElement !== cIn) cIn.value = data.contact;
      if (document.activeElement !== tIn) tIn.value = data.title;
      tIn.placeholder = data.titleDefault;
      if (document.activeElement !== pIn) pIn.value = data.privacy;
      pIn.placeholder = data.privacyDefault;
      const N = data.notify || {}, nt = N.ntfy || {};
      nState.textContent = nt.ready ? t(nt.token ? "k.adm.ntfy.ready" : "k.adm.ntfy.readyNoToken", { url: nt.url, topic: nt.topic }) : t("k.adm.ntfy.off");
      Object.entries(nSw).forEach(([k, i]) => { i.checked = !!N[k]; i.disabled = !nt.ready; });
      if (document.activeElement !== nTime) nTime.value = N.time || "08:00";
      nTime.disabled = nTest.disabled = nNow.disabled = !nt.ready;
      const L2 = nt.last;
      nLast.textContent = !L2 ? "" : L2.ok ? t("k.adm.ntfy.last", { when: fmtDate(L2.when, { dateStyle: "short", timeStyle: "short" }), title: L2.title })
        : t("k.adm.ntfy.lastFail", { when: fmtDate(L2.when, { dateStyle: "short", timeStyle: "short" }), error: L2.error });
    }
    async function load() { try { data = await api("GET", "/api/kiosk/admin"); draw(); } catch (e) { fail(e); } }
    async function post(url, b) { data = await api("POST", url, b); draw(); }
    const onSwitch = el => () => {
      if (!el.checked && !confirm(t("k.adm.offAsk"))) { el.checked = true; return; }
      post("/api/kiosk/admin/settings", { enabled: el.checked }).then(() => toast(t(el.checked ? "k.adm.onDone" : "k.adm.offDone"))).catch(e => { fail(e); draw(); });
    };
    swIn.onchange = onSwitch(swIn); stSw.onchange = onSwitch(stSw);
    jobBtn.onclick = () => { jobBtn.disabled = true; post("/api/kiosk/admin/job", {}).then(() => toast(t("k.adm.jobDone"))).catch(fail).finally(() => { jobBtn.disabled = false; }); };
    purgeBtn.onclick = () => {
      const hours = Number(purgeIn.value);
      if (!(hours >= 1 && hours <= 720) || !confirm(t("k.adm.purgeAsk", { hours }))) return;
      purgeBtn.disabled = true;
      api("POST", "/api/kiosk/admin/purge", { hours }).then(d => { data = d; draw(); toast(t("k.adm.purgeDone", { n: d.purged })); })
        .catch(fail).finally(() => { purgeBtn.disabled = false; });
    };
    save.onclick = () => post("/api/kiosk/admin/settings", { contact: cIn.value, title: tIn.value, privacy: pIn.value }).then(() => toast(t("k.adm.saved"))).catch(fail);
    Object.entries(nSw).forEach(([k, i]) => { i.onchange = () => post("/api/kiosk/admin/notify", { [k]: i.checked }).then(() => toast(t("k.adm.saved"))).catch(e => { fail(e); draw(); }); });
    nTime.onchange = () => { if (nTime.value) post("/api/kiosk/admin/notify", { time: nTime.value }).then(() => toast(t("k.adm.saved"))).catch(e => { fail(e); draw(); }); };
    const sendBtn = (b, url) => () => {
      b.disabled = true;
      api("POST", url, {}).then(d => { data = d; draw(); toast(t("k.adm.ntfy.sent")); }).catch(e => { fail(e); load(); }).finally(() => { b.disabled = false; });
    };
    nTest.onclick = sendBtn(nTest, "/api/kiosk/admin/notify/test");
    nNow.onclick = sendBtn(nNow, "/api/kiosk/admin/notify/summary");
    deflt.onclick =() => { if (!pIn.value.trim() || confirm(t("k.adm.privacyDefaultAsk"))) pIn.value = data ? data.privacyDefault : ""; };

    ENTER.PK = load;
    const enterSettings = ENTER.P11; ENTER.P11 = () => { enterSettings(); load(); };
    ALIAS.kiosk = "PK";
    onLang(draw);
    load();
  });
})();
