// Puzzle-Renderer: macht aus einem geteilten Puzzle ein MP4 (Roger 23.11.2026, Auftrag «MP4-Download»).
// Läuft in einem eigenen Container ohne Netzwerk (network_mode: none). Austausch nur über Dateien in RENDER_DIR:
//   queue/<pid>.json    Auftrag, geschrieben von der App (server.py/kiosk.py): Hash, Titel, Einstellungen, Musik
//   videos/<pid>.mp4    fertiges Video (erst .tmp, dann umbenannt)
//   videos/<pid>.json   Stand: {hash, state: running|ready|error, progress, tries, error}
//   control.json        {enabled}: Hauptschalter aus dem Admin; aus = nichts Neues rendern
//   heartbeat           Zeitstempel für den Healthcheck
// Die Animation zeichnet dieselbe puzzle-engine.js wie Editor und Empfängerseite, hier mit Skia (@napi-rs/canvas)
// statt im Browser, Bild für Bild (draw(t) hängt nur von t ab). ffmpeg schreibt H.264 und mischt die Musik dazu.
// Eins nach dem anderen: nie zwei Videos gleichzeitig. Löschen macht die App (mit dem Puzzle, 24-h-Job).
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm"), { spawn } = require("child_process");
const { createCanvas, loadImage, GlobalFonts } = require("@napi-rs/canvas");

const env = (k, d) => process.env[k] || d, num = (k, d) => { const v = Number(process.env[k]); return Number.isFinite(v) && v > 0 ? v : d; };
const RENDER_DIR = env("RENDER_DIR", "/data/render"), PUZZLE_DIR = env("PUZZLE_DIR", "/data/puzzles");
const MUSIC_DIR = env("MUSIC_DIR", "/music"), UPLOAD_MUSIC_DIR = env("UPLOAD_MUSIC_DIR", "/data/music");
const ENGINE_DIR = env("ENGINE_DIR", path.join(__dirname, "engine"));
const FPS = num("RENDER_FPS", 30), SHORT = num("RENDER_SIZE", 1080);  // kurze Seite in Pixeln: 1080 = Full HD
const POLL = num("RENDER_POLL_SECONDS", 10), DEBOUNCE = num("RENDER_DEBOUNCE_SECONDS", 60);
const TIMEOUT = num("RENDER_TIMEOUT_MINUTES", 15) * 60000, TRIES = num("RENDER_TRIES", 2), MAX_T = 90;
const QUEUE = path.join(RENDER_DIR, "queue"), VIDEOS = path.join(RENDER_DIR, "videos");
const PID = /^[A-Za-z0-9_-]{8,64}$/;
const log = (...a) => console.log(new Date().toISOString(), ...a);

// ---------- Engine laden (ohne Browser: kleiner Ersatz für document/window) ----------
globalThis.window = { devicePixelRatio: 1 };
globalThis.document = { currentScript: null, fonts: null, head: { appendChild() {} },
  createElement: tag => tag === "canvas" ? createCanvas(1, 1) : { textContent: "" } };
vm.runInThisContext(fs.readFileSync(path.join(ENGINE_DIR, "puzzle-engine.js"), "utf8") +
  "\nglobalThis.Puzzle = Puzzle; globalThis.puzzleConfig = puzzleConfig;", { filename: "puzzle-engine.js" });

// Schriften: die 13 Collage-Schriften wie im Browser; die Systemschriften (Segoe Script, Georgia, Impact …) gibt es
// unter Linux nicht, darum freie Ersatzschriften unter deren Namen (Roger 23.11.2026: «freie Ersatzschriften ok»)
const FONT_FILES = { roboto: "Roboto-Bold", sourceserif: "SourceSerifPro-Semibold", amatic: "AmaticSC-Bold", opensans: "OpenSans-Bold",
  lato: "Lato-Bold", montserrat: "Montserrat-Bold", bebas: "BebasNeue-Regular", anton: "Anton-Regular", archivo: "ArchivoBlack-Regular",
  lilita: "LilitaOne-Regular", fredoka: "Fredoka-Bold", chewy: "Chewy-Regular", luckiest: "LuckiestGuy-Regular" };
const SYSTEM_ALIAS = { "Segoe Script": "chewy", "Brush Script MT": "amatic", "Comic Sans MS": "chewy", Georgia: "sourceserif",
  "Palatino Linotype": "sourceserif", "Segoe UI": "opensans", Impact: "anton", "Overlay Sans": "opensans" };
function registerFonts() {
  const dir = path.join(ENGINE_DIR, "fonts");
  for (const [k, f] of Object.entries(FONT_FILES)) {
    const file = path.join(dir, f + ".woff");
    if (fs.existsSync(file)) GlobalFonts.registerFromPath(file, "C-" + k);
  }
  for (const [name, k] of Object.entries(SYSTEM_ALIAS)) {
    const file = path.join(dir, FONT_FILES[k] + ".woff");
    if (fs.existsSync(file)) GlobalFonts.registerFromPath(file, name);
  }
}

// ---------- Schatten vorrechnen ----------
// shadowBlur kostet auf der CPU ~80 % der Zeit (Messung 23.11.2026: 130–225 ms statt 28–48 ms pro Bild in Full HD).
// Darum pro Teil einmal ein weichgezeichnetes Schattenbild und dieses beim Fliegen mit passender Deckkraft zeichnen.
const SHADOW_BLUR = 11;
function shadowSprite(p) {
  if (p.sh && p.sh.src === p.cv) return p.sh;
  const pad = SHADOW_BLUR * 2, c = createCanvas(p.cv.width + 2 * pad, p.cv.height + 2 * pad), g = c.getContext("2d");
  const off = c.width + 10;  // Teil ausserhalb zeichnen, nur der Schatten fällt ins Bild
  g.shadowColor = "rgba(0,0,0,.5)"; g.shadowBlur = SHADOW_BLUR; g.shadowOffsetX = off;
  g.drawImage(p.cv, pad - off, pad);
  return (p.sh = { src: p.cv, cv: c, pad });
}
Puzzle.prototype.drawPiece = function (p, x, y, rot, sc, shadow) {
  const g = this.g, S = this.S, w = (this.cw + 2 * this.m) * S, h = (this.ch + 2 * this.m) * S;
  const px = this.bx + x * S, py = this.by + y * S;
  if (shadow) {
    const s = shadowSprite(p), k = w / p.cv.width, pw = s.pad * k;
    g.save(); g.globalAlpha = clamp(.35 + shadow / 18 * .65, 0, 1);
    g.translate(px, py + shadow * .5); g.rotate(rot); g.scale(sc, sc);
    g.drawImage(s.cv, -w / 2 - pw, -h / 2 - pw, w + 2 * pw, h + 2 * pw); g.restore();
  }
  g.save(); g.translate(px, py); g.rotate(rot); g.scale(sc, sc); g.drawImage(p.cv, -w / 2, -h / 2, w, h); g.restore();
};

// ---------- Titel und Fusszeile wie auf der Empfängerseite (play.html) ----------
function overlay(g, job, W, H) {
  const u = Math.min(W, H) / 1080, out = { top: 0, bottom: 0 };
  g.save(); g.setTransform(1, 0, 0, 1, 0, 0); g.textAlign = "center"; g.textBaseline = "top";
  g.shadowColor = "rgba(0,0,0,.7)"; g.shadowBlur = 10 * u; g.shadowOffsetY = 2 * u;
  let y = 30 * u;
  if (job.title) {
    g.font = `700 ${Math.round(44 * u)}px "Overlay Sans",sans-serif`; g.fillStyle = "#eeeeee";
    g.fillText(fit(g, job.title, W * .9), W / 2, y); y += 54 * u;
    if (job.sub) { g.font = `400 ${Math.round(22 * u)}px "Overlay Sans",sans-serif`; g.fillStyle = "#cccccc"; g.fillText(fit(g, job.sub, W * .9), W / 2, y); y += 30 * u; }
    out.top = y + 10 * u;
  }
  const foot = (job.foot || []).filter(Boolean);
  if (foot.length) {
    g.font = `400 ${Math.round(18 * u)}px "Overlay Sans",sans-serif`; g.fillStyle = "#aaaaaa"; g.textBaseline = "bottom";
    let fy = H - 14 * u;
    for (const line of foot.slice().reverse()) { g.fillText(fit(g, line, W * .94), W / 2, fy); fy -= 26 * u; }
    out.bottom = H - fy + 4 * u;
  }
  g.restore();
  return out;
}
function fit(g, s, max) {
  s = String(s);
  if (g.measureText(s).width <= max) return s;
  while (s.length > 1 && g.measureText(s + "…").width > max) s = s.slice(0, -1);
  return s + "…";
}

// ---------- Ein Video rendern ----------
function inside(root, rel) {
  if (!root || typeof rel !== "string" || !rel) return null;
  const r = fs.realpathSync(root), p = path.resolve(r, rel);
  let real;
  try { real = fs.realpathSync(p); } catch (e) { return null; }
  return real.startsWith(r + path.sep) && fs.statSync(real).isFile() ? real : null;
}
function musicFile(m) {
  if (!m || !m.path) return null;
  return inside(m.src === "up" ? UPLOAD_MUSIC_DIR : m.src === "lib" ? MUSIC_DIR : "", m.path);
}

async function render(pid, job, progress) {
  const dir = path.join(PUZZLE_DIR, pid);
  const img = await loadImage(fs.readFileSync(path.join(dir, "photo.jpg")));
  const cfg0 = { ...(job.config || {}), title: job.title || "" };
  const T = clamp(Number(cfg0.T) || 20, 3, MAX_T);
  // Format nach dem Bild: quer 1920 × 1080, hoch 1080 × 1920 (Rogers Wahl 23.11.2026)
  const probe = puzzleConfig({ ...cfg0, count: 24 }, img), landscape = probe.src.width >= probe.src.height;
  const long = Math.round(SHORT * 16 / 9 / 2) * 2, W = landscape ? long : SHORT, H = landscape ? SHORT : long;
  const cv = createCanvas(W, H), P = new Puzzle(cv, { w: W, h: H });
  const ov = createCanvas(W, H), inset = overlay(ov.getContext("2d"), job, W, H);
  P.inset = inset;
  P.setup(puzzleConfig(cfg0, img));
  const frames = Math.round(T * FPS) + 1;
  const music = musicFile(job.music), args = ["-hide_banner", "-loglevel", "error", "-y",
    "-f", "rawvideo", "-pix_fmt", "rgba", "-s", `${W}x${H}`, "-r", String(FPS), "-i", "pipe:0"];
  if (music) args.push("-i", music);
  args.push("-map", "0:v");
  if (music) {
    const vol = clamp(Number(job.music.volume ?? .8), 0, 1), af = [`volume=${vol.toFixed(3)}`];
    if (job.music.fade && T > 3) af.push(`afade=t=out:st=${(T - 3).toFixed(3)}:d=3`);  // wie musicVolume(): letzte 3 s leiser
    args.push("-map", "1:a", "-af", af.join(","), "-c:a", "aac", "-b:a", "128k");
  }
  const tmp = path.join(VIDEOS, pid + ".mp4.tmp");
  args.push("-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-threads", "2",
    "-movflags", "+faststart", "-t", T.toFixed(3), "-f", "mp4", tmp);
  const ff = spawn("ffmpeg", args, { stdio: ["pipe", "ignore", "pipe"] });
  let err = "", exited = null;
  ff.stderr.on("data", d => { err = (err + d).slice(-2000); });
  const done = new Promise(ok => ff.on("close", code => { exited = code; ok(code); }));
  ff.stdin.on("error", () => {});
  const started = Date.now();
  try {
    for (let i = 0; i < frames; i++) {
      if (exited !== null) throw new Error("ffmpeg: " + (err.trim() || "beendet " + exited));
      if (Date.now() - started > TIMEOUT) throw new Error("Zeitlimit");
      P.draw(Math.min(T, i / FPS));
      if (inset.top || inset.bottom) { P.g.setTransform(1, 0, 0, 1, 0, 0); P.g.drawImage(ov, 0, 0); }
      if (!ff.stdin.write(cv.data())) await new Promise(ok => ff.stdin.once("drain", ok));
      if (i % (FPS * 2) === 0) progress(Math.round(i / frames * 100));
    }
    ff.stdin.end();
    const code = await done;
    if (code !== 0) throw new Error("ffmpeg: " + (err.trim() || "Code " + code));
  } catch (e) {
    ff.kill("SIGKILL"); await done; rm(tmp); throw e;
  }
  return { tmp, W, H, T, frames, sec: (Date.now() - started) / 1000 };
}

// ---------- Warteschlange ----------
const readJson = f => { try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch (e) { return null; } };
function writeJson(f, data) { const t = f + "." + process.pid + ".tmp"; fs.writeFileSync(t, JSON.stringify(data)); fs.renameSync(t, f); }
function rm(f) { try { fs.unlinkSync(f); } catch (e) { /* schon weg */ } }
const enabled = () => (readJson(path.join(RENDER_DIR, "control.json")) || {}).enabled !== false;

function nextJob(now) {
  let names;
  try { names = fs.readdirSync(QUEUE); } catch (e) { return null; }
  const jobs = [];
  for (const n of names) {
    if (!n.endsWith(".json") || !PID.test(n.slice(0, -5))) continue;
    const f = path.join(QUEUE, n);
    let st; try { st = fs.statSync(f); } catch (e) { continue; }
    if (now - st.mtimeMs < DEBOUNCE * 1000) continue;  // Gast ändert vielleicht noch: erst nach Ruhe rendern
    jobs.push({ pid: n.slice(0, -5), file: f, mtime: st.mtimeMs });
  }
  jobs.sort((a, b) => a.mtime - b.mtime);
  return jobs[0] || null;
}

async function work(j) {
  const job = readJson(j.file), statusFile = path.join(VIDEOS, j.pid + ".json");
  if (!job || !job.hash) { rm(j.file); return; }
  if (!fs.existsSync(path.join(PUZZLE_DIR, j.pid, "photo.jpg"))) { rm(j.file); return; }  // Puzzle schon gelöscht
  const old = readJson(statusFile) || {};
  if (old.hash === job.hash && old.state === "ready" && fs.existsSync(path.join(VIDEOS, j.pid + ".mp4"))) { rm(j.file); return; }
  const tries = old.hash === job.hash ? (old.tries || 0) : 0;
  const status = { hash: job.hash, state: "running", progress: 0, tries: tries + 1, at: Date.now() };
  writeJson(statusFile, status);
  log("rendern", j.pid, "Versuch", tries + 1);
  try {
    const r = await render(j.pid, job, pc => { status.progress = pc; writeJson(statusFile, status); touch(); });
    const still = readJson(j.file);
    if (!fs.existsSync(path.join(PUZZLE_DIR, j.pid))) { rm(r.tmp); rm(statusFile); rm(j.file); log("verworfen (gelöscht)", j.pid); return; }
    fs.renameSync(r.tmp, path.join(VIDEOS, j.pid + ".mp4"));
    writeJson(statusFile, { hash: job.hash, state: "ready", progress: 100, tries: tries + 1, at: Date.now(),
      size: fs.statSync(path.join(VIDEOS, j.pid + ".mp4")).size, seconds: Math.round(r.sec), w: r.W, h: r.H });
    if (still && still.hash === job.hash) rm(j.file);  // sonst hat die App inzwischen neu bestellt: nochmals rendern
    log("fertig", j.pid, `${r.W}x${r.H}`, r.frames, "Bilder in", r.sec.toFixed(1), "s");
  } catch (e) {
    const last = tries + 1 >= TRIES;
    writeJson(statusFile, { hash: job.hash, state: last ? "error" : "queued", progress: 0, tries: tries + 1, at: Date.now(), error: String(e.message || e).slice(0, 300) });
    if (last) rm(j.file); else fs.utimesSync(j.file, new Date(), new Date());  // ans Ende der Schlange
    log("Fehler", j.pid, e.message || e);
  }
}

function touch() { try { fs.writeFileSync(path.join(RENDER_DIR, "heartbeat"), String(Date.now())); } catch (e) { /* nächstes Mal */ } }
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

async function main() {
  for (const d of [QUEUE, VIDEOS]) fs.mkdirSync(d, { recursive: true });
  // Reste eines Absturzes: halbe Videos weg, «running» wieder in die Schlange
  for (const n of fs.readdirSync(VIDEOS)) if (n.endsWith(".tmp")) rm(path.join(VIDEOS, n));
  registerFonts();
  log("Puzzle-Renderer bereit", `${SHORT}p`, FPS, "fps");
  for (;;) {
    touch();
    const j = enabled() ? nextJob(Date.now()) : null;
    if (j) await work(j); else await new Promise(ok => setTimeout(ok, POLL * 1000));
  }
}

if (require.main === module) {
  if (process.argv[2] === "--once") {  // Test: node render.js --once <pid> <job.json> <out.mp4>
    registerFonts();
    const [, , , pid, jobFile, out] = process.argv;
    fs.mkdirSync(VIDEOS, { recursive: true });
    render(pid, readJson(jobFile), pc => log(pc + " %")).then(r => { fs.renameSync(r.tmp, out); log("fertig", r); })
      .catch(e => { console.error(e); process.exit(1); });
  } else if (process.argv[2] === "--health") {
    const t = Number(fs.readFileSync(path.join(RENDER_DIR, "heartbeat"), "utf8"));
    process.exit(Date.now() - t < 5 * 60000 ? 0 : 1);  // beim Rendern alle 2 s Video-Zeit, sonst alle POLL s
  } else main().catch(e => { console.error(e); process.exit(1); });
}
module.exports = { render, nextJob, work, musicFile };
