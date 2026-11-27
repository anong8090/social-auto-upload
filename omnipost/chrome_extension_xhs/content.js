/**
 * 小红书发布页填入：标题、正文、配图、AI 声明。
 *
 * 只填不发布：绝不点击「发布」，也不点「暂存离开」。
 * 由用户在自己的日常 Chrome 里复核后自己点。
 */
(() => {
  const BRIDGE = "http://127.0.0.1:5419";
  if (window.__lanxuXhsFillLoaded) return;
  window.__lanxuXhsFillLoaded = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // 逐步回传进度：弹窗能实时看到卡在哪一步，而不是一直「正在填入…」
  // 同时把当前步骤写进标签页标题，弹窗关了也能从标题看出卡在哪。
  const originalTitle = document.title;
  const progress = (text) => {
    try {
      document.title = `填入中·${text}`;
    } catch (_) {
      /* ignore */
    }
    try {
      chrome.runtime.sendMessage({ type: "LX_PROGRESS", platform: "xhs", text });
    } catch (_) {
      /* 弹窗已关闭时忽略 */
    }
  };
  const restoreTitle = () => {
    try {
      document.title = originalTitle;
    } catch (_) {
      /* ignore */
    }
  };

  async function waitFor(fn, timeout = 20000, interval = 250) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      try {
        const v = fn();
        if (v) return v;
      } catch (_) {
        /* 页面重建中，继续等 */
      }
      await sleep(interval);
    }
    return null;
  }

  function setInputValue(el, value) {
    const proto =
      el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function fetchAsFile(image) {
    const res = await fetch(`${BRIDGE}${image.url}`);
    if (!res.ok) throw new Error(`图片下载失败 ${image.name}: HTTP ${res.status}`);
    const blob = await res.blob();
    return new File([blob], image.name, { type: blob.type || "image/png" });
  }

  function findFileInput() {
    return (
      document.querySelector('input[type="file"][accept*="image"]') ||
      document.querySelector("div[class^='upload-content'] input.upload-input") ||
      [...document.querySelectorAll('input[type="file"]')].find(
        (el) => !(el.getAttribute("accept") || "").includes("video")
      ) ||
      null
    );
  }

  async function uploadImages(images) {
    progress(`正在下载 ${images.length} 张配图…`);
    const files = [];
    for (const image of images) files.push(await fetchAsFile(image));

    progress("等待图片上传框…");
    const input = await waitFor(findFileInput, 8000);
    if (!input) {
      const total = document.querySelectorAll('input[type="file"]').length;
      throw new Error(
        `没找到图片上传框（页面 file input 数量=${total}）。` +
          "请确认当前是「上传图文」页，并先点过一次「上传图片」让上传框出现。"
      );
    }
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    progress(`已投递 ${files.length} 张配图，等待编辑区出现…`);
    return files.length;
  }

  async function fillTitle(title) {
    const el = await waitFor(
      () =>
        document.querySelector('input[placeholder*="填写标题"]') ||
        document.querySelector('input[placeholder*="标题"]'),
      20000
    );
    if (!el) throw new Error("没找到标题输入框");
    el.focus();
    setInputValue(el, title.slice(0, 20));
    el.blur();
  }

  function findDescEditor() {
    const marker = document.querySelector(
      'p[data-placeholder*="输入正文描述"], [data-placeholder*="输入正文描述"]'
    );
    if (marker) return marker.closest('[contenteditable="true"]') || marker;
    return null;
  }

  async function fillDesc(body, tags) {
    const editor = await waitFor(findDescEditor, 20000);
    if (!editor) throw new Error("没找到正文编辑器");
    editor.focus();
    // 清空已有内容，避免重复填入时叠加
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(editor);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand("delete", false, null);

    const paragraphs = String(body || "")
      .split(/\n{2,}/)
      .map((p) => p.trim())
      .filter(Boolean);
    for (let i = 0; i < paragraphs.length; i += 1) {
      document.execCommand("insertText", false, paragraphs[i]);
      if (i < paragraphs.length - 1) document.execCommand("insertParagraph", false, null);
    }

    if (tags && tags.length) {
      document.execCommand("insertParagraph", false, null);
      for (const tag of tags) {
        document.execCommand("insertText", false, `#${tag} `);
        await sleep(120);
      }
    }

    editor.dispatchEvent(new Event("input", { bubbles: true }));
  }

  async function markAiDeclaration() {
    const findText = (text) =>
      [...document.querySelectorAll("div,span,button,label")].find(
        (el) => el.childElementCount === 0 && (el.textContent || "").trim() === text
      );

    const trigger = await waitFor(() => findText("添加内容类型声明"), 6000);
    if (!trigger) return "未找到「添加内容类型声明」入口";
    trigger.click();
    const option = await waitFor(() => findText("笔记含AI合成内容"), 5000);
    if (!option) return "未找到「笔记含AI合成内容」选项";
    option.click();
    await sleep(300);
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, bubbles: true })
    );
    return "已勾选「笔记含AI合成内容」";
  }

  async function fill(payload) {
    const report = [];
    progress("开始填入…");
    const imageCount = Math.min((payload.images || []).length, 9);
    if (imageCount) {
      await uploadImages(payload.images.slice(0, 9));
      report.push(`已上传 ${imageCount} 张配图`);
    }
    progress("填写标题…");
    await fillTitle(payload.title);
    report.push("已填标题");
    await sleep(600);
    progress("填写正文…");
    await fillDesc(payload.body, payload.tags || []);
    report.push("已填正文");
    if ((payload.tags || []).length) {
      report.push("话题已写成 #文本，可在页面下拉里点选成标签");
    }
    try {
      progress("处理 AI 声明…");
      report.push(await markAiDeclaration());
    } catch (e) {
      report.push(`AI 声明未完成：${e.message || e}`);
    }
    report.push("未点击发布，请复核后自行操作");
    restoreTitle();
    return { steps: report };
  }

  // URL 触发：发布页带上 ?lx_fill=<目录>&lx_kind=daily 时自动填入。
  // 这样不需要点扩展弹窗，任何能打开标签页的工具都能触发。
  const params = new URLSearchParams(location.search);
  const autoDir = params.get("lx_fill");
  if (autoDir) {
    const autoKind = params.get("lx_kind") || "daily";
    setTimeout(async () => {
      if (window.__lanxuAutoFillStarted) return;
      window.__lanxuAutoFillStarted = true;
      progress("读取内容…");
      try {
        const res = await fetch(
          `${BRIDGE}/payload?dir=${encodeURIComponent(autoDir)}&kind=${encodeURIComponent(autoKind)}`
        );
        const payload = await res.json();
        if (!res.ok || !payload.ok) {
          document.title = `填入失败·${(payload.error || "读取失败").slice(0, 40)}`;
          return;
        }
        const result = await fill(payload);
        // 去掉触发参数，避免刷新页面时重复填入
        try {
          const url = new URL(location.href);
          url.searchParams.delete("lx_fill");
          url.searchParams.delete("lx_kind");
          history.replaceState(null, "", url.toString());
        } catch (_) {
          /* ignore */
        }
        document.title = `填入完成·${result.steps.length} 步（未发布）`;
      } catch (err) {
        document.title = `填入失败·${String(err.message || err).slice(0, 40)}`;
      }
    }, 2500);
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg) return undefined;
    if (msg.type === "LX_PING") {
      sendResponse({ ok: true, loaded: true });
      return undefined;
    }
    if (msg.type !== "LX_FILL") return undefined;
    fill(msg.payload || {})
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((err) => {
        restoreTitle();
        sendResponse({ ok: false, error: String(err.message || err) });
      });
    return true;
  });
})();
