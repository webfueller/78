/* The branch map, drawn once and used twice: live in the page, and as a
   standalone file you can post.

   Colours are literals rather than CSS variables on purpose. An exported SVG
   has no stylesheet to inherit from, and a map that renders correctly in the app
   and grey on someone's timeline is worse than no export at all.

   Nothing personal goes on the card. Plan names, counts and probabilities only:
   no contact is named, no subject line appears, no message body is quoted. That
   is what makes it safe to share, and it is a design constraint, not an
   oversight. */

const PALETTE = {
  ground: "#0C1720", panel: "#12212C", rule: "#21384A", ruleSoft: "#1A2C3A",
  ink: "#D9E4E9", inkMid: "#A8BCC6", inkDim: "#7B95A3",
  real: "#E8A33D", sim: "#61B7C5", dead: "#C4614C",
};

const MONO = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';
const SERIF = '"Iowan Old Style", Charter, Georgia, serif';

const xmlesc = s => String(s).replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function lab(x, y, text, fill, anchor = "start", size = 9.5) {
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${fill}" font-family='${MONO}'
    font-size="${size}" letter-spacing="1.1">${xmlesc(text)}</text>`;
}

/* Returns { body, width, height } -- body is SVG markup with no <svg> wrapper,
   so the page can drop it into a live element and the exporter can wrap it. */
function buildBranchMap(map, opts = {}) {
  const W = opts.width || 1000;
  const plans = map.plans;
  const rows = plans.reduce((n, p) => n + Math.max(1, p.branches.length), 0);
  const H = Math.max(220, rows * 30 + plans.length * 16 + 46);
  const xRoot = 34, xPlan = opts.xPlan || 300, xEnd = W - 210;

  const parts = [];
  for (let d = 0; d <= map.horizon_days; d++) {
    const x = xPlan + (xEnd - xPlan) * (d / map.horizon_days);
    parts.push(`<line x1="${x}" y1="26" x2="${x}" y2="${H - 10}" stroke="${PALETTE.ruleSoft}" stroke-width="1"/>`);
    if (d % 2 === 0) parts.push(lab(x, 18, "Day " + d, PALETTE.inkDim));
  }

  let y = 40;
  const planY = plans.map(p => {
    const band = Math.max(1, p.branches.length) * 30;
    const centre = y + band / 2 - 15;
    y += band + 16;
    return centre;
  });
  const yRoot = H / 2;

  plans.forEach((p, i) => {
    const rec = p.id === map.recommended;
    const col = rec ? PALETTE.real : PALETTE.sim;
    const py = planY[i];
    parts.push(`<path class="edge p${i}" fill="none" stroke-linecap="round" stroke="${col}"
      stroke-width="${rec ? 2.6 : 1.6}"
      d="M${xRoot},${yRoot} C${(xRoot + xPlan) / 2},${yRoot} ${(xRoot + xPlan) / 2},${py} ${xPlan},${py}"/>`);
    parts.push(lab(xPlan - 8, py - 7, p.name, col, "end"));

    let by = py - (p.branches.length - 1) * 15;
    p.branches.forEach((b, j) => {
      const late = b.metrics.late_surprises > 0;
      const ec = late ? PALETTE.dead : col;
      parts.push(`<path class="edge p${i} b${i}-${j}" fill="none" stroke-linecap="round" stroke="${ec}"
        stroke-width="${(0.8 + 5 * b.p).toFixed(2)}" opacity="${(0.35 + 0.65 * b.p).toFixed(2)}"
        d="M${xPlan},${py} C${(xPlan + xEnd) / 2},${py} ${(xPlan + xEnd) / 2},${by} ${xEnd},${by}"/>`);
      parts.push(`<circle cx="${xEnd}" cy="${by}" r="${rec ? 4.5 : 3.5}" fill="${ec}"/>`);
      const text = `${b.metrics.replies} reply · ${b.metrics.late_surprises} late · ${Math.round(b.p * 100)}%`;
      parts.push(
        `<g class="endpoint" data-plan="${i}" data-branch="${j}" tabindex="0" role="button"
           aria-label="${xmlesc(p.name)}: ${xmlesc(text)}">
           <rect x="${xEnd}" y="${by - 11}" width="200" height="22" fill="transparent"/>
           ${lab(xEnd + 10, by + 3.5, text, late ? PALETTE.dead : PALETTE.inkMid)}
         </g>`);
      by += 30;
    });
  });

  parts.push(`<circle cx="${xRoot}" cy="${yRoot}" r="5" fill="${PALETTE.real}"/>`);
  parts.push(lab(xRoot, yRoot - 13, "Now", PALETTE.real));

  return { body: parts.join(""), width: W, height: H };
}

/* A standalone card: the same map, on its own ground, with a headline and a
   plain statement of what it is. */
function branchMapCard(map, meta = {}) {
  const inner = buildBranchMap(map, { width: 1000 });
  const TOP = 96, BOTTOM = 54;
  const W = inner.width, H = inner.height + TOP + BOTTOM;
  const title = meta.title || "A week, rehearsed";
  const sub = meta.subtitle || "";
  const foot = meta.footer || "Antechamber — rehearse the week before you live it";

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <rect width="${W}" height="${H}" fill="${PALETTE.ground}"/>
  <text x="34" y="46" fill="${PALETTE.ink}" font-family='${SERIF}' font-size="27">${xmlesc(title)}</text>
  ${sub ? `<text x="34" y="72" fill="${PALETTE.inkMid}" font-family='${SERIF}' font-size="15">${xmlesc(sub)}</text>` : ""}
  <line x1="34" y1="${TOP - 12}" x2="${W - 34}" y2="${TOP - 12}" stroke="${PALETTE.rule}"/>
  <g transform="translate(0,${TOP})">${inner.body}</g>
  <line x1="34" y1="${H - 34}" x2="${W - 34}" y2="${H - 34}" stroke="${PALETTE.rule}"/>
  ${lab(34, H - 14, foot, PALETTE.inkDim)}
  ${lab(W - 34, H - 14, "Amber: recommended · Cyan: simulated · Rust: late surprise", PALETTE.inkDim, "end")}
</svg>`;
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadMapSVG(map, meta, filename = "branch-map.svg") {
  saveBlob(new Blob([branchMapCard(map, meta)], { type: "image/svg+xml" }), filename);
}

function downloadMapPNG(map, meta, filename = "branch-map.png", scale = 2) {
  const svg = branchMapCard(map, meta);
  const size = svg.match(/width="(\d+)" height="(\d+)"/);
  const w = +size[1], h = +size[2];
  const img = new Image();
  // A data URL keeps the canvas untainted, which a blob URL does not reliably do.
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = w * scale; c.height = h * scale;
    const ctx = c.getContext("2d");
    ctx.fillStyle = PALETTE.ground;
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    c.toBlob(b => saveBlob(b, filename), "image/png");
  };
  img.onerror = () => downloadMapSVG(map, meta, filename.replace(/\.png$/, ".svg"));
  img.src = "data:image/svg+xml;base64," +
    btoa(unescape(encodeURIComponent(svg)));
}
