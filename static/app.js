/* 录入页逻辑：点/观测录入、平差与状态机、网形检查面板。
   网形检查结论由后端规则模块计算并快照，本文件只负责请求与渲染。 */
const $ = (id) => document.getElementById(id);
const state = { epochId: null, report: null, status: null };

function headers() {
  return {
    "Content-Type": "application/json",
    "X-User": $("user").value || "alice",
    "X-Role": $("role").value || "editor",
  };
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: headers(),
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `请求失败 (${res.status})`);
    err.status = res.status;
    err.payload = data;
    throw err;
  }
  return data;
}

function toast(message, kind = "") {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast show " + kind;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => (el.className = "toast"), 3600);
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function kindLabel(kind) {
  return { distance: "边长", angle: "角度", height_difference: "高差" }[kind] || kind;
}

/* ---------- 期次 ---------- */
async function loadEpoch(showToast = false) {
  const id = Number($("epoch").value);
  if (!id) return;
  state.epochId = id;
  try {
    const epoch = await api(`/api/epochs/${id}`);
    state.status = epoch.status;
    $("status").innerHTML = `状态：<b>${esc(epoch.status)}</b>`;
    await Promise.all([refreshPoints(), refreshObservations(), refreshCheck(false)]);
    if (showToast) toast(`已读取期次 ${id}`, "ok");
  } catch (err) {
    state.status = null;
    $("status").innerHTML = `<span class="pill err">${esc(err.message)}</span>`;
    toast(err.message, "error");
  }
}

/* ---------- 点数据 ---------- */
async function savePoint() {
  const payload = {
    point: $("p-name").value.trim(),
    x: $("p-x").value === "" ? null : Number($("p-x").value),
    y: $("p-y").value === "" ? null : Number($("p-y").value),
    elevation: $("p-h").value === "" ? null : Number($("p-h").value),
    x_known: $("p-xk").checked,
    y_known: $("p-yk").checked,
    elevation_known: $("p-hk").checked,
  };
  if (!payload.point) return toast("点号不能为空", "error");
  try {
    await api(`/api/epochs/${state.epochId}/points`, { method: "POST", body: JSON.stringify(payload) });
    toast(`点 ${payload.point} 已保存`, "ok");
    await refreshPoints();
    await refreshCheck(true);
  } catch (err) {
    toast(err.message, "error");
  }
}

async function refreshPoints() {
  const { points } = await api(`/api/epochs/${state.epochId}/points`);
  const rows = points
    .map((p) => {
      const flags = [p.x_known ? "X已知" : null, p.y_known ? "Y已知" : null, p.elevation_known ? "H已知" : null]
        .filter(Boolean)
        .join("，");
      return `<tr><td>${esc(p.point)}</td><td>${p.x ?? "—"}</td><td>${p.y ?? "—"}</td><td>${p.elevation ?? "—"}</td><td>${esc(flags) || "待求"}</td></tr>`;
    })
    .join("");
  $("point-table").innerHTML = `<tr><th>点号</th><th>X</th><th>Y</th><th>高程 H</th><th>约束</th></tr>${rows}`;
}

/* ---------- 观测 ---------- */
function onKindChange() {
  $("o-p3").style.display = $("o-kind").value === "angle" ? "" : "none";
}

async function addObservation() {
  const kind = $("o-kind").value;
  const payload = {
    kind,
    p1: $("o-p1").value.trim(),
    p2: $("o-p2").value.trim(),
    p3: kind === "angle" ? $("o-p3").value.trim() : null,
    value: Number($("o-value").value),
    weight: Number($("o-weight").value || 1),
  };
  if (!payload.p1 || !payload.p2 || (kind === "angle" && !payload.p3)) {
    return toast("观测端点不完整（角度需要测站、两个照准点）", "error");
  }
  try {
    await api(`/api/epochs/${state.epochId}/observations`, { method: "POST", body: JSON.stringify(payload) });
    toast(`已添加${kindLabel(kind)}观测`, "ok");
    await refreshObservations();
    await refreshCheck(true);
  } catch (err) {
    if (err.status === 409) {
      if (confirm("发现疑似重复观测，仍要保留吗？")) {
        try {
          await api(`/api/epochs/${state.epochId}/observations`, {
            method: "POST",
            body: JSON.stringify({ ...payload, allow_duplicate: true }),
          });
          await refreshObservations();
          await refreshCheck(true);
        } catch (retryErr) {
          toast(retryErr.message, "error");
        }
      }
      return;
    }
    toast(err.message, "error");
  }
}

async function refreshObservations() {
  const { observations } = await api(`/api/epochs/${state.epochId}/observations`);
  const rows = observations
    .map((o) => {
      const endpoints = o.kind === "angle" ? `${esc(o.p1)} 站，${esc(o.p2)}→${esc(o.p3)}` : `${esc(o.p1)} — ${esc(o.p2)}`;
      const mark = o.status === "outlier" ? ' <span class="pill err">异常</span>' : "";
      return `<tr><td>${kindLabel(o.kind)}</td><td>${endpoints}</td><td>${o.value}</td><td>${o.weight}</td>${mark ? `<td>${mark}</td>` : "<td></td>"}</tr>`;
    })
    .join("");
  $("obs-table").innerHTML = `<tr><th>类型</th><th>端点/方向</th><th>观测值</th><th>权</th><th>状态</th></tr>${rows}`;
}

/* ---------- 网形检查 ---------- */
async function refreshCheck(persist) {
  if (!state.epochId) return;
  try {
    const path = `/api/epochs/${state.epochId}/network-check`;
    state.report = persist ? await api(path, { method: "POST", body: "{}" }) : await api(path);
    renderCheck();
  } catch (err) {
    toast("网形检查失败：" + err.message, "error");
  }
}

function renderGroup(network, group) {
  const known = group.known_points.length ? `已知: ${group.known_points.join(", ")}` : "无已知控制点";
  const edgeWord = network === "planar" ? "连接" : "高差段";
  const note = group.note ? `｜${group.note}` : "";
  return `<div class="group-row ${group.status === "error" ? "error" : group.status === "info" ? "info" : ""}">
    <span class="pill ${group.status === "ok" ? "ok" : group.status === "error" ? "err" : "idle"}">
      ${group.isolated ? "孤立点" : group.status === "ok" ? "可解" : "待补"}</span>
    <b>${group.points.join(", ")}</b>
    <span class="tag">${group.points.length} 点 · ${group.edge_count} ${edgeWord} · ${esc(known)}${esc(note)}</span>
  </div>`;
}

function renderIssue(issue) {
  const fix = issue.suggestion ? `<p class="fix"><b>还缺：</b>${esc(issue.suggestion)}</p>` : "";
  return `<div class="issue ${issue.severity}">
    <b>${esc(issue.message)}</b><span class="code">${esc(issue.network)}/${esc(issue.code)}</span>${fix}
  </div>`;
}

function renderCheck() {
  const report = state.report;
  const solvable = report.solvable;
  $("check-banner").className = "banner " + (solvable ? "ok" : "err");
  const errors = report.issues.filter((i) => i.severity === "error").length;
  const infos = report.issues.length - errors;
  $("check-banner").innerHTML = solvable
    ? `✅ 网形可解：平面网 ${report.planar.groups.length} 个连通组、高程网 ${report.height.groups.length} 个连通组，无阻断问题，可以提交复核。`
    : `⛔ 网形不可解：共 ${errors} 个阻断问题${infos ? `、${infos} 个提示` : ""}。按下列建议补连接或补基准后才能提交复核。`;
  $("planar-groups").innerHTML = report.planar.groups.map((g) => renderGroup("planar", g)).join("") || '<p class="muted">暂无点数据</p>';
  $("height-groups").innerHTML = report.height.groups.map((g) => renderGroup("height", g)).join("") || '<p class="muted">暂无点数据</p>';
  $("check-issues").innerHTML = report.issues.length
    ? report.issues.map(renderIssue).join("")
    : '<div class="issue info"><b>没有发现网形问题。</b></div>';
  $("check-time").textContent = report.last_saved_at
    ? `最近保存的检查结论：${report.last_saved_at}（${report.snapshot_solvable ? "当时可解" : "当时不可解"}）；上方为按当前点/观测实时重算的结果`
    : "尚未保存检查结论（执行检查或提交复核时会保存，重开页面仍可见）";
  $("btn-submit").disabled = !solvable || state.status !== "draft";
  $("submit-hint").textContent = state.status !== "draft"
    ? `期次处于 ${state.status} 状态，不能提交`
    : solvable
      ? "检查通过，可以提交复核"
      : "网形检查未通过，提交复核已锁定";
}

/* ---------- 平差与审核 ---------- */
async function adjust() {
  try {
    const result = await api(`/api/epochs/${state.epochId}/adjust`, { method: "POST", body: "{}" });
    toast(`平差完成：迭代 ${result.iterations} 次，残差 RMS=${result.residual_rms.toFixed(4)}`, "ok");
    await refreshCheck(true);
    await loadEpoch();
  } catch (err) {
    toast(err.message, "error");
    if (err.status === 422) refreshCheck(true);
  }
}

async function transition(action) {
  try {
    const epoch = await api(`/api/epochs/${state.epochId}/transition/${action}`, { method: "POST", body: "{}" });
    state.status = epoch.status;
    $("status").innerHTML = `状态：<b>${esc(epoch.status)}</b>`;
    toast(`已执行 ${action}：${epoch.status}`, "ok");
    await refreshCheck(false);
  } catch (err) {
    toast(err.message, "error");
    if (err.status === 422) refreshCheck(true);
  }
}

async function loadResults() {
  try {
    const { results } = await api(`/api/epochs/${state.epochId}/results`);
    $("result-box").textContent = JSON.stringify(results, null, 2);
  } catch (err) {
    toast(err.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  onKindChange();
  $("o-kind").addEventListener("change", onKindChange);
  loadEpoch();
});
