/* The sort animation: photos drop onto a messy pile, rejects slide to the discard tray,
   the rest fly into moment clusters, Claude's winners lift out, and picks land in the rail.
   Every card lives on one absolutely-positioned layer; each phase computes target
   positions and tweens to them, so a window resize can re-lay out any phase instantly. */
(function () {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const D = (s) => (reduce ? 0 : s);
  const CW = 78;           // card width on the pile, px
  const rand = (a, b) => a + Math.random() * (b - a);

  const S = {
    stage: null, layer: null, clustersEl: null, tray: null, rail: null,
    cards: new Map(),      // fp -> card
    order: [],             // cards in arrival order
    clusters: new Map(),   // moment id -> {el, cards}
    phase: "empty",
    clock: 0,              // when the next dropped card may start (performance.now seconds)
    state: null,
  };

  function now() { return performance.now() / 1000; }
  function box() { return S.stage.getBoundingClientRect(); }

  function init() {
    S.stage = document.getElementById("stage");
    S.layer = document.getElementById("layer");
    S.clustersEl = document.getElementById("clusters");
    S.tray = document.getElementById("discard");
    S.rail = document.getElementById("rail");
    let t;
    addEventListener("resize", () => { clearTimeout(t); t = setTimeout(relayout, 120); });
  }

  function reset() {
    gsap.killTweensOf("*");
    S.layer.innerHTML = "";
    S.clustersEl.innerHTML = "";
    S.cards.clear(); S.order = []; S.clusters.clear();
    S.phase = "empty"; S.clock = 0; S.state = null;
    S.rail.classList.remove("shown");
    setCount("discard-count", 0); setCount("rail-picks", 0); setCount("rail-gems", 0);
  }

  function setCount(id, n) { document.getElementById(id).textContent = n; }

  /* ---------- pile ---------- */
  function pileSpot() {
    const b = box();
    const a = rand(0, Math.PI * 2), r = Math.sqrt(Math.random());
    return {
      x: b.width / 2 + Math.cos(a) * r * b.width * 0.30,
      y: b.height * 0.44 + Math.sin(a) * r * b.height * 0.27,
      r: rand(-32, 32), s: 1,
    };
  }

  function addCard(fp, total) {
    if (S.cards.has(fp)) return;
    const el = document.createElement("img");
    el.className = "card";
    el.alt = "";
    el.decoding = "async";
    el.style.width = CW + "px";
    el.style.height = CW / 1.5 + "px";
    el.onload = () => { el.style.height = Math.round(CW * el.naturalHeight / el.naturalWidth) + "px"; };
    el.src = "/api/thumb/" + fp;
    el.style.zIndex = S.order.length + 1;
    S.layer.appendChild(el);
    const card = { el, fp, id: null, status: null, moment: null, home: pileSpot() };
    S.cards.set(fp, card);
    S.order.push(card);
    S.phase = "pile";

    const gap = Math.min(0.05, Math.max(0.008, 3.2 / Math.max(total, 1)));
    const t = now();
    const delay = Math.max(0, S.clock - t);
    S.clock = Math.max(S.clock, t) + gap;
    const h = card.home;
    gsap.set(el, { xPercent: -50, yPercent: -50 });
    gsap.fromTo(el,
      { x: h.x + rand(-90, 90), y: -140, rotation: h.r + rand(-70, 70), scale: 1.35, opacity: 0 },
      { x: h.x, y: h.y, rotation: h.r, scale: 1, opacity: 1, duration: D(0.75), delay: D(delay), ease: "power3.out" });
  }

  /** Milliseconds until every queued drop has landed. */
  function settleMs() { return Math.max(0, (S.clock - now()) * 1000 + (reduce ? 0 : 750)); }

  /* ---------- discard ---------- */
  function trayTarget() {
    const b = box(), t = S.tray.getBoundingClientRect();
    return { x: t.left - b.left + t.width / 2 + rand(-18, 18), y: t.top - b.top + t.height / 2 - 8 + rand(-8, 8),
             r: rand(-14, 14), s: 0.55 };
  }

  function discard(cards) {
    let n = 0;
    cards.forEach((c, i) => {
      c.home = trayTarget();
      c.el.classList.add("rejected");
      gsap.to(c.el, { x: c.home.x, y: c.home.y, rotation: c.home.r, scale: c.home.s, duration: D(0.6),
        delay: D(i * 0.012), ease: "power2.in", onComplete: () => setCount("discard-count", ++n) });
    });
    return cards.length * 12 + 600;
  }

  /* ---------- clusters ---------- */
  function clusterGrid(n) {
    const b = box(), pad = 16, gap = 10;
    const aw = b.width - pad * 2, ah = b.height - pad - 120;
    let best = { size: 0 };
    for (let c = 1; c <= n; c++) {
      const rows = Math.ceil(n / c);
      const cw = (aw - (c - 1) * gap) / c, ch = (ah - (rows - 1) * gap) / rows;
      const size = Math.min(cw, ch * 4 / 3);
      if (size > best.size) best = { size, cols: c };
    }
    const w = best.size, h = best.size * 3 / 4;
    const totalW = best.cols * w + (best.cols - 1) * gap;
    const x0 = (b.width - totalW) / 2;
    return (i) => ({ x: x0 + (i % best.cols) * (w + gap), y: pad + Math.floor(i / best.cols) * (h + gap), w, h });
  }

  function cardH(c) { return parseFloat(c.el.style.height) || CW / 1.5; }

  function fanTargets(cl) {
    const { rect, cards } = cl;
    const k = cards.length;
    const fit = (c) => Math.min((rect.w * 0.6) / CW, ((rect.h - 22) * 0.8) / cardH(c));
    const spread = Math.min(rect.w * 0.22 / Math.max(k, 1), 9);
    const turn = Math.min(4, 18 / Math.max(k, 1));
    // winners on top, then gems, then the rest
    const rank = (c) => (c.status === "pick" ? 2 : c.status === "gem" ? 1 : 0);
    const ordered = [...cards].sort((a, b) => rank(a) - rank(b));
    ordered.forEach((c, i) => {
      const off = i - (k - 1) / 2;
      const lifted = rank(c) > 0;
      c.home = {
        x: rect.x + rect.w / 2 + (lifted ? 0 : off * spread),
        y: rect.y + rect.h / 2 - 8 - (lifted ? 4 : 0),
        r: lifted ? rand(-2, 2) : off * turn,
        s: lifted ? fit(c) * 1.12 : fit(c),
      };
      c.z = 1000 + cl.index * 20 + i + (lifted ? 10 : 0);
    });
    return ordered;
  }

  function buildClusters(state) {
    S.state = state;
    const byId = new Map();
    state.photos.forEach((p) => {
      const c = S.cards.get(p.fp);
      if (c) { c.id = p.id; c.status = p.status; c.moment = p.moment; c.p = p; byId.set(p.id, c); }
    });
    const live = state.moments
      .map((m) => ({ m, cards: m.members.map((id) => byId.get(id)).filter((c) => c && c.status !== "rejected") }))
      .filter((x) => x.cards.length);
    const grid = clusterGrid(live.length);
    S.clustersEl.innerHTML = "";
    S.clusters.clear();
    live.forEach(({ m, cards }, i) => {
      const el = document.createElement("div");
      el.className = "cluster";
      const first = byId.get(m.members[0]).p;
      el.innerHTML = `<div class="clabel">${timeLabel(first.taken)} · ${cards.length}</div>`;
      S.clustersEl.appendChild(el);
      S.clusters.set(m.id, { el, cards, index: i, rect: grid(i) });
    });
    return live;
  }

  function placeCluster(cl) {
    const r = cl.rect;
    Object.assign(cl.el.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
  }

  function toClusters(state) {
    const rejected = S.order.filter((c) => {
      const p = state.photos.find((q) => q.fp === c.fp);
      return p && p.status === "rejected";
    });
    const tDiscard = discard(rejected);
    buildClusters(state);
    S.phase = "clusters";
    let k = 0;
    S.clusters.forEach((cl) => {
      placeCluster(cl);
      gsap.to(cl.el, { delay: D(tDiscard / 1000 * 0.5 + cl.index * 0.02), duration: D(0.01), onStart: () => cl.el.classList.add("shown") });
      fanTargets(cl).forEach((c) => {
        c.el.style.zIndex = c.z;
        applyClass(c);
        gsap.to(c.el, { x: c.home.x, y: c.home.y, rotation: c.home.r, scale: c.home.s, duration: D(1.15),
          delay: D(tDiscard / 1000 * 0.5 + 0.004 * k++), ease: "power3.inOut" });
      });
    });
    return tDiscard * 0.5 + k * 4 + 1150;
  }

  function applyClass(c) {
    c.el.classList.toggle("pick", c.status === "pick");
    c.el.classList.toggle("gem", c.status === "gem");
    c.el.classList.toggle("dim", ["similar", "below", "rejected"].includes(c.status) && S.phase !== "pile");
  }

  /* ---------- review ---------- */
  function reviewStart(momentId) {
    const cl = S.clusters.get(momentId);
    if (cl) cl.el.classList.add("reviewing");
  }

  function reviewDone(momentId, photos) {
    const cl = S.clusters.get(momentId);
    photos.forEach((p) => {
      const c = S.cards.get(p.fp);
      if (c) { c.status = p.status; c.p = p; }
    });
    if (!cl) return;
    cl.el.classList.remove("reviewing");
    cl.el.classList.toggle("has-pick", cl.cards.some((c) => c.status === "pick"));
    fanTargets(cl).forEach((c) => {
      c.el.style.zIndex = c.z;
      applyClass(c);
      const win = c.status === "pick" || c.status === "gem";
      gsap.to(c.el, { x: c.home.x, y: c.home.y, rotation: c.home.r, scale: c.home.s,
        duration: D(win ? 0.7 : 0.5), ease: win ? "back.out(2)" : "power2.out" });
    });
  }

  /** Apply a full state without per-moment events (cached reviews). */
  function applyAll(state) {
    const byMoment = new Map();
    state.photos.forEach((p) => {
      if (!byMoment.has(p.moment)) byMoment.set(p.moment, []);
      byMoment.get(p.moment).push(p);
    });
    let i = 0;
    byMoment.forEach((ps, m) => setTimeout(() => reviewDone(m, ps), reduce ? 0 : 25 * i++));
    return reduce ? 0 : 25 * i + 700;
  }

  /* ---------- finale ---------- */
  function finale(state) {
    S.state = state;
    state.photos.forEach((p) => { const c = S.cards.get(p.fp); if (c) { c.status = p.status; c.p = p; } });
    S.phase = "final";
    S.rail.classList.add("shown");
    const b = box(), r = S.rail.getBoundingClientRect();
    const picks = S.order.filter((c) => c.status === "pick").sort((a, b2) => (b2.p.final || 0) - (a.p.final || 0));
    const gems = S.order.filter((c) => c.status === "gem");
    const h = 52, pickW = r.width * 0.72, gemX = r.left - b.left + r.width * 0.78;
    const step = (n, w) => Math.min(h * 1.6, (w - h * 1.5) / Math.max(n - 1, 1));
    picks.forEach((c, i) => {
      c.home = { x: r.left - b.left + h * 0.9 + i * step(picks.length, pickW), y: r.top - b.top + r.height / 2 - 6,
                 r: rand(-3, 3), s: h / cardH(c) };
      c.el.style.zIndex = 3000 + i;
    });
    gems.forEach((c, i) => {
      c.home = { x: gemX + h * 0.9 + i * step(gems.length, r.width * 0.2), y: r.top - b.top + r.height / 2 - 6,
                 r: rand(-3, 3), s: h / cardH(c) };
      c.el.style.zIndex = 3000 + picks.length + i;
    });
    let nPicks = 0, nGems = 0;
    [...picks, ...gems].forEach((c, i) => {
      applyClass(c);
      gsap.to(c.el, { x: c.home.x, y: c.home.y, rotation: c.home.r, scale: c.home.s, duration: D(0.9),
        delay: D(0.05 * i), ease: "power3.inOut",
        onComplete: () => c.status === "pick" ? setCount("rail-picks", ++nPicks) : setCount("rail-gems", ++nGems) });
    });
    setTimeout(() => { setCount("rail-picks", picks.length); setCount("rail-gems", gems.length); },
      reduce ? 0 : 900 + 50 * (picks.length + gems.length));
    S.order.forEach((c) => {
      if (c.status !== "pick" && c.status !== "gem" && c.status !== "rejected") {
        gsap.to(c.el, { opacity: 0.22, duration: D(0.6), delay: D(0.3) });
      }
    });
    return reduce ? 0 : 900 + 50 * (picks.length + gems.length);
  }

  /* ---------- resize ---------- */
  function relayout() {
    if (S.phase === "pile" || S.phase === "empty") {
      S.order.forEach((c) => { c.home = pileSpot(); gsap.set(c.el, { x: c.home.x, y: c.home.y }); });
      return;
    }
    const tray = S.order.filter((c) => c.status === "rejected");
    tray.forEach((c) => { c.home = trayTarget(); gsap.set(c.el, { x: c.home.x, y: c.home.y }); });
    const grid = clusterGrid(S.clusters.size);
    S.clusters.forEach((cl) => { cl.rect = grid(cl.index); placeCluster(cl); });
    if (S.phase === "final") { finale(S.state); return; }
    S.clusters.forEach((cl) => fanTargets(cl).forEach((c) =>
      gsap.set(c.el, { x: c.home.x, y: c.home.y, rotation: c.home.r, scale: c.home.s })));
  }

  function timeLabel(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  window.Stage = { init, reset, addCard, settleMs, toClusters, reviewStart, reviewDone, applyAll, finale };
})();
