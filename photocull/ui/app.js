/* Goodframes UI: screens, API calls, live events and the results browser. */
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const app = { settings: null, shoot: null, estimate: null, tab: "pick", reviewing: null, spent: 0 };

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
    fillModelSelects();
    connectEvents();
    if (!app.settings.has_key) show("onboarding");
    else showHome();
  }

  function fillModelSelects() {
    ["#model-select", "#settings-model"].forEach((sel) => {
      $(sel).innerHTML = app.settings.models
        .map((m) => `<option value="${m.id}">${m.label} — $${m.in}/$${m.out} per 1M tokens</option>`)
        .join("");
      $(sel).value = app.settings.model;
    });
  }

  /* ---------- onboarding ---------- */
  $("#key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.submitter;
    $("#key-error").textContent = "";
    btn.disabled = true;
    btn.textContent = "Checking key…";
    try {
      await api("/api/settings", { body: { model: $("#model-select").value } });
      await api("/api/key", { body: { key: $("#key-input").value } });
      $("#key-input").value = "";
      app.settings = await api("/api/settings");
      fillModelSelects();
      app.shoot ? show("results") : showHome();
    } catch (err) {
      $("#key-error").textContent = err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "Continue";
    }
  });

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-url]");
    if (a) {
      e.preventDefault();
      api("/api/open-url", { body: { url: a.dataset.url } });
    }
  });

  /* ---------- home ---------- */
  async function showHome() {
    app.settings = await api("/api/settings");
    const recent = app.settings.recent;
    $("#recent-wrap").hidden = !recent.length;
    $("#recent").innerHTML = recent
      .map((f) => `<li><button data-folder="${esc(f)}"><span>${esc(f.split("/").filter(Boolean).pop())}</span>
        <span class="path">${esc(f)}</span></button></li>`)
      .join("");
    $("#path-form").hidden = app.settings.desktop;
    show("home");
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
        Stage.addCard(e.fp, e.n);
        status(`Reading ${e.i} / ${e.n}`);
        break;
      case "analyzed": {
        if (!e.state || !e.state.photos.length) {
          toast("No JPEG or HEIC photos in that folder.");
          showHome();
          return;
        }
        app.shoot = e.state;
        app.estimate = e.estimate;
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
        status(`Claude reviewed ${app.reviewing.done} / ${app.reviewing.total} moments · $${e.spent.toFixed(2)}`);
        break;
      case "review_error":
        toast(e.error);
        break;
      case "reviewed":
        app.spent = e.spent;
        if (e.errors.length) toast(`${e.errors.length} moment(s) couldn't be reviewed: ${e.errors[0]}`);
        break;
      case "state":
        app.shoot = e.state;
        finish();
        break;
      case "error":
        toast("Something went wrong: " + e.error);
        break;
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
    $("#est-text").innerHTML = `<strong>${est.moments} moments to review</strong>
      <span>${est.photos} photos · about $${est.cost.toFixed(2)} and ${est.minutes} min with ${esc(est.model_label)}</span>`;
    $("#start-review").textContent = app.settings.has_key ? "Review with Claude" : "Add API key";
    $("#estimate").hidden = false;
    gsap.fromTo("#estimate", { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" });
  }

  $("#start-review").addEventListener("click", async () => {
    if (!app.settings.has_key) {
      show("onboarding");
      return;
    }
    try {
      await api("/api/review", { body: {} });
      $("#estimate").hidden = true;
      setStep("review");
      app.reviewing = { done: 0, total: app.estimate.moments };
      status(`Claude is reviewing 0 / ${app.estimate.moments} moments`);
    } catch (err) {
      toast(err.message);
    }
  });

  function finish() {
    setStep("done");
    const c = app.shoot.counts;
    const t = Stage.finale(app.shoot);
    status(app.spent ? `Done · $${app.spent.toFixed(2)}` : "Done");
    later(t + 200, () => {
      $("#finished-text").innerHTML = `<strong>${c.pick} picks${c.gem ? ` and ${c.gem} hidden gem${c.gem > 1 ? "s" : ""}` : ""}</strong>
        <span>from ${app.shoot.photos.length} photos</span>`;
      $("#finished").hidden = false;
      gsap.fromTo("#finished", { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" });
    });
  }

  $("#see-results").addEventListener("click", () => openResults());
  $("#stage-back").addEventListener("click", showHome);
  $("#results-back").addEventListener("click", showHome);

  /* ---------- results ---------- */
  const INTRO = {
    pick: "The best frame of each moment, as shot. Ranked by Claude's score.",
    gem: "Frames with strong bones that an edit will bring to life. Each comes with a recipe.",
    moments: "Every moment of the shoot, with all its candidates.",
    rejected: "Frames that failed the technical check or Claude's review. Nothing here was deleted.",
  };

  async function openResults() {
    const data = await api("/api/shoot");
    app.shoot = data.state;
    app.shootSettings = data.settings;
    $("#results-name").textContent = app.shoot.name;
    renderResults();
    show("results");
  }

  function counts() {
    const c = app.shoot.counts;
    const moments = app.shoot.moments.length;
    return { pick: c.pick, gem: c.gem, moments, rejected: c.rejected };
  }

  function renderResults() {
    const c = counts();
    $$("#tabs button").forEach((b) => {
      b.querySelector("b").textContent = c[b.dataset.tab];
      b.setAttribute("aria-selected", b.dataset.tab === app.tab);
    });
    $("#tab-intro").textContent = INTRO[app.tab];
    const ps = app.shoot.photos;
    let html = "";
    if (app.tab === "moments") {
      app.shoot.moments.forEach((m) => {
        const members = m.members.map((id) => ps[id - 1]);
        const shown = members.filter((p) => p.status !== "rejected");
        if (!shown.length) return;
        html += `<div class="moment-head">Moment ${m.id} · ${time(members[0].taken)}</div>
          <div class="moment-row">${shown.map(tile).join("")}</div>`;
      });
    } else {
      let list = ps.filter((p) => p.status === app.tab);
      list.sort((a, b) => app.tab === "gem" ? (b.edit_potential || 0) - (a.edit_potential || 0)
        : app.tab === "pick" ? (b.final || 0) - (a.final || 0) : a.id - b.id);
      html = list.map(tile).join("") || `<div class="empty">${app.tab === "pick" ? "No picks yet. Run the review, or lower the pick threshold in Settings." : "Nothing here."}</div>`;
    }
    $("#grid").innerHTML = html;
  }

  const LABEL = { pick: "Pick", gem: "Gem", rejected: "Reject", similar: "Similar", below: "Below cut", candidate: "Unreviewed" };

  function tile(p) {
    const score = p.score != null ? `${p.score.toFixed(1)}` : "–";
    const line = p.status === "gem" ? `<div class="recipe-line">${esc(p.edit_recipe || p.why)}</div>` : "";
    const reason = p.status === "rejected" ? p.why : (p.reason || p.why || "");
    return `<div class="tile ${p.status}" data-id="${p.id}" tabindex="0">
      <span class="badge ${p.status}">${LABEL[p.status] || p.status}</span>
      <div class="acts">
        <button class="chip-btn ${p.status === "pick" ? "on" : ""}" data-act="pick" title="Pick">✓ Pick</button>
        <button class="chip-btn gem ${p.status === "gem" ? "on" : ""}" data-act="gem" title="Hidden gem">◆</button>
      </div>
      <img loading="lazy" src="/api/thumb/${p.fp}" alt="">
      <div class="meta"><p class="reason">${esc(reason)}</p>${line}
        <div class="sub"><span>${esc(p.file)}</span><span>Claude ${score}${p.final ? ` · final ${p.final.toFixed(1)}` : ""}</span></div>
      </div></div>`;
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
    const t = e.target.closest(".tile");
    if (!t) return;
    const id = +t.dataset.id;
    const act = e.target.closest("[data-act]");
    if (act) toggle(id, act.dataset.act);
    else openDetail(id);
  });
  $("#grid").addEventListener("keydown", (e) => {
    const t = e.target.closest(".tile");
    if (t && e.key === "Enter") openDetail(+t.dataset.id);
  });

  async function toggle(id, kind) {
    const p = app.shoot.photos[id - 1];
    let action;
    if (kind === "pick") action = p.status === "pick" ? (p.override === "pick" ? null : "unpick") : "pick";
    else action = p.status === "gem" ? (p.override === "gem" ? null : "ungem") : "gem";
    const data = await api("/api/override", { body: { id, action } });
    app.shoot = data.state;
    renderResults();
    if (!$("#detail").hidden) openDetail(id);
  }

  /* ---------- detail ---------- */
  let detailId = null;
  function openDetail(id) {
    detailId = id;
    const p = app.shoot.photos[id - 1];
    $("#detail-img").src = `/api/thumb/${p.fp}`;
    $("#detail-crop").src = `/api/crop/${p.id}`;
    const badge = $("#detail-badge");
    badge.className = `badge ${p.status}`;
    badge.textContent = LABEL[p.status] || p.status;
    $("#detail-title").textContent = p.file;
    $("#detail-reason").textContent = p.status === "rejected" ? p.why : (p.reason || p.why || "Not reviewed yet");
    const rows = [["Claude", p.score], ["After edit", p.edit_potential], ["Technical", p.technical], ["Final", p.final]]
      .filter(([, v]) => v != null)
      .map(([k, v]) => `<dt>${k}</dt><dd>${(+v).toFixed(1)}</dd>`).join("");
    $("#detail-scores").innerHTML = rows + `<dt>Taken</dt><dd>${time(p.taken)}</dd>`;
    const recipe = $("#detail-recipe");
    recipe.hidden = !p.edit_recipe;
    recipe.innerHTML = p.edit_recipe ? `<b>Edit recipe</b>${esc(p.edit_recipe)}` : "";
    $("#detail-notes").textContent = p.notes.length ? "Technical: " + p.notes.join(" · ") : "";
    $("#detail-actions").innerHTML = `
      <button class="btn ${p.status === "pick" ? "primary" : ""}" data-act="pick">${p.status === "pick" ? "✓ Picked" : "Pick"}</button>
      <button class="btn ${p.status === "gem" ? "primary" : ""}" data-act="gem" style="${p.status === "gem" ? "background:var(--gem);border-color:var(--gem)" : ""}">◆ ${p.status === "gem" ? "Hidden gem" : "Mark as gem"}</button>`;
    const moment = app.shoot.moments.find((m) => m.id === p.moment);
    $("#detail-strip").innerHTML = moment.members.map((mid) => {
      const q = app.shoot.photos[mid - 1];
      return `<img src="/api/thumb/${q.fp}" data-id="${q.id}" class="${q.id === id ? "cur" : q.status}" title="${esc(q.file)}" alt="">`;
    }).join("");
    $("#detail").hidden = false;
  }

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

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$(".modal").forEach((m) => (m.hidden = true));
    if ($("#detail").hidden) return;
    const ids = visibleIds();
    const i = ids.indexOf(detailId);
    if (e.key === "ArrowRight" && i < ids.length - 1) openDetail(ids[i + 1]);
    if (e.key === "ArrowLeft" && i > 0) openDetail(ids[i - 1]);
    if (e.key === "p") toggle(detailId, "pick");
    if (e.key === "g") toggle(detailId, "gem");
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
      toast(`Copied ${r.picks} picks${r.gems ? ` and ${r.gems} hidden gems` : ""} next to your originals`, {
        label: "Show in Finder", run: () => api("/api/reveal", { body: { folder: r.picks_dir } }),
      });
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
    $("#taste-count").textContent = app.settings.taste_count
      ? `Learned from ${app.settings.taste_count} of your choices` : "Nothing learned yet";
    const shootOpen = !!app.shoot && !$("#results").hidden;
    $("#shoot-settings").hidden = !shootOpen;
    if (shootOpen && app.shootSettings) {
      $("#min-score").value = app.shootSettings.min_score;
      $("#gem-threshold").value = app.shootSettings.gem_threshold;
      syncOutputs();
    }
    $("#settings").hidden = false;
  }

  function syncOutputs() {
    $("#min-score-out").textContent = (+$("#min-score").value).toFixed(1);
    $("#gem-out").textContent = (+$("#gem-threshold").value).toFixed(1);
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("#theme-toggle").setAttribute("aria-checked", String(theme === "dark"));
  }

  $("#theme-toggle").addEventListener("click", async () => {
    const previous = document.documentElement.dataset.theme;
    applyTheme(previous === "dark" ? "light" : "dark");
    try {
      app.settings = await api("/api/settings", { body: { theme: document.documentElement.dataset.theme } });
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
  ["#min-score", "#gem-threshold"].forEach((sel) =>
    $(sel).addEventListener("input", () => {
      syncOutputs();
      clearTimeout(sliderTimer);
      sliderTimer = setTimeout(async () => {
        const body = { min_score: +$("#min-score").value, gem_threshold: +$("#gem-threshold").value };
        app.shootSettings = { ...app.shootSettings, ...body };
        app.shoot = (await api("/api/shoot-settings", { body })).state;
        renderResults();
      }, 200);
    }));

  $("#replace-key").addEventListener("click", () => {
    $("#settings").hidden = true;
    show("onboarding");
  });
  $("#remove-key").addEventListener("click", async () => {
    await api("/api/key", { method: "DELETE" });
    $("#settings").hidden = true;
    app.settings.has_key = false;
    show("onboarding");
  });
  $("#reset-taste").addEventListener("click", async () => {
    await api("/api/taste/reset", { body: {} });
    $("#taste-count").textContent = "Nothing learned yet";
  });

  boot().catch((err) => toast(err.message));
})();
