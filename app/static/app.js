"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const state = { userId: null, view: "matched", seen: new Set(), firstLoad: true, defaultThreshold: 2.0 };

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join("; ") : data.detail;
    throw new Error(detail || res.statusText);
  }
  return data;
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function timeAgo(ts) {
  const d = (Date.now() / 1000 - ts);
  if (d < 60) return "только что";
  if (d < 3600) return `${Math.floor(d / 60)} мин назад`;
  if (d < 86400) return `${Math.floor(d / 3600)} ч назад`;
  return new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function highlight(text, forms) {
  let html = esc(text);
  if (!forms.length) return html;
  const sorted = [...new Set(forms)].sort((a, b) => b.length - a.length).map(f => reEsc(esc(f)));
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${sorted.join("|")})(?![\\p{L}\\p{N}])`, "giu");
  return html.replace(re, "<mark>$1</mark>");
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

async function loadUsers(selectId) {
  const users = await api("/api/users");
  const sel = $("#user-select");
  sel.innerHTML = users.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join("");
  const saved = Number(selectId ?? localStorage.getItem("userId"));
  state.userId = users.some(u => u.id === saved) ? saved : users[0]?.id ?? null;
  if (state.userId) sel.value = state.userId;
  await switchUser();
}

async function switchUser() {
  localStorage.setItem("userId", state.userId);
  state.seen.clear();
  state.firstLoad = true;
  await Promise.all([loadFilters(), loadFeeds(), loadFeed()]);
}

$("#user-select").addEventListener("change", e => { state.userId = Number(e.target.value); switchUser(); });

$("#new-user-btn").addEventListener("click", async () => {
  const name = prompt("Имя нового пользователя:");
  if (!name?.trim()) return;
  try {
    const u = await api("/api/users", { method: "POST", body: { name: name.trim() } });
    await loadUsers(u.id);
  } catch (e) { alert(e.message); }
});

$("#delete-user-btn").addEventListener("click", async () => {
  const name = $("#user-select").selectedOptions[0]?.textContent;
  if (!state.userId || !confirm(`Удалить пользователя «${name}» вместе с его фильтрами?`)) return;
  await api(`/api/users/${state.userId}`, { method: "DELETE" });
  localStorage.removeItem("userId");
  await loadUsers();
});

document.querySelectorAll(".tabs button").forEach(btn => btn.addEventListener("click", () => {
  document.querySelectorAll(".tabs button, .tab").forEach(el => el.classList.remove("active"));
  btn.classList.add("active");
  $(`#tab-${btn.dataset.tab}`).classList.add("active");
  if (btn.dataset.tab === "test") loadPosts();
}));

const filterForm = $("#filter-form");

function syncFilterForm() {
  const sem = filterForm.kind.value === "semantic";
  filterForm.querySelector(".threshold-row").classList.toggle("hidden", !sem);
  filterForm.querySelector(".kw-help").classList.toggle("hidden", sem);
  filterForm.querySelector(".sem-help").classList.toggle("hidden", !sem);
  filterForm.query.placeholder = sem
    ? "космос, астрономия, исследование планет"
    : 'нейросеть, ИИ, "машинное обучение", -реклама';
  if (sem && !filterForm.threshold.value) filterForm.threshold.value = state.defaultThreshold;
}
filterForm.querySelectorAll("input[name=kind]").forEach(r => r.addEventListener("change", syncFilterForm));

filterForm.addEventListener("submit", async e => {
  e.preventDefault();
  const kind = filterForm.kind.value;
  const body = { kind, name: filterForm.name.value, query: filterForm.query.value };
  if (kind === "semantic") body.threshold = Number(filterForm.threshold.value) || state.defaultThreshold;
  try {
    await api(`/api/users/${state.userId}/filters`, { method: "POST", body });
    filterForm.name.value = filterForm.query.value = "";
    await Promise.all([loadFilters(), loadFeed()]);
  } catch (err) { alert(err.message); }
});

async function loadFilters() {
  if (!state.userId) return;
  const filters = await api(`/api/users/${state.userId}/filters`);
  const box = $("#filter-list");
  if (!filters.length) {
    box.innerHTML = `<p class="hint">Фильтров нет: лента пуста. Добавьте фильтр выше.</p>`;
    return;
  }
  box.innerHTML = filters.map(f => `
    <div class="row ${f.enabled ? "" : "disabled"}" data-id="${f.id}">
      <div class="row-head">
        <input type="checkbox" class="toggle" ${f.enabled ? "checked" : ""} title="Включить/выключить">
        <span class="tag ${f.kind}">${f.kind === "keyword" ? "слова" : "тема"}</span>
        <b class="grow">${esc(f.name)}</b>
        ${f.kind === "semantic" ? `<label class="hint">z ≥ <input class="thr" type="number" step="0.1" value="${f.threshold ?? state.defaultThreshold}"></label>` : ""}
        <button class="ghost del" title="Удалить">✕</button>
      </div>
      <div class="sub">${esc(f.query)}</div>
    </div>`).join("");
}

$("#filter-list").addEventListener("change", async e => {
  const id = e.target.closest(".row")?.dataset.id;
  if (!id) return;
  if (e.target.classList.contains("toggle")) {
    await api(`/api/filters/${id}`, { method: "PATCH", body: { enabled: e.target.checked } });
  } else if (e.target.classList.contains("thr")) {
    await api(`/api/filters/${id}`, { method: "PATCH", body: { threshold: Number(e.target.value) } });
  } else return;
  await Promise.all([loadFilters(), loadFeed()]);
});

$("#filter-list").addEventListener("click", async e => {
  if (!e.target.classList.contains("del")) return;
  const id = e.target.closest(".row").dataset.id;
  await api(`/api/filters/${id}`, { method: "DELETE" });
  await Promise.all([loadFilters(), loadFeed()]);
});

async function loadFeeds() {
  if (!state.userId) return;
  const feeds = await api(`/api/users/${state.userId}/feeds`);
  $("#feed-list").innerHTML = feeds.map(f => `
    <div class="row ${f.subscribed ? "" : "disabled"}" data-id="${f.id}">
      <div class="row-head">
        <input type="checkbox" class="sub-toggle" ${f.subscribed ? "checked" : ""} title="Читать канал">
        <b class="grow">${esc(f.title || f.url)}</b>
        <span class="hint">${f.articles} нов.</span>
        ${f.is_local ? `<span class="tag miss">тестовый</span>` : `<button class="ghost del" title="Удалить из каталога">✕</button>`}
      </div>
      <div class="sub">${esc(f.url)}${f.last_fetched ? ` · опрошен ${timeAgo(f.last_fetched)}` : " · ещё не опрашивался"}</div>
      ${f.last_error ? `<div class="err">Ошибка: ${esc(f.last_error)}</div>` : ""}
    </div>`).join("");
}
const loadFeedsDebounced = debounce(loadFeeds, 500);

$("#feed-list").addEventListener("change", async e => {
  if (!e.target.classList.contains("sub-toggle")) return;
  const id = e.target.closest(".row").dataset.id;
  await api(`/api/users/${state.userId}/feeds/${id}`, { method: "PUT", body: { subscribed: e.target.checked } });
  await Promise.all([loadFeeds(), loadFeed()]);
});

$("#feed-list").addEventListener("click", async e => {
  if (!e.target.classList.contains("del")) return;
  if (!confirm("Удалить канал из общего каталога (у всех пользователей)?")) return;
  await api(`/api/feeds/${e.target.closest(".row").dataset.id}`, { method: "DELETE" });
  await Promise.all([loadFeeds(), loadFeed()]);
});

$("#feed-form").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target, msg = $("#feed-form-msg"), btn = form.querySelector("button");
  btn.disabled = true;
  msg.className = "msg";
  msg.textContent = "Загружаю канал…";
  try {
    const r = await api(`/api/users/${state.userId}/feeds`, { method: "POST", body: { url: form.url.value.trim() } });
    if (r.feed.last_error) {
      msg.className = "msg err";
      msg.textContent = `Канал добавлен, но не прочитан: ${r.feed.last_error}`;
    } else {
      msg.textContent = `Добавлен «${r.feed.title}»: новых статей ${r.new_articles}`;
      form.url.value = "";
    }
    await Promise.all([loadFeeds(), loadFeed()]);
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = err.message;
  } finally { btn.disabled = false; }
});

async function loadPosts() {
  const posts = await api("/api/test-posts");
  $("#post-list").innerHTML = posts.length ? posts.map(p => `
    <div class="row">
      <b>${esc(p.title)}</b>
      <div class="sub">${timeAgo(p.created_at)}${p.author ? " · " + esc(p.author) : ""}</div>
    </div>`).join("") : `<p class="hint">Постов пока нет.</p>`;
}

$("#post-form").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target, msg = $("#post-form-msg");
  try {
    await api("/api/test-posts", { method: "POST", body: {
      title: form.title.value, body: form.body.value, author: form.author.value || null } });
    form.reset();
    msg.className = "msg";
    msg.textContent = "Опубликовано. Если пост подходит под фильтры, он появится в ленте через пару секунд.";
    loadPosts();
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = err.message;
  }
});

document.querySelectorAll("input[name=view]").forEach(r => r.addEventListener("change", e => {
  state.view = e.target.value;
  loadFeed();
}));

const FIELD = { title: "в заголовке", summary: "в тексте" };

function matchBadge(m) {
  if (m.kind === "keyword") {
    const forms = [...new Set(m.details.hits.flatMap(h => h.where.flatMap(w => w.forms)))];
    return `<span class="tag keyword" title="Фильтр по ключевым словам">слова · ${esc(m.name)}: ${esc(forms.slice(0, 4).join(", "))}</span>`;
  }
  return `<span class="tag semantic" title="Семантический фильтр">тема · ${esc(m.name)}: z=${m.details.z} ≥ ${m.details.threshold}</span>`;
}

function missBadge(m) {
  if (m.kind === "keyword") {
    const d = m.details;
    let why = "нет совпадений";
    if (d.excluded.length) why = `исключено: ${d.excluded.map(x => x.term).join(", ")}`;
    else if (d.missing.length) why = `нет обязательного: ${d.missing.join(", ")}`;
    return `<span class="tag miss">${esc(m.name)}: ${esc(why)}</span>`;
  }
  if (m.details.z === undefined) return `<span class="tag miss">${esc(m.name)}: ${esc(m.details.error)}</span>`;
  return `<span class="tag miss">${esc(m.name)}: z=${m.details.z} &lt; ${m.details.threshold}</span>`;
}

function explain(m) {
  const d = m.details;
  if (m.kind === "keyword") {
    const hits = d.hits.map(h => `<li>«${esc(h.term)}» <span class="k">(${esc(h.method)})</span> → ` +
      h.where.map(w => `${FIELD[w.field]}: ${w.forms.map(f => `«${esc(f)}»`).join(", ")} ×${w.count}`).join("; ") + `</li>`).join("");
    const excl = d.excluded.map(h => `<li>исключающий терм «${esc(h.term)}» найден</li>`).join("");
    const miss = d.missing.map(t => `<li>обязательный терм «${esc(t)}» не найден</li>`).join("");
    return `<b>${esc(m.name)}</b> <span class="tag keyword">ключевые слова</span> <span class="k">запрос: ${esc(m.query)}</span><ul>${hits}${excl}${miss || ""}${!hits && !excl && !miss ? "<li>ни один терм не найден</li>" : ""}</ul>`;
  }
  if (d.z === undefined) return `<b>${esc(m.name)}</b>: ${esc(d.error)}`;
  const ok = d.z >= d.threshold;
  return `<b>${esc(m.name)}</b> <span class="tag semantic">семантика</span><ul>
    <li>ближайшая формулировка темы: «${esc(d.best_phrase)}», косинус = ${d.cosine}</li>
    <li>фон (40 нейтральных рубрик): μ = ${d.bg_mean}, σ = ${d.bg_std}</li>
    <li>z = (${d.cosine} − ${d.bg_mean}) / ${d.bg_std} = <b>${d.z}</b> ${ok ? "≥" : "&lt;"} порог ${d.threshold} → ${ok ? "проходит" : "не проходит"}</li>
    <li class="k">модель: ${esc(d.model)}</li></ul>`;
}

let feedNames = {};
async function loadFeed() {
  if (!state.userId) return;
  const [data, feeds] = await Promise.all([
    api(`/api/users/${state.userId}/feed?all=${state.view === "all"}`),
    api(`/api/users/${state.userId}/feeds`),
  ]);
  feedNames = Object.fromEntries(feeds.map(f => [f.id, f.title || f.url]));
  $("#feed-stats").textContent =
    `Прошло фильтры: ${data.total_matched} из ${data.total_candidates} новостей в подписках · фильтров: ${data.filters}` +
    (data.semantic_ready ? "" : " · семантическая модель загружается…");

  const box = $("#feed-list-items");
  if (!data.items.length) {
    box.innerHTML = `<div class="empty">${data.filters ? "Пока нет новостей, подходящих под фильтры." : "Добавьте фильтры слева: в ленте показываются только новости, прошедшие хотя бы один фильтр."}</div>`;
    return;
  }
  box.innerHTML = data.items.map(a => {
    const forms = a.matches.filter(m => m.kind === "keyword")
      .flatMap(m => m.details.hits.flatMap(h => h.where.flatMap(w => w.forms)));
    const fresh = !state.firstLoad && !state.seen.has(a.id);
    const all = [...a.matches, ...(a.misses || [])];
    return `<article class="article ${a.matches.length ? "" : "nomatch"} ${fresh ? "fresh" : ""}" data-id="${a.id}">
      <h3><a href="${esc(a.link || "#")}" target="_blank" rel="noopener">${highlight(a.title, forms)}</a></h3>
      <div class="meta">${esc(feedNames[a.feed_id] || "")} · ${timeAgo(a.published)}</div>
      ${a.summary ? `<p class="summary">${highlight(a.summary.length > 400 ? a.summary.slice(0, 400) + "…" : a.summary, forms)}</p>` : ""}
      <div class="badges">${a.matches.map(matchBadge).join("")}${(a.misses || []).map(missBadge).join("")}</div>
      ${all.length ? `<details class="why"><summary>Почему эта новость здесь (технические критерии)</summary>${all.map(explain).join("<hr>")}</details>` : ""}
    </article>`;
  }).join("");
  data.items.forEach(a => state.seen.add(a.id));
  state.firstLoad = false;
  setTimeout(() => document.querySelectorAll(".article.fresh").forEach(el => el.classList.remove("fresh")), 4000);
}
const loadFeedDebounced = debounce(loadFeed, 700);

let statusTimer = null;
async function loadStatus() {
  clearTimeout(statusTimer);
  const s = await api("/api/status");
  state.defaultThreshold = s.default_threshold;
  const el = $("#status-model");
  if (s.semantic_ready) { el.className = "pill ok"; el.textContent = `семантика: ${s.model}`; }
  else if (s.semantic_error) { el.className = "pill warn"; el.textContent = "семантика недоступна"; el.title = s.semantic_error; }
  else { el.className = "pill warn"; el.textContent = "модель загружается…"; statusTimer = setTimeout(loadStatus, 3000); }
}

$("#refresh-btn").addEventListener("click", () => api("/api/refresh", { method: "POST" }));

function connectEvents() {
  const live = $("#status-live");
  const es = new EventSource("/api/events");
  es.onopen = () => { live.className = "pill ok"; live.textContent = "online"; };
  es.onerror = () => { live.className = "pill warn"; live.textContent = "переподключение…"; };
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === "articles") { loadFeedDebounced(); loadFeedsDebounced(); loadStatus(); }
    if (ev.type === "feeds") loadFeedsDebounced();
  };
}

(async function init() {
  await loadStatus();
  syncFilterForm();
  await loadUsers();
  connectEvents();
})();
