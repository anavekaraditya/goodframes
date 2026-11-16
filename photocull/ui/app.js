/* Goodframes UI: screens, API calls, live events and the results browser. */
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const app = { settings: null, shoot: null, estimate: null, tab: "pick", person: null, reviewing: null, spent: 0, chatSpent: 0, brief: null, refining: false, briefBusy: false };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.body !== undefined || opts.method ? opts.method || "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
    return data;
  }

  function show(id) {
    $$(".screen").forEach((s) => (s.hidden = s.id !== id));
  }

  let toastTimer;
  function toast(msg, action) {
    const t = $("#toast");
    t.innerHTML = "";
    t.append(msg);
    if (action) {
      const b = document.createElement("button");
      b.className = "btn ghost";
      b.style.marginLeft = "12px";
      b.textContent = action.label;
      b.onclick = action.run;
      t.append(b);
    }
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), action ? 7000 : 4000);
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  /* ---------- boot ---------- */
  async function boot() {
    Stage.init();
    app.settings = await api("/api/settings");
    applyTheme(app.settings.theme);
    applyTileSize(app.settings.tile_size);
    fillModelSelects();
    connectEvents();
    const opening = intro();
    if (!app.settings.has_key || !app.settings.profile.done) startOnboarding();
    else await showHome();
    opening.play();
  }

  // ?still turns motion off, for checking layouts in a browser that doesn't run animations.
  const MOTION = !!window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches && !location.search.includes("still");

  // Contents of the visible screen rise into place.
  function enter(targets) {
    const els = $$(targets);
    if (!MOTION || !els.length || app.introPlaying) return;
    gsap.from(els, { y: 14, opacity: 0, duration: .5, stagger: .045, ease: "power2.out", clearProps: "transform,opacity" });
  }

  const ENTER = "#home .home-hero > *:not([hidden]), #home .shoots > li, #onboarding .ob-step:not([hidden]) > *:not(.confetti)";

  /* Once per launch: scattered prints fly in, fan out into the mark, the keeper is chosen, then the app appears. */
  function intro() {
    const el = $("#intro");
    if (!MOTION) {
      el.remove();
      return { play() {} };
    }
    const word = $(".intro-word");
    word.innerHTML = [...word.textContent].map((c) => `<span>${esc(c)}</span>`).join("");
    const prints = $$(".intro-mark span");
    app.introPlaying = true;
    const tl = gsap.timeline({ paused: true, onComplete: () => el.remove() });
    tl.from(prints, {
      x: (i) => [-260, 240, 30][i], y: (i) => [-140, 120, 220][i], rotation: (i) => [-50, 40, -20][i],
      opacity: 0, scale: 1.3, duration: .8, stagger: .07, ease: "power3.out",
    })
      .to(prints[0], { rotation: -16, x: -22, duration: .5, ease: "back.out(2)" }, "-=.25")
      .to(prints[1], { rotation: 10, x: 22, duration: .5, ease: "back.out(2)" }, "<")
      .fromTo(prints[2], { boxShadow: "0 0 0 0 rgba(223,189,120,0)" },
        { boxShadow: "0 0 0 6px rgba(223,189,120,.25)", y: -6, duration: .35, yoyo: true, repeat: 1, ease: "power1.inOut" }, "-=.15")
      .from(word.children, { y: 18, opacity: 0, duration: .45, stagger: .03, ease: "power3.out" }, "-=.55")
      .from(".intro-tag", { opacity: 0, y: 8, duration: .4, ease: "power2.out" }, "-=.2")
      .to(".intro-inner", { scale: .94, opacity: 0, duration: .4, ease: "power2.in" }, "+=.3")
      .to(el, { opacity: 0, duration: .4, ease: "power1.out" }, "-=.15")
      .add(() => { app.introPlaying = false; enter(ENTER); }, "-=.35");
    const skip = () => tl.progress(1);
    el.addEventListener("click", skip);
    addEventListener("keydown", skip, { once: true });
    return tl;
  }

  function fillModelSelects() {
    ["#model-select", "#settings-model"].forEach((sel) => {
      $(sel).innerHTML = app.settings.models
        .map((m) => `<option value="${m.id}">${m.label} — $${m.in}/$${m.out} per 1M tokens</option>`)
        .join("");
      $(sel).value = app.settings.model;
    });
  }

  const icon = (name) => `<svg class="i" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  /* ---------- profile pickers (onboarding and settings) ---------- */
  const GENRES = [["weddings", "Weddings"], ["portraits", "Portraits"], ["events", "Events"], ["family", "Family"],
    ["sports", "Sports"], ["wildlife", "Wildlife"], ["street", "Street"], ["travel", "Travel"], ["landscape", "Landscape"],
    ["products", "Products"]];
  const LOOK_PAIRS = [
    [["candid", "Candid moments"], ["posed", "Posed and polished"]],
    [["grain_ok", "Grain and blur are fine"], ["sharp", "Tack sharp or bust"]],
    [["moody", "Moody light"], ["airy", "Bright and airy"]],
    [["emotion", "Emotion first"], ["composition", "Composition first"]]];
  const KEEPS = [["tight", "Ruthless", "Just the best 2", 2], ["balanced", "Balanced", "About 5", 5],
    ["generous", "Generous", "Up to 10", 10]];

  function renderPickers(root, p) {
    const pressed = (on) => `aria-pressed="${on}"`;
    root.querySelector("[data-kind=genres]").innerHTML = GENRES.map(([id, label]) =>
      `<button type="button" class="chip" data-genre="${id}" ${pressed(p.genres.includes(id))}>${icon(id)}${label}</button>`).join("");
    root.querySelector("[data-kind=look]").innerHTML = LOOK_PAIRS.map((pair) => `<div class="pair">${pair.map(([id, label]) =>
      `<button type="button" class="choice" data-look="${id}" ${pressed(p.look.includes(id))}>${icon(id)}${label}</button>`)
      .join(`<span class="vs">or</span>`)}</div>`).join("");
    root.querySelector("[data-kind=keep]").innerHTML = KEEPS.map(([id, label, hint, n]) =>
      `<button type="button" class="keep" data-keep="${id}" ${pressed(p.keep === id)}>${icon(id)}<b>${label}</b>
        <span class="frames">${Array.from({ length: 30 }, (_, i) => `<i class="${i < n ? "kept" : ""}"></i>`).join("")}</span>
        <span class="hint">${hint}</span></button>`).join("");
  }

  // Clicks on a picker change the profile p in place; returns whether anything changed.
  function pick(e, p) {
    const g = e.target.closest("[data-genre]"), l = e.target.closest("[data-look]"), k = e.target.closest("[data-keep]");
    if (g) {
      const id = g.dataset.genre;
      p.genres = p.genres.includes(id) ? p.genres.filter((x) => x !== id) : [...p.genres, id];
    } else if (l) {
      const id = l.dataset.look;
      const partner = LOOK_PAIRS.find((pair) => pair.some(([x]) => x === id)).map(([x]) => x);
      const on = p.look.includes(id);
      p.look = p.look.filter((x) => !partner.includes(x)).concat(on ? [] : [id]);
    } else if (k) {
      p.keep = k.dataset.keep;
    } else return false;
    const el = g || l || k;
    if (MOTION) gsap.fromTo(el, { scale: .94 }, { scale: 1, duration: .35, ease: "back.out(3)", clearProps: "transform" });
    return true;
  }

  function profileSummary(p) {
    const genres = GENRES.filter(([id]) => p.genres.includes(id)).map(([, l]) => l.toLowerCase());
    const list = genres.length > 1 ? `${genres.slice(0, -1).join(", ")} and ${genres.at(-1)}` : genres[0];
    const how = { tight: "ruthlessly", balanced: "with a balanced eye", generous: "generously" }[p.keep];
    return list ? `You shoot ${list} and edit ${how}.` : `You edit ${how}.`;
  }

  /* ---------- onboarding ---------- */
  const PROFILE_STEPS = ["name", "genres", "look", "keep"];
  const ob = { steps: [], i: 0, draft: null, after: null };

  // opts.only: just these steps (e.g. ["key"]). opts.redo: run the profile setup again.
  function startOnboarding(opts = {}) {
    let steps = opts.only || [];
    if (!opts.only) {
      if (!app.settings.profile.done || opts.redo) steps = [...PROFILE_STEPS];
      if (!app.settings.has_key) steps.push("key");
      steps.push("done");
    }
    ob.steps = steps;
    ob.draft = structuredClone(app.settings.profile);
    ob.after = opts.after || (() => (app.shoot ? show("results") : showHome()));
    renderPickers($("#onboarding"), ob.draft);
    $("#ob-name").value = ob.draft.name;
    $("#ob-dots").innerHTML = steps.filter((x) => x !== "done").map(() => "<li></li>").join("");
    show("onboarding");
    goStep(0, 0);
  }

  function askForKey() {
    const back = !$("#results").hidden ? "results" : !$("#stage-screen").hidden ? "stage-screen" : null;
    startOnboarding({ only: ["key"], after: () => (back ? show(back) : showHome()) });
  }

  function goStep(i, dir) {
    const step = ob.steps[i];
    const prev = $(`#onboarding .ob-step:not([hidden])`);
    const next = $(`#onboarding .ob-step[data-step="${step}"]`);
    ob.i = i;
    $$("#ob-dots li").forEach((li, n) => { li.className = n < i ? "done" : n === i ? "on" : ""; });
    const profileStep = PROFILE_STEPS.includes(step);
    $("#ob-skip").hidden = !profileStep;
    $("#ob-back").style.visibility = i > 0 && step !== "done" ? "visible" : "hidden";
    $("#ob-next").textContent = { name: "Let's go", keep: "Save my profile", key: "Connect",
      done: app.shoot ? "Back to my shoot" : "Choose a shoot" }[step] || "Next";
    if (step === "key") {
      fillModelSelects();
      $("#key-error").textContent = "";
    }
    if (step === "done") {
      const name = ob.draft.name;
      $("#ob-done-title").textContent = name ? `Nice to meet you, ${name}!` : "You're all set!";
      $("#ob-done-text").textContent = profileSummary(ob.draft) + " I'll keep that in mind on every cull. You can change it any time in Settings.";
    }
    const swap = () => {
      if (prev && prev !== next) prev.hidden = true;
      next.hidden = false;
      if (MOTION && !app.introPlaying) {
        gsap.from(next.children, { x: 28 * (dir || 1), opacity: 0, duration: .45, stagger: .05, ease: "power3.out", clearProps: "all" });
      }
      if (step === "name") setTimeout(() => $("#ob-name").focus(), 50);
      if (step === "key") setTimeout(() => $("#key-input").focus(), 50);
      if (step === "done") confetti();
    };
    if (prev && prev !== next && MOTION && dir) {
      gsap.to(prev.children, { x: -24 * dir, opacity: 0, duration: .2, ease: "power2.in", onComplete: () => {
        gsap.set(prev.children, { clearProps: "all" });
        swap();
      } });
    } else swap();
  }

  async function saveProfile(p) {
    const { profile } = await api("/api/profile", { body: { name: p.name, genres: p.genres, look: p.look, keep: p.keep } });
    app.settings.profile = profile;
    return profile;
  }

  async function obNext() {
    const step = ob.steps[ob.i];
    if (step === "name") ob.draft.name = $("#ob-name").value.trim();
    if (step === "keep") {
      try {
        await saveProfile(ob.draft);
      } catch (err) {
        toast(err.message);
        return;
      }
    }
    if (step === "key" && !(await submitKey())) return;
    if (ob.i + 1 < ob.steps.length) goStep(ob.i + 1, 1);
    else ob.after();
  }

  async function submitKey() {
    const btn = $("#ob-next");
    $("#key-error").textContent = "";
    if (!$("#key-input").value.trim()) {
      $("#key-error").textContent = "Paste your API key to continue.";
      return false;
    }
    btn.disabled = true;
    btn.textContent = "Checking key…";
    try {
      await api("/api/settings", { body: { model: $("#model-select").value } });
      await api("/api/key", { body: { key: $("#key-input").value } });
      $("#key-input").value = "";
      app.settings = await api("/api/settings");
      fillModelSelects();
      return true;
    } catch (err) {
      $("#key-error").textContent = err.message;
      btn.textContent = "Connect";
      return false;
    } finally {
      btn.disabled = false;
    }
  }

  $("#ob-next").addEventListener("click", obNext);
  $("#ob-back").addEventListener("click", () => ob.i > 0 && goStep(ob.i - 1, -1));
  $("#ob-skip").addEventListener("click", async () => {
    ob.draft.name = ob.draft.name || $("#ob-name").value.trim();
    try {
      await saveProfile(ob.draft);  // whatever they chose so far; the rest stays at the defaults
    } catch (err) {
      toast(err.message);
      return;
    }
    const rest = ob.steps.findIndex((x) => !PROFILE_STEPS.includes(x));
    if (rest >= 0 && ob.steps[rest] !== "done") goStep(rest, 1);
    else ob.after();
  });
  $("#ob-name").addEventListener("keydown", (e) => { if (e.key === "Enter") obNext(); });
  $("#key-form").addEventListener("submit", (e) => {
    e.preventDefault();
    obNext();
  });
  $("#onboarding .ob-stage").addEventListener("click", (e) => {
    if (pick(e, ob.draft)) renderPickers($("#onboarding"), ob.draft);
  });

  // Little prints burst out of the mark.
  function confetti() {
    const box = $("#ob-confetti");
    box.innerHTML = "";
    if (!MOTION) return;
    const colors = ["var(--pick)", "var(--gem)", "#ddd5c8", "#c9c1b4"];
    for (let n = 0; n < 28; n++) {
      const i = document.createElement("i");
      i.style.background = colors[n % colors.length];
      box.append(i);
      const angle = Math.random() * Math.PI * 2, dist = 140 + Math.random() * 200;
      gsap.fromTo(i, { x: -5, y: 0, rotation: 0, opacity: 1, scale: .6 },
        { x: Math.cos(angle) * dist, y: Math.sin(angle) * dist * .7 + 120, rotation: Math.random() * 540 - 270, scale: 1,
          opacity: 0, duration: 1.4 + Math.random() * .6, ease: "power2.out", delay: Math.random() * .15 });
    }
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-url]");
    if (a) {
      e.preventDefault();
      api("/api/open-url", { body: { url: a.dataset.url } });
    }
  });

  /* ---------- home ---------- */
  async function showHome() {
    const [settings, shoots] = await Promise.all([api("/api/settings"), api("/api/recent")]);
    app.settings = settings;
    $("#recent-wrap").hidden = !shoots.length;
    $("#recent").innerHTML = shoots.map(shootCard).join("");
    $("#path-form").hidden = app.settings.desktop;
    $("#dz-sub").textContent = app.settings.desktop
      ? "A folder of JPEG, HEIC or RAW photos. Or drop one here." : "A folder of JPEG, HEIC or RAW photos";
    const name = app.settings.profile.name, hour = new Date().getHours();
    $("#home-hello").hidden = !name;
    $("#home-hello").textContent = `${hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"}, ${name}`;
    show("home");
    enter(ENTER);
    pollCards();
  }

  /* Camera cards: offer to cull one as soon as it's plugged in. */
  let cardTimer = null, cardsShown = "";
  const dismissedCards = new Set();
  async function pollCards() {
    clearTimeout(cardTimer);
    if ($("#home").hidden) return;
    try {
      const cards = (await api("/api/cards")).filter((c) => !dismissedCards.has(c.name));
      const key = JSON.stringify(cards);
      if (key !== cardsShown) {  // re-render only on change, so a hovered button doesn't flicker
        cardsShown = key;
        renderCards(cards);
      }
    } catch (err) { /* checking for cards is best effort */ }
    cardTimer = setTimeout(pollCards, 4000);
  }

  function renderCards(cards) {
    $("#cards").hidden = !cards.length;
    $("#cards").innerHTML = cards.map((c) => {
      const one = c.folders.length === 1;
      const buttons = c.folders.map((f) => `<button class="btn ${one ? "primary" : ""}" data-card-folder="${esc(f.folder)}">${
        one ? "Cull this card" : `${esc(f.name)} · ${f.photos}`}</button>`).join("");
      return `<li class="card-banner"><span class="card-icon">${icon("card")}</span>
        <div class="card-text"><b>${esc(c.name)}</b><span>${c.photos} photo${c.photos > 1 ? "s" : ""} on this card</span></div>
        <div class="card-acts">${buttons}
          <button class="icon-btn" data-dismiss-card="${esc(c.name)}" aria-label="Not now" title="Not now">${icon("close")}</button></div></li>`;
    }).join("");
    if (cards.length && MOTION) gsap.from("#cards li", { y: -8, opacity: 0, duration: .35, ease: "power2.out", clearProps: "all" });
  }

  $("#cards").addEventListener("click", (e) => {
    const f = e.target.closest("[data-card-folder]"), d = e.target.closest("[data-dismiss-card]");
    if (f) openShoot(f.dataset.cardFolder);
    if (d) {
      dismissedCards.add(d.dataset.dismissCard);
      cardsShown = "";
      pollCards();
    }
  });

  /* Drop a folder on the window. The desktop app passes its path in from Python (see desktop.py);
     a browser can't see paths, so there the path field is the way in. */
  window.goodframes = {
    openDropped(folder) {
      document.body.classList.remove("dragging");
      if (!$("#onboarding").hidden) return;
      $$(".modal").forEach((m) => (m.hidden = true));
      openShoot(folder);
    },
  };
  let dragDepth = 0;
  addEventListener("dragenter", (e) => {
    if (!$("#onboarding").hidden || ![...e.dataTransfer.types].includes("Files")) return;
    dragDepth++;
    document.body.classList.add("dragging");
  });
  addEventListener("dragleave", () => {
    if (--dragDepth <= 0) {
      dragDepth = 0;
      document.body.classList.remove("dragging");
    }
  });
  addEventListener("dragover", (e) => e.preventDefault());
  addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    document.body.classList.remove("dragging");
    if (!app.settings.desktop) toast("Dropping a folder works in the Goodframes app. Here, type its path instead.");
  });

  function shootCard(s) {
    const covers = s.covers.map((fp) => `<img src="/api/cover?folder=${encodeURIComponent(s.folder)}&fp=${fp}" alt="" loading="lazy">`).join("")
      || `<div class="mark" aria-hidden="true"><span></span><span></span><span></span></div>`;
    const info = [s.photos ? `${s.photos} photos` : "Not opened yet"];
    if (s.reviewed) info.push(`<span class="culled">Culled</span>`);
    if (s.opened) info.push(ago(s.opened));
    return `<li><button class="shoot-card" data-folder="${esc(s.folder)}" title="${esc(s.folder)}">
      <div class="covers n${s.covers.length}">${covers}</div>
      <div class="shoot-meta"><span class="shoot-title">${esc(s.name)}</span>
        <span class="shoot-info">${info.join("<span aria-hidden=\"true\">·</span>")}</span></div></button></li>`;
  }

  function ago(seconds) {
    const diff = seconds - Date.now() / 1000;
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    for (const [unit, size] of [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]]) {
      if (Math.abs(diff) >= size) return rtf.format(Math.round(diff / size), unit);
    }
    return "just now";
  }

  $("#recent").addEventListener("click", (e) => {
    const b = e.target.closest("[data-folder]");
    if (b) openShoot(b.dataset.folder);
  });

  $("#choose-folder").addEventListener("click", async () => {
    if (!app.settings.desktop) {
      $("#path-input").focus();
      return;
    }
    try {
      const { folder } = await api("/api/pick-folder", { body: {} });
      if (folder) openShoot(folder);
    } catch (err) {
      toast(err.message);
    }
  });

  $("#path-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = $("#path-input").value.trim();
    if (f) openShoot(f);
  });

  async function openShoot(folder) {
    timeouts.splice(0).forEach(clearTimeout);  // drop pending steps of a previous run
    Stage.reset();
    app.shoot = null;
    app.spent = 0;
    app.chatSpent = 0;
    app.refining = false;
    app.unreadable = [];
    app.live = [];
    app.reviewStartedAt = null;
    undoStack.length = 0;
    $("#peek").hidden = true;
    $("#estimate").hidden = $("#finished").hidden = true;
    $("#shoot-name").textContent = folder.split("/").filter(Boolean).pop();
    setStep("ingest");
    status("Opening…");
    show("stage-screen");
    try {
      await api("/api/shoot", { body: { folder } });
    } catch (err) {
      toast(err.message);
      showHome();
    }
  }

  function setStep(step) {
    const order = ["ingest", "technical", "moments", "review", "done"];
    const at = order.indexOf(step);
    $$("#steps li").forEach((li) => {
      const i = order.indexOf(li.dataset.step);
      li.classList.toggle("active", i === at);
      li.classList.toggle("done", i < at);
    });
  }

  function status(t) {
    $("#status").textContent = t;
  }

  /* ---------- live events ---------- */
  function connectEvents() {
    const es = new EventSource("/api/events");
    es.onmessage = (m) => handle(JSON.parse(m.data));
  }

  const timeouts = [];
  function later(ms, fn) {
    timeouts.push(setTimeout(fn, ms));
  }

  function handle(e) {
    switch (e.type) {
      case "stage":  // later steps follow the animation, not the (faster) server
        if (e.stage === "ingest") setStep("ingest");
        break;
      case "ingest":
        if (e.error) {
          app.unreadable.push(e.file);
          break;
        }
        Stage.addCard(e.fp, e.n);
        status(`Reading ${e.i} / ${e.n}`);
        break;
      case "analyzed": {
        if (!e.state) break;  // the pipeline's own early notice; the server's, with the state, follows
        if (!e.state.photos.length) {
          emptyFolder(e.folder_info);
          return;
        }
        if (app.unreadable.length) toast(unreadableText());
        app.shoot = e.state;
        app.estimate = e.estimate;
        // A shoot that was culled before opens on its saved results; reviewing again is the user's choice.
        if (reviewed(e.state)) {
          openResults();
          return;
        }
        const rejects = e.state.counts.rejected;
        later(Stage.settleMs(), () => {
          setStep("technical");
          status(`${rejects} failed the technical check`);
          const t = Stage.toClusters(e.state);
          later(t * 0.45, () => {
            setStep("moments");
            const n = e.state.moments.filter((m) => m.members.some((id) => e.state.photos[id - 1].status !== "rejected")).length;
            status(`${n} moments`);
          });
          later(t + 700, afterGrouping);
        });
        break;
      }
      case "review_start":
        Stage.reviewStart(e.moment);
        break;
      case "review_done":
        app.reviewing.done++;
        app.spent = e.spent;
        Stage.reviewDone(e.moment, e.photos);
        livePicks(e.photos);
        progress(`Claude reviewed ${app.reviewing.done} / ${app.reviewing.total} moments · $${(e.spent + app.chatSpent).toFixed(2)}`);
        break;
      case "review_error":
        toast(e.error);
        break;
      case "final_edit_start":
        progress(`Final edit: comparing the best ${e.frames} frames`);
        break;
      case "final_edit_error":
        toast(`The final edit didn't finish, so picks use review scores. ${e.error}`);
        break;
      case "reviewed":
        app.spent = e.spent;
        if (e.errors.length) toast(`${e.errors.length} review step(s) didn't finish: ${e.errors[0]}`);
        break;
      case "state":
        app.shoot = e.state;
        if (app.refining) openResults();
        else finish();
        break;
      case "job_done":
        if (app.refining && e.job === "review") {
          app.refining = false;
          $("#refine-status").hidden = true;
        }
        break;
      case "error":
        toast("Something went wrong: " + e.error);
        break;
    }
  }

  function progress(t) {
    status(t);
    if (app.refining) {
      $("#refine-status").textContent = t;
      $("#refine-status").hidden = false;
    }
  }

  function afterGrouping() {
    const est = app.estimate;
    if (est.requests === 0) {
      status("Using saved reviews");
      setStep("review");
      const t = Stage.applyAll(app.shoot);
      later(t + 400, finish);
      return;
    }
    const heading = est.moments ? `${est.moments} moments to review` : "Final edit still to do";
    $("#est-text").innerHTML = `<strong>${heading}</strong>
      <span>${est.photos ? `${est.photos} photos · ` : ""}about $${est.cost.toFixed(2)} and ${est.minutes} min with ${esc(est.model_label)}</span>`;
    $("#start-review").textContent = app.settings.has_key ? "Set up the cull" : "Add API key";
    $("#estimate").hidden = false;
    if (app.settings.has_key) openBrief();
    gsap.fromTo("#estimate", { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" });
  }

  $("#start-review").addEventListener("click", () => {
    if (app.settings.has_key) openBrief();
    else askForKey();
  });

  function reviewed(state) {
    return state.photos.some((p) => p.score != null);
  }

  /* ---------- the brief ---------- */
  const FALLBACK_OPENING = "What's this shoot, and what matters most to you in it?";
  const FALLBACK_CORRECTION = "What should change in this cull?";

  async function openBrief() {
    try {
      app.brief = await api("/api/brief");
    } catch (err) {
      toast(err.message);
      return;
    }
    app.chatSpent = app.brief.spent;
    $("#brief-error").textContent = "";
    $("#brief-heading").textContent = $("#results").hidden ? "What do you want from this shoot?" : "What should change?";
    renderBrief();
    $("#brief").hidden = false;
    $("#brief-input").focus();
    // Claude's opening question is a paid request: ask it only before a shoot's first cull.
    if (!app.brief.messages.length && !reviewed(app.shoot)) sendBrief(null);
  }

  function renderMessages(pending) {
    const b = app.brief;
    const list = b.messages.length || !pending ? [...b.messages] : [];
    if (!list.length && !pending) list.push({ role: "assistant", text: reviewed(app.shoot) ? FALLBACK_CORRECTION : FALLBACK_OPENING });
    if (pending && pending.text) list.push({ role: "user", text: pending.text });
    const box = $("#brief-messages");
    box.innerHTML = list.map((m) => `<div class="msg ${m.role === "user" ? "user" : ""}">${esc(m.text)}</div>`).join("")
      + (pending ? `<div class="msg typing">Claude is thinking…</div>` : "");
    box.scrollTop = box.scrollHeight;
  }

  function renderBrief(pending) {
    const b = app.brief, d = b.draft, est = b.estimate;
    renderMessages(pending);
    const rows = [
      ["This shoot", d.shoot], ["Keep above all", d.priorities.join(", ")], ["Don't want", d.avoid.join(", ")],
      ["Look", d.look],
      ["Per moment", d.per_moment === "few" ? "Several frames can stay" : "The best frame"],
      ["Technical check", d.technical_tolerance === "loose" ? "Loose: more frames reach Claude" : "Standard"],
      ["My style", b.style.join(", ")],
    ].filter(([, v]) => v);
    $("#brief-fields").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
    if (document.activeElement !== $("#brief-count")) $("#brief-count").value = d.target_count || "";
    $("#brief-count-clear").hidden = !d.target_count;
    $("#brief-count-hint").textContent = d.target_count
      ? `The cull keeps your best ${d.target_count}. With fewer moments than that, the strongest extra frames fill the set.`
      : "Leave it empty and Claude recommends how many to keep.";
    $("#brief-shots").innerHTML = d.shots.map((s, k) =>
      `<li><span>${esc(s)}</span><button type="button" class="icon-btn" data-drop-shot="${k}" aria-label="Remove ${esc(s)}" title="Remove">${ICON.close}</button></li>`).join("");
    $("#brief-shots").hidden = !d.shots.length;
    $("#brief-shot-form").hidden = d.shots.length >= 12;
    $("#brief-shots-hint").textContent = d.shots.length
      ? "The cull keeps the best frame of each, and tells you which it couldn't find."
      : "Photos you must come away with. The cull keeps the best frame of each.";
    $("#brief-style").hidden = !b.suggestions.length;
    $("#brief-suggestions").innerHTML = b.suggestions.map((s) =>
      `<label><input type="checkbox" value="${esc(s)}"> <span>${esc(s)}</span></label>`).join("");
    const work = est.moments ? `Reviews ${est.moments} moment${est.moments > 1 ? "s" : ""}${est.final_edit ? ", then the final edit" : ""}. About ${est.minutes} min with ${est.model_label}.`
      : est.requests ? `Keeps the existing reviews and reruns only the final edit, with ${est.model_label}.` : "Nothing needs to rerun.";
    $("#brief-rerun").textContent = work;
    const confirm = $("#brief-confirm");
    confirm.textContent = est.requests ? `Confirm and start · about $${est.cost.toFixed(2)}` : "Confirm · no cost";
    confirm.disabled = app.briefBusy || countTimer !== null || shotsSaving;
    $("#brief-send").disabled = $("#brief-standard").disabled = app.briefBusy;
  }

  async function sendBrief(text) {
    app.briefBusy = true;
    $("#brief-error").textContent = "";
    renderBrief({ text });
    try {
      app.brief = await api(text ? "/api/brief/message" : "/api/brief/open", { body: text ? { text } : {} });
      app.chatSpent = app.brief.spent;
    } catch (err) {
      $("#brief-error").textContent = text ? `Your message wasn't sent: ${err.message}` : err.message;
      if (text) $("#brief-input").value = text;
    }
    app.briefBusy = false;
    renderBrief();
  }

  $("#brief-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#brief-input").value.trim();
    if (!text || app.briefBusy) return;
    $("#brief-input").value = "";
    sendBrief(text);
  });
  $("#brief-input").addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") $("#brief").hidden = true;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#brief-form").requestSubmit();
    }
  });

  // The count is part of the brief, so the confirm button waits until the server has it.
  let countTimer = null;
  async function saveCount() {
    const raw = $("#brief-count").value.trim();
    const n = raw ? Math.round(+raw) : null;
    try {
      app.brief = await api("/api/brief/target", { body: { target_count: n && n > 0 ? n : null } });
      $("#brief-error").textContent = "";
    } catch (err) {
      $("#brief-error").textContent = `The photo count wasn't saved: ${err.message}`;
    }
    countTimer = null;
    renderBrief();
  }

  $("#brief-count").addEventListener("input", () => {
    clearTimeout(countTimer);
    countTimer = setTimeout(saveCount, 450);
    $("#brief-confirm").disabled = true;
  });
  $("#brief-count").addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") $("#brief").hidden = true;
  });
  $("#brief-count").addEventListener("blur", () => {
    if (countTimer === null) return;
    clearTimeout(countTimer);
    saveCount();
  });
  $("#brief-count-clear").addEventListener("click", () => {
    clearTimeout(countTimer);
    $("#brief-count").value = "";
    saveCount();
  });

  // The shot list is part of the brief too: saved straight away, applied on confirm.
  let shotsSaving = false;
  async function saveShots(shots) {
    shotsSaving = true;
    $("#brief-confirm").disabled = true;
    try {
      app.brief = await api("/api/brief/shots", { body: { shots } });
      $("#brief-error").textContent = "";
    } catch (err) {
      $("#brief-error").textContent = `The shot list wasn't saved: ${err.message}`;
    }
    shotsSaving = false;
    renderBrief();
  }
  $("#brief-shot-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#brief-shot").value.trim();
    if (!text || shotsSaving) return;
    $("#brief-shot").value = "";
    saveShots([...app.brief.draft.shots, text]);
  });
  $("#brief-shot").addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") $("#brief").hidden = true;
  });
  $("#brief-shots").addEventListener("click", (e) => {
    const b = e.target.closest("[data-drop-shot]");
    if (b && !shotsSaving) saveShots(app.brief.draft.shots.filter((_, k) => k !== +b.dataset.dropShot));
  });

  $("#brief-standard").addEventListener("click", async () => {
    try {
      app.brief = await api("/api/brief/standard", { body: {} });
      renderBrief();
    } catch (err) {
      $("#brief-error").textContent = err.message;
    }
  });

  /* Confirming is the only thing that starts a cull. */
  $("#brief-confirm").addEventListener("click", async () => {
    const onResults = !$("#results").hidden;
    const before = app.shoot.photos.filter((p) => p.status === "rejected").length;
    let res;
    try {
      res = await api("/api/review", { body: {
        brief_version: app.brief.version, expected_cost: app.brief.estimate.cost,
        save_style: $$("#brief-suggestions input:checked").map((i) => i.value),
      } });
    } catch (err) {
      $("#brief-error").textContent = err.message;
      app.brief = await api("/api/brief").catch(() => app.brief);
      renderBrief();
      return;
    }
    $("#brief").hidden = true;
    app.shoot = res.state;
    if (onResults) {
      if (res.started) {
        app.refining = true;
        app.reviewing = { done: 0, total: res.estimate.moments };
        progress("Applying your correction…");
      } else {
        await openResults();
      }
      return;
    }
    $("#estimate").hidden = true;
    setStep("review");
    app.reviewStartedAt = Date.now();
    if (!res.started) {
      const t = Stage.applyAll(app.shoot);
      later(t + 400, finish);
      return;
    }
    if (res.state.photos.filter((p) => p.status === "rejected").length !== before) Stage.toClusters(res.state);
    app.reviewing = { done: 0, total: res.estimate.moments };
    status(`Claude is reviewing 0 / ${res.estimate.moments} moments`);
  });

  $("#update-review").addEventListener("click", () => {
    if (app.settings.has_key) openBrief();
    else askForKey();
  });

  $("#refine").addEventListener("click", () => {
    if (app.refining) return;
    if (app.settings.has_key) openBrief();
    else askForKey();
  });

  function finish() {
    setStep("done");
    const c = app.shoot.counts;
    const t = Stage.finale(app.shoot);
    status(app.spent + app.chatSpent ? `Done · $${(app.spent + app.chatSpent).toFixed(2)}` : "Done");
    $("#peek").hidden = $("#peek-modal").hidden = true;
    later(t + 200, () => {
      const n = app.shoot.photos.length;
      const took = app.reviewStartedAt ? (Date.now() - app.reviewStartedAt) / 1000 : 0;
      const byHand = n * HAND_SECONDS;
      const saved = took && byHand >= 60 && byHand > took * 1.5 ? ` · about ${duration(byHand)} by hand` : "";
      $("#finished-text").innerHTML = `<strong>${c.pick} picks${c.gem ? ` and ${c.gem} hidden gem${c.gem > 1 ? "s" : ""}` : ""}</strong>
        <span>from ${n} photos${took ? ` in ${duration(took)}` : ""}${saved}</span>`;
      $("#finished").hidden = false;
      gsap.fromTo("#finished", { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" });
    });
  }

  // A first pass by hand: a few seconds a frame to look, compare with its neighbours and flag it.
  const HAND_SECONDS = 3;

  function duration(s) {
    if (s < 60) return "under a minute";
    if (s < 3600) return `${Math.round(s / 60)} min`;
    return `${(s / 3600).toFixed(1).replace(/\.0$/, "")} hr`;
  }

  /* Picks so far: each reviewed moment's best frame, browsable before the cull finishes. */
  function livePicks(photos) {
    if (app.refining) return;
    for (const p of photos.filter((q) => q.best)) {
      app.live = app.live.filter((q) => q.id !== p.id).concat(p);
    }
    $("#peek-count").textContent = app.live.length;
    $("#peek").hidden = !app.live.length;
    if (!$("#peek-modal").hidden) renderPeek();
  }

  function renderPeek() {
    $("#peek-grid").innerHTML = [...app.live].sort((a, b) => (b.score || 0) - (a.score || 0)).map((p) =>
      `<div class="tile pick" title="${esc(p.file)}"><img src="/api/thumb/${p.fp}" alt="${esc(p.file)}">
        <div class="meta"><p class="reason">${esc(p.reason || "")}</p></div></div>`).join("");
  }

  $("#peek").addEventListener("click", () => {
    renderPeek();
    $("#peek-modal").hidden = false;
  });

  function unreadableText() {
    const n = app.unreadable.length, names = app.unreadable.slice(0, 3).join(", ");
    return `${n} file${n > 1 ? "s" : ""} couldn't be read and ${n > 1 ? "were" : "was"} skipped: ${names}${n > 3 ? "…" : ""}`;
  }

  function emptyFolder(info) {
    showHome();
    const subs = (info && info.subfolders) || [];
    if (subs.length) {
      done({ icon: "folder-add", title: "The photos are in folders inside this one",
        text: "Goodframes culls one folder at a time. Which one?",
        actions: subs.slice(0, 4).map((s) => ({ label: `${s.name} · ${s.photos} photos`, run: () => openShoot(s.folder) })),
        dismiss: "Cancel" });
    } else if (info && info.videos) {
      done({ icon: "folder-add", title: "Only videos in this folder",
        text: `It has ${info.videos} video${info.videos > 1 ? "s" : ""} and no photos. Goodframes culls JPEG, HEIC and RAW photos.` });
    } else {
      done({ icon: "folder-add", title: "No photos in this folder",
        text: "Goodframes culls JPEG, HEIC and RAW photos. Choose a folder that has some." });
    }
  }

  /* A finished action: what happened, and the next step. */
  function done({ title, text, icon: name = "pick", actions = [], dismiss = "Done" }) {
    $("#done-icon").innerHTML = icon(name);
    $("#done-title").textContent = title;
    $("#done-text").textContent = text;
    const box = $("#done-actions");
    box.innerHTML = "";
    box.classList.toggle("stack", actions.length > 1);
    [...actions, { label: dismiss }].forEach((a, i) => {
      const b = document.createElement("button");
      b.className = `btn ${i === 0 ? "primary" : a.run ? "" : "ghost"}`;
      b.textContent = a.label;
      b.onclick = () => {
        $("#done-sheet").hidden = true;
        if (a.run) a.run();
      };
      box.append(b);
    });
    $("#done-sheet").hidden = false;
    box.firstChild.focus();
  }

  $("#see-results").addEventListener("click", () => openResults());
  $("#stage-back").addEventListener("click", showHome);
  $("#results-back").addEventListener("click", showHome);

  /* ---------- results ---------- */
  const INTRO = {
    pick: "The final edit, strongest first. Adjust the selection size in Settings.",
    gem: "Frames with strong bones that an edit will bring to life. Each comes with a recipe.",
    moments: "Every moment of the shoot, with all its candidates.",
    people: "Everyone who appears in three or more photos, grouped by face on this Mac. Name them, and fix any mix-ups.",
    rejected: "Frames that failed the technical check or Claude's review. Nothing here was deleted.",
  };

  async function openResults() {
    const data = await api("/api/shoot");
    app.shoot = data.state;
    app.estimate = data.estimate;
    app.shootSettings = data.settings;
    $("#results-name").textContent = app.shoot.name;
    renderResults();
    show("results");
  }

  function counts() {
    const c = app.shoot.counts;
    const moments = app.shoot.moments.length;
    const people = (app.shoot.people || []).filter((x) => !x.hidden).length;
    return { pick: c.pick, gem: c.gem, moments, people, rejected: c.rejected };
  }

  // The shot list against the cull: which shots have a pick, and which have none.
  function renderCoverage(est) {
    const cov = app.shoot.coverage || [];
    const box = $("#coverage");
    // Until every moment has been reviewed against the list, "missing" would be a guess.
    box.hidden = !cov.length || !reviewed(app.shoot) || app.refining || !!(est && est.moments);
    if (box.hidden) return;
    const done = cov.filter((c) => c.picks.length).length;
    const item = (c) => {
      if (c.picks.length) {
        const more = c.picks.length > 1 ? ` <span class="n">${c.picks.length}</span>` : "";
        return `<li><button type="button" class="shot ok" data-shot-photo="${c.picks[0]}" title="Open the pick">${ICON.check}${esc(c.shot)}${more}</button></li>`;
      }
      if (c.others.length) {
        const n = c.others.length;
        return `<li><button type="button" class="shot weak" data-shot-photo="${c.others[0]}" title="Open the frame">${esc(c.shot)}<span class="n">${n} unpicked ${n === 1 ? "frame" : "frames"}</span></button></li>`;
      }
      return `<li><span class="shot none">${esc(c.shot)}<span class="n">no frame found</span></span></li>`;
    };
    const head = done === cov.length ? `Shot list: all ${cov.length} covered`
      : `Shot list: ${done} of ${cov.length} covered`;
    box.innerHTML = `<b>${head}</b><ul class="shots">${cov.map(item).join("")}</ul>`;
  }
  $("#coverage").addEventListener("click", (e) => {
    const b = e.target.closest("[data-shot-photo]");
    if (b) openDetail(+b.dataset.shotPhoto);
  });

  // Who is in the picks: one face per person, with how many picks they're in.
  const MAX_PEOPLE = 16;
  function renderCast() {
    const cast = (app.shoot.people || []).filter((c) => !c.hidden);
    const box = $("#cast");
    box.hidden = cast.length < 2 || !reviewed(app.shoot) || app.refining;
    if (box.hidden) return;
    const out = cast.filter((c) => !c.picks.length).length;
    const person = (c) => {
      const n = c.picks.length;
      const frames = `${c.photos} photos`;
      const tip = n ? `in ${n} of your picks (${frames})`
        : c.usable ? `in none of your picks (${frames})` : `in none of your picks: all ${frames} were rejected`;
      return `<li><button type="button" class="person ${n ? "" : "none"}" data-person="${c.id}" title="${esc(personName(c))}: ${tip}" aria-label="${esc(personName(c))}. ${tip}">
        <img src="/api/face/${c.cover[0]}/${c.cover[1]}" alt="" loading="lazy"><span class="n">${n}</span></button></li>`;
    };
    const head = out ? `People: ${out} of ${cast.length} ${out === 1 ? "is" : "are"} in none of your picks`
      : `People: all ${cast.length} are in your picks`;
    const more = cast.length > MAX_PEOPLE ? `<span class="more">and ${cast.length - MAX_PEOPLE} more</span>` : "";
    // Anyone left out comes first, so they're never among the ones cut off.
    const shown = [...cast].sort((a, b) => !!a.picks.length - !!b.picks.length || a.id - b.id).slice(0, MAX_PEOPLE);
    box.innerHTML = `<b>${head}</b><ul class="people">${shown.map(person).join("")}</ul>${more}
      <button type="button" class="btn ghost small" data-person="">See everyone</button>`;
  }
  $("#cast").addEventListener("click", (e) => {
    const b = e.target.closest("[data-person]");
    if (b) openPerson(b.dataset.person ? +b.dataset.person : null);
  });

  /* ---------- people ---------- */
  const personName = (c) => c.name || `Person ${c.id}`;
  const faceSrc = (c) => `/api/face/${c.cover[0]}/${c.cover[1]}`;
  const findPerson = (id) => (app.shoot.people || []).find((c) => c.id === id);

  function openPerson(id) {
    $("#detail").hidden = true;
    app.tab = "people";
    if (id != null) app.person = id;
    renderResults();
    $("#results .results-body").scrollTop = 0;
  }

  // A list of everyone down the side; the chosen person's photos, by what became of them.
  function peopleView() {
    const cast = app.shoot.people || [];
    if (!cast.length) return `<div class="empty">Nobody appears in three or more photos of this shoot.</div>`;
    const cur = findPerson(app.person) || cast.find((c) => !c.hidden) || cast[0];
    app.person = cur.id;
    const done = reviewed(app.shoot);
    const row = (c) => {
      const n = c.picks.length;
      const count = !done ? `${c.photos} photos` : n ? `${n} ${n === 1 ? "pick" : "picks"} · ${c.photos} photos` : `No picks · ${c.photos} photos`;
      return `<li><button type="button" class="pv-who${c.id === cur.id ? " cur" : ""}${done && !n ? " none" : ""}" data-person="${c.id}"
        ${c.id === cur.id ? 'aria-current="true"' : ""}><img class="face-img" src="${faceSrc(c)}" alt="" loading="lazy">
        <span class="nm">${esc(personName(c))}</span><span class="ct">${count}</span></button></li>`;
    };
    const shown = cast.filter((c) => !c.hidden), hidden = cast.filter((c) => c.hidden);
    const left = done ? shown.filter((c) => !c.picks.length).length : 0;
    const head = left ? `<p class="pv-sum">${left} of ${shown.length} in none of your picks</p>` : "";
    const rest = hidden.length ? `<details class="pv-hidden"${cur.hidden ? " open" : ""}><summary>Hidden <span>${hidden.length}</span></summary>
      <ul>${hidden.map(row).join("")}</ul></details>` : "";
    return `<div class="pv"><aside class="pv-list" aria-label="People">${head}<ul>${shown.map(row).join("")}</ul>${rest}</aside>
      <section class="pv-main" aria-label="${esc(personName(cur))}">${personPage(cur, cast)}</section></div>`;
  }

  function personPage(c, cast) {
    const ps = app.shoot.photos;
    const frames = c.frames.map((id) => ps[id - 1]);
    const picks = c.picks.map((id) => ps[id - 1]);
    const best = (a, b) => (b.final ?? -1) - (a.final ?? -1) || b.technical - a.technical;
    const others = frames.filter((p) => p.status !== "pick" && p.status !== "rejected").sort(best);
    const rejects = frames.filter((p) => p.status === "rejected");
    const done = reviewed(app.shoot);
    const stat = done ? (picks.length ? `In ${picks.length} of your picks · ${c.photos} photos`
      : `In none of your picks · ${c.photos} photos${others.length ? ". Pick one below to include them." : ""}`)
      : `${c.photos} photos`;
    const section = (title, list, empty) => `<div class="pv-sec"><h4>${title} <span class="n">${list.length}</span></h4>
      ${list.length ? `<div class="grid">${list.map(tile).join("")}</div>` : `<p class="pv-none">${empty}</p>`}</div>`;
    const more = cast.filter((o) => o.id !== c.id);
    const merge = more.length ? `<div class="pv-merge" id="pv-merge" hidden>
        <p>Who else is this? Their photos join ${esc(personName(c))}.</p>
        <ul>${more.map((o) => `<li><button type="button" data-merge="${o.id}" title="${esc(personName(o))}">
          <img class="face-img" src="${faceSrc(o)}" alt="" loading="lazy"><span>${esc(personName(o))}</span></button></li>`).join("")}</ul></div>` : "";
    return `<div class="pv-head"><img class="face-img big" src="${faceSrc(c)}" alt="">
        <div class="pv-title"><input id="pv-name" class="pv-name" value="${esc(c.name)}" placeholder="Person ${c.id}" maxlength="40"
          aria-label="Name this person" title="Name this person" autocomplete="off" spellcheck="false">
          <p class="pv-stat">${stat}${c.hidden ? " · Hidden" : ""}</p></div>
        <div class="pv-acts">
          ${more.length ? `<button type="button" class="btn small" data-pv="merge" aria-expanded="false">${icon("people")}Same person as…</button>` : ""}
          ${c.merged ? `<button type="button" class="btn ghost small" data-pv="split">Undo merge</button>` : ""}
          <button type="button" class="btn ghost small" data-pv="${c.hidden ? "show" : "hide"}" title="${c.hidden
            ? "Count them again" : "A passer-by, a poster, a stranger: leave them out of the People counts"}">${c.hidden ? "Show again" : "Hide"}</button>
        </div></div>${merge}
      ${done ? section("In your picks", picks, "None yet.") + section("Not picked", others, "Every frame of theirs that could be a pick already is.")
        : section("Photos", frames.filter((p) => p.status !== "rejected"), "None.")}
      ${rejects.length ? section("Rejected", rejects, "") : ""}`;
  }

  async function editPerson(body, message, undo) {
    let data;
    try {
      data = await api("/api/person", { body: { id: app.person, ...body } });
    } catch (err) {
      return toast(err.message);
    }
    app.shoot = data.state;
    app.person = data.person;
    renderResults();
    if (message) toast(message, undo && { label: "Undo", run: undo });
  }

  function personAct(act, c) {
    const name = personName(c);
    if (act === "merge") {
      const box = $("#pv-merge"), b = $('[data-pv="merge"]');
      box.hidden = !box.hidden;
      b.setAttribute("aria-expanded", !box.hidden);
    } else if (act === "split") editPerson({ action: "split" }, `Split ${name} back up`);
    else if (act === "hide") editPerson({ action: "hide" }, `Hid ${name}`, () => editPerson({ action: "show" }));
    else if (act === "show") editPerson({ action: "show" }, `${name} is counted again`);
  }

  function notThem(c, pid) {
    if (c.frames.length < 2) return toast("That's their only photo. Hide them instead.");
    const k = c.faces[pid];
    editPerson({ action: "remove", photo: pid }, `Took that photo out of ${personName(c)}`,
      () => editPerson({ action: "restore", photo: pid, face: k }));
  }

  function mergeInto(c, other) {
    const o = findPerson(other);
    editPerson({ action: "merge", other }, `${personName(o)} is now part of ${personName(c)}`,
      () => editPerson({ action: "split" }));
  }

  $("#grid").addEventListener("change", (e) => {
    if (e.target.id !== "pv-name") return;
    const c = findPerson(app.person);
    const name = e.target.value.trim();
    if (c && name !== c.name) editPerson({ action: "name", name });
  });
  $("#grid").addEventListener("keydown", (e) => {
    if (e.target.id === "pv-name" && e.key === "Enter") e.target.blur();
  });

  function renderResults() {
    const c = counts();
    $$("#tabs button").forEach((b) => {
      b.querySelector("b").textContent = c[b.dataset.tab];
      b.setAttribute("aria-selected", b.dataset.tab === app.tab);
    });
    const anyone = (app.shoot.people || []).length > 0;
    $('#tabs [data-tab="people"]').hidden = !anyone;
    if (app.tab === "people" && !anyone) app.tab = "pick";
    $("#tab-intro").textContent = INTRO[app.tab];
    const est = app.estimate;
    $("#update-notice").hidden = app.refining || !est || !est.requests;
    if (est && est.requests) {
      $("#update-text").textContent = est.moments
        ? `${est.moments} moment${est.moments > 1 ? "s were" : " was"} reviewed by an earlier version, or not at all. Showing your saved results.`
        : "The final edit for this shoot isn't finished. Showing picks from review scores.";
    }
    $("#unreadable").hidden = !(app.unreadable || []).length;
    if (!$("#unreadable").hidden) $("#unreadable").textContent = unreadableText();
    const target = app.shoot.target, sel = app.shoot.selection;
    const have = sel ? sel.total : app.shoot.counts.pick;
    $("#shortfall").hidden = !(target && have < target && reviewed(app.shoot));
    $("#shortfall").textContent = `You asked for ${target} photos, but only ${have} ${have === 1 ? "frame was" : "frames were"} `
      + "strong enough to keep. You can add more yourself from the Moments tab.";
    renderCoverage(est);
    renderCast();
    if (app.tab === "people") $("#cast").hidden = true;  // this tab is the long form of it
    const unmet = app.shoot.unmet || [];
    $("#unmet").hidden = !unmet.length;
    $("#unmet").textContent = unmet.length ? `No frame found for: ${unmet.join(", ")}` : "";
    const ps = app.shoot.photos;
    let html = "";
    if (app.tab === "people") {
      html = peopleView();
    } else if (app.tab === "moments") {
      app.shoot.moments.forEach((m) => {
        const members = m.members.map((id) => ps[id - 1]);
        const shown = members.filter((p) => p.status !== "rejected");
        if (!shown.length) return;
        const cmp = shown.length > 1
          ? `<button class="btn ghost small" data-compare="${m.id}">${icon("compare")}Compare</button>` : "";
        html += `<div class="moment-head">Moment ${m.id} · ${time(members[0].taken)}${cmp}</div>
          <div class="moment-row">${shown.map(tile).join("")}</div>`;
      });
    } else {
      let list = ps.filter((p) => p.status === app.tab);
      list.sort((a, b) => app.tab === "gem" ? (b.edit_potential || 0) - (a.edit_potential || 0)
        : app.tab === "pick" ? (a.rank || Infinity) - (b.rank || Infinity) || (b.final || 0) - (a.final || 0) : a.id - b.id);
      html = list.map(tile).join("") || `<div class="empty">${app.tab === "pick" ? "No picks yet. Run the review, or change the selection in Settings." : "Nothing here."}</div>`;
    }
    const focused = focusedTile(), before = visibleIds();
    $("#grid").dataset.tab = app.tab;
    $("#grid").innerHTML = html;
    // Keep the keyboard where it was: on the same frame, or the one that took its place.
    if (focused) {
      const ids = visibleIds();
      const id = ids.includes(focused) ? focused : ids[Math.min(before.indexOf(focused), ids.length - 1)];
      if (id) focusTile(id);
    }
  }

  function applyTileSize(px) {
    document.documentElement.style.setProperty("--tile", `${px}px`);
    $("#tile-size").value = px;
  }

  let sizeTimer;
  $("#tile-size").addEventListener("input", (e) => {
    applyTileSize(+e.target.value);
    clearTimeout(sizeTimer);
    sizeTimer = setTimeout(() => api("/api/settings", { body: { tile_size: +e.target.value } }).catch(() => {}), 400);
  });
  $("#shortcuts-btn").addEventListener("click", () => ($("#shortcuts").hidden = false));

  const LABEL = { pick: "Pick", gem: "Gem", rejected: "Reject", similar: "Similar", below: "Below cut", candidate: "Unreviewed" };
  const ICON = { check: icon("pick"), gem: icon("gem"), reject: icon("reject"), close: icon("close") };

  // Changes when the recipe does, so an edited preview is never a stale one.
  const rev = (p) => encodeURIComponent(p.edit.join("|"));

  // The photo and Claude's reason; the file name and scores are in the detail view.
  function tile(p) {
    const line = p.status === "gem" ? `<div class="recipe-line">${esc(p.edit_recipe || p.why)}</div>` : "";
    const reason = p.status === "rejected" ? p.why : (p.reason || p.why || "");
    const after = app.tab === "gem" && p.edit.length;  // gems are shown as they'd look edited
    const rank = app.tab === "pick" && p.rank ? `<span class="rank" title="Place in the final edit">${p.rank}</span>` : "";
    return `<div class="tile ${p.status}" data-id="${p.id}" tabindex="0" title="${esc(p.file)}">
      <span class="badge ${p.status}">${LABEL[p.status] || p.status}</span>${rank}
      <div class="acts">
        <button class="chip-btn ${p.status === "pick" ? "on" : ""}" data-act="pick" aria-label="Pick (P)" title="Pick (P)">${ICON.check}</button>
        <button class="chip-btn gem ${p.status === "gem" ? "on" : ""}" data-act="gem" aria-label="Hidden gem (G)" title="Hidden gem (G)">${ICON.gem}</button>
        <button class="chip-btn reject ${p.override === "reject" ? "on" : ""}" data-act="reject" aria-label="Reject (X)" title="Reject (X)">${ICON.reject}</button>
      </div>
      ${after ? `<span class="after-tag">After edit</span>` : ""}
      ${app.tab === "people" ? `<button class="not-them" data-act="not-them" title="This isn't them: take the photo out">Not them</button>` : ""}
      <img loading="lazy" src="${after ? `/api/edited/${p.id}?r=${rev(p)}` : `/api/thumb/${p.fp}`}" alt="${esc(p.file)}">
      <div class="meta"><p class="reason">${esc(reason)}</p>${line}</div></div>`;
  }

  function time(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    app.tab = b.dataset.tab;
    renderResults();
  });

  $("#grid").addEventListener("click", (e) => {
    const who = e.target.closest("[data-person]");
    if (who) return openPerson(+who.dataset.person);
    const pv = e.target.closest("[data-pv]");
    if (pv) return personAct(pv.dataset.pv, findPerson(app.person));
    const m = e.target.closest("[data-merge]");
    if (m) return mergeInto(findPerson(app.person), +m.dataset.merge);
    const c = e.target.closest("[data-compare]");
    if (c) return openCompare(+c.dataset.compare);
    const t = e.target.closest(".tile");
    if (!t) return;
    const id = +t.dataset.id;
    const act = e.target.closest("[data-act]");
    if (act && act.dataset.act === "not-them") notThem(findPerson(app.person), id);
    else if (act) toggle(id, act.dataset.act);
    else openDetail(id);
  });
  $("#grid").addEventListener("keydown", (e) => {
    const t = e.target.closest(".tile");
    if (t && e.key === "Enter") openDetail(+t.dataset.id);
  });

  function toggle(id, kind) {
    const p = app.shoot.photos[id - 1];
    let action, msg;
    if (kind === "pick") {
      action = p.status === "pick" ? (p.override === "pick" ? null : "unpick") : "pick";
      msg = p.status === "pick" ? "Removed from picks" : "Picked";
    } else if (kind === "gem") {
      action = p.status === "gem" ? (p.override === "gem" ? null : "ungem") : "gem";
      msg = p.status === "gem" ? "No longer a hidden gem" : "Marked as a hidden gem";
    } else {
      if (p.status === "rejected" && p.override !== "reject") return toast("Already a reject. Pick it to bring it back.");
      action = p.override === "reject" ? null : "reject";
      msg = action ? "Rejected" : "Brought back";
    }
    return override([{ id, action }], `${msg}: ${p.file}`);
  }

  /* ---------- overrides, with undo ---------- */
  const undoStack = [];

  // steps: [{id, action}], applied and undone together.
  async function override(steps, message) {
    const entry = steps.map(({ id, action }) => ({ id, prev: app.shoot.photos[id - 1].override || null, applied: action }));
    let data;
    try {
      for (const s of steps) data = await api("/api/override", { body: s });
    } catch (err) {
      toast(err.message);
      if (!data) return;
    }
    app.shoot = data.state;
    undoStack.push(entry);
    refresh();
    toast(message, { label: "Undo", run: undo });
  }

  async function undo() {
    const entry = undoStack.pop();
    if (!entry) return toast("Nothing to undo");
    let data;
    try {
      // forget: the undone choice taught Goodframes your taste, so take that lesson back too
      for (const s of [...entry].reverse()) {
        data = await api("/api/override", { body: { id: s.id, action: s.prev, undo: true, forget: !!s.applied } });
      }
    } catch (err) {
      toast(err.message);
      if (!data) return;
    }
    app.shoot = data.state;
    refresh();
    toast("Undone");
  }

  function refresh() {
    renderResults();
    if (!$("#detail").hidden) openDetail(detailId);
    if (!$("#compare").hidden) openCompare(compareMoment);
  }

  /* ---------- compare: a moment side by side ---------- */
  let compareMoment = null;
  function openCompare(mid) {
    compareMoment = mid;
    const m = app.shoot.moments.find((x) => x.id === mid);
    const all = m.members.map((id) => app.shoot.photos[id - 1]);
    const live = all.filter((p) => p.status !== "rejected");
    const frames = live.length > 1 ? live : all;
    const pick = frames.find((p) => p.status === "pick");
    $("#compare-title").textContent = `Moment ${m.id} · ${frames.length} frames · ${time(all[0].taken)}`;
    $("#compare-why").textContent = !pick ? "No pick in this moment yet."
      : pick.override === "pick" ? `You picked ${pick.file}.${pick.reason ? ` Claude's take: ${pick.reason}` : ""}`
      : `Why ${pick.file} is the pick: ${pick.reason || pick.why}`;
    $("#compare-row").innerHTML = frames.map((p, i) => `
      <figure class="cmp ${p.status}" data-id="${p.id}">
        <div class="cmp-img"><img src="/api/thumb/${p.fp}" alt="${esc(p.file)}">${i < 9 ? `<kbd class="cmp-n">${i + 1}</kbd>` : ""}</div>
        <figcaption>
          <div class="cmp-top"><span class="badge ${p.status}">${LABEL[p.status] || p.status}</span>
            <span class="score">${p.score != null ? p.score.toFixed(1) : ""}</span></div>
          <p class="cmp-reason">${esc(p.status === "rejected" ? p.why : p.reason || p.why || "Not reviewed")}</p>
          ${p.status === "pick" ? `<span class="cmp-chosen">${ICON.check}The pick</span>`
            : `<button class="btn" data-make-pick="${p.id}">${ICON.check}Make this the pick</button>`}
        </figcaption></figure>`).join("");
    $("#compare").hidden = false;
  }

  // The chosen frame becomes the moment's only pick.
  function makePick(id) {
    const p = app.shoot.photos[id - 1];
    const m = app.shoot.moments.find((x) => x.id === p.moment);
    const others = m.members.map((mid) => app.shoot.photos[mid - 1]).filter((q) => q.status === "pick" && q.id !== id);
    if (p.status === "pick" && !others.length) return;
    const steps = [{ id, action: "pick" }, ...others.map((q) => ({ id: q.id, action: q.override === "pick" ? null : "unpick" }))];
    return override(steps, `${p.file} is now the pick`);
  }

  $("#compare-row").addEventListener("click", (e) => {
    const b = e.target.closest("[data-make-pick]");
    if (b) return makePick(+b.dataset.makePick);
    const f = e.target.closest(".cmp");
    if (f && e.target.closest(".cmp-img")) {
      $("#compare").hidden = true;
      openDetail(+f.dataset.id);
    }
  });

  /* ---------- detail ---------- */
  let detailId = null, zoomed = false, showAfter = false;
  function openDetail(id) {
    if (id !== detailId) showAfter = app.tab === "gem";  // a gem opens on its edit; the rest as shot
    detailId = id;
    const p = app.shoot.photos[id - 1];
    const canEdit = p.edit.length > 0;
    $("#detail-img").src = canEdit && showAfter ? `/api/edited/${p.id}?r=${rev(p)}` : `/api/thumb/${p.fp}`;
    $("#detail-ba").hidden = !canEdit;
    $$("#detail-ba button").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.ba === "after") === (canEdit && showAfter))));
    $("#detail-crop").src = `/api/crop/${p.id}`;
    $("#detail-main").classList.toggle("zoomed", zoomed);
    const badge = $("#detail-badge");
    badge.className = `badge ${p.status}`;
    badge.textContent = LABEL[p.status] || p.status;
    $("#detail-title").textContent = p.file;
    $("#detail-reason").textContent = p.status === "rejected" ? p.why : (p.reason || p.why || "Not reviewed yet");
    const sub = p.subscores || {};
    const rows = [["Claude", p.score], ["After edit", p.edit_potential], ["Impact", sub.impact], ["Moment", sub.moment],
      ["Light", sub.light], ["Composition", sub.composition], ["Subject focus", sub.subject_focus],
      ["Technical", p.technical], ["Apple aesthetics", p.aesthetic], ["Final", p.final]]
      .filter(([, v]) => v != null)
      .map(([k, v]) => `<dt>${k}</dt><dd>${(+v).toFixed(1)}</dd>`).join("");
    const rank = p.rank ? `<dt>Final edit</dt><dd>#${p.rank}</dd>` : "";
    $("#detail-scores").innerHTML = rank + rows + `<dt>Taken</dt><dd>${time(p.taken)}</dd>`;
    renderStats(p);
    $("#detail-share").hidden = !app.settings.desktop && !navigator.canShare;
    const recipe = $("#detail-recipe");
    recipe.hidden = !p.edit_recipe && !canEdit;
    recipe.innerHTML = (p.edit_recipe ? `<b>Edit recipe</b>${esc(p.edit_recipe)}` : "")
      + (canEdit ? `<b class="applied">The preview applies</b>${esc(p.edit.join(" · "))}
        <span class="fine">A quick preview of where the edit leads, not a finished edit.</span>` : "");
    const cut = p.status === "below" && p.why ? `Why it was left out: ${p.why}. ` : "";
    const listed = (p.shots || []).length ? `On your shot list: ${p.shots.join(", ")}. ` : "";
    const here = (p.people || []).map(findPerson).filter(Boolean);
    $("#detail-people").hidden = !here.length;
    $("#detail-people").innerHTML = here.length ? `<h4 class="eyebrow">In this photo</h4><ul>${here.map((c) => `<li><button type="button"
      data-person="${c.id}" title="See every photo of ${esc(personName(c))}"><img class="face-img" src="${faceSrc(c)}" alt="">
      <span>${esc(personName(c))}</span></button></li>`).join("")}</ul>` : "";
    $("#detail-notes").textContent = listed + cut + (p.notes.length ? "Technical: " + p.notes.join(" · ") : "");
    $("#detail-actions").innerHTML = `
      <button class="btn ${p.status === "pick" ? "primary" : ""}" data-act="pick">${ICON.check}${p.status === "pick" ? "Picked" : "Pick"}</button>
      <button class="btn ${p.status === "gem" ? "primary gem" : ""}" data-act="gem">${ICON.gem}Gem</button>
      <button class="btn ${p.override === "reject" ? "danger" : "ghost"}" data-act="reject">${ICON.reject}${p.override === "reject" ? "Rejected" : "Reject"}</button>`;
    const moment = app.shoot.moments.find((m) => m.id === p.moment);
    whyNot(p, moment);
    $("#detail-compare").hidden = moment.members.length < 2;
    $("#detail-strip").innerHTML = moment.members.map((mid) => {
      const q = app.shoot.photos[mid - 1];
      return `<img src="/api/thumb/${q.fp}" data-id="${q.id}" class="${q.id === id ? "cur" : q.status}" title="${esc(q.file)}" alt="">`;
    }).join("");
    renderArrows();
    $("#detail").hidden = false;
  }

  // Previous and next follow the grid behind the photo, the same order as the arrow keys.
  function neighbours() {
    const ids = visibleIds(), i = ids.indexOf(detailId);
    return i < 0 ? [null, null] : [ids[i - 1] ?? null, ids[i + 1] ?? null];
  }
  function renderArrows() {
    const [prev, next] = neighbours();
    $("#detail-prev").hidden = prev == null;
    $("#detail-next").hidden = next == null;
  }
  $("#detail-prev").addEventListener("click", () => { const [prev] = neighbours(); if (prev != null) openDetail(prev); });
  $("#detail-next").addEventListener("click", () => { const [, next] = neighbours(); if (next != null) openDetail(next); });

  // The few numbers worth reading at a glance; the rest wait under "All scores".
  const ASPECTS = { impact: "impact", moment: "the moment", light: "light", composition: "composition", subject_focus: "focus on the subject" };
  function renderStats(p) {
    const picks = app.shoot.photos.filter((q) => q.status === "pick").length;
    const stat = (value, label) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`;
    const cells = [p.final != null ? stat(`${(+p.final).toFixed(1)}<small>/10</small>`, "Score") : stat("–", "Not reviewed yet")];
    if (p.status === "pick" && p.rank) cells.push(stat(`#${p.rank}<small> of ${picks}</small>`, "Final edit"));
    else if (p.status === "gem" && p.edit_potential != null) cells.push(stat(`${(+p.edit_potential).toFixed(1)}<small>/10</small>`, "After edit"));
    cells.push(stat(esc(time(p.taken)).replace(/\s*([AP]\.?M\.?)$/i, "<small> $1</small>"), "Taken"));
    $("#detail-stats").innerHTML = cells.join("");
    const sub = Object.entries(p.subscores || {}).filter(([k]) => ASPECTS[k]).sort((a, b) => b[1] - a[1]);
    const strong = sub.filter(([, v]) => v >= 7).slice(0, 2).map(([k]) => ASPECTS[k]);
    const weak = sub.filter(([, v]) => v < 5).slice(-1).map(([k]) => ASPECTS[k]);
    const line = [strong.length && `Strongest on ${strong.join(" and ")}`, weak.length && `weakest on ${weak[0]}`].filter(Boolean).join("; ");
    $("#detail-strengths").hidden = !line;
    $("#detail-strengths").textContent = line ? line[0].toUpperCase() + line.slice(1) + "." : "";
  }

  /* ---------- save a copy, share ---------- */
  async function saveCopy(p) {
    if (!app.settings.desktop) {  // the browser saves it as a download
      const a = Object.assign(document.createElement("a"), { href: `/api/original/${p.id}`, download: p.file });
      document.body.append(a);
      a.click();
      a.remove();
      return;
    }
    try {
      const res = await api("/api/save-copy", { body: { id: p.id } });
      if (res.saved) toast(`Saved a copy as ${res.saved.split("/").pop()}`, { label: "Show", run: () => api("/api/reveal", { body: { folder: res.saved.replace(/\/[^/]*$/, "") } }) });
    } catch (err) {
      toast(err.message);
    }
  }

  async function sharePhoto(p, btn) {
    if (!app.settings.desktop) {
      try {
        const blob = await (await fetch(`/api/original/${p.id}`)).blob();
        const file = new File([blob], p.file, { type: blob.type });
        await navigator.share({ files: [file], title: p.file });
      } catch (err) {
        if (err.name !== "AbortError") toast("Sharing works in the Goodframes app. Save a copy instead.");
      }
      return;
    }
    const r = btn.getBoundingClientRect();
    btn.disabled = true;
    try {
      await api("/api/share", { body: { id: p.id, rect: [r.left, r.top, r.width, r.height] } });
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  }

  $("#detail-save").addEventListener("click", () => saveCopy(app.shoot.photos[detailId - 1]));
  $("#detail-share").addEventListener("click", (e) => sharePhoto(app.shoot.photos[detailId - 1], e.currentTarget));

  // Frames Claude passed over: who won the moment, and why, with a way to see or question it.
  function whyNot(p, moment) {
    const box = $("#detail-whynot");
    const passed = ["similar", "below", "candidate"].includes(p.status) || (p.status === "rejected" && p.score != null);
    box.hidden = !passed;
    if (!passed) return;
    const q = moment.members.map((id) => app.shoot.photos[id - 1]).find((x) => x.status === "pick" && x.id !== p.id);
    const vs = q && q.score != null && p.score != null ? ` (${q.score.toFixed(1)} against this frame's ${p.score.toFixed(1)})` : "";
    box.innerHTML = `<b>Why not this one?</b>` + (q
      ? `<div class="vs-row"><img src="/api/thumb/${q.fp}" alt="" data-goto="${q.id}">
          <p>Claude picked <button class="link" data-goto="${q.id}">${esc(q.file)}</button>${vs}: ${esc(q.reason || q.why || "")}</p></div>`
      : `<p>${esc(p.why || "It scored below the cut.")}</p>`)
      + `<div class="row start">${moment.members.length > 1 ? `<button class="btn small" data-compare-here>${icon("compare")}Compare</button>` : ""}
          <button class="btn ghost small" data-ask>Ask Claude</button></div>`;
    box.dataset.pick = q ? q.id : "";
  }

  $("#detail-people").addEventListener("click", (e) => {
    const b = e.target.closest("[data-person]");
    if (b) openPerson(+b.dataset.person);
  });

  $("#detail-whynot").addEventListener("click", (e) => {
    const p = app.shoot.photos[detailId - 1];
    const go = e.target.closest("[data-goto]");
    if (go) openDetail(+go.dataset.goto);
    if (e.target.closest("[data-compare-here]")) openCompare(p.moment);
    if (e.target.closest("[data-ask]")) {
      const q = $("#detail-whynot").dataset.pick ? app.shoot.photos[+$("#detail-whynot").dataset.pick - 1] : null;
      askAbout(p, q);
    }
  });

  // Opens the Refine chat with a question about this frame, for the photographer to edit and send.
  async function askAbout(p, q) {
    $("#detail").hidden = true;
    if (!app.settings.has_key) return askForKey();
    await openBrief();
    $("#brief-input").value = q ? `Why did you pick ${q.file} over ${p.file}? ` : `Why wasn't ${p.file} picked? `;
    $("#brief-input").focus();
  }

  function setAfter(on) {
    if (!app.shoot.photos[detailId - 1].edit.length) return;
    showAfter = on;
    openDetail(detailId);
  }
  $("#detail-ba").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ba]");
    if (b) setAfter(b.dataset.ba === "after");
  });

  function toggleZoom() {
    zoomed = !zoomed;
    $("#detail-main").classList.toggle("zoomed", zoomed);
  }
  $("#detail-zoom").addEventListener("click", toggleZoom);
  $("#detail-compare").addEventListener("click", () => openCompare(app.shoot.photos[detailId - 1].moment));

  $("#detail-actions").addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (b) toggle(detailId, b.dataset.act);
  });
  $("#detail-strip").addEventListener("click", (e) => {
    const img = e.target.closest("[data-id]");
    if (img) openDetail(+img.dataset.id);
  });

  function visibleIds() {
    return $$("#grid .tile").map((t) => +t.dataset.id);
  }

  function focusedTile() {
    const t = document.activeElement && document.activeElement.closest && document.activeElement.closest("#grid .tile");
    return t ? +t.dataset.id : null;
  }

  function focusTile(id) {
    const t = $(`#grid .tile[data-id="${id}"]`);
    if (!t) return;
    t.focus({ preventScroll: true });
    t.scrollIntoView({ block: "nearest" });
  }

  // The tile in the next row up or down (dir -1 / 1) nearest the current one's column.
  function tileVertical(id, dir) {
    const tiles = $$("#grid .tile").map((t) => [t, t.getBoundingClientRect()]);
    const cur = tiles.find(([t]) => +t.dataset.id === id);
    if (!cur) return null;
    const r = cur[1], cx = r.left + r.width / 2;
    const next = tiles.filter(([, b]) => (dir > 0 ? b.top > r.bottom - 2 : b.bottom < r.top + 2));
    if (!next.length) return null;
    const rowTop = (dir > 0 ? Math.min : Math.max)(...next.map(([, b]) => b.top));
    const row = next.filter(([, b]) => Math.abs(b.top - rowTop) < 4);
    const dist = ([, b]) => Math.abs(b.left + b.width / 2 - cx);
    return +row.reduce((a, c) => (dist(c) < dist(a) ? c : a))[0].dataset.id;
  }

  const KEY_ACTS = { p: "pick", g: "gem", x: "reject" };

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      $$(".modal").forEach((m) => (m.hidden = true));
      return;
    }
    if ($("#results").hidden || e.target.closest("input, textarea, select")) return;
    const open = $$(".modal").filter((m) => !m.hidden).map((m) => m.id);
    if (open.some((id) => !["detail", "compare"].includes(id))) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if ((k === "z" && (e.metaKey || e.ctrlKey)) || (k === "u" && !e.metaKey && !e.ctrlKey)) {
      e.preventDefault();
      undo();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey || (k === " " && e.target.closest("button"))) return;
    if (e.key === "?" || (e.code === "Slash" && e.shiftKey)) {
      $("#shortcuts").hidden = false;
      return;
    }
    if (open.includes("compare")) {
      const btn = $$("#compare-row .cmp")[+e.key - 1];
      if (btn) makePick(+btn.dataset.id);
      return;
    }
    const inDetail = open.includes("detail");
    const id = inDetail ? detailId : focusedTile();
    const ids = visibleIds();
    const i = ids.indexOf(id);
    const go = (target) => {
      if (target == null) return;
      if (inDetail) openDetail(target);
      else focusTile(target);
    };
    if (!id && ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", " "].includes(k)) go(ids[0]);
    else if (k === "ArrowRight" || k === " ") go(ids[i + 1]);
    else if (k === "ArrowLeft") go(ids[i - 1]);
    else if ((k === "ArrowDown" || k === "ArrowUp") && !inDetail) go(tileVertical(id, k === "ArrowDown" ? 1 : -1));
    else if (KEY_ACTS[k] && id) toggle(id, KEY_ACTS[k]);
    else if (k === "c" && id) openCompare(app.shoot.photos[id - 1].moment);
    else if (k === "z" && inDetail) toggleZoom();
    else if (k === "e" && inDetail) setAfter(!showAfter);
    else return;
    e.preventDefault();
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]") || e.target.classList.contains("modal")) {
      e.target.closest(".modal").hidden = true;
    }
    const opener = e.target.closest("[data-open]");
    if (opener) openSettings();
  });

  /* ---------- export ---------- */
  $("#export").addEventListener("click", async () => {
    const btn = $("#export");
    btn.disabled = true;
    try {
      const r = await api("/api/export", { body: {} });
      done({ icon: "export", title: `Copied ${r.picks} pick${r.picks === 1 ? "" : "s"}${r.gems ? ` and ${r.gems} hidden gem${r.gems > 1 ? "s" : ""}` : ""}`,
        text: "They're in the Goodframes folder next to your originals, numbered in the order of the final edit. Your originals weren't moved or changed.",
        actions: [{ label: "Show in Finder", run: () => api("/api/reveal", { body: { folder: r.picks_dir } }) }] });
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- settings ---------- */
  async function openSettings() {
    app.settings = await api("/api/settings");
    applyTheme(app.settings.theme);
    fillModelSelects();
    renderStyle();
    renderProfile();
    $("#two-tier-toggle").setAttribute("aria-checked", String(app.settings.two_tier));
    $("#taste-count").textContent = app.settings.taste_count
      ? `Learned from ${app.settings.taste_count} of your choices` : "Nothing learned yet";
    const shootOpen = !!app.shoot && !$("#results").hidden;
    $("#shoot-settings").hidden = !shootOpen;
    if (shootOpen && app.shootSettings) {
      $("#min-score").value = app.shootSettings.min_score;
      syncSelection();
      $("#gem-threshold").value = app.shootSettings.gem_threshold;
      syncOutputs();
    }
    settingsPane(shootOpen ? "culling" : "profile");
    $("#settings").hidden = false;
  }

  function settingsPane(name) {
    $$(".prefs-nav [data-pane]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.pane === name)));
    $$(".prefs-body .pane").forEach((p) => (p.hidden = p.dataset.pane !== name));
  }

  $(".prefs-nav").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pane]");
    if (b) settingsPane(b.dataset.pane);
  });

  function renderProfile() {
    const p = app.settings.profile;
    $("#pf-avatar").textContent = (p.name || "?").slice(0, 1).toUpperCase();
    $("#pf-title").textContent = p.name || "Your profile";
    $("#pf-summary").textContent = profileSummary(p);
    if (document.activeElement !== $("#pf-name")) $("#pf-name").value = p.name;
    renderPickers($('.pane[data-pane="profile"]'), p);
  }

  let profileTimer;
  function profileChanged() {
    clearTimeout(profileTimer);
    profileTimer = setTimeout(async () => {
      try {
        await saveProfile(app.settings.profile);
      } catch (err) {
        toast("Couldn't save your profile");
      }
    }, 400);
  }

  $('.pane[data-pane="profile"]').addEventListener("click", (e) => {
    if (!pick(e, app.settings.profile)) return;
    renderProfile();
    profileChanged();
  });
  $("#pf-name").addEventListener("input", (e) => {
    app.settings.profile.name = e.target.value.trim();
    renderProfile();
    profileChanged();
  });
  $("#pf-redo").addEventListener("click", () => {
    $("#settings").hidden = true;
    startOnboarding({ redo: true });
  });

  function renderStyle() {
    const style = app.settings.style || [];
    $("#style-empty").hidden = !!style.length;
    $("#style-list").innerHTML = style.map((s) => `<li><span>${esc(s)}</span>
      <button class="btn ghost" data-forget="${esc(s)}" aria-label="Forget ${esc(s)}">Forget</button></li>`).join("");
  }

  $("#style-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-forget]");
    if (!b) return;
    const { style } = await api("/api/style/forget", { body: { item: b.dataset.forget } });
    app.settings.style = style;
    renderStyle();
  });

  function syncSelection() {
    const sel = app.shoot && app.shoot.selection;
    $("#selection-wrap").hidden = !sel;
    $("#threshold-wrap").hidden = !!sel;
    if (!sel) return;
    $("#selection-size").max = sel.total;
    $("#selection-size").value = sel.size;
    $("#selection-hint").textContent = `Claude recommends ${sel.recommended} of the ${sel.total} strongest frames. Changing this costs nothing.`;
    $("#selection-out").textContent = sel.size;
  }

  function syncOutputs() {
    $("#min-score-out").textContent = (+$("#min-score").value).toFixed(1);
    $("#gem-out").textContent = (+$("#gem-threshold").value).toFixed(1);
  }

  $("#two-tier-toggle").addEventListener("click", async () => {
    try {
      app.settings = await api("/api/settings", { body: { two_tier: !app.settings.two_tier } });
      $("#two-tier-toggle").setAttribute("aria-checked", String(app.settings.two_tier));
      toast(app.settings.two_tier ? "Two-tier review on for new reviews" : "Every moment reviewed by the chosen model");
    } catch (err) {
      toast("Couldn't save the setting");
    }
  });

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $$("[data-theme-choice]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === theme)));
  }

  $("#theme-choice").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-theme-choice]");
    const previous = document.documentElement.dataset.theme;
    if (!b || b.dataset.themeChoice === previous) return;
    applyTheme(b.dataset.themeChoice);
    try {
      app.settings = await api("/api/settings", { body: { theme: b.dataset.themeChoice } });
    } catch (err) {
      applyTheme(previous);
      toast("Couldn't save the theme");
    }
  });

  $("#settings-model").addEventListener("change", async (e) => {
    app.settings = await api("/api/settings", { body: { model: e.target.value } });
    toast(`Using ${app.settings.models.find((m) => m.id === app.settings.model).label} for new reviews`);
  });

  let sliderTimer;
  ["#selection-size", "#min-score", "#gem-threshold"].forEach((sel) =>
    $(sel).addEventListener("input", () => {
      syncOutputs();
      $("#selection-out").textContent = $("#selection-size").value;
      clearTimeout(sliderTimer);
      sliderTimer = setTimeout(async () => {
        const body = { min_score: +$("#min-score").value, gem_threshold: +$("#gem-threshold").value };
        const selection = app.shoot.selection;
        // Back at Claude's number means following its recommendation again.
        if (selection) body.selection_size = +$("#selection-size").value === selection.recommended ? 0 : +$("#selection-size").value;
        app.shootSettings = { ...app.shootSettings, ...body };
        app.shoot = (await api("/api/shoot-settings", { body })).state;
        renderResults();
      }, 200);
    }));

  $("#replace-key").addEventListener("click", () => {
    $("#settings").hidden = true;
    askForKey();
  });
  $("#remove-key").addEventListener("click", async () => {
    await api("/api/key", { method: "DELETE" });
    $("#settings").hidden = true;
    app.settings.has_key = false;
    askForKey();
  });
  $("#reset-taste").addEventListener("click", async () => {
    await api("/api/taste/reset", { body: {} });
    $("#taste-count").textContent = "Nothing learned yet";
  });

  boot().catch((err) => { console.error(err); toast(err.message); });
})();
