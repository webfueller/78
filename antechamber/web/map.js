/* The branch map, drawn once and used twice: live in the page, and as a
   standalone file you can post.

   Colours are literals rather than CSS variables on purpose. An exported SVG
   has no stylesheet to inherit from, and a map that renders correctly in the app
   and grey on someone's timeline is worse than no export at all.

   Nothing personal goes on the card. Plan names, counts and probabilities only:
   no contact is named, no subject line appears, no message body is quoted. That
   is what makes it safe to share, and it is a design constraint, not an
   oversight.

   Layout, left to right: a gutter that belongs to the plan names and nothing
   else, the fork out of Now, the week the fork runs through, and a small table
   of how each future lands. Names live in the gutter because a curve and a
   label fighting over the same pixels is how the old map lost both. */

const PALETTE = {
  ground: "#0C1720", panel: "#12212C", sunken: "#091219", rule: "#21384A", ruleSoft: "#1A2C3A",
  ink: "#D9E4E9", inkMid: "#A8BCC6", inkDim: "#7B95A3",
  real: "#E8A33D", sim: "#61B7C5",
  // `dead` is the stroke; `deadInk` is the same idea at a lightness that clears
  // 4.5:1 as text on both grounds. One rust would have to fail one of the jobs.
  dead: "#C4614C", deadInk: "#DC7B63",
};

const MONO = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';
const SERIF = '"Iowan Old Style", Charter, Georgia, serif';

/* The width the shareable card is always drawn at, whatever the screen the
   person exporting it happens to be on. A card that changed shape with the
   browser window would be a different artifact every time. */
const CARD_WIDTH = 1060;

const xmlesc = s => String(s).replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function lab(x, y, text, fill, anchor = "start", size = 9.5, extra = "") {
  const track = size >= 12 ? 0.3 : size >= 11 ? 0.6 : 1.1;
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${fill}" font-family='${MONO}'
    font-size="${size}" letter-spacing="${track}" ${extra}>${xmlesc(text)}</text>`;
}

/* Monospace makes the advance width knowable, so a name can be wrapped to the
   gutter without measuring text in a DOM that the exporter does not have. */
function wrapMono(text, size, maxWidth, maxLines) {
  const budget = Math.max(6, Math.floor(maxWidth / (size * 0.61)));
  const lines = [];
  let cur = "";
  for (const word of String(text).split(/\s+/)) {
    const next = cur ? cur + " " + word : word;
    if (next.length <= budget) { cur = next; continue; }
    if (cur) lines.push(cur);
    cur = word.length > budget ? word.slice(0, budget - 1) + "…" : word;
  }
  if (cur) lines.push(cur);
  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  kept[maxLines - 1] = kept[maxLines - 1].slice(0, budget - 1).replace(/[\s·+]+$/, "") + "…";
  return kept;
}

/* Late futures are marked by shape as well as colour: a triangle instead of a
   dot, a broken line instead of a solid one. Rust alone would put the one thing
   on the map that is bad news behind a colour channel not everyone has. */
function marker(cx, cy, r, fill, late) {
  return late
    ? `<path d="M${cx},${(cy - r - 1).toFixed(1)} L${(cx + r + 1).toFixed(1)},${(cy + r).toFixed(1)}
         L${(cx - r - 1).toFixed(1)},${(cy + r).toFixed(1)} Z" fill="${fill}"/>`
    : `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}"/>`;
}

/* Returns { body, width, height } -- body is SVG markup with no <svg> wrapper,
   so the page can drop it into a live element and the exporter can wrap it. */
function buildBranchMap(map, opts = {}) {
  const W = Math.round(opts.width || CARD_WIDTH);
  const tight = W < 780;
  const plans = map.plans;
  const days = Math.max(1, map.horizon_days);

  const PAD = 14;
  const ROW = tight ? 24 : 27;        // one future
  const GAP = tight ? 26 : 32;        // between plans
  const TOP = 52, BOT = 18;

  const endW = tight ? 172 : 214;
  const gut = Math.round(Math.min(Math.max(W * 0.21, 108), 236));
  const xEnd = W - PAD - endW;
  // The root needs elbow room: with an odd number of plans one band centre lands
  // on it, and NOW butting up against that plan's name reads as one word.
  const xRoot = gut + 44;
  const xPlan = Math.round(xRoot + (xEnd - xRoot) * 0.3);

  // Columns inside the little table on the right, right-aligned so the digits
  // stack into a column you can read down.
  const cRep = xEnd + (tight ? 48 : 60);
  const cLate = xEnd + (tight ? 88 : 110);
  const cPct = xEnd + (tight ? 128 : 156);
  const barX = xEnd + (tight ? 134 : 164);
  const barW = tight ? 32 : 44;

  let y = TOP;
  const bands = plans.map(p => {
    const n = Math.max(1, p.branches.length);
    const band = { y0: y, h: n * ROW, n };
    y += band.h + GAP;
    return band;
  });
  const H = Math.round(y - GAP + BOT);
  const gridTop = TOP - 14, gridBot = H - BOT + 8;
  const yRoot = Math.round((TOP + (y - GAP)) / 2);

  const parts = [];

  // ---- the week, as a ruler. Every day gets a tick; the labels thin out when
  // there is no room, but the ticks never lie about how many days there are.
  const dayX = d => xPlan + (xEnd - xPlan) * (d / days);
  const step = tight && days > 5 ? 2 : 1;
  for (let d = 0; d <= days; d++) {
    const x = +dayX(d).toFixed(1);
    const edge = d === 0 || d === days;
    parts.push(`<line x1="${x}" y1="${gridTop}" x2="${x}" y2="${gridBot}"
      stroke="${edge ? PALETTE.rule : PALETTE.ruleSoft}" stroke-width="1"/>`);
    // The last day's number tucks inside the axis so it cannot be read as the
    // first word of the column headings that begin just to its right.
    if (d % step === 0 || edge) {
      parts.push(d === days
        ? lab(x - 5, TOP - 22, String(d), PALETTE.inkDim, "end", 9.5)
        : lab(x, TOP - 22, String(d), PALETTE.inkDim, "middle", 9.5));
    }
  }

  // ---- what each region of the picture is
  parts.push(lab(PAD, 20, "PLAN", PALETTE.inkDim, "start", 9));
  parts.push(lab(xPlan, 20, tight ? "DAYS FROM NOW" : "DAYS FROM NOW →", PALETTE.inkDim, "start", 9));
  parts.push(lab(cRep, TOP - 22, tight ? "REPL" : "REPLIES", PALETTE.inkDim, "end", 9));
  parts.push(lab(cLate, TOP - 22, "LATE", PALETTE.inkDim, "end", 9));
  parts.push(lab(cPct, TOP - 22, "CHANCE", PALETTE.inkDim, "end", 9));
  parts.push(`<line x1="${xEnd}" y1="${TOP - 15}" x2="${W - PAD}" y2="${TOP - 15}"
    stroke="${PALETTE.rule}" stroke-width="1"/>`);

  // ---- the futures, plan by plan
  plans.forEach((p, i) => {
    const rec = p.id === map.recommended;
    const col = rec ? PALETTE.real : PALETTE.sim;
    const band = bands[i];
    const py = Math.round(band.y0 + band.h / 2);

    if (i) parts.push(`<line x1="${PAD}" y1="${band.y0 - GAP / 2}" x2="${W - PAD}" y2="${band.y0 - GAP / 2}"
      stroke="${PALETTE.ruleSoft}" stroke-width="1"/>`);
    if (rec) parts.push(`<rect x="${PAD}" y="${band.y0 - 7}" width="${W - 2 * PAD}" height="${band.h + 14}"
      fill="${PALETTE.real}" fill-opacity="0.055"/>`);

    // The gutter belongs to the plan and nothing else: a recommendation, the
    // name, and the honest note about how much of the distribution is on show.
    // Each of those is its own line, because one long line was how the label got
    // clipped off the left edge of the card.
    const nameSize = tight ? 10 : 11;
    const LH = nameSize + 3, MH = 11.5;
    const lines = wrapMono(p.name, nameSize, gut - PAD - 10, 2);
    const cover = Math.round((p.coverage || 0) * 100);
    const notes = [];
    if (rec) notes.push(["RECOMMENDED", PALETTE.real]);
    if (cover) notes.push([tight ? `${cover}% of the odds`
                                 : `${band.n} ${band.n === 1 ? "future" : "futures"} · ${cover}% of the odds`,
                           PALETTE.inkDim]);
    const block = lines.length * LH + notes.length * MH;
    let ty = py - block / 2 + nameSize * 0.82;
    lines.forEach(ln => {
      parts.push(lab(gut, ty, ln, rec ? PALETTE.real : PALETTE.ink, "end", nameSize));
      ty += LH;
    });
    notes.forEach(([text, colour]) => {
      parts.push(lab(gut, ty, text, colour, "end", 8.5));
      ty += MH;
    });

    // A dotted leader ties the name to its spine without ever crossing it.
    parts.push(`<line x1="${gut + 10}" y1="${py}" x2="${xRoot - 10}" y2="${py}"
      stroke="${PALETTE.rule}" stroke-width="1" stroke-dasharray="1.5 3.5"/>`);

    parts.push(`<path class="edge p${i}" fill="none" stroke-linecap="round" stroke="${col}"
      stroke-width="${rec ? 2.8 : 1.6}"
      d="M${xRoot},${yRoot} C${(xRoot + xPlan) / 2},${yRoot} ${(xRoot + xPlan) / 2},${py} ${xPlan},${py}"/>`);
    parts.push(marker(xPlan, py, rec ? 3.4 : 2.6, col, false));

    p.branches.forEach((b, j) => {
      const late = b.metrics.late_surprises > 0;
      const ec = late ? PALETTE.dead : col;
      const by = Math.round(band.y0 + ROW / 2 + j * ROW);
      const pct = Math.round(b.p * 100);
      parts.push(`<path class="edge p${i} b${i}-${j}" fill="none" stroke-linecap="round" stroke="${ec}"
        stroke-width="${(0.9 + 4.4 * b.p).toFixed(2)}" opacity="${(0.4 + 0.6 * b.p).toFixed(2)}"
        ${late ? 'stroke-dasharray="7 3.5"' : ""}
        d="M${xPlan},${py} C${(xPlan + xEnd) / 2},${py} ${(xPlan + xEnd) / 2},${by} ${xEnd},${by}"/>`);
      parts.push(marker(xEnd, by, rec ? 4.4 : 3.4, ec, late));

      const ty = by + 3.4;
      const nLate = b.metrics.late_surprises;
      const row = [
        lab(cRep, ty, String(b.metrics.replies), PALETTE.inkMid, "end", 10.5),
        nLate
          ? lab(cLate, ty, "▲ " + nLate, PALETTE.deadInk, "end", 10.5)
          : lab(cLate, ty, "—", PALETTE.inkDim, "end", 10.5),
        lab(cPct, ty, pct + "%", late ? PALETTE.deadInk : PALETTE.inkMid, "end", 10.5),
        `<rect x="${barX}" y="${by - 3}" width="${barW}" height="6" fill="${PALETTE.sunken}" stroke="${PALETTE.ruleSoft}"/>`,
        `<rect x="${barX}" y="${by - 3}" width="${Math.max(1, barW * b.p).toFixed(1)}" height="6" fill="${col}" fill-opacity="0.85"/>`,
      ].join("");

      // The hit rect carries `fill="none"` as an attribute, not only as CSS. An
      // exported card has no stylesheet, and an unpainted <rect> defaults to
      // solid black -- twenty-five black bars straight across the artifact. The
      // page's own rule still wins for the hover and focus states.
      const reads = `${b.metrics.replies} ${b.metrics.replies === 1 ? "reply" : "replies"}, ` +
        `${nLate ? nLate + " late surprise" + (nLate === 1 ? "" : "s") : "nothing lands late"}, ` +
        `${pct}% likely`;
      parts.push(
        `<g class="endpoint${late ? " late" : ""}" data-plan="${i}" data-branch="${j}" tabindex="0" role="button"
           aria-label="${xmlesc(p.name)}, future ${j + 1} of ${band.n}: ${reads}">
           <rect class="hit" fill="none" x="${xEnd - 8}" y="${by - ROW / 2 + 1}"
             width="${W - PAD - xEnd + 8}" height="${ROW - 2}" rx="2"/>
           ${row}
         </g>`);
    });
  });

  // ---- Now, last so it sits on top of the fan
  parts.push(`<line x1="${xRoot}" y1="${yRoot - 16}" x2="${xRoot}" y2="${yRoot + 16}"
    stroke="${PALETTE.rule}" stroke-width="1"/>`);
  parts.push(`<circle cx="${xRoot}" cy="${yRoot}" r="5" fill="${PALETTE.real}"/>`);
  parts.push(lab(xRoot, yRoot - 21, "NOW", PALETTE.real, "middle", 9.5));

  return { body: parts.join(""), width: W, height: H };
}

/* A standalone card: the same map, on its own ground, with a headline, a legend
   that explains its own colours, and a plain statement of what it is. */
function branchMapCard(map, meta = {}) {
  const inner = buildBranchMap(map, { width: CARD_WIDTH });
  const TOP = 104, BOTTOM = 74;
  const W = inner.width, H = inner.height + TOP + BOTTOM;
  const title = meta.title || "A week, rehearsed";
  const sub = meta.subtitle || "";
  const foot = meta.footer || "Antechamber — rehearse the week before you live it";
  const legendY = H - 44;

  const key = (x, text, colour, shape) => `
    ${shape === "tri"
      ? `<path d="M${x + 6},${legendY - 8} L${x + 12},${legendY + 1} L${x},${legendY + 1} Z" fill="${colour}"/>`
      : `<line x1="${x}" y1="${legendY - 3}" x2="${x + 14}" y2="${legendY - 3}" stroke="${colour}"
           stroke-width="2.4" stroke-linecap="round" ${shape === "dash" ? 'stroke-dasharray="5 3"' : ""}/>`}
    ${lab(x + 21, legendY, text, colour, "start", 9.5)}`;

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <rect width="${W}" height="${H}" fill="${PALETTE.ground}"/>
  <text x="34" y="50" fill="${PALETTE.ink}" font-family='${SERIF}' font-size="28">${xmlesc(title)}</text>
  ${sub ? `<text x="34" y="78" fill="${PALETTE.inkMid}" font-family='${SERIF}' font-size="15">${xmlesc(sub)}</text>` : ""}
  <line x1="34" y1="${TOP - 14}" x2="${W - 34}" y2="${TOP - 14}" stroke="${PALETTE.rule}"/>
  <g transform="translate(0,${TOP})">${inner.body}</g>
  <line x1="34" y1="${legendY - 22}" x2="${W - 34}" y2="${legendY - 22}" stroke="${PALETTE.rule}"/>
  ${key(34, "RECOMMENDED", PALETTE.real, "line")}
  ${key(200, "SIMULATED FUTURE", PALETTE.sim, "line")}
  ${key(400, "SOMETHING LANDS LATE", PALETTE.deadInk, "tri")}
  ${lab(W - 34, legendY, foot, PALETTE.inkDim, "end")}
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

/* ------------------------------------------------------- glue both pages share

   The map is drawn at the width the column actually has, and redrawn when that
   changes. Below 600 units it stops scaling and starts scrolling: shrinking a
   graphic until its own labels are 6px tall is not responsive, it is broken. */

const MAP_MIN = 600, MAP_MAX = 1180;

function branchMapWidth(scroller) {
  const avail = Math.round((scroller && scroller.clientWidth) || 900);
  return Math.max(MAP_MIN, Math.min(avail, MAP_MAX));
}

function wireBranchMap(svgEl, built, handlers = {}) {
  svgEl.setAttribute("viewBox", `0 0 ${built.width} ${built.height}`);
  svgEl.style.minWidth = built.width + "px";
  svgEl.innerHTML = built.body;
  svgEl.querySelectorAll(".endpoint").forEach(g => {
    const i = +g.dataset.plan, j = +g.dataset.branch;
    if (handlers.onPick) {
      g.addEventListener("click", () => handlers.onPick(i, j));
      g.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handlers.onPick(i, j); }
      });
    }
    if (handlers.onHover) {
      g.addEventListener("mouseenter", () => handlers.onHover(i, j));
      g.addEventListener("mouseleave", () => handlers.onHover(null));
      g.addEventListener("focus", () => handlers.onHover(i, j));
      g.addEventListener("blur", () => handlers.onHover(null));
    }
  });
}

function onContainerResize(el, fn) {
  if (window.ResizeObserver) {
    let last = 0;
    new ResizeObserver(() => {
      const w = branchMapWidth(el);
      if (Math.abs(w - last) < 8) return;
      last = w;
      fn();
    }).observe(el);
  } else {
    window.addEventListener("resize", fn);
  }
}
