/* NumberBank dashboard — بدون هیچ وابستگی خارجی */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { page: 0, limit: 25, filters: {}, total: 0 };

function toast(message, isError = false) {
  const box = $("#toast");
  if (!box) return;
  box.textContent = message;
  box.style.borderColor = isError ? "var(--bad)" : "var(--line)";
  box.style.display = "block";
  clearTimeout(box._t);
  box._t = setTimeout(() => (box.style.display = "none"), 4200);
}

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${text.slice(0, 180)}`);
  }
  return res.json();
}

function num(value) {
  if (value === null || value === undefined || value === "") return "—";
  return Number(value).toLocaleString("fa-IR");
}

function badge(text, kind = "info") {
  return `<span class="chip ${kind}">${escapeHtml(text ?? "—")}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function bandKind(band) {
  return { VERY_HIGH: "ok", HIGH: "ok", MEDIUM: "info", LOW: "warn", INVALID: "bad" }[band] || "info";
}

function statusKind(status) {
  return {
    ACTIVE: "ok", MERGED: "info", QUARANTINED: "warn", REJECTED: "bad",
    DONE: "ok", RUNNING: "info", PENDING: "warn", FAILED: "bad", CANCELLED: "warn",
    VALID: "ok", PROBABLE: "info", UNVERIFIED: "warn", INVALID: "bad",
  }[status] || "info";
}

function bar(value, max) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return `<div class="bar"><i style="width:${pct}%"></i></div>`;
}

function queryString(params) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    if (Array.isArray(value)) value.forEach((v) => usp.append(key, v));
    else usp.append(key, value);
  });
  return usp.toString();
}

/* ------------------------------- dashboard ------------------------------- */
async function renderDashboard() {
  try {
    const stats = await api("/api/stats?include_synthetic=true");
    const b = stats.businesses, p = stats.phones, e = stats.evidence, loc = stats.locations;
    $("#kpi-businesses").textContent = num(b.active);
    $("#kpi-businesses-sub").textContent = `کل: ${num(b.total)} | اعتبار بالا: ${num(b.high_confidence)}`;
    $("#kpi-phones").textContent = num(p.total);
    $("#kpi-phones-sub").textContent = `معتبر: ${num(p.valid)} | نامعتبر: ${num(p.invalid)}`;
    $("#kpi-evidence").textContent = num(e.observations);
    $("#kpi-evidence-sub").textContent = `${num(e.distinct_urls)} آدرس متمایز`;
    $("#kpi-coverage").textContent = `${num(loc.cities_with_data)}/${num(loc.cities_total)}`;
    $("#kpi-coverage-sub").textContent = `${loc.coverage_percent}% پوشش شهری`;
    $("#kpi-duplicates").textContent = num(stats.duplicates.merged_records);
    $("#kpi-errors").textContent = num(stats.errors);

    const bands = Object.entries(stats.bands || {});
    const maxBand = Math.max(1, ...bands.map(([, v]) => v));
    $("#bands").innerHTML = bands.length
      ? bands.map(([k, v]) => `
        <div class="flex" style="justify-content:space-between">
          <span>${badge(k, bandKind(k))}</span><span>${num(v)}</span>
        </div>${bar(v, maxBand)}`).join("")
      : `<div class="muted">دادهای ثبت نشده است</div>`;

    const cats = Object.entries(stats.categories || {});
    const maxCat = Math.max(1, ...cats.map(([, v]) => v));
    $("#categories").innerHTML = cats.length
      ? cats.map(([k, v]) => `
        <div class="flex" style="justify-content:space-between">
          <span class="chip">${escapeHtml(k)}</span><span>${num(v)}</span>
        </div>${bar(v, maxCat)}`).join("")
      : `<div class="muted">دادهای ثبت نشده است</div>`;

    const jobs = stats.jobs_recent || [];
    $("#recent-jobs").innerHTML = jobs.length ? jobs.map((j) => `
      <tr>
        <td><a href="/jobs#job-${j.id}">${escapeHtml(j.name)}</a></td>
        <td>${badge(j.status, statusKind(j.status))}</td>
        <td>${num(j.businesses_new)} / ${num(j.phones_new)}</td>
        <td>${num(j.errors)}</td>
        <td class="small muted">${escapeHtml((j.finished_at || j.started_at || "").slice(0, 19))}</td>
      </tr>`).join("") : `<tr><td colspan="5" class="muted">Jobی ثبت نشده است</td></tr>`;

    const cities = stats.top_cities || [];
    const maxCity = Math.max(1, ...cities.map((c) => c.count));
    $("#top-cities").innerHTML = cities.length ? cities.map((c) => `
      <div class="flex" style="justify-content:space-between"><span>${escapeHtml(c.city)}</span><span>${num(c.count)}</span></div>
      ${bar(c.count, maxCity)}`).join("") : `<div class="muted">دادهای ثبت نشده است</div>`;
  } catch (err) {
    toast(`خطا در بارگذاری آمار: ${err.message}`, true);
  }
}

/* ------------------------------ businesses ------------------------------ */
async function renderBusinesses() {
  const tbody = $("#business-rows");
  tbody.innerHTML = `<tr><td colspan="8"><span class="spinner"></span> در حال بارگذاری…</td></tr>`;
  try {
    const data = await api(`/api/v1/businesses?${queryString({ ...state.filters, limit: state.limit, offset: state.page * state.limit })}`);
    state.total = data.total;
    $("#result-count").textContent = `${num(data.total)} رکورد`;
    tbody.innerHTML = data.items.length ? data.items.map((row) => `
      <tr>
        <td><a href="/businesses/${row.id}">${escapeHtml(row.name || "—")}</a>
            ${row.is_synthetic ? '<span class="chip warn">آزمایشی</span>' : ""}</td>
        <td>${escapeHtml(row.city || "—")}<div class="small muted">${escapeHtml(row.province || "")}</div></td>
        <td>${escapeHtml(row.category_label || row.primary_category || "—")}</td>
        <td class="mono small">${(row.phones || []).map((ph) => escapeHtml(ph.number || ph.e164)).join("<br>") || "—"}</td>
        <td>${badge(row.band_label || row.confidence_band, bandKind(row.confidence_band))}<div class="small muted">${num(row.confidence)}</div></td>
        <td>${num(row.source_count)}</td>
        <td>${badge(row.status, statusKind(row.status))}</td>
        <td class="small muted">${escapeHtml((row.discovered_at || "").slice(0, 10))}</td>
      </tr>`).join("") : `<tr><td colspan="8" class="muted">رکوردی یافت نشد</td></tr>`;
    $("#page-info").textContent = `صفحه ${num(state.page + 1)} از ${num(Math.max(1, Math.ceil(state.total / state.limit)))}`;
    $("#prev").disabled = state.page === 0;
    $("#next").disabled = (state.page + 1) * state.limit >= state.total;
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">خطا: ${escapeHtml(err.message)}</td></tr>`;
  }
}

function wireBusinessFilters() {
  const apply = () => {
    state.page = 0;
    state.filters = {
      search: $("#f-search").value.trim() || undefined,
      province: $("#f-province").value.trim() || undefined,
      city: $("#f-city").value.trim() || undefined,
      category: $("#f-category").value || undefined,
      band: $("#f-band").value || undefined,
      min_confidence: $("#f-min").value || undefined,
      include_synthetic: true,
    };
    renderBusinesses();
  };
  ["#f-search", "#f-province", "#f-city", "#f-category", "#f-band", "#f-min"].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener("change", apply);
  });
  const search = $("#f-search");
  if (search) search.addEventListener("keydown", (ev) => { if (ev.key === "Enter") apply(); });
  $("#apply")?.addEventListener("click", apply);
  $("#prev")?.addEventListener("click", () => { if (state.page > 0) { state.page -= 1; renderBusinesses(); } });
  $("#next")?.addEventListener("click", () => { state.page += 1; renderBusinesses(); });
}

/* --------------------------- business detail --------------------------- */
async function renderBusinessDetail(id) {
  try {
    const row = await api(`/api/v1/businesses/${id}`);
    $("#detail-title").textContent = row.name || `کسبوکار #${id}`;
    $("#detail-meta").innerHTML = [
      badge(row.status, statusKind(row.status)),
      badge(row.confidence_band, bandKind(row.confidence_band)),
      `${num(row.confidence)} امتیاز`,
      row.primary_category ? badge(row.primary_category) : "",
      row.business_type ? badge(row.business_type) : "",
      row.is_synthetic ? badge("داده آزمایشی", "warn") : "",
    ].join(" ");
    const kv = {
      "استان": row.province, "شهرستان": row.county, "شهر": row.city,
      "نشانی": row.address, "مالک/مسئول": row.owner_name, "وضعیت مالک": row.owner_status,
      "وبسایت": row.website, "نوع کسب‌وکار": row.business_type_label || row.business_type,
      "امتیاز ارتباط": row.relevance_score, "تعداد منابع": row.source_count,
      "تاریخ کشف": (row.discovered_at || "").slice(0, 19),
      "آخرین اعتبارسنجی": (row.last_validated_at || "").slice(0, 19) || "—",
    };
    $("#detail-kv").innerHTML = Object.entries(kv)
      .map(([k, v]) => `<dt>${k}</dt><dd>${v === null || v === undefined || v === "" ? '<span class="muted">ثبت نشده</span>' : escapeHtml(v)}</dd>`).join("");

    const linkByPhone = {};
    (row.phone_links || []).forEach((link) => { linkByPhone[link.phone_id] = link; });
    const phones = row.phones || [];
    $("#detail-phones").innerHTML = phones.length ? phones.map((ph) => {
      const link = linkByPhone[ph.id] || {};
      return `
      <tr>
        <td class="mono">${escapeHtml(ph.number || ph.e164 || "—")}</td>
        <td>${badge(ph.type || "—")}</td>
        <td>${badge(ph.status || "—", statusKind(ph.status))}</td>
        <td>${num(ph.confidence)}</td>
        <td>${link.is_primary ? badge("اصلی", "ok") : ""}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="5" class="muted">شمارهای ثبت نشده است</td></tr>`;

    const evidence = row.evidence || [];
    $("#detail-evidence").innerHTML = evidence.length ? evidence.map((item) => `
      <tr>
        <td><a href="${escapeHtml(item.url)}" rel="noreferrer nofollow" target="_blank">${escapeHtml(item.url)}</a>
          <div class="small muted">${escapeHtml(item.source)} • ${escapeHtml(item.kind || "")}</div></td>
        <td class="small">${escapeHtml((item.snippet || "").slice(0, 180))}</td>
        <td>${badge(item.status || "؟")}</td>
        <td class="small muted">${escapeHtml((item.observed_at || "").slice(0, 19))}</td>
      </tr>`).join("") : `<tr><td colspan="4" class="muted">شاهدی ثبت نشده است</td></tr>`;

    const history = row.validation_history || [];
    $("#detail-validations").innerHTML = history.length ? history.map((item) => `
      <tr><td class="small">${escapeHtml((item.checked_at || "").slice(0, 19))}</td>
      <td>${badge(item.status || "—", statusKind(item.status))}</td>
      <td>${badge(item.band || "—", bandKind(item.band))}</td>
      <td>${num(item.confidence)}</td>
      <td class="small muted">${escapeHtml(item.method || "")}</td></tr>`).join("")
      : `<tr><td colspan="5" class="muted">اعتبارسنجی مجددی ثبت نشده است</td></tr>`;

    $("#detail-breakdown").textContent = JSON.stringify(row.confidence_breakdown || {}, null, 2);
    $("#detail-relevance").textContent = JSON.stringify(row.relevance_evidence || {}, null, 2);
  } catch (err) {
    toast(`خطا: ${err.message}`, true);
  }
}

/* -------------------------------- jobs -------------------------------- */
async function renderJobs() {
  try {
    const data = await api("/api/v1/jobs?limit=50");
    const rows = data.items || [];
    $("#job-rows").innerHTML = rows.length ? rows.map((j) => `
      <tr id="job-${j.id}">
        <td>${num(j.id)}</td>
        <td>${escapeHtml(j.name)}<div class="small muted">${escapeHtml(j.message || "").slice(0, 90)}</div></td>
        <td>${badge(j.status, statusKind(j.status))}</td>
        <td>${num(j.progress)}%${bar(j.progress, 100)}</td>
        <td>${num(j.businesses_new)}</td>
        <td>${num(j.phones_new)}</td>
        <td>${num(j.errors)}</td>
        <td class="small muted">${escapeHtml((j.finished_at || j.started_at || "").slice(0, 19))}</td>
      </tr>`).join("") : `<tr><td colspan="8" class="muted">Jobی ثبت نشده است</td></tr>`;
  } catch (err) {
    toast(`خطا در فهرست Jobها: ${err.message}`, true);
  }
}

async function createJob(form) {
  const payload = {
    name: $("#job-name").value.trim() || `کشف ${new Date().toISOString().slice(0, 16)}`,
    provinces: splitList($("#job-provinces").value),
    cities: splitList($("#job-cities").value),
    categories: splitList($("#job-categories").value),
    business_types: splitList($("#job-business-types").value),
    sources: splitList($("#job-sources").value),
    target: Number($("#job-target").value || 500),
    min_confidence: Number($("#job-min-confidence").value || 45),
    workers: Number($("#job-workers").value || 6),
    max_pages_per_query: Number($("#job-pages").value || 2),
    time_limit: Number($("#job-time-limit").value || 900),
    run_dedup: true,
  };
  try {
    const res = await fetch("/api/v1/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const job = await res.json();
    toast(`Job ${job.id} ساخته شد. برای اجرا: numberbank run-job ${job.id}`);
    form.reset();
    renderJobs();
  } catch (err) {
    toast(`خطا در ساخت Job: ${err.message}`, true);
  }
}

async function cancelJob(id) {
  try {
    await fetch(`/api/v1/jobs/${id}/cancel`, { method: "POST" });
    toast(`درخواست توقف Job ${id} ثبت شد`);
    renderJobs();
  } catch (err) {
    toast(`خطا: ${err.message}`, true);
  }
}

function splitList(value) {
  return String(value || "").split(/[,،\n]/).map((s) => s.trim()).filter(Boolean);
}

/* ------------------------------ coverage ------------------------------ */
async function renderCoverage() {
  try {
    const data = await api("/api/v1/coverage");
    const rows = (data.by_province || []).filter((r) => r.businesses || r.cities_with_data);
    const max = Math.max(1, ...rows.map((r) => r.cities_with_data || 0));
    $("#coverage-rows").innerHTML = rows.length ? rows.map((r) => `
      <tr>
        <td>${escapeHtml(r.province)}</td>
        <td>${num(r.counties)}</td>
        <td>${num(r.cities)}</td>
        <td>${num(r.cities_with_data)}</td>
        <td>${num(r.businesses)}</td>
        <td style="min-width:140px">${bar(r.cities_with_data || 0, max)}</td>
      </tr>`).join("") : `<tr><td colspan="6" class="muted">دادهای موجود نیست</td></tr>`;
    $("#coverage-summary").textContent =
      `شهرهای دارای داده: ${num(data.cities_with_data)} از ${num(data.total_cities)} | ` +
      `شهرهای بررسی‌شده: ${num(data.queried_cities)} | پوشش بررسی: ${data.coverage_percent}%`;
  } catch (err) {
    toast(`خطا در پوشش: ${err.message}`, true);
  }
}

/* ------------------------------- sources ------------------------------- */
async function renderSources() {
  try {
    const data = await api("/api/v1/sources");
    const rows = data.items || [];
    $("#source-rows").innerHTML = rows.length ? rows.map((s) => `
      <tr>
        <td>${escapeHtml(s.name)}<div class="small muted mono">${escapeHtml(s.key)}</div></td>
        <td>${badge(s.kind)}</td>
        <td>${badge(s.enabled ? "فعال" : "غیرفعال", s.enabled ? "ok" : "warn")}</td>
        <td>${num(s.reliability)}</td>
        <td>${num(s.observations)}</td>
        <td class="small muted">${escapeHtml(s.note || "")}</td>
      </tr>`).join("") : `<tr><td colspan="6" class="muted">منبعی ثبت نشده است</td></tr>`;
  } catch (err) {
    toast(`خطا در منابع: ${err.message}`, true);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const page = document.body.dataset.page;
  if (page === "dashboard") renderDashboard();
  if (page === "businesses") { wireBusinessFilters(); renderBusinesses(); }
  if (page === "business-detail") renderBusinessDetail(document.body.dataset.businessId);
  if (page === "jobs") {
    renderJobs();
    $("#job-form")?.addEventListener("submit", (ev) => { ev.preventDefault(); createJob(ev.target); });
    document.addEventListener("click", (ev) => {
      const target = ev.target.closest("[data-cancel-job]");
      if (target) cancelJob(target.dataset.cancelJob);
    });
  }
  if (page === "coverage") renderCoverage();
  if (page === "sources") renderSources();
  window.addEventListener("error", (ev) => toast(`خطای اجرا: ${ev.message}`, true));
});
