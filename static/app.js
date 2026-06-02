let deferredPrompt = null;
let lastAlertId = Number(localStorage.getItem("lastAlertId") || "0");

function $(id) { return document.getElementById(id); }
function fmt(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  return Number(v).toFixed(d);
}
function pctClass(v) { if (v > 0) return "green"; if (v < 0) return "red"; return ""; }

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
}

function switchView(name) {
  document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.querySelector(`.tab[data-view="${name}"]`).classList.add("active");
  document.getElementById(name).classList.add("active");
}

function reasonList(arr) {
  if (!arr || !arr.length) return "";
  return `<ul class="reason-list">${arr.map(x => `<li>${x}</li>`).join("")}</ul>`;
}

function indicatorGrid(item) {
  const i = item.indicators || {};
  return `
    <div class="metrics">
      <div class="metric"><span>SMA20 / SMA50</span><b>${fmt(i.sma20)} / ${fmt(i.sma50)}</b></div>
      <div class="metric"><span>SMA100 / SMA200</span><b>${fmt(i.sma100)} / ${fmt(i.sma200)}</b></div>
      <div class="metric"><span>EMA12 / EMA26</span><b>${fmt(i.ema12)} / ${fmt(i.ema26)}</b></div>
      <div class="metric"><span>RSI14</span><b>${fmt(i.rsi14, 1)}</b></div>
      <div class="metric"><span>MACD / Signal</span><b>${fmt(i.macd, 3)} / ${fmt(i.macd_signal, 3)}</b></div>
      <div class="metric"><span>MACD Hist</span><b>${fmt(i.macd_hist, 3)}</b></div>
      <div class="metric"><span>BB Upper/Mid/Lower</span><b>${fmt(i.bb_upper)} / ${fmt(i.bb_mid)} / ${fmt(i.bb_lower)}</b></div>
      <div class="metric"><span>Stoch K/D</span><b>${fmt(i.stoch_k, 1)} / ${fmt(i.stoch_d, 1)}</b></div>
      <div class="metric"><span>ADX +DI/-DI</span><b>${fmt(i.adx14, 1)} | ${fmt(i.plus_di, 1)} / ${fmt(i.minus_di, 1)}</b></div>
      <div class="metric"><span>ATR14</span><b>${fmt(i.atr14)}</b></div>
      <div class="metric"><span>MFI14</span><b>${fmt(i.mfi14, 1)}</b></div>
      <div class="metric"><span>CCI / Williams %R</span><b>${fmt(i.cci20, 1)} / ${fmt(i.willr14, 1)}</b></div>
    </div>
  `;
}

function buyCard(item) {
  const cls = item.buy_score >= 78 ? "good" : "";
  const dayClass = pctClass(item.day_pct);
  return `
    <div class="stock-card ${cls}">
      <div class="stock-head">
        <div><div class="symbol">${item.symbol}</div><div class="muted">${item.updated_at}</div></div>
        <div class="price">${fmt(item.price)}<div class="${dayClass}" style="font-size:14px">${fmt(item.day_pct)}%</div></div>
      </div>
      <div class="signal">${item.buy_signal} | Điểm mua ${item.buy_score}/100</div>
      <div class="action">${item.buy_action}</div>
      ${reasonList(item.buy_reasons)}
      <div class="metrics">
        <div class="metric"><span>Vùng mua</span><b>${item.buy_zone ? item.buy_zone.map(x => fmt(x)).join(" - ") : "-"}</b></div>
        <div class="metric"><span>Stop mua</span><b class="red">${fmt(item.buy_stop)}</b></div>
        <div class="metric"><span>Volume/Vol20</span><b>${fmt(item.vol_ratio)}x</b></div>
        <div class="metric"><span>Đỉnh/đáy 20 phiên</span><b>${fmt(item.high20)} / ${fmt(item.low20)}</b></div>
      </div>
      ${indicatorGrid(item)}
      <div class="links">
        <a class="link-btn" href="${item.vndirect_url}" target="_blank">Mở VNDIRECT</a>
        <button class="secondary" onclick="quickAddPosition('${item.symbol}', ${item.price})">Đã mua mã này</button>
      </div>
    </div>
  `;
}

function positionCard(item) {
  const pos = item.position || {};
  const danger = pos.status && pos.status.startsWith("BÁN");
  const cls = danger ? "danger" : "";
  const dayClass = pctClass(item.day_pct);
  return `
    <div class="stock-card ${cls}">
      <div class="stock-head">
        <div><div class="symbol">${item.symbol}</div><div class="muted">${item.updated_at}</div></div>
        <div class="price">${fmt(item.price)}<div class="${dayClass}" style="font-size:14px">${fmt(item.day_pct)}%</div></div>
      </div>
      <div class="signal">${pos.status || "GIỮ / THEO DÕI"} | Điểm bán ${item.sell_score}/100</div>
      <div class="action">${pos.reason || item.sell_action}</div>
      ${reasonList(item.sell_reasons)}
      <div class="metrics">
        <div class="metric"><span>Giá mua</span><b>${fmt(pos.buy_price)}</b></div>
        <div class="metric"><span>Lãi/lỗ</span><b class="${pctClass(pos.pnl_pct)}">${fmt(pos.pnl_pct)}%</b></div>
        <div class="metric"><span>Stop cuối</span><b class="red">${fmt(pos.final_stop)}</b></div>
        <div class="metric"><span>Tín hiệu bán</span><b>${item.sell_signal}</b></div>
        <div class="metric"><span>Volume/Vol20</span><b>${fmt(item.vol_ratio)}x</b></div>
        <div class="metric"><span>Đáy 10/20 phiên</span><b>${fmt(item.low10)} / ${fmt(item.low20)}</b></div>
      </div>
      ${indicatorGrid(item)}
      <div class="links">
        <a class="link-btn" href="${item.vndirect_url}" target="_blank">Mở VNDIRECT</a>
        <button class="secondary" onclick="removePosition('${item.symbol}')">Xóa khỏi danh mục</button>
      </div>
    </div>
  `;
}

function fullTechCard(item) {
  return `
    ${buyCard(item)}
    <div class="stock-card ${item.sell_score >= 78 ? "danger" : ""}">
      <div class="signal">${item.sell_signal} | Điểm bán ${item.sell_score}/100</div>
      <div class="action">${item.sell_action}</div>
      ${reasonList(item.sell_reasons)}
      <div class="metrics">
        <div class="metric"><span>Stop kỹ thuật</span><b class="red">${fmt(item.technical_stop)}</b></div>
        <div class="metric"><span>Đáy 10/20 phiên</span><b>${fmt(item.low10)} / ${fmt(item.low20)}</b></div>
      </div>
    </div>
  `;
}

async function loadConfig() {
  const cfg = await api("/api/config");
  $("autoEnabled").checked = !!cfg.auto_scan_enabled;
  $("scanInterval").value = String(cfg.scan_interval_sec || 10);
  $("minBuyScore").value = String(cfg.min_buy_score || 78);
  $("minSellScore").value = String(cfg.min_sell_score || 78);
  $("alertCooldown").value = String(cfg.alert_cooldown_sec || 900);
  $("scanSymbols").value = (cfg.scan_symbols || []).join(",");
}

async function saveConfig() {
  const symbols = $("scanSymbols").value.replace(/\n/g, ",").split(",").map(x => x.trim().toUpperCase()).filter(Boolean);
  const payload = {
    auto_scan_enabled: $("autoEnabled").checked,
    scan_interval_sec: Number($("scanInterval").value),
    min_buy_score: Number($("minBuyScore").value),
    min_sell_score: Number($("minSellScore").value),
    alert_cooldown_sec: Number($("alertCooldown").value),
    scan_symbols: symbols
  };
  await api("/api/config", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload) });
  alert("Đã lưu cài đặt.");
  await refreshLatest();
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    $("serverStatus").innerText = `Server online | lần quét gần nhất: ${h.last_scan_at || "-"}`;
    $("autoStatus").innerText = h.scan_running ? "Đang quét" : "Đang chờ";
    $("lastScan").innerText = h.last_scan_at || "-";
    $("scanCount").innerText = h.scan_count ?? "-";
    $("scanError").innerText = h.last_scan_error || "-";
  } catch (e) {
    $("serverStatus").innerText = "Mất kết nối server";
  }
}

async function refreshLatest() {
  try {
    const data = await api("/api/latest");
    $("autoStatus").innerText = data.scan_running ? "Đang quét" : "Đang chờ";
    $("lastScan").innerText = data.last_scan_at || "-";
    $("scanCount").innerText = data.scan_count ?? "-";
    $("scanError").innerText = data.last_scan_error || "-";
    $("buyResults").innerHTML = (data.buy_results || []).map(buyCard).join("") || `<div class="card">Chưa có điểm mua đạt điều kiện.</div>`;
    $("positionResults").innerHTML = (data.position_results || []).map(positionCard).join("") || `<div class="card">Chưa có cổ phiếu đang cầm.</div>`;
  } catch (e) {
    $("buyResults").innerHTML = `<div class="card danger">Lỗi cập nhật: ${e.message}</div>`;
  }
}

function renderAlerts(alerts) {
  if (!alerts.length) return;
  const html = alerts.map(a => `
    <div class="alert-item ${a.kind === "SELL" ? "danger" : "good"}">
      <div class="alert-title">${a.title}</div>
      <div>${a.body}</div>
      <div class="alert-time">${a.created_at}</div>
    </div>
  `).join("");
  const el = $("alertsList");
  if (el.innerText.includes("Chưa có cảnh báo")) el.innerHTML = html;
  else el.innerHTML = html + el.innerHTML;
}

async function pollAlerts() {
  try {
    const data = await api(`/api/alerts?since_id=${lastAlertId}`);
    if (data.alerts && data.alerts.length) {
      renderAlerts(data.alerts);
      for (const a of data.alerts) notify(a.title, a.body, a);
    }
    if (data.latest_id && data.latest_id > lastAlertId) {
      lastAlertId = data.latest_id;
      localStorage.setItem("lastAlertId", String(lastAlertId));
    }
  } catch {}
}

async function clearAlerts() {
  await api("/api/alerts/clear", { method: "POST" });
  lastAlertId = 0;
  localStorage.setItem("lastAlertId", "0");
  $("alertsList").innerHTML = "Chưa có cảnh báo.";
}

async function manualScan() {
  $("buyResults").innerHTML = `<div class="card">Đang quét thủ công...</div>`;
  await api("/api/scan-now", { method: "POST" });
  await refreshLatest();
  await pollAlerts();
}

async function analyzeOne() {
  const symbol = $("symbolInput").value.trim().toUpperCase();
  if (!symbol) return alert("Nhập mã cổ phiếu trước.");
  $("singleResult").innerHTML = `<div class="card">Đang phân tích ${symbol}...</div>`;
  try {
    const item = await api(`/api/analyze/${symbol}`);
    $("singleResult").innerHTML = fullTechCard(item);
  } catch (e) {
    $("singleResult").innerHTML = `<div class="card danger">Lỗi: ${e.message}</div>`;
  }
}

async function getPositions() {
  const data = await api("/api/positions");
  return data.positions || [];
}

async function setPositions(list) {
  await api("/api/positions", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(list) });
}

async function addPosition() {
  const symbol = $("posSymbol").value.trim().toUpperCase();
  if (!symbol) return alert("Nhập mã trước.");
  const list = (await getPositions()).filter(x => x.symbol !== symbol);
  list.push({
    symbol,
    buy_price: $("posBuyPrice").value.trim(),
    quantity: $("posQty").value.trim(),
    manual_stop: $("posStop").value.trim(),
    buy_date: new Date().toISOString().slice(0, 10),
    note: $("posNote").value.trim()
  });
  await setPositions(list);
  $("posSymbol").value = "";
  $("posBuyPrice").value = "";
  $("posQty").value = "";
  $("posStop").value = "";
  $("posNote").value = "";
  alert(`Đã thêm ${symbol}. App sẽ báo điểm bán cho mã này nếu có tín hiệu.`);
  await refreshLatest();
}

async function quickAddPosition(symbol, price) {
  const buy = prompt(`Nhập giá mua của ${symbol}`, price || "");
  if (buy === null) return;
  const list = (await getPositions()).filter(x => x.symbol !== symbol);
  list.push({ symbol, buy_price: buy, quantity: "", manual_stop: "", buy_date: new Date().toISOString().slice(0, 10), note: "" });
  await setPositions(list);
  alert(`Đã thêm ${symbol} vào danh mục đang cầm.`);
  await refreshLatest();
}

async function removePosition(symbol) {
  const list = (await getPositions()).filter(x => x.symbol !== symbol);
  await setPositions(list);
  await refreshLatest();
}

async function enableNotifications() {
  if (!("Notification" in window)) return alert("Trình duyệt chưa hỗ trợ thông báo.");
  const perm = await Notification.requestPermission();
  alert(perm === "granted" ? "Đã bật thông báo." : "Chưa cấp quyền thông báo.");
}

function notify(title, body, obj) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.getRegistration().then(reg => {
      if (reg) reg.showNotification(title, { body, icon: "/static/icon-192.png", badge: "/static/icon-192.png", data: obj });
      else new Notification(title, { body });
    });
  } else new Notification(title, { body });
}

async function setupFirebasePush() {
  try {
    const cfg = await api("/api/fcm/config");
    if (!cfg.enabled) return alert("Firebase Push chưa bật trong config.json.");
    if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
      return alert("Web Push cần HTTPS. Deploy app lên domain HTTPS trước.");
    }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return alert("Mày chưa cấp quyền thông báo.");

    const { initializeApp } = await import("https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js");
    const { getMessaging, getToken, onMessage } = await import("https://www.gstatic.com/firebasejs/10.12.5/firebase-messaging.js");

    const firebaseApp = initializeApp(cfg.firebaseConfig);
    const messaging = getMessaging(firebaseApp);
    const swReg = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
    const token = await getToken(messaging, { vapidKey: cfg.vapidKey, serviceWorkerRegistration: swReg });
    if (!token) return alert("Không lấy được FCM token.");

    await api("/api/fcm/register", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ token }) });
    onMessage(messaging, payload => notify(payload?.notification?.title || "Stock Alert", payload?.notification?.body || "", payload?.data || {}));
    alert("Đã bật Firebase Push.");
  } catch (e) {
    alert("Bật Firebase Push lỗi: " + e.message);
  }
}

async function testFirebasePush() {
  try {
    await api("/api/fcm/test", { method: "POST" });
    alert("Đã gửi test push từ server.");
  } catch (e) {
    alert("Test push lỗi: " + e.message);
  }
}

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
});

async function installPWA() {
  if (!deferredPrompt) return alert("Mở menu trình duyệt và chọn Add to Home Screen.");
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
}

document.addEventListener("DOMContentLoaded", async () => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");

  document.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));

  $("notifyBtn").addEventListener("click", enableNotifications);
  $("manualScanBtn").addEventListener("click", manualScan);
  $("clearAlertBtn").addEventListener("click", clearAlerts);
  $("addPosBtn").addEventListener("click", addPosition);
  $("analyzeBtn").addEventListener("click", analyzeOne);
  $("saveSettingsBtn").addEventListener("click", saveConfig);
  $("installBtn").addEventListener("click", installPWA);
  $("firebasePushBtn").addEventListener("click", setupFirebasePush);
  $("testPushBtn").addEventListener("click", testFirebasePush);

  $("symbolInput").addEventListener("keydown", e => { if (e.key === "Enter") analyzeOne(); });

  await loadConfig();
  await refreshHealth();
  await refreshLatest();
  await pollAlerts();

  setInterval(refreshHealth, 5000);
  setInterval(refreshLatest, 10000);
  setInterval(pollAlerts, 3000);
});
