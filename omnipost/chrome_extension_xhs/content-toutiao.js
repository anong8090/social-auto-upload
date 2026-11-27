/**
 * 今日头条图文发布页填入：标题、正文、封面、作品声明。
 *
 * 为什么不是「存草稿」：该账号的自动保存请求被头条服务端以 code=7050
 * 拒绝（页面自己发的请求，不是参数问题），草稿箱不会有新稿。
 * 所以这里只填不发布，由你复核后自己点「预览并发布」。
 */
(() => {
  const BRIDGE = "http://127.0.0.1:5419";
  if (window.__lanxuToutiaoFillLoaded) return;
  window.__lanxuToutiaoFillLoaded = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function waitFor(fn, timeout = 20000, interval = 250) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      try {
        const v = fn();
        if (v) return v;
      } catch (_) {
        /* 编辑器重建中，继续等 */
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

  async function fillTitle(title) {
    const el = await waitFor(
      () =>
        document.querySelector('input[placeholder*="标题"]') ||
        document.querySelector('textarea[placeholder*="标题"]'),
      20000
    );
    if (!el) throw new Error("没找到标题输入框");
    el.focus();
    setInputValue(el, title.slice(0, 30));
    el.blur();
  }

  async function fillBody(body) {
    const editor = await waitFor(
      () => document.querySelector('div[contenteditable="true"]'),
      20000
    );
    if (!editor) throw new Error("没找到正文编辑器");
    editor.focus();
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
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  }

  async function findCoverInput() {
    const isImageInput = (el) => (el.getAttribute("accept") || "").includes("image");
    let input = [...document.querySelectorAll('input[type="file"]')].find(isImageInput);
    if (input) return input;
    // 新版页面封面区需要先点开「+」才会出现 file input
    const add = [...document.querySelectorAll("div,span,button,label")].find((el) => {
      const cls = String(el.className || "");
      return /article-cover-add|article-cover-img-wrap|article-cover-images/.test(cls);
    });
    if (add) {
      add.click();
      await sleep(1200);
      input = [...document.querySelectorAll('input[type="file"]')].find(isImageInput);
    }
    return input || null;
  }

  async function uploadCover(image) {
    if (!image) return "没有配图，跳过封面";
    const input = await findCoverInput();
    if (!input) return "未找到封面上传入口，请手动设置封面";
    const file = await fetchAsFile(image);
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(1500);
    return `已上传封面：${image.name}`;
  }

  async function markAi() {
    const label = [...document.querySelectorAll("label")].find((el) =>
      (el.innerText || "").includes("引用AI")
    );
    if (!label) return "未找到作品声明「引用AI」";
    const input = label.querySelector('input[type="checkbox"]');
    if (input && input.checked) return "作品声明已是「引用AI」";
    label.click();
    await sleep(300);
    const ok = input ? input.checked : /checked|ch\b/.test(label.className || "");
    return ok ? "已勾选作品声明「引用AI」" : "作品声明可能未勾上，请人工确认";
  }

  async function fill(payload) {
    const report = [];
    await fillTitle(payload.title);
    report.push("已填标题");
    await sleep(500);
    await fillBody(payload.body);
    report.push("已填正文（Markdown 标记已清理）");
    report.push(await uploadCover((payload.images || [])[0]));
    report.push(await markAi());
    report.push("未点击发布：请复核配图与声明后自行点「预览并发布」");
    return { steps: report };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg) return undefined;
    if (msg.type === "LX_PING_TOUTIAO") {
      sendResponse({ ok: true, loaded: true });
      return undefined;
    }
    if (msg.type !== "LX_FILL_TOUTIAO") return undefined;
    fill(msg.payload || {})
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  });
})();
