const state = {
  streams: [],
  metrics: {},
  settings: {},
  events: [],
  filter: "all",
  query: "",
  busy: false,
  editingStreamId: null,
};

const $ = (selector) => document.querySelector(selector);
const platformLabels = { bilibili: "Bilibili", huya: "虎牙", douyin: "抖音" };
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
}

function streamMatchesFilter(stream) {
  const meta = statusMeta(stream);
  if (state.filter === "live") return meta.key === "live";
  if (state.filter === "offline") return meta.key === "offline" || meta.key === "replay";
  if (state.filter === "attention") return ["error", "unknown", "disabled"].includes(meta.key);
  return true;
}

function streamMatchesQuery(stream) {
  if (!state.query) return true;
  const haystack = [
    stream.display_name,
    stream.anchor_name,
    stream.title,
    stream.room_key,
    platformLabels[stream.platform],
  ].join(" ").toLowerCase();
  return haystack.includes(state.query.toLowerCase());
}

function renderStreams() {
  const tbody = $("#streamRows");
  const emptyState = $("#emptyState");
  const filtered = state.streams.filter(
    (stream) => streamMatchesFilter(stream) && streamMatchesQuery(stream),
  );
  tbody.innerHTML = filtered
    .map((stream, index) => {
      const meta = statusMeta(stream);
      const platform = Object.hasOwn(platformLabels, stream.platform)
        ? stream.platform
        : "bilibili";
      const title = stream.title || "暂无直播标题";
      const name = stream.display_name || stream.anchor_name || `${platformLabels[stream.platform]} 房间`;
      const detail = stream.anchor_name && stream.display_name
        ? stream.anchor_name
        : `房间 ID ${stream.room_key}`;
      const error = meta.key === "error"
        ? `<span class="row-error">${escapeHtml(meta.detail)}</span>`
        : "";
      return `
        <tr class="stream-row" style="--row-index: ${index}">
          <td>
            <div class="room-cell">
              <strong>${escapeHtml(name)}</strong>
              <span>${escapeHtml(detail)}</span>
              ${error}
            </div>
          </td>
          <td>
            <span class="platform-badge platform-${platform}">${escapeHtml(platformLabels[stream.platform])}</span>
          </td>
          <td>
            <span class="status-badge status-${meta.key}">
              <span class="status-dot"></span>${escapeHtml(meta.label)}
            </span>
          </td>
          <td class="time-cell">${escapeHtml(formatTime(stream.last_checked_at))}</td>
          <td class="title-cell" title="${escapeHtml(title)}">${escapeHtml(title)}</td>
          <td>
            <div class="row-actions">
              <a class="table-action" href="${escapeHtml(stream.room_url)}" target="_blank" rel="noreferrer">打开</a>
              <button class="table-action" data-action="edit-stream" data-id="${stream.id}">编辑</button>
              <button class="table-action" data-action="check-stream" data-id="${stream.id}">检查</button>
              <button class="table-action" data-action="toggle-stream" data-id="${stream.id}">${stream.enabled ? "停用" : "启用"}</button>
              <button class="table-action action-danger" data-action="delete-stream" data-id="${stream.id}">删除</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  const hasStreams = state.streams.length > 0;
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
}

function renderEvents() {
  const target = $("#eventList");
  if (!state.events.length) {
    target.innerHTML = `<div class="event-empty">还没有通知记录</div>`;
    return;
  }
  target.innerHTML = state.events
    .map((event) => {
      const streamName = event.display_name || event.anchor_name || event.room_key || "直播间";
      const eventLabel = event.event_type === "started" ? "开播" : "下播";
      const stateLabel = event.delivered ? "已送达" : "发送失败";
      const statusClass = event.delivered ? "event-success" : "event-failed";
      return `
        <div class="event-row">
          <span class="event-type ${statusClass}">${escapeHtml(eventLabel)}</span>
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
}

async function loadDashboard() {
  try {
    const [dashboard, events] = await Promise.all([
      requestJSON("/api/streams"),
      requestJSON("/api/notifications?limit=8"),
    ]);
    state.streams = dashboard.items || [];
    state.metrics = dashboard.metrics || {};
    state.settings = dashboard.settings || {};
    state.events = events.items || [];
    renderMetrics();
    renderStreams();
    renderEvents();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  $("#toastRegion").appendChild(toast);
  window.setTimeout(() => toast.remove(), 3600);
}

function setButtonBusy(button, busy, busyText = "处理中") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
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
    $("#profileUrlInput").value = stream.profile_url || "";
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
  $("#profileUrlField").classList.toggle("is-hidden", !isDouyin);
  $("#roomInputLabel").textContent = isDouyin
    ? "抖音直播间链接或房间 ID"
    : "直播间链接或房间 ID";
  $("#roomInput").placeholder = isDouyin ? "例如：https://live.douyin.com/..." : "例如：123456";
  $("#roomInputHint").textContent = isDouyin
    ? "支持 live.douyin.com/直播间ID；同时填写主播主页链接更适合长期监测。"
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
        profile_url: formData.get("profile_url"),
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
  if (!window.confirm(`确定删除“${name}”吗？删除后不会再监测此直播间。`)) return;
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

async function openSettingsDialog() {
  try {
    const payload = await requestJSON("/api/settings");
    state.settings = payload.settings || {};
    const settings = state.settings;
    $("#intervalInput").value = settings.monitor_interval_seconds ?? 60;
    $("#providerInput").value = settings.notify_provider ?? "none";
    $("#serverchanInput").value = "";
    $("#wecomInput").value = "";
    $("#serverchanState").textContent = settings.serverchan_sendkey_set
      ? `当前：${settings.serverchan_sendkey_masked}`
      : "尚未设置";
    $("#wecomState").textContent = settings.wecom_webhook_set
      ? `当前：${settings.wecom_webhook_masked}`
      : "尚未设置";
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
    if (action === "close-stream-dialog") $("#streamDialog").close();
    if (action === "close-settings-dialog") $("#settingsDialog").close();
    if (action === "check-all") checkAll(actionTarget);
    if (action === "check-stream") checkStream(actionTarget.dataset.id, actionTarget);
    if (action === "toggle-stream") toggleStream(actionTarget.dataset.id, actionTarget);
    if (action === "delete-stream") deleteStream(actionTarget.dataset.id, actionTarget);
    if (action === "test-notification") testNotification(actionTarget);
  }

  const filterTarget = event.target.closest("[data-filter]");
  if (filterTarget) {
    state.filter = filterTarget.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((button) => {
      button.classList.toggle("is-active", button === filterTarget);
    });
    renderStreams();
  }
});

$("#streamForm").addEventListener("submit", submitStream);
$("#settingsForm").addEventListener("submit", submitSettings);
$("#platformInput").addEventListener("change", updateRoomHint);
$("#providerInput").addEventListener("change", updateProviderFields);
$("#searchInput").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  renderStreams();
});

loadDashboard();
window.setInterval(loadDashboard, 30000);
