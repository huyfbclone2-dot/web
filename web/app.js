const state = {
  activities: [],
  filtered: [],
  server: "s2",
  hiddenOnly: false,
  selectedId: null,
};

const rowTemplate = document.getElementById("rowTemplate");
const appShell = document.getElementById("appShell");
const landingScreen = document.getElementById("landingScreen");
const landingChoice = document.getElementById("landingChoice");
const landingLoginPanel = document.getElementById("landingLoginPanel");
const landingLoginToggle = document.getElementById("landingLoginToggle");
const landingGuestButton = document.getElementById("landingGuestButton");
const landingUser = document.getElementById("landingUser");
const landingPass = document.getElementById("landingPass");
const landingLoginSubmit = document.getElementById("landingLoginSubmit");
const landingBackButton = document.getElementById("landingBackButton");
const landingError = document.getElementById("landingError");
const activityRows = document.getElementById("activityRows");
const searchInput = document.getElementById("searchInput");
const resConfigInput = document.getElementById("resConfigInput");
const loadConfigButton = document.getElementById("loadConfigButton");
const logoutButton = document.getElementById("logoutButton");
const authBadge = document.getElementById("authBadge");
const tableSummary = document.getElementById("tableSummary");
const previewTitle = document.getElementById("previewTitle");
const previewMeta = document.getElementById("previewMeta");
const heroImage = document.getElementById("heroImage");
const heroEmpty = document.getElementById("heroEmpty");
const thumbRail = document.getElementById("thumbRail");
const statusBadge = document.getElementById("statusBadge");
const adminPanel = document.getElementById("adminPanel");
const accountUserInput = document.getElementById("accountUserInput");
const accountPassInput = document.getElementById("accountPassInput");
const accountRoleSelect = document.getElementById("accountRoleSelect");
const saveAccountButton = document.getElementById("saveAccountButton");
const clearAccountButton = document.getElementById("clearAccountButton");
const accountMessage = document.getElementById("accountMessage");
const accountRows = document.getElementById("accountRows");
state.current = null;
state.auth = { authenticated: false, username: "", role: "guest", canManageAccounts: false };
state.appUnlocked = false;
state.accounts = [];
state.editingAccount = "";

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
  });
  if (!response.ok) {
    const message = await response.text();
    let payload = null;
    try {
      payload = JSON.parse(message);
    } catch {
      payload = null;
    }
    throw new Error(payload?.error || message || `Request failed: ${response.status}`);
  }
  return response.json();
}

function updateAuthUi() {
  const authed = !!state.auth?.authenticated;
  logoutButton.disabled = !authed;
  logoutButton.classList.toggle("hidden", !authed);
  authBadge.classList.toggle("hidden", !authed);
  authBadge.textContent = authed ? `${state.auth.username} (${state.auth.role === "admin" ? "admin" : "user"})` : "";
  adminPanel.classList.toggle("hidden", !(authed && state.auth?.canManageAccounts));
}

function toggleLandingLogin(show) {
  landingChoice.classList.toggle("hidden", show);
  landingLoginPanel.classList.toggle("hidden", !show);
  landingError.textContent = "";
  if (show) {
    landingUser.focus();
  } else {
    landingPass.value = "";
  }
}

function clearAccountForm() {
  state.editingAccount = "";
  accountUserInput.value = "";
  accountPassInput.value = "";
  accountRoleSelect.value = "user";
}

function setAccountMessage(text = "", isError = false) {
  accountMessage.textContent = text;
  accountMessage.style.color = isError ? "var(--red)" : "";
}

function renderAccounts() {
  accountRows.textContent = "";
  if (!(state.auth?.authenticated && state.auth?.canManageAccounts)) {
    return;
  }
  if (!state.accounts.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="3" class="subtle">Chưa có tài khoản nào.</td>';
    accountRows.append(row);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const account of state.accounts) {
    const row = document.createElement("tr");
    const roleClass = account.role === "admin" ? "role-chip admin" : "role-chip";
    row.innerHTML = `
      <td>
        <strong>${account.username}</strong>
        ${account.isCurrent ? '<div class="subtle">Đang đăng nhập</div>' : ""}
      </td>
      <td><span class="${roleClass}">${account.role}</span></td>
      <td>
        <div class="account-actions">
          <button class="ghost" type="button" data-action="edit">Sửa</button>
          <button class="ghost" type="button" data-action="delete">Xóa</button>
        </div>
      </td>
    `;
    row.querySelector('[data-action="edit"]').addEventListener("click", () => {
      state.editingAccount = account.username;
      accountUserInput.value = account.username;
      accountPassInput.value = "";
      accountRoleSelect.value = account.role || "user";
      setAccountMessage(`Đang sửa ${account.username}. Để trống mật khẩu nếu không đổi.`, false);
      accountUserInput.focus();
    });
    row.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      if (!confirm(`Xóa tài khoản ${account.username}?`)) {
        return;
      }
      try {
        await fetchJson("/api/auth/accounts/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: account.username }),
        });
        setAccountMessage(`Đã xóa ${account.username}.`, false);
        if (state.editingAccount === account.username) {
          clearAccountForm();
        }
        await loadAccounts();
      } catch (error) {
        setAccountMessage(String(error.message || error), true);
      }
    });
    fragment.append(row);
  }
  accountRows.append(fragment);
}

async function loadAccounts() {
  if (!(state.auth?.authenticated && state.auth?.canManageAccounts)) {
    state.accounts = [];
    renderAccounts();
    return;
  }
  const payload = await fetchJson("/api/auth/accounts");
  state.accounts = payload.accounts || [];
  renderAccounts();
}

function enterApp() {
  state.appUnlocked = true;
  landingScreen.classList.add("hidden");
  appShell.classList.remove("shell-hidden");
  setStatus("Ready");
  updateAuthUi();
  renderAccounts();
}

function exitApp() {
  state.appUnlocked = false;
  appShell.classList.add("shell-hidden");
  landingScreen.classList.remove("hidden");
  toggleLandingLogin(false);
  setStatus("Idle");
  state.accounts = [];
  clearAccountForm();
  setAccountMessage("");
  renderAccounts();
}

function formatTime(activity) {
  if (!activity.timeStart && !activity.timeEnd) {
    return '<span class="subtle">Không có mốc thời gian</span>';
  }
  const parts = [];
  if (activity.timeStart) parts.push(`<div class="time-start">${activity.timeStart}</div>`);
  if (activity.timeEnd) parts.push(`<div class="time-end">${activity.timeEnd}</div>`);
  return parts.join("");
}

function displayTitle(activity) {
  return activity.btnLabel || activity.title || activity.url || `Activity ${activity.id}`;
}

function cachedInfo(activity) {
  return activity.cached?.[state.server] || null;
}

function updateSummary() {
  if (!state.activities.length) {
    tableSummary.textContent = "Chưa có dữ liệu. Nhập tên res_config để nạp danh sách activity.";
    return;
  }
  tableSummary.textContent = state.current?.resConfigName || "Đã nạp res_config";
}

function applyFilters() {
  const query = searchInput.value.trim().toLowerCase();
  state.filtered = state.activities.filter((activity) => {
    if (state.hiddenOnly && activity.isTabShow !== "0") return false;
    if (!query) return true;
    const haystack = [
      activity.id,
      activity.url,
      activity.btnLabel,
      activity.title,
      activity.description,
      activity.activity,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
  renderRows();
  updateSummary();
}

function renderRows() {
  activityRows.textContent = "";
  if (!state.filtered.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="5" class="subtle">Chưa có dữ liệu. Nhập tên res_config_...xml rồi bấm Nạp Config.</td>';
    activityRows.append(row);
    return;
  }
  const fragment = document.createDocumentFragment();

  for (const activity of state.filtered) {
    const clone = rowTemplate.content.cloneNode(true);
    clone.querySelector(".id-cell").innerHTML = `<span class="id-chip">${activity.id || "-"}</span>`;
    clone.querySelector(".row-title").textContent = displayTitle(activity);
    clone.querySelector(".row-subtitle").textContent = activity.isTabShow === "0" ? "Hidden" : "Hiển thị tab";
    clone.querySelector(".desc-cell").textContent = activity.description || "-";
    clone.querySelector(".time-cell").innerHTML = formatTime(activity);

    const button = document.createElement("button");
    const cached = cachedInfo(activity);
    button.className = "view-btn";
    button.type = "button";
    button.innerHTML = cached
      ? `Xem ảnh <small>${cached.imageCount} ảnh</small>`
      : `Xem ảnh`;
    button.addEventListener("click", () => loadActivity(activity.id));
    clone.querySelector(".action-cell").append(button);

    fragment.append(clone);
  }

  activityRows.append(fragment);
}

function setStatus(text) {
  statusBadge.textContent = text;
}

function setHeroImage(url) {
  if (!url) {
    heroImage.classList.remove("visible");
    heroImage.removeAttribute("src");
    heroEmpty.style.display = "grid";
    return;
  }
  heroImage.src = url;
  heroImage.classList.add("visible");
  heroEmpty.style.display = "none";
}

function renderGallery(images, previewUrl) {
  thumbRail.textContent = "";
  setHeroImage(previewUrl);

  for (const image of images) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "thumb-button";
    button.title = `${image.source || "bitmap"} | ${image.width}x${image.height}`;
    button.innerHTML = `<img src="${image.url}" alt="${image.name}" loading="lazy" />`;
    button.classList.toggle("active", image.url === previewUrl);
    button.addEventListener("click", () => {
      setHeroImage(image.url);
      [...thumbRail.children].forEach((node) => node.classList.remove("active"));
      button.classList.add("active");
    });
    thumbRail.append(button);
  }
}

async function loadActivity(id, allowRecovery = true) {
  const activity = state.activities.find((item) => item.id === id);
  if (!activity) return;

  state.selectedId = id;
  previewTitle.textContent = displayTitle(activity);
  previewMeta.textContent = "Đang tải asset, decode SWF và extract ảnh...";
  setStatus("Loading");

  try {
    const resConfigPart = state.current?.resConfigName
      ? `&resConfigName=${encodeURIComponent(state.current.resConfigName)}`
      : "";
    const detail = await fetchJson(
      `/api/detail?id=${encodeURIComponent(id)}&server=${encodeURIComponent(state.server)}${resConfigPart}`
    );
    activity.cached = activity.cached || {};
    activity.cached[state.server] = {
      imageCount: detail.images.imageCount,
      previewUrl: detail.images.previewUrl,
    };
    previewTitle.textContent = `${displayTitle(detail)} (#${detail.id})`;
    previewMeta.textContent = [
      detail.url,
      detail.timeStart ? `${detail.timeStart} -> ${detail.timeEnd || "?"}` : "Không có mốc thời gian",
      `${detail.images.imageCount} ảnh`,
    ]
      .filter(Boolean)
      .join(" | ");
    renderGallery(detail.images.images, detail.images.previewUrl);
    renderRows();
    setStatus("Ready");
  } catch (error) {
    const message = String(error.message || error);
    if (allowRecovery && message.includes("Unknown activity id")) {
      const configName = state.current?.resConfigName || resConfigInput.value.trim();
      if (configName) {
        try {
          await loadRemoteConfig(configName, { silent: true });
          await loadActivity(id, false);
          return;
        } catch {
        }
      }
    }
    previewMeta.textContent = message;
    setHeroImage("");
    thumbRail.textContent = "";
    setStatus("Error");
  }
}

async function init() {
  setStatus("Booting");
  const payload = await fetchJson("/api/reset");
  await fetchJson("/api/auth/logout", { method: "POST" });
  state.auth = { authenticated: false, username: "", role: "guest", canManageAccounts: false };
  state.current = payload.current || null;
  state.activities = payload.activities;
  resConfigInput.value = "";
  applyFilters();
  updateAuthUi();
  exitApp();
}

searchInput.addEventListener("input", applyFilters);

async function loadRemoteConfig(nameOverride = "", options = {}) {
  const { silent = false } = options;
  const name = (nameOverride || resConfigInput.value.trim()).trim();
  if (!name) {
    previewMeta.textContent = "Nhập tên file res_config trước đã.";
    return;
  }

  resConfigInput.value = name;
  loadConfigButton.disabled = true;
  if (!silent) {
    setStatus("Loading");
    previewTitle.textContent = "Đang nạp res_config";
    previewMeta.textContent = `Đang tải ${name} từ CDN, lấy activityList và decode...`;
  }

  try {
    const payload = await fetchJson(
      `/api/load-res-config?server=${encodeURIComponent(state.server)}&name=${encodeURIComponent(name)}`
    );
    state.current = payload.current || null;
    state.activities = payload.activities;
    applyFilters();
    setStatus("Ready");
    if (!silent) {
      previewMeta.textContent = [
        state.current?.resConfigName || name,
        state.current?.activityListUrl || "",
        `${payload.total} row`,
      ]
        .filter(Boolean)
        .join(" | ");
    }
    const firstWithAsset = state.activities.find((item) => item.assetRelativeUrl);
    if (firstWithAsset) {
      await loadActivity(firstWithAsset.id);
    } else {
      setHeroImage("");
      thumbRail.textContent = "";
    }
    return payload;
  } catch (error) {
    setStatus("Error");
    previewMeta.textContent = String(error.message || error);
    throw error;
  } finally {
    loadConfigButton.disabled = false;
  }
}

loadConfigButton.addEventListener("click", () => {
  loadRemoteConfig();
});
resConfigInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadRemoteConfig();
  }
});

async function reloadSelectedActivity() {
  if (state.selectedId) {
    await loadActivity(state.selectedId, false);
  }
}

async function loginWithCredentials(username, password, options = {}) {
  const { unlockApp = false } = options;
  if (!username || !password) {
    landingError.textContent = "Nhập tài khoản và mật khẩu trước đã.";
    return;
  }

  try {
    const result = await fetchJson("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    state.auth = result;
    updateAuthUi();
    landingUser.value = username;
    landingPass.value = "";
    if (unlockApp && !state.appUnlocked) {
      enterApp();
    }
    await loadAccounts();
    clearAccountForm();
    setAccountMessage("");
    await reloadSelectedActivity();
  } catch (error) {
    landingError.textContent = String(error.message || error);
  }
}

async function logoutCurrentUser() {
  try {
    await fetchJson("/api/auth/logout", {
      method: "POST",
    });
  } catch {
  }
  state.auth = { authenticated: false, username: "", role: "guest", canManageAccounts: false };
  state.accounts = [];
  clearAccountForm();
  setAccountMessage("");
  renderAccounts();
  updateAuthUi();
}

saveAccountButton.addEventListener("click", async () => {
  try {
    const payload = await fetchJson("/api/auth/accounts/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: accountUserInput.value.trim(),
        password: accountPassInput.value,
        role: accountRoleSelect.value,
      }),
    });
    setAccountMessage(
      payload.created
        ? `Đã tạo tài khoản ${payload.account.username}.`
        : `Đã cập nhật tài khoản ${payload.account.username}.`,
      false
    );
    state.auth = await fetchJson("/api/auth/status");
    updateAuthUi();
    await loadAccounts();
    clearAccountForm();
  } catch (error) {
    setAccountMessage(String(error.message || error), true);
  }
});

clearAccountButton.addEventListener("click", () => {
  clearAccountForm();
  setAccountMessage("");
});

accountPassInput.addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    saveAccountButton.click();
  }
});

logoutButton.addEventListener("click", async () => {
  await logoutCurrentUser();
  exitApp();
});

landingLoginSubmit.addEventListener("click", async () => {
  await loginWithCredentials(landingUser.value.trim(), landingPass.value, { unlockApp: true });
});

landingLoginToggle.addEventListener("click", () => {
  toggleLandingLogin(true);
});

landingBackButton.addEventListener("click", () => {
  toggleLandingLogin(false);
});

landingGuestButton.addEventListener("click", async () => {
  await logoutCurrentUser();
  enterApp();
});

landingPass.addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    await loginWithCredentials(landingUser.value.trim(), landingPass.value, { unlockApp: true });
  }
});

landingUser.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    landingPass.focus();
  }
});

init().catch((error) => {
  setStatus("Error");
  previewMeta.textContent = String(error.message || error);
});
