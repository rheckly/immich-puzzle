// Puzzle-Engine: legt ein Foto Teil für Teil auf einem Tisch zusammen, live auf einem <canvas> (kein Video).
// Aus dem Mockup docs/design/mockups/puzzle/puzzle.html (Smart Albums), gleich im Editor (puzzle.html) und auf der
// Empfängerseite (play.html). Wer hier etwas ändert, ändert beide Seiten.
// Einstellungen (cfg): src (Canvas mit dem Motiv), cols, rows, seed, start (scatter|heap|mix), rot, frame, ghost,
// T (Dauer in s), hold (fertiges Bild 2–10 s), tempo (0–2, Stärke des Tempo-Bogens), frameAt (15–70 % der Zeit: Rahmen fertig),
// ramp (0–60 %: schneller werden ab), title, text, textMode (in|end), textFont (FONTS), textSize (50–200 %),
// textPos (tl tc tr ml mc mr bl bc br), textColor (#rrggbb).
"use strict";
function mulberry(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
// Zeit als m:ss. Nicht fmt: das ist in i18n.js die Zahlenformatierung
const fmtT = s => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const ease = p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
const PUZZLE_COUNTS = [24, 48, 100, 200, 500];
const PUZZLE_MAXT = 60;
const TEXT_COLORS = ["#ffffff", "#111111", "#9e9e9e", "#ffd24d", "#fff176", "#ff9f43", "#e53935", "#ff6b9a", "#9b59b6", "#2e86de", "#74c0fc", "#2ecc71", "#1b5e20", "#8d6e63"];
// Schriften: 8 aus dem System, 13 aus Collage (app/fonts, @font-face «C-key» in common.css). Reihenfolge wie im Editor
const FONTS = {
  hand: "'Segoe Script','Bradley Hand',cursive", script: "'Brush Script MT','Segoe Script',cursive",
  serif: "Georgia,'Times New Roman',serif", elegant: "'Palatino Linotype','Book Antiqua',Palatino,serif",
  sans: "'Segoe UI',Helvetica,Arial,sans-serif", bold: "Impact,'Arial Black',sans-serif",
  mono: "'Courier New',monospace", fun: "'Comic Sans MS','Chalkboard SE',cursive",
};
const CFONTS = { roboto: "Roboto-Bold", sourceserif: "SourceSerifPro-Semibold", amatic: "AmaticSC-Bold", opensans: "OpenSans-Bold",
  lato: "Lato-Bold", montserrat: "Montserrat-Bold", bebas: "BebasNeue-Regular", anton: "Anton-Regular", archivo: "ArchivoBlack-Regular",
  lilita: "LilitaOne-Regular", fredoka: "Fredoka-Bold", chewy: "Chewy-Regular", luckiest: "LuckiestGuy-Regular" };
for (const k in CFONTS) FONTS[k] = `'C-${k}',sans-serif`;
// @font-face für die Collage-Schriften, relativ zu dieser Datei (Editor «/», Empfängerseite «/puzzles/»)
(() => {
  const base = ((document.currentScript && document.currentScript.src) || "").replace(/puzzle-engine\.js.*$/, "") + "fonts/";
  const css = document.createElement("style");
  css.textContent = Object.entries(CFONTS).map(([k, f]) => `@font-face{font-family:"C-${k}";src:url("${base}${f}.woff") format("woff");font-display:swap}`).join("");
  document.head.appendChild(css);
})();
const TEXT_POS = ["tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br"];
const OLD_POS = { top: "tc", mid: "mc", bottom: "bc" };
const textPos = p => OLD_POS[p] || (TEXT_POS.includes(p) ? p : "mc");
const fontCss = (key, px) => `${CFONTS[key] ? 400 : 700} ${px}px ${FONTS[key] || FONTS.hand}`;
// Collage-Schrift vor dem Zeichnen laden (sonst zeichnet das Canvas mit der Ersatzschrift)
function loadFont(key) {
  if (!CFONTS[key] || !document.fonts || !document.fonts.load) return Promise.resolve();
  return document.fonts.load(fontCss(key, 40), "AÄaä").catch(() => {});
}
// Schatten: helle Farben dunkel hinterlegen, dunkle (#0…–#3…) hell
const darkColor = c => /^#[0-3]/i.test(c || "");

function drawText(g, c, W, H, alpha) {
  // Genau wie die Vorschau im Panel Schriftzug (CSS white-space:pre-line, overflow-wrap:anywhere, Rand 6 % der Breite):
  // alle Zeilen inkl. Leerzeilen bleiben, lange Zeilen werden umgebrochen, zu lange Wörter getrennt (Roger 21.11.2026).
  // Nur wenn der ganze Text höher ist als das Bild, wird die Schrift kleiner (die Vorschau schneidet dort ab).
  if (!String(c.text || "").trim()) return;
  const raw = String(c.text).replace(/\r?\n$/, "").split(/\r?\n/).map(l => l.replace(/\s+/g, " ").trim());
  const pad = W * .06, maxW = W - 2 * pad, maxH = H - 2 * pad;
  let fs = W / 16 * (c.textSize || 100) / 100, lines = [];
  const wrap = () => {
    g.font = fontCss(c.textFont, fs); lines = [];
    const fits = t => g.measureText(t).width <= maxW;
    for (const l of raw) {
      if (!l) { lines.push(""); continue; }
      let cur = "";
      for (let w of l.split(" ")) {
        const t = cur ? cur + " " + w : w;
        if (fits(t)) { cur = t; continue; }
        if (cur) { lines.push(cur); cur = ""; }
        while (!fits(w)) {  // Wort länger als die Zeile: irgendwo trennen
          let k = w.length - 1; while (k > 1 && !fits(w.slice(0, k))) k--;
          lines.push(w.slice(0, k)); w = w.slice(k);
        }
        cur = w;
      }
      lines.push(cur);
    }
  };
  wrap();
  for (let i = 0; i < 30 && fs * 1.15 * lines.length > maxH; i++) { fs *= .92; wrap(); }
  const pos = textPos(c.textPos), v = pos[0], h = pos[1];
  const lh = fs * 1.15, tot = lh * lines.length;
  const y0 = v === "t" ? pad : v === "b" ? H - pad - tot : (H - tot) / 2;
  const x = h === "l" ? pad : h === "r" ? W - pad : W / 2;
  g.save(); g.globalAlpha = alpha; g.textAlign = h === "l" ? "left" : h === "r" ? "right" : "center"; g.textBaseline = "top";
  g.fillStyle = c.textColor || "#ffffff";
  g.shadowColor = darkColor(c.textColor) ? "rgba(255,255,255,.7)" : "rgba(0,0,0,.7)"; g.shadowBlur = fs * .15; g.shadowOffsetY = fs * .04;
  lines.forEach((l, i) => g.fillText(l, x, y0 + i * lh + fs * .05)); g.restore();
}

// Tempo-Kurve (Mockup P4, Roger 21.11.2026): Grundtempo, ab «Schneller werden ab» ein Bogen (je stärker, desto spitzer).
// Mit «Zuerst den Rahmen» liegen bis tR genau die Randteile, bis END alle; dazwischen weicher Übergang, kein Bruch.
// Gibt die Rate (Teile pro s) in Schritten von STEP zurück.
function tempoCurve(o) {
  const T = o.T, H = o.hold, END = Math.max(T * .5, T - H), T0 = Math.min(1.5, END * .1), STEP = .25, v = o.tempo || 0;
  const N = o.N, EDGE = o.frame ? o.EDGE : 0;
  const tR = T0 + (END - T0) * (o.frameAt || 40) / 100, S = T0 + (END - T0) * (o.ramp ?? 5) / 100;
  const raw = [];
  for (let t = 0; t <= END + 1e-9; t += STEP) {
    const u = (t - T0) / (END - T0), w = Math.max(0, (t - S) / (END - S));
    const bell = Math.sin(Math.PI * w) ** 2, rip = .12 * Math.max(0, Math.sin(t * 1.35));
    raw.push(t < T0 ? 0 : (1 + v * 3 * bell ** (1 + v)) * (1 + rip) * Math.min(1, u * 12) * Math.min(1, (1 - u) * 12 + .15));
  }
  let rate;
  if (EDGE > 0 && EDGE < N) {
    const W = END * .12, sm = t => { const x = clamp((t - tR + W) / (2 * W), 0, 1); return x * x * (3 - 2 * x); };
    let a11 = 0, a12 = 0, a21 = 0, a22 = 0;
    raw.forEach((r, i) => { const t = i * STEP, q = sm(t), A = r * STEP; a21 += A * (1 - q); a22 += A * q; if (t < tR) { a11 += A * (1 - q); a12 += A * q; } });
    const det = a11 * a22 - a12 * a21 || 1;
    let k1 = (EDGE * a22 - a12 * N) / det, k2 = (a11 * N - a21 * EDGE) / det;
    if (k2 < .05) { k2 = .05; k1 = (EDGE - a12 * k2) / (a11 || 1); }
    if (k1 > 0) rate = raw.map((r, i) => { const q = sm(i * STEP); return r * (k1 * (1 - q) + k2 * q); });
  }
  if (!rate) { const sum = raw.reduce((a, r) => a + r * STEP, 0) || 1; rate = raw.map(r => r * N / sum); }
  return { rate, STEP, T, END, T0, tR: EDGE ? tR : null, S, peak: Math.max(...rate) };
}

// Bild (img oder canvas) auf das Format zuschneiden: aspect 0 = wie das Foto. Längste Seite höchstens max
function cropImage(s, aspect, max = 1200) {
  const sw = s.naturalWidth || s.width, sh = s.naturalHeight || s.height;
  let w = sw, h = sh;
  if (aspect) { if (w / h > aspect) w = Math.round(h * aspect); else h = Math.round(w / aspect); }
  const k = Math.min(1, max / Math.max(w, h)), c = document.createElement("canvas");
  c.width = Math.round(w * k); c.height = Math.round(h * k);
  c.getContext("2d").drawImage(s, (sw - w) / 2, (sh - h) / 2, w, h, 0, 0, c.width, c.height);
  return c;
}
// Motiv der Teile: im Modus «Im Puzzle» steht der Text schon im Bild
function motifSrc(base, t) {
  if (!t.text || t.textMode !== "in") return base;
  const c = document.createElement("canvas"); c.width = base.width; c.height = base.height;
  const g = c.getContext("2d"); g.drawImage(base, 0, 0); drawText(g, t, c.width, c.height, 1);
  return c;
}
function grid(n, a) { const cols = Math.max(2, Math.round(Math.sqrt(n * a))), rows = Math.max(2, Math.round(n / cols)); return [cols, rows]; }

function edgePath(g, x0, y0, x1, y1, s, tab) {
  if (!s) { g.lineTo(x1, y1); return; }
  const dx = x1 - x0, dy = y1 - y0, len = Math.hypot(dx, dy), ox = dy / len * tab * s, oy = -dx / len * tab * s;
  const P = (u, v) => [x0 + u * dx + v * ox, y0 + u * dy + v * oy];
  const segs = [[[.30, 0], [.40, .02], [.38, .10]], [[.30, .20], [.36, .30], [.50, .30]], [[.64, .30], [.70, .20], [.62, .10]], [[.60, .02], [.70, 0], [1, 0]]];
  for (const sg of segs) { const a = P(...sg[0]), b = P(...sg[1]), c = P(...sg[2]); g.bezierCurveTo(a[0], a[1], b[0], b[1], c[0], c[1]); }
}

class Puzzle {
  constructor(canvas) { this.cv = canvas; this.g = canvas.getContext("2d"); this.t = 0; this.cfg = null; }
  setup(cfg) {
    this.cfg = cfg; const r = mulberry(cfg.seed); this.r = r;
    const W = cfg.src.width, H = cfg.src.height, cw = W / cfg.cols, ch = H / cfg.rows;
    Object.assign(this, { W, H, cw, ch, tab: Math.min(cw, ch), m: Math.min(cw, ch) * .34 + 2 });
    const Hs = [], Vs = [];
    for (let y = 0; y <= cfg.rows; y++) { Hs[y] = []; Vs[y] = []; for (let x = 0; x <= cfg.cols; x++) { Hs[y][x] = r() < .5 ? 1 : -1; Vs[y][x] = r() < .5 ? 1 : -1; } }
    this.pieces = [];
    for (let y = 0; y < cfg.rows; y++) for (let x = 0; x < cfg.cols; x++) {
      const top = y === 0 ? 0 : -Hs[y][x], bottom = y === cfg.rows - 1 ? 0 : Hs[y + 1][x], left = x === 0 ? 0 : -Vs[y][x], right = x === cfg.cols - 1 ? 0 : Vs[y][x + 1];
      this.pieces.push({ x, y, top, right, bottom, left, edge: !top || !bottom || !left || !right, tx: x * cw + cw / 2, ty: y * ch + ch / 2 });
    }
    this.layout(); this.schedule(); this.place(); this.render();
  }
  layout() {
    const dpr = window.devicePixelRatio || 1, w = this.cv.clientWidth || 600, h = this.cv.clientHeight || 400;
    this.cv.width = Math.round(w * dpr); this.cv.height = Math.round(h * dpr); this.dpr = dpr; this.vw = w; this.vh = h;
    this.S = Math.min(w * .6 / this.W, h * .62 / this.H); this.bx = (w - this.W * this.S) / 2; this.by = (h - this.H * this.S) / 2;
  }
  render() {  // Teile vorab zeichnen, in Pixelgrösse der Anzeige
    const { cw, ch, m, tab } = this, sc = this.S * this.dpr, src = this.cfg.src;
    for (const p of this.pieces) {
      const c = p.cv || document.createElement("canvas"); c.width = Math.max(1, Math.ceil((cw + 2 * m) * sc)); c.height = Math.max(1, Math.ceil((ch + 2 * m) * sc));
      const g = c.getContext("2d");
      g.setTransform(sc, 0, 0, sc, 0, 0); g.translate(m, m);
      const path = () => { g.beginPath(); g.moveTo(0, 0); edgePath(g, 0, 0, cw, 0, p.top, tab); edgePath(g, cw, 0, cw, ch, p.right, tab); edgePath(g, cw, ch, 0, ch, p.bottom, tab); edgePath(g, 0, ch, 0, 0, p.left, tab); g.closePath(); };
      path(); g.save(); g.shadowColor = "rgba(0,0,0,.55)"; g.shadowBlur = 3 * this.dpr; g.shadowOffsetY = this.dpr; g.fillStyle = "#000"; g.fill(); g.restore();
      g.save(); path(); g.clip(); g.drawImage(src, -p.x * cw, -p.y * ch);
      g.lineWidth = 2 / this.S; g.strokeStyle = "rgba(255,255,255,.28)"; path(); g.stroke(); g.restore();
      g.lineWidth = .8 / this.S; g.strokeStyle = "rgba(0,0,0,.45)"; path(); g.stroke();
      p.cv = c;
    }
  }
  schedule() {
    const c = this.cfg, r = mulberry(c.seed * 7 + 3), N = this.pieces.length;
    const sh = a => { for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
    let order;
    if (c.frame) { const e = this.pieces.filter(p => p.edge), i = this.pieces.filter(p => !p.edge); order = sh(e).concat(sh(i)); } else order = sh(this.pieces.slice());
    const cv = tempoCurve({ T: c.T, hold: c.hold, tempo: c.tempo, frameAt: c.frameAt, ramp: c.ramp, frame: c.frame, N, EDGE: this.pieces.filter(p => p.edge).length });
    this.curve = cv;
    // Teil i ist fertig, wenn die kumulierte Rate i+1 erreicht; Flugzeit je nach Tempo (schneller = kürzer)
    let cum = 0, k = 0;
    for (let j = 0; j < cv.rate.length && k < N; j++) {
      const add = cv.rate[j] * cv.STEP;
      while (k < N && cum + add >= k + 1 - 1e-9) {
        const p = order[k], e = j * cv.STEP + (add > 0 ? (k + 1 - cum) / add : 0) * cv.STEP;
        const d = clamp(2.4 / (1 + cv.rate[j] * .45), .35, 2.2);
        p.e = Math.min(e, cv.END); p.d = d; p.s = Math.max(.05, p.e - d); p.d = p.e - p.s; p.ord = k; k++;
      }
      cum += add;
    }
    for (; k < N; k++) { const p = order[k]; p.e = cv.END; p.s = Math.max(.05, cv.END - .6); p.d = p.e - p.s; p.ord = k; }
    this.order = order; this.done = Math.max(...order.map(p => p.e));
    this.frameDone = c.frame ? Math.max(...order.filter(p => p.edge).map(p => p.e)) : null;
    const ev = []; order.forEach(p => { ev.push([p.s, 1]); ev.push([p.e, -1]); }); ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let cur = 0, mx = 0; ev.forEach(e => { cur += e[1]; mx = Math.max(mx, cur); }); this.maxPar = mx;
  }
  place() {  // Startlage auf dem Tisch, in Bild-Einheiten relativ zum Brett
    const c = this.cfg, r = mulberry(c.seed * 13 + 5), S = this.S, x0 = -this.bx / S, y0 = -this.by / S, x1 = (this.vw - this.bx) / S, y1 = (this.vh - this.by) / S, pad = Math.max(this.cw, this.ch) * .55;
    const inBoard = (x, y) => x > -pad && x < this.W + pad && y > -pad && y < this.H + pad;
    const freeL = -x0, freeT = -y0, side = freeL >= freeT ? "l" : "t";
    const heap = () => { const g = () => (r() + r() + r() - 1.5); if (side === "l") return [x0 + (-x0) * .5 + g() * (-x0) * .35, this.H * .62 + g() * this.H * .25]; return [this.W * .25 + g() * this.W * .2, y1 - (y1 - this.H) * .5 + g() * (y1 - this.H) * .3]; };
    const scat = () => { for (let i = 0; i < 40; i++) { const x = x0 + pad * .6 + r() * (x1 - x0 - pad * 1.2), y = y0 + pad * .6 + r() * (y1 - y0 - pad * 1.2); if (!inBoard(x, y)) return [x, y]; } return heap(); };
    this.pieces.forEach(p => {
      const mode = c.start === "mix" ? (r() < .5 ? "heap" : "scatter") : c.start; [p.sx, p.sy] = mode === "heap" ? heap() : scat();
      p.sr = c.rot ? (r() - .5) * Math.PI * 2 : (r() - .5) * .15; p.z = r(); p.bend = (r() - .5) * .5;
    });
    this.resting = this.pieces.slice().sort((a, b) => a.z - b.z);
  }
  resize() { if (!this.cfg) return; this.layout(); this.place(); this.render(); this.draw(this.t); }
  drawPiece(p, x, y, rot, sc, shadow) {
    const g = this.g, S = this.S; g.save(); g.translate(this.bx + x * S, this.by + y * S); g.rotate(rot); g.scale(sc, sc);
    if (shadow) { g.shadowColor = "rgba(0,0,0,.5)"; g.shadowBlur = shadow; g.shadowOffsetY = shadow * .5; }
    const w = (this.cw + 2 * this.m) * S, h = (this.ch + 2 * this.m) * S; g.drawImage(p.cv, -w / 2, -h / 2, w, h); g.restore();
  }
  draw(t) {
    this.t = t; const g = this.g, S = this.S, c = this.cfg; g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const bg = g.createRadialGradient(this.vw / 2, this.vh / 2, 10, this.vw / 2, this.vh / 2, Math.max(this.vw, this.vh) * .7);
    bg.addColorStop(0, "#2d3236"); bg.addColorStop(1, "#16191b"); g.fillStyle = bg; g.fillRect(0, 0, this.vw, this.vh);
    // Schluss (Entscheid 13): fertiges Bild 0,3 s nach «fertig» langsam auf Bildschirmgrösse zoomen, um die Brettmitte
    const zMax = Math.min(this.vw * .97 / (this.W * S), this.vh * .97 / (this.H * S)), zDur = clamp((c.T - this.done) * .7, 1.2, 4);
    const z = t > this.done ? 1 + (zMax - 1) * ease(clamp((t - this.done - .3) / zDur, 0, 1)) : 1;
    if (z !== 1) { const cx = this.bx + this.W * S / 2, cy = this.by + this.H * S / 2; g.translate(cx, cy); g.scale(z, z); g.translate(-cx, -cy); }
    g.fillStyle = "rgba(255,255,255,.04)"; g.fillRect(this.bx, this.by, this.W * S, this.H * S);
    if (c.ghost) { g.globalAlpha = .13; g.drawImage(c.src, this.bx, this.by, this.W * S, this.H * S); g.globalAlpha = 1; }
    g.setLineDash([4, 4]); g.strokeStyle = "rgba(255,255,255,.22)"; g.strokeRect(this.bx + .5, this.by + .5, this.W * S - 1, this.H * S - 1); g.setLineDash([]);
    const placed = [], fly = [], rest = [];
    for (const p of this.order) { if (t >= p.e) placed.push(p); else if (t > p.s) fly.push(p); }
    for (const p of this.resting) if (t <= p.s) rest.push(p);
    for (const p of placed) { const k = clamp((t - p.e) / .25, 0, 1); this.drawPiece(p, p.tx, p.ty, 0, 1 + .03 * (1 - k) * (k > 0 ? 1 : 0), 0); }
    for (const p of rest) this.drawPiece(p, p.sx, p.sy, p.sr, 1, 0);
    for (const p of fly) {
      const q = (t - p.s) / p.d, e = ease(q), mx = (p.sx + p.tx) / 2, my = (p.sy + p.ty) / 2, dx = p.tx - p.sx, dy = p.ty - p.sy;
      const cx = mx - dy * p.bend, cy = my + dx * p.bend, a = (1 - e) * (1 - e), b = 2 * (1 - e) * e, d = e * e, lift = Math.sin(Math.PI * q);
      this.drawPiece(p, a * p.sx + b * cx + d * p.tx, a * p.sy + b * cy + d * p.ty, p.sr * (1 - e), 1 + .1 * lift, 4 + 14 * lift);
    }
    if (t > this.done) {
      const k = clamp((t - this.done) / 1.2, 0, 1); g.globalAlpha = k; g.drawImage(c.src, this.bx, this.by, this.W * S, this.H * S); g.globalAlpha = 1;
      if (c.text && c.textMode === "end") { const k2 = clamp((t - this.done - .4) / 1, 0, 1); if (k2 > 0) { g.save(); g.translate(this.bx, this.by); g.scale(S, S); drawText(g, c, this.W, this.H, k2); g.restore(); } }
    }
  }
}

// Gespeicherte Einstellungen + Foto (img) → cfg für Puzzle.setup
function puzzleConfig(saved, img) {
  const src = motifSrc(cropImage(img, 0), saved), [cols, rows] = grid(saved.count, src.width / src.height);
  return { ...saved, src, cols, rows };
}
// Musik leise werden lassen in den letzten Sekunden («Ausblenden am Ende»)
function musicVolume(t, T, vol, fade) {
  if (!fade) return vol;
  const left = T - t;
  return left < 3 ? vol * clamp(left / 3, 0, 1) : vol;
}
