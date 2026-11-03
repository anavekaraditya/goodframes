/* Goodframes UI: screens, API calls, live events and the results browser. */
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const app = { settings: null, shoot: null, estimate: null, tab: "pick", reviewing: null, spent: 0, chatSpent: 0, brief: null, refining: false, briefBusy: false };

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
    app.chatSpent = 0;
    app.refining = false;
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
    else show("onboarding");
  });

  /* ---------- the brief ---------- */
  const FALLBACK_OPENING = "What's this shoot, and what matters most to you in it?";

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
    if (!app.brief.messages.length) sendBrief(null);
  }

  function renderMessages(pending) {
    const b = app.brief;
    const list = b.messages.length || !pending ? [...b.messages] : [];
    if (!list.length && !pending) list.push({ role: "assistant", text: FALLBACK_OPENING });
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
      ["Look", d.look], ["Final count", d.target_count || "Claude's recommendation"],
      ["Per moment", d.per_moment === "few" ? "Several frames can stay" : "The best frame"],
      ["Technical check", d.technical_tolerance === "loose" ? "Loose: more frames reach Claude" : "Standard"],
      ["My style", b.style.join(", ")],
    ].filter(([, v]) => v);
    $("#brief-fields").innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
    $("#brief-style").hidden = !b.suggestions.length;
    $("#brief-suggestions").innerHTML = b.suggestions.map((s) =>
      `<label><input type="checkbox" value="${esc(s)}"> <span>${esc(s)}</span></label>`).join("");
    const work = est.moments ? `Reviews ${est.moments} moment${est.moments > 1 ? "s" : ""}${est.final_edit ? ", then the final edit" : ""}. About ${est.minutes} min with ${est.model_label}.`
      : est.requests ? `Keeps the existing reviews and reruns only the final edit, with ${est.model_label}.` : "Nothing needs to rerun.";
    $("#brief-rerun").textContent = work;
    const confirm = $("#brief-confirm");
    confirm.textContent = est.requests ? `Confirm and start · about $${est.cost.toFixed(2)}` : "Confirm · no cost";
    confirm.disabled = app.briefBusy;
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
    if (!res.started) {
      const t = Stage.applyAll(app.shoot);
      later(t + 400, finish);
      return;
    }
    if (res.state.photos.filter((p) => p.status === "rejected").length !== before) Stage.toClusters(res.state);
    app.reviewing = { done: 0, total: res.estimate.moments };
    status(`Claude is reviewing 0 / ${res.estimate.moments} moments`);
  });

  $("#refine").addEventListener("click", () => {
    if (app.refining) return;
    if (app.settings.has_key) openBrief();
    else show("onboarding");
  });

  function finish() {
    setStep("done");
    const c = app.shoot.counts;
    const t = Stage.finale(app.shoot);
    status(app.spent + app.chatSpent ? `Done · $${(app.spent + app.chatSpent).toFixed(2)}` : "Done");
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
    pick: "The final edit, strongest first. Adjust the selection size in Settings.",
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
    const unmet = app.shoot.unmet || [];
    $("#unmet").hidden = !unmet.length;
    $("#unmet").textContent = unmet.length ? `No frame found for: ${unmet.join(", ")}` : "";
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
        : app.tab === "pick" ? (a.rank || Infinity) - (b.rank || Infinity) || (b.final || 0) - (a.final || 0) : a.id - b.id);
      html = list.map(tile).join("") || `<div class="empty">${app.tab === "pick" ? "No picks yet. Run the review, or change the selection in Settings." : "Nothing here."}</div>`;
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
    const sub = p.subscores || {};
    const rows = [["Claude", p.score], ["After edit", p.edit_potential], ["Impact", sub.impact], ["Moment", sub.moment],
      ["Light", sub.light], ["Composition", sub.composition], ["Subject focus", sub.subject_focus],
      ["Technical", p.technical], ["Final", p.final]]
      .filter(([, v]) => v != null)
      .map(([k, v]) => `<dt>${k}</dt><dd>${(+v).toFixed(1)}</dd>`).join("");
    const rank = p.rank ? `<dt>Final edit</dt><dd>#${p.rank}</dd>` : "";
    $("#detail-scores").innerHTML = rank + rows + `<dt>Taken</dt><dd>${time(p.taken)}</dd>`;
    const recipe = $("#detail-recipe");
    recipe.hidden = !p.edit_recipe;
    recipe.innerHTML = p.edit_recipe ? `<b>Edit recipe</b>${esc(p.edit_recipe)}` : "";
    const cut = p.status === "below" && p.why ? `Why it was left out: ${p.why}. ` : "";
    $("#detail-notes").textContent = cut + (p.notes.length ? "Technical: " + p.notes.join(" · ") : "");
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
    renderStyle();
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
    $("#settings").hidden = false;
  }

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
