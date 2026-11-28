// QR-Code für Links, lokal erzeugt, kein fremder Dienst (Entscheid 80). Byte-Modus, Stufe M, Version 1–6, Maske 0,
// Ruhezone 4 Module, weiss hinterlegt. Gemeinsam für App und Kiosk (kiosk.js, Klasse kQr); stammt aus dem Kiosk (seit 0.1.8), hier seit 0.1.28.
(() => {
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
  function qrSvg(text, cls) {
    const mx = qrMatrix(text); if (!mx) return null;
    const n = mx.length, q = 4, ns = "http://www.w3.org/2000/svg";
    let d = ""; mx.forEach((row, y) => row.forEach((v, x) => { if (v) d += `M${x + q},${y + q}h1v1h-1z`; }));
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${n + 2 * q} ${n + 2 * q}`); svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("role", "img"); svg.setAttribute("class", cls || "qr");
    const bg = document.createElementNS(ns, "rect"); bg.setAttribute("width", "100%"); bg.setAttribute("height", "100%"); bg.setAttribute("fill", "#fff");
    const path = document.createElementNS(ns, "path"); path.setAttribute("d", d); path.setAttribute("fill", "#000");
    svg.append(bg, path);
    return svg;
  }
  window.qrMatrix = qrMatrix;
  window.qrSvg = qrSvg;
})();
