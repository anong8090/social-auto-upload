const BRIDGE = "http://127.0.0.1:5419";

const $ = (id) => document.getElementById(id);

const PLATFORMS = {
  xhs: {
    label: "小红书（上传图文页）",
    match: /^https:\/\/creator\.xiaohongshu\.com\//,
    kind: "daily",
    file: "content.js",
    ping: "LX_PING",
    fill: "LX_FILL",
  },
  toutiao: {
    label: "今日头条（图文发布页）",
    match: /^https:\/\/mp\.toutiao\.com\//,
    kind: "long",
    file: "content-toutiao.js",
    ping: "LX_PING_TOUTIAO",
    fill: "LX_FILL_TOUTIAO",
  },
};

let platform = null;
let payload = null;

function setStatus(text) {
  $("status").textContent = text;
}

async function detectPlatform() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = (tab && tab.url) || "";
  const key = Object.keys(PLATFORMS).find((k) => PLATFORMS[k].match.test(url));
  platform = key ? { key, tab, ...PLATFORMS[key] } : null;
  $("platform").textContent = platform
    ? `当前页：${platform.label}`
    : "当前页不是小红书/头条发布页，请先打开对应页面";
  return platform;
}

async function loadDirs() {
  const res = await fetch(`${BRIDGE}/dirs`);
  const data = await res.json();
  if (!data.ok) throw new Error("桥接返回异常");
  const select = $("source");
  select.innerHTML = "";
  const daily = document.createElement("optgroup");
  daily.label = "每日短稿";
  for (const item of data.daily) daily.appendChild(new Option(item, item));
  const long = document.createElement("optgroup");
  long.label = "长文";
  for (const item of data.long) long.appendChild(new Option(item, item));
  select.appendChild(daily);
  select.appendChild(long);
  const preferred = platform && platform.kind === "long" ? data.long[0] : data.daily[0];
  if (preferred) select.value = preferred;
}

async function loadPayload() {
  const source = $("source").value;
  if (!source) {
    setStatus("没有可选的内容目录");
    return;
  }
  const kind = source.startsWith("长文-") ? "long" : "daily";
  setStatus("正在读取…");
  payload = null;
  $("fill").disabled = true;
  try {
    const res = await fetch(
      `${BRIDGE}/payload?dir=${encodeURIComponent(source)}&kind=${kind}`
    );
    const body = await res.json();
    if (!res.ok || !body.ok) throw new Error(body.error || `HTTP ${res.status}`);
    payload = body;
    $("title").textContent = body.title;
    $("meta").textContent = `${body.images.length} 张配图 · 正文 ${body.body_chars} 字${
      body.tags.length ? ` · ${body.tags.length} 个话题` : ""
    }`;
    setStatus(body.warnings.length ? body.warnings.join("\n") : "内容已就绪，可填入当前页。");
    $("fill").disabled = false;
  } catch (err) {
    setStatus(
      `读取失败：${err.message || err}\n请确认桥接服务已启动：\n` +
        "omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py"
    );
  }
}

async function ensureContentScript(tabId, platform) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: platform.ping });
  } catch (_) {
    await chrome.scripting.executeScript({ target: { tabId }, files: [platform.file] });
  }
}

async function fillCurrentTab() {
  if (!payload || !platform) return;
  setStatus("正在填入…");
  $("fill").disabled = true;
  try {
    await ensureContentScript(platform.tab.id, platform);
    const result = await chrome.tabs.sendMessage(platform.tab.id, {
      type: platform.fill,
      payload,
    });
    if (!result || !result.ok) throw new Error((result && result.error) || "填入失败");
    setStatus(result.steps.join("\n"));
  } catch (err) {
    setStatus(`填入失败：${err.message || err}`);
  } finally {
    $("fill").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("load").addEventListener("click", loadPayload);
  $("fill").addEventListener("click", fillCurrentTab);
  $("source").addEventListener("change", () => {
    $("fill").disabled = true;
  });
  await detectPlatform();
  try {
    await loadDirs();
    if ($("source").value) await loadPayload();
  } catch (err) {
    setStatus(
      `读取目录失败：${err.message || err}\n请先启动桥接服务：\n` +
        "omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py"
    );
  }
});
