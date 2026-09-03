const state = {
  streams: [],
  metrics: {},
  settings: {},
  events: [],
  filter: "all",
  platform: "all",
  query: "",
  busy: false,
  editingStreamId: null,
  authenticated: false,
  view: "home",
  streamPage: 1,
  eventPage: 1,
  pageSize: 20,
  streamPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
  eventPagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
  searchTimer: null,
  pendingConfirmation: null,
};

const $ = (selector) => document.querySelector(selector);
const platformLabels = { bilibili: "Bilibili", huya: "虎牙", douyin: "抖音" };
const platformMarks = { bilibili: "B", huya: "虎", douyin: "抖" };
const currentPath = window.location.pathname;
const appBasePath = currentPath === "/liveradar" || currentPath.startsWith("/liveradar/")
  ? "/liveradar"
  : "";

function appUrl(path) {
  return `${appBasePath}${path}`;
}

async function requestJSON(url, options = {}) {
  const response = await fetch(appUrl(url), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && !url.endsWith("/api/auth/login")) {
    showAuth();
  }
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "尚未检查";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDateTime(value) {
  if (!value) return "尚未开播";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const pad = (number) => String(number).padStart(2, "0");
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  ].join(" ");
}

function formatDurationSeconds(value) {
  const totalSeconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (number) => String(number).padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function updateNextScanCountdown() {
  const countdown = $("#nextScanCountdown");
  const hint = $("#nextScanHint");
  if (!countdown || !hint) return;

  const nextCheckAt = Date.parse(state.metrics?.next_check_at || "");
  if (Number.isNaN(nextCheckAt)) {
    countdown.textContent = "正在检测";
    countdown.dataset.state = "checking";
    hint.textContent = "等待下一轮检测计划";
    return;
  }

  const remainingSeconds = Math.ceil((nextCheckAt - Date.now()) / 1000);
  if (remainingSeconds <= 0) {
    countdown.textContent = "正在检测";
    countdown.dataset.state = "checking";
    hint.textContent = "本轮检测进行中";
    return;
  }

  countdown.textContent = formatDurationSeconds(remainingSeconds);
  countdown.dataset.state = "waiting";
  hint.textContent = `间隔 ${state.settings.monitor_interval_seconds ?? 60} 秒`;
}

function streamHasLiveSession(stream) {
  return stream.status === "live" || Boolean(Number(stream.live_session_active));
}

function streamDurationSeconds(stream) {
  const storedDuration = Math.max(
    0,
    Math.floor(Number(stream.live_duration_seconds) || 0),
  );
  if (!stream.live_started_at || !streamHasLiveSession(stream)) {
    return storedDuration;
  }
  const startedAt = Date.parse(stream.live_started_at);
  if (Number.isNaN(startedAt)) {
    return storedDuration;
  }
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
}

function formatStreamDuration(stream) {
  if (!stream.live_started_at && !Number(stream.live_duration_seconds)) {
    return "—";
  }
  return formatDurationSeconds(streamDurationSeconds(stream));
}

function updateLiveDurations() {
  document.querySelectorAll(".duration-cell[data-live-active='true']").forEach(
    (cell) => {
      const startedAt = Date.parse(cell.dataset.liveStartedAt || "");
      if (Number.isNaN(startedAt)) return;
      cell.textContent = formatDurationSeconds(
        (Date.now() - startedAt) / 1000,
      );
    },
  );
}

function statusMeta(stream) {
  if (!stream.enabled) return { key: "disabled", label: "已停用", detail: "手动停用" };
  if (stream.status === "live") return { key: "live", label: "直播中", detail: "正在直播" };
  if (stream.status === "replay") return { key: "replay", label: "回放", detail: "平台回放状态" };
  if (stream.status === "error") {
    return { key: "error", label: "检查失败", detail: stream.error_message || "接口暂时不可用" };
  }
  if (stream.status === "offline") return { key: "offline", label: "未开播", detail: "当前没有直播" };
  return { key: "unknown", label: "待检查", detail: "等待第一次检查" };
}

function renderMetrics() {
  const metrics = state.metrics || {};
  $("#metricTotal").textContent = metrics.total ?? 0;
  $("#metricEnabled").textContent = `${metrics.enabled ?? 0} 个已启用`;
  $("#metricLive").textContent = metrics.live ?? 0;
  $("#metricOffline").textContent = metrics.offline ?? 0;
  $("#metricErrors").textContent = (metrics.errors ?? 0) + (metrics.unknown ?? 0);
  $("#lastScan").textContent = metrics.last_checked_at
    ? `上次 ${formatTime(metrics.last_checked_at)}`
    : "尚未检查";
  $("#scanInterval").textContent = `每 ${state.settings.monitor_interval_seconds ?? 60} 秒`;
  updateNextScanCountdown();
}

function normalizeView(value) {
  return ["home", "rooms", "notifications"].includes(value) ? value : "home";
}

function switchView(view, updateHash = true) {
  state.view = normalizeView(view);
  document.querySelectorAll("[data-view-tab]").forEach((tab) => {
    const active = tab.dataset.viewTab === state.view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-view-section]").forEach((section) => {
    section.classList.toggle("is-hidden", section.dataset.viewSection !== state.view);
  });
  if (updateHash && window.location.hash !== `#${state.view}`) {
    window.history.replaceState(null, "", `#${state.view}`);
  }
}

function streamOpenUrl(stream) {
  return stream.room_url;
}

function streamOpenLabel(stream) {
  return "打开";
}

function streamRenderSignature(stream) {
  return JSON.stringify([
    stream.id,
    stream.platform,
    stream.room_key,
    stream.room_url,
    stream.display_name,
    stream.anchor_name,
    stream.enabled,
    stream.status,
    stream.error_message,
    stream.live_started_at,
    stream.live_duration_seconds,
    stream.last_checked_at,
    stream.title,
  ]);
}

function streamRowMarkup(stream) {
  const meta = statusMeta(stream);
  const platform = Object.hasOwn(platformLabels, stream.platform)
    ? stream.platform
    : "bilibili";
  const title = stream.title || "暂无直播标题";
  const name = stream.display_name || stream.anchor_name || `${platformLabels[stream.platform]} 房间`;
  const detail = stream.anchor_name && stream.display_name
    ? stream.anchor_name
    : `房间 ID ${stream.room_key}`;
  const openUrl = streamOpenUrl(stream);
  const openLabel = streamOpenLabel(stream);
  const liveStartedAt = stream.live_started_at || "";
  const liveSessionActive = streamHasLiveSession(stream);
  const hasError = meta.key === "error";
  return `
    <td data-stream-field="room">
      <div class="room-cell">
        <strong data-stream-field="name">${escapeHtml(name)}</strong>
        <span data-stream-field="detail">${escapeHtml(detail)}</span>
        <span class="row-error${hasError ? "" : " is-hidden"}" data-stream-field="error">${escapeHtml(meta.detail)}</span>
      </div>
    </td>
    <td data-stream-field="platform">
      <span class="platform-badge platform-${platform}" data-stream-field="platform-badge">
        <span class="platform-icon platform-icon-${platform}" data-stream-field="platform-icon" aria-hidden="true">${platformMarks[platform]}</span>
        <span data-stream-field="platform-label">${escapeHtml(platformLabels[platform])}</span>
      </span>
    </td>
    <td data-stream-field="status">
      <span class="status-badge status-${meta.key}" data-stream-field="status-badge">
        <span class="status-dot" data-stream-field="status-dot"></span><span data-stream-field="status-label">${escapeHtml(meta.label)}</span>
      </span>
    </td>
    <td class="start-time-cell" data-stream-field="start-time">${escapeHtml(formatDateTime(liveStartedAt))}</td>
    <td
      class="duration-cell"
      data-stream-field="duration"
      data-live-started-at="${escapeHtml(liveStartedAt)}"
      data-live-active="${liveSessionActive}"
    >${escapeHtml(formatStreamDuration(stream))}</td>
    <td class="time-cell" data-stream-field="checked-at">${escapeHtml(formatTime(stream.last_checked_at))}</td>
    <td class="title-cell" data-stream-field="title" title="${escapeHtml(title)}">${escapeHtml(title)}</td>
    <td>
      <div class="row-actions">
        <a class="table-action" data-stream-field="open-link" href="${escapeHtml(openUrl)}" target="_blank" rel="noreferrer">${openLabel}</a>
        <button class="table-action" data-action="edit-stream" data-id="${stream.id}">编辑</button>
        <button class="table-action" data-action="check-stream" data-id="${stream.id}">检查</button>
        <button class="table-action" data-stream-field="toggle" data-action="toggle-stream" data-id="${stream.id}">${stream.enabled ? "停用" : "启用"}</button>
        <button class="table-action action-danger" data-action="delete-stream" data-id="${stream.id}">删除</button>
      </div>
    </td>
  `;
}

function updateStreamRow(row, stream) {
  const meta = statusMeta(stream);
  const platform = Object.hasOwn(platformLabels, stream.platform)
    ? stream.platform
    : "bilibili";
  const title = stream.title || "暂无直播标题";
  const name = stream.display_name || stream.anchor_name || `${platformLabels[stream.platform]} 房间`;
  const detail = stream.anchor_name && stream.display_name
    ? stream.anchor_name
    : `房间 ID ${stream.room_key}`;
  const openUrl = streamOpenUrl(stream);
  const openLabel = streamOpenLabel(stream);
  const liveStartedAt = stream.live_started_at || "";
  const liveSessionActive = streamHasLiveSession(stream);
  const field = (name) => row.querySelector(`[data-stream-field="${name}"]`);

  field("name").textContent = name;
  field("detail").textContent = detail;
  const error = field("error");
  error.textContent = meta.detail;
  error.classList.toggle("is-hidden", meta.key !== "error");

  const platformBadge = field("platform-badge");
  platformBadge.className = `platform-badge platform-${platform}`;
  field("platform-icon").className = `platform-icon platform-icon-${platform}`;
  field("platform-icon").textContent = platformMarks[platform];
  field("platform-label").textContent = platformLabels[platform];

  const statusBadge = field("status-badge");
  statusBadge.className = `status-badge status-${meta.key}`;
  field("status-dot").className = `status-dot`;
  field("status-label").textContent = meta.label;

  field("start-time").textContent = formatDateTime(liveStartedAt);
  const duration = field("duration");
  duration.dataset.liveStartedAt = liveStartedAt;
  duration.dataset.liveActive = String(liveSessionActive);
  duration.textContent = formatStreamDuration(stream);
  field("checked-at").textContent = formatTime(stream.last_checked_at);
  const titleCell = field("title");
  titleCell.textContent = title;
  titleCell.title = title;
  const openLink = field("open-link");
  openLink.href = openUrl;
  openLink.textContent = openLabel;
  field("toggle").textContent = stream.enabled ? "停用" : "启用";
}

function renderStreams() {
  const tbody = $("#streamRows");
  const emptyState = $("#emptyState");
  const filtered = state.streams;
  const existingRows = new Map(
    Array.from(tbody.children).map((row) => [row.dataset.streamId, row]),
  );
  const renderedIds = new Set();

  filtered.forEach((stream, index) => {
    const streamId = String(stream.id);
    let row = existingRows.get(streamId);
    if (!row) {
      row = document.createElement("tr");
      row.className = "stream-row";
      row.dataset.streamId = streamId;
    }

    row.style.setProperty("--row-index", index);
    const signature = streamRenderSignature(stream);
    if (!row.dataset.renderSignature) {
      row.innerHTML = streamRowMarkup(stream);
    } else if (row.dataset.renderSignature !== signature) {
      updateStreamRow(row, stream);
    }
    row.dataset.renderSignature = signature;
    renderedIds.add(streamId);

    const currentRow = tbody.children[index];
    if (currentRow !== row) {
      tbody.insertBefore(row, currentRow || null);
    }
  });

  Array.from(tbody.children).forEach((row) => {
    if (!renderedIds.has(row.dataset.streamId)) {
      row.remove();
    }
  });

  const hasStreams = Number(state.metrics?.total || 0) > 0;
  const hasResults = filtered.length > 0;
  document.querySelector(".table-frame").classList.toggle("has-data", hasResults);
  emptyState.classList.toggle("is-hidden", hasResults);
  if (!hasStreams) {
    emptyState.querySelector("h3").textContent = "还没有添加直播间";
    emptyState.querySelector("p").textContent = "先添加一个 Bilibili、虎牙或抖音直播间，监测列表会从这里开始。";
  } else if (!hasResults) {
    emptyState.querySelector("h3").textContent = "没有匹配的直播间";
    emptyState.querySelector("p").textContent = "换一个筛选条件或搜索词。";
  }
  renderPagination("#streamPagination", state.streamPagination, "streams");
  updateLiveDurations();
}

function renderEvents() {
  const target = $("#eventList");
  updateNotificationActions();
  const signature = JSON.stringify(state.events);
  if (target.dataset.renderSignature === signature) {
    renderPagination("#eventPagination", state.eventPagination, "events");
    return;
  }
  target.dataset.renderSignature = signature;
  if (!state.events.length) {
    target.innerHTML = `<div class="event-empty">还没有通知记录</div>`;
    renderPagination("#eventPagination", state.eventPagination, "events");
    return;
  }
  target.innerHTML = state.events
    .map((event) => {
      const streamName = event.display_name || event.anchor_name || event.room_key || "直播间";
      const eventLabel = event.event_type === "started" ? "开播" : "下播";
      const stateLabel = event.delivered ? "已送达" : "发送失败";
      const statusClass = event.delivered ? "event-success" : "event-failed";
      const eventTypeClass = event.event_type === "started"
        ? "event-started"
        : "event-stopped";
      return `
        <div class="event-row">
          <span class="event-type ${eventTypeClass}">${escapeHtml(eventLabel)}</span>
          <div class="event-copy">
            <strong>${escapeHtml(streamName)}</strong>
            <span>${escapeHtml(event.message.split("\n")[0] || event.title)}</span>
          </div>
          <span class="event-result ${statusClass}">${escapeHtml(stateLabel)}</span>
          <time>${escapeHtml(formatTime(event.created_at))}</time>
        </div>
      `;
    })
    .join("");
  renderPagination("#eventPagination", state.eventPagination, "events");
}

function updateNotificationActions() {
  const button = $("#clearNotificationsButton");
  if (button) {
    button.disabled = Number(state.eventPagination?.total || 0) === 0;
  }
}

function renderPagination(selector, pagination, type) {
  const target = $(selector);
  if (!target) return;
  const total = Number(pagination?.total || 0);
  const totalPages = Math.max(1, Number(pagination?.total_pages || 0));
  const page = Math.min(
    totalPages,
    Math.max(1, Number(pagination?.page || 1)),
  );

  const previousPage = Math.max(1, page - 1);
  const nextPage = Math.min(totalPages, page + 1);
  target.classList.remove("is-hidden");
  const signature = `${type}:${page}:${totalPages}:${total}:${state.pageSize}`;
  if (target.dataset.renderSignature === signature) {
    return;
  }
  target.dataset.renderSignature = signature;
  target.innerHTML = `
    <span class="pagination-copy">第 ${page} / ${totalPages} 页 · 共 ${total} 条 · 每页 ${state.pageSize} 条</span>
    <span class="pagination-actions">
      <button class="pagination-button" data-page-action="${type}" data-page="${previousPage}" ${total === 0 || page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="pagination-button" data-page-action="${type}" data-page="${nextPage}" ${total === 0 || page >= totalPages ? "disabled" : ""}>下一页</button>
    </span>
  `;
}

async function loadDashboard(options = {}) {
  if (!state.authenticated) return;
  if (options.resetStreams) state.streamPage = 1;
  if (options.resetEvents) state.eventPage = 1;
  try {
    const streamParams = new URLSearchParams({
      page: String(state.streamPage),
      filter: state.filter,
      platform: state.platform,
      query: state.query,
    });
    const eventParams = new URLSearchParams({
      page: String(state.eventPage),
    });
    const [dashboard, events] = await Promise.all([
      requestJSON(`/api/streams?${streamParams.toString()}`),
      requestJSON(`/api/notifications?${eventParams.toString()}`),
    ]);
    state.streams = dashboard.items || [];
    state.streamPagination = dashboard.pagination || state.streamPagination;
    state.metrics = dashboard.metrics || {};
    state.settings = dashboard.settings || {};
    state.events = events.items || [];
    state.eventPagination = events.pagination || state.eventPagination;
    if (
      state.streamPagination.total_pages > 0
      && state.streamPage > state.streamPagination.total_pages
    ) {
      state.streamPage = state.streamPagination.total_pages;
      return loadDashboard();
    }
    if (
      state.eventPagination.total_pages > 0
      && state.eventPage > state.eventPagination.total_pages
    ) {
      state.eventPage = state.eventPagination.total_pages;
      return loadDashboard();
    }
    renderMetrics();
    renderStreams();
    renderEvents();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function showAuth(message = "") {
  state.authenticated = false;
  $("#appShell").classList.add("is-hidden");
  $("#authScreen").classList.remove("is-hidden");
  $("#loginError").textContent = message;
  window.setTimeout(() => $("#loginUsername").focus(), 60);
}

function showApp() {
  state.authenticated = true;
  $("#loginError").textContent = "";
  $("#authScreen").classList.add("is-hidden");
  $("#appShell").classList.remove("is-hidden");
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  $("#toastRegion").appendChild(toast);
  window.setTimeout(() => toast.remove(), 3600);
}

function openConfirmDialog({ title, message, confirmLabel, action }) {
  state.pendingConfirmation = action;
  $("#confirmDialogTitle").textContent = title;
  $("#confirmDialogMessage").textContent = message;
  $("#confirmSubmit").textContent = confirmLabel;
  $("#confirmDialog").showModal();
}

function closeConfirmDialog() {
  state.pendingConfirmation = null;
  if ($("#confirmDialog").open) {
    $("#confirmDialog").close();
  }
}

async function submitConfirmation(event) {
  event.preventDefault();
  const action = state.pendingConfirmation;
  if (!action) return;
  const submit = $("#confirmSubmit");
  setButtonBusy(submit, true, "处理中");
  try {
    await action();
    closeConfirmDialog();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(submit, false);
  }
}

function setButtonBusy(button, busy, busyText = "处理中") {
  if (!button) return;
  if (busy) {
    button.dataset.originalMarkup = button.innerHTML;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.innerHTML = button.dataset.originalMarkup || button.innerHTML;
    button.disabled = false;
  }
}

async function submitLogin(event) {
  event.preventDefault();
  const submit = $("#loginSubmit");
  const formData = new FormData(event.currentTarget);
  $("#loginError").textContent = "";
  setButtonBusy(submit, true, "正在验证");
  try {
    await requestJSON("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: formData.get("username"),
        password: formData.get("password"),
      }),
    });
    showApp();
    await loadDashboard();
  } catch (error) {
    $("#loginError").textContent = error.message || "登录失败，请检查账号和密码";
    $("#loginPassword").select();
  } finally {
    setButtonBusy(submit, false);
  }
}

async function logout() {
  try {
    await requestJSON("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    state.streams = [];
    state.metrics = {};
    state.events = [];
    showAuth("你已退出登录");
  }
}

function togglePassword(button) {
  const input = $("#loginPassword");
  const visible = input.type === "text";
  input.type = visible ? "password" : "text";
  button.textContent = visible ? "显示" : "隐藏";
  button.setAttribute("aria-label", visible ? "显示密码" : "隐藏密码");
  button.setAttribute("aria-pressed", String(!visible));
}

function openStreamDialog(stream = null) {
  $("#streamForm").reset();
  state.editingStreamId = stream ? Number(stream.id) : null;
  $("#streamDialogLabel").textContent = stream ? "EDIT" : "WATCHLIST";
  $("#streamDialogTitle").textContent = stream ? "编辑直播间" : "添加直播间";
  $("#streamSubmit").textContent = stream ? "保存并检查" : "添加并检查";
  if (stream) {
    $("#platformInput").value = stream.platform;
    $("#roomInput").value = stream.room_url;
    $("#displayNameInput").value = stream.display_name || "";
  }
  updateRoomHint();
  $("#streamDialog").showModal();
  window.setTimeout(() => $("#roomInput").focus(), 60);
}

function updateRoomHint() {
  const platform = $("#platformInput").value;
  const isHuya = platform === "huya";
  const isDouyin = platform === "douyin";
  $("#roomInputLabel").textContent = isDouyin
    ? "抖音直播间 ID"
    : "直播间链接或房间 ID";
  $("#roomInput").placeholder = isDouyin ? "例如：6096197105" : "例如：123456";
  $("#roomInputHint").textContent = isDouyin
    ? "填写 live.douyin.com/ 后面的数字直播间 ID。"
    : isHuya
      ? "支持 www.huya.com/房间ID，或直接填写房间 ID。"
      : "支持 live.bilibili.com/数字房间ID，或直接填写数字 ID。";
}

async function submitStream(event) {
  event.preventDefault();
  const submit = $("#streamSubmit");
  const formData = new FormData(event.currentTarget);
  const editing = state.editingStreamId !== null;
  const endpoint = editing ? `/api/streams/${state.editingStreamId}` : "/api/streams";
  setButtonBusy(submit, true, "正在检查");
  try {
    await requestJSON(endpoint, {
      method: editing ? "PATCH" : "POST",
      body: JSON.stringify({
        platform: formData.get("platform"),
        room_url: formData.get("room_url"),
        display_name: formData.get("display_name"),
      }),
    });
    $("#streamDialog").close();
    state.editingStreamId = null;
    showToast(editing ? "直播间已更新并完成检查" : "直播间已添加并完成首次检查");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(submit, false);
  }
}

async function checkStream(id, button) {
  setButtonBusy(button, true, "检查中");
  try {
    await requestJSON(`/api/streams/${id}/check`, { method: "POST", body: "{}" });
    showToast("检查完成");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function checkAll(button) {
  setButtonBusy(button, true, "检查中");
  try {
    await requestJSON("/api/check-all", { method: "POST", body: "{}" });
    showToast("全部直播间检查完成");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function toggleStream(id, button) {
  const stream = state.streams.find((item) => item.id === Number(id));
  if (!stream) return;
  setButtonBusy(button, true, "保存中");
  try {
    await requestJSON(`/api/streams/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !stream.enabled }),
    });
    showToast(stream.enabled ? "已停用监测" : "已启用监测");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function deleteStream(id, button) {
  const stream = state.streams.find((item) => item.id === Number(id));
  if (!stream) return;
  const name = stream.display_name || stream.anchor_name || `${platformLabels[stream.platform]} 房间`;
  openConfirmDialog({
    title: "删除直播间",
    message: `确定删除“${name}”吗？删除后不会再监测此直播间，且无法恢复。`,
    confirmLabel: "删除直播间",
    action: () => performDeleteStream(id, button),
  });
}

async function performDeleteStream(id, button) {
  setButtonBusy(button, true, "删除中");
  try {
    await requestJSON(`/api/streams/${id}`, { method: "DELETE" });
    showToast("直播间已删除");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function requestClearNotifications() {
  const total = Number(state.eventPagination?.total || 0);
  openConfirmDialog({
    title: "清空通知记录",
    message: `确定清空全部 ${total} 条通知记录吗？清空后无法恢复。`,
    confirmLabel: "清空记录",
    action: clearNotifications,
  });
}

async function clearNotifications() {
  const payload = await requestJSON("/api/notifications", { method: "DELETE" });
  state.eventPage = 1;
  showToast(`已清空 ${payload.deleted || 0} 条通知记录`);
  await loadDashboard({ resetEvents: true });
}

async function openSettingsDialog() {
  try {
    const payload = await requestJSON("/api/settings");
    state.settings = payload.settings || {};
    const settings = state.settings;
    $("#intervalInput").value = settings.monitor_interval_seconds ?? 60;
    $("#providerInput").value = settings.notify_provider ?? "none";
    $("#serverchanInput").value = "";
    $("#wecomInput").value = "";
    $("#wxpusherSptInput").value = "";
    $("#serverchanState").textContent = settings.serverchan_sendkey_set
      ? `当前：${settings.serverchan_sendkey_masked}`
      : "尚未设置";
    $("#wecomState").textContent = settings.wecom_webhook_set
      ? `当前：${settings.wecom_webhook_masked}`
      : "尚未设置";
    $("#wxpusherSptState").textContent = settings.wxpusher_spt_set
      ? `当前已设置 ${settings.wxpusher_spt_count || 1} 个 SPT`
      : "填写以 SPT_ 开头的个人推送令牌。";
    $("#notifyStartInput").checked = settings.notify_on_start !== false;
    $("#notifyStopInput").checked = settings.notify_on_stop === true;
    updateProviderFields();
    $("#settingsDialog").showModal();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function updateProviderFields() {
  const provider = $("#providerInput").value;
  $("#serverchanFields").classList.toggle("is-hidden", provider !== "serverchan");
  $("#wecomFields").classList.toggle("is-hidden", provider !== "wecom");
  $("#wxpusherFields").classList.toggle("is-hidden", provider !== "wxpusher");
}

async function submitSettings(event) {
  event.preventDefault();
  const submit = $("#settingsSubmit");
  setButtonBusy(submit, true, "保存中");
  try {
    await requestJSON("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        monitor_interval_seconds: Number($("#intervalInput").value),
        notify_provider: $("#providerInput").value,
        serverchan_sendkey: $("#serverchanInput").value,
        wecom_webhook: $("#wecomInput").value,
        wxpusher_spt: $("#wxpusherSptInput").value,
        notify_on_start: $("#notifyStartInput").checked,
        notify_on_stop: $("#notifyStopInput").checked,
      }),
    });
    $("#settingsDialog").close();
    showToast("通知设置已保存");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(submit, false);
  }
}

async function testNotification(button) {
  setButtonBusy(button, true, "发送中");
  try {
    await requestJSON("/api/notifications/test", { method: "POST", body: "{}" });
    showToast("测试通知已发送");
    await loadDashboard();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

document.addEventListener("click", (event) => {
  const actionTarget = event.target.closest("[data-action]");
  if (actionTarget) {
    const action = actionTarget.dataset.action;
    if (action === "add-stream") openStreamDialog();
    if (action === "edit-stream") {
      const stream = state.streams.find((item) => item.id === Number(actionTarget.dataset.id));
      if (stream) openStreamDialog(stream);
    }
    if (action === "settings") openSettingsDialog();
    if (action === "logout") logout();
    if (action === "toggle-password") togglePassword(actionTarget);
    if (action === "close-stream-dialog") $("#streamDialog").close();
    if (action === "close-settings-dialog") $("#settingsDialog").close();
    if (action === "close-confirm-dialog") closeConfirmDialog();
    if (action === "check-all") checkAll(actionTarget);
    if (action === "check-stream") checkStream(actionTarget.dataset.id, actionTarget);
    if (action === "toggle-stream") toggleStream(actionTarget.dataset.id, actionTarget);
    if (action === "delete-stream") deleteStream(actionTarget.dataset.id, actionTarget);
    if (action === "clear-notifications") requestClearNotifications();
    if (action === "test-notification") testNotification(actionTarget);
  }

  const viewTarget = event.target.closest("[data-view-tab]");
  if (viewTarget) {
    switchView(viewTarget.dataset.viewTab);
  }

  const pageTarget = event.target.closest("[data-page-action]");
  if (pageTarget && !pageTarget.disabled) {
    const page = Number(pageTarget.dataset.page);
    if (pageTarget.dataset.pageAction === "streams") {
      state.streamPage = page;
    } else {
      state.eventPage = page;
    }
    loadDashboard();
  }

  const filterTarget = event.target.closest("[data-filter]");
  if (filterTarget) {
    state.filter = filterTarget.dataset.filter;
    state.streamPage = 1;
    document.querySelectorAll("[data-filter]").forEach((button) => {
      button.classList.toggle("is-active", button === filterTarget);
    });
    loadDashboard();
  }

});

$("#streamForm").addEventListener("submit", submitStream);
$("#settingsForm").addEventListener("submit", submitSettings);
$("#confirmForm").addEventListener("submit", submitConfirmation);
$("#confirmDialog").addEventListener("close", () => {
  state.pendingConfirmation = null;
});
$("#loginForm").addEventListener("submit", submitLogin);
$("#platformInput").addEventListener("change", updateRoomHint);
$("#providerInput").addEventListener("change", updateProviderFields);
$("#platformFilter").addEventListener("change", (event) => {
  state.platform = event.target.value;
  state.streamPage = 1;
  loadDashboard();
});
$("#searchInput").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  state.streamPage = 1;
  window.clearTimeout(state.searchTimer);
  state.searchTimer = window.setTimeout(() => loadDashboard(), 260);
});

window.addEventListener("hashchange", () => {
  switchView(window.location.hash.slice(1), false);
});

async function initialize() {
  try {
    const auth = await requestJSON("/api/auth/me");
    if (auth.authenticated) {
      showApp();
      switchView(window.location.hash.slice(1), false);
      await loadDashboard();
    } else {
      showAuth();
    }
  } catch (error) {
    showAuth("暂时无法连接服务器，请稍后重试");
  }
}

initialize();
switchView(window.location.hash.slice(1), false);
window.setInterval(loadDashboard, 30000);
window.setInterval(updateLiveDurations, 1000);
window.setInterval(updateNextScanCountdown, 1000);
