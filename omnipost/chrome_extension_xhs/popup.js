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

async function tryPayload(source) {
  const kind = source.startsWith("长文-") ? "long" : "daily";
  let res;
  try {
    res = await fetch(`${BRIDGE}/payload?dir=${encodeURIComponent(source)}&kind=${kind}`);
  } catch (err) {
    const netError = new Error(
      `连不上桥接服务（${err.message || err}）\n请先启动：\n` +
        "omnipost/.venv/bin/python omnipost/scripts/xhs_fill_bridge.py"
    );
    netError.network = true;
    throw netError;
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) {
    const gateError = new Error(body.error || `HTTP ${res.status}`);
    gateError.status = res.status;
    throw gateError;
  }
  return body;
}

async function loadPayload() {
  const select = $("source");
  if (!select.value) {
    setStatus("没有可选的内容目录");
    return;
  }
  setStatus("正在读取…");
  payload = null;
  $("fill").disabled = true;

  const skipped = [];
  let index = select.selectedIndex;
  for (let attempts = 0; attempts < 4 && index < select.options.length; attempts += 1) {
    const source = select.options[index].value;
    try {
      const body = await tryPayload(source);
      payload = body;
      $("title").textContent = body.title;
      $("meta").textContent = `${body.images.length} 张配图 · 正文 ${body.body_chars} 字${
        body.tags.length ? ` · ${body.tags.length} 个话题` : ""
      }`;
      const lines = [];
      if (skipped.length) {
        lines.push(`已跳过不合规的目录：`);
        lines.push(...skipped.map((s) => `  · ${s}`));
      }
      lines.push(body.warnings.length ? body.warnings.join("\n") : "内容已就绪，可填入当前页。");
      setStatus(lines.join("\n"));
      $("fill").disabled = false;
      return;
    } catch (err) {
      if (err.network) {
        setStatus(`读取失败：${err.message}`);
        return;
      }
      if (err.status === 422) {
        // 合规闸门拒绝：自动换下一个候选，不再让用户猜哪一期能用
        skipped.push(`${source}（${err.message.replace(/^合规自检未通过：/, "")}）`);
        if (index + 1 < select.options.length) {
          index += 1;
          select.selectedIndex = index;
          continue;
        }
      }
      setStatus(`读取失败：${err.message || err}`);
      return;
    }
  }
  setStatus(
    "这些目录都没通过合规自检：\n  · " +
      skipped.join("\n  · ") +
      "\n请先改稿，或换一个内容来源。"
  );
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
