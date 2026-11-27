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

  async function uploadImages(images) {
    const input = await waitFor(
      () => document.querySelector('input[type="file"][accept*="image"]'),
      20000
    );
    if (!input) {
      throw new Error("没找到图片上传框，请确认当前页是「上传图文」");
    }
    const files = [];
    for (const image of images) files.push(await fetchAsFile(image));
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
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
    const imageCount = Math.min((payload.images || []).length, 9);
    if (imageCount) {
      await uploadImages(payload.images.slice(0, 9));
      report.push(`已上传 ${imageCount} 张配图`);
    }
    await fillTitle(payload.title);
    report.push("已填标题");
    await sleep(600);
    await fillDesc(payload.body, payload.tags || []);
    report.push("已填正文");
    if ((payload.tags || []).length) {
      report.push("话题已写成 #文本，可在页面下拉里点选成标签");
    }
    try {
      report.push(await markAiDeclaration());
    } catch (e) {
      report.push(`AI 声明未完成：${e.message || e}`);
    }
    report.push("未点击发布，请复核后自行操作");
    return { steps: report };
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
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  });
})();
