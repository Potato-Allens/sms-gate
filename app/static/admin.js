/* SMS Gate - admin console */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const content = $("content");
  const S = { csrf: "", me: null, route: "", pageState: {} };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  function fmt(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function toast(msg, kind) {
    const el = $("toast");
    el.textContent = msg;
    el.className = "toast show " + (kind || "");
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = "toast " + (kind || ""); }, 3000);
  }
  function badge(text, cls) { return '<span class="badge ' + (cls || "") + '">' + esc(text) + "</span>"; }
  function copy(text) {
    const s = String(text == null ? "" : text);
    if (!s) { toast("没有可复制的内容", "bad"); return; }
    const lines = s.split("\n").length;
    const good = () => toast(lines > 1 ? "已复制 " + lines + " 行" : "已复制", "good");
    const bad = () => toast("浏览器拦截了剪贴板，请点「下载 txt」或手动全选复制", "bad");
    const legacy = () => {
      try {
        const ta = document.createElement("textarea");
        ta.value = s;
        ta.setAttribute("readonly", "");
        ta.style.cssText = "position:fixed;top:-2000px;left:0;opacity:0";
        document.body.appendChild(ta);
        ta.focus(); ta.select(); ta.setSelectionRange(0, s.length);
        const done = document.execCommand("copy");
        ta.remove();
        done ? good() : bad();
      } catch (e) { bad(); }
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(s).then(good, legacy);
    } else legacy();
  }

  async function api(path, opt) {
    opt = opt || {};
    const init = { method: opt.method || "GET", credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (opt.body) {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRF"] = S.csrf;
      init.body = JSON.stringify(opt.body);
    }
    const r = await fetch(path, init);
    let d;
    try { d = await r.json(); } catch (e) { d = { ok: false, error: "响应异常" }; }
    d._status = r.status;
    if (r.status === 401 && path !== "/admin/api/login") {
      location.href = "/admin/login";
      throw new Error("unauthorized");
    }
    return d;
  }

  async function boot() {
    let me;
    try {
      me = await api("/admin/api/me");
    } catch (e) {
      return; // 401 already redirected to the login page
    }
    if (!me.ok) return;
    S.me = me; S.csrf = me.csrf;
    $("meName").textContent = me.username;
    $("meAvatar").textContent = (me.username || "A").slice(0, 1).toUpperCase();
    window.addEventListener("hashchange", route);
    content.addEventListener("click", onClick);
    content.addEventListener("submit", onSubmit);
    // The dialog is a sibling of #content, so its buttons (复制全部 / 复制链接) never
    // reached the delegated handler and silently did nothing. Bind it as well.
    $("modal").addEventListener("click", onClick);
    $("modal").addEventListener("submit", onSubmit);
    $("modalClose").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => {
      if (e.target !== $("modal")) return;
      // Generated links only live in this dialog: a stray backdrop click must not destroy them.
      if ($("modal").dataset.lock === "1") { toast("请点右上角 × 关闭，避免误关丢失列表"); return; }
      closeModal();
    });
    $("btnLogout").addEventListener("click", async () => {
      await api("/admin/api/logout", { method: "POST", body: {} });
      location.href = "/admin/login";
    });
    setInterval(() => {
      const d = new Date();
      $("clock").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
    }, 1000);
    route();
  }

  const TITLES = {
    dashboard: ["总览", "实时运营与安全态势"],
    cards: ["卡密管理", "创建、核销与状态跟踪"],
    relay: ["直链卡", "卡号:手机号----接码地址 · 可持续取码"],
    numbers: ["号码池", "号源库存与分配情况"],
    redemptions: ["兑换情况", "卡密授权与访问记录"],
    codes: ["接码情况", "短信验证码到达明细"],
    usage: ["使用情况", "卡密会话与访问"],
    audit: ["安全审计", "全量操作与安全事件"],
    ips: ["IP 管理", "访问统计、封禁与限流"],
    settings: ["系统设置", "号源、策略与站点信息"],
    shop: ["号码商城", "平台定价、上架与同步"],
    shoporders: ["商城订单", "直链卡发货与退款"],
    rcodes: ["充值码", "批量生成与核销情况"],
    shopusers: ["商城用户", "余额、状态与消费"],
    stock: ["卡密库存", "回收卡密链接 · 现货优先发货"],
  };

  function route() {
    const hash = (location.hash || "#/dashboard").replace(/^#\//, "");
    const r = TITLES[hash] ? hash : "dashboard";
    S.route = r;
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === r));
    $("pageTitle").textContent = TITLES[r][0];
    $("pageSub").textContent = TITLES[r][1];
    ({ dashboard: pageDashboard, cards: pageCards, relay: pageRelay, numbers: pageNumbers, redemptions: pageRedemptions,
       codes: pageCodes, usage: pageUsage, audit: pageAudit, ips: pageIps, settings: pageSettings,
       shop: pageShop, shoporders: pageShopOrders, rcodes: pageRcodes, shopusers: pageShopUsers,
       stock: pageStock })[r]();
  }

  function closeModal() {
    $("modal").classList.add("hidden");
    $("modal").dataset.lock = "";
    $("modalBody").innerHTML = "";
  }
  function openModal(title, html, lock) {
    $("modalTitle").textContent = title;
    $("modalBody").innerHTML = html;
    $("modal").dataset.lock = lock ? "1" : "";
    $("modal").classList.remove("hidden");
  }

  function onClick(e) {
    const el = e.target.closest("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    if (act === "copy") { copy(el.dataset.text || ""); return; }
    if (act === "modal-close") { closeModal(); return; }
    if (/^(shop-|ord-|rc-|u-|stock-)/.test(act) || act === "copy-all-codes" || act === "dl-codes") {
      shopAction(act, el); return;
    }
    if (act === "page") { goPage(el.dataset.page); return; }
    if (act === "card-action") { cardAction(el.dataset.id, el.dataset.op); return; }
    if (act === "num-action") { numAction(el.dataset.id, el.dataset.op); return; }
    if (act === "block-ip") { blockIp(el.dataset.ip); return; }
    if (act === "unblock-ip") { ipAction(el.dataset.ip, "unblock"); return; }
    if (act === "test-provider") { testProvider(); return; }
    if (act === "test-d1jiema") { testProvider("d1jiema"); return; }
    if (act === "refill") { refillPool(); return; }
    if (act === "cooldown-clear") { clearCooldown(); return; }
    if (act === "queue-stats") { refreshQueueStats(); return; }
    if (act === "relay-action") { relayAction(el.dataset.id, el.dataset.op); return; }
    if (act === "relay-export") { relayExport(); return; }
    if (act === "copy-all") {
      const ta = $("allLinks");
      copy((S.lastRelay && S.lastRelay.lines) || (ta ? ta.value : ""));
      return;
    }
    if (act === "relay-batch-copy") { relayBatchExport("copy"); return; }
    if (act === "relay-batch-download") { relayBatchExport("download"); return; }
    if (act === "relay-batch-preview") { relayBatchPreview(); return; }
  }

  function onSubmit(e) {
    if (e.target.id === "createForm") { e.preventDefault(); createCards(); }
    else if (e.target.id === "relayForm") { e.preventDefault(); createRelay(); }
    else if (e.target.id === "relayAllocForm") { e.preventDefault(); allocateRelay(); }
    else if (e.target.id === "relayPoolForm") { e.preventDefault(); relayFromPool(e.target); }
    else if (e.target.id === "importForm") { e.preventDefault(); importNumbers(); }
    else if (e.target.id === "settingsForm") { e.preventDefault(); saveSettings(); }
    else if (e.target.id === "blockForm") { e.preventDefault(); blockIpSubmit(); }
    else if (e.target.id === "stockForm") { e.preventDefault(); stockRecover(e.target); }
  }

  const KIND = {
    card_redeem: "卡密兑换", number_alloc: "分配号码", code_received: "收到验证码",
    admin_login: "后台登录", admin_login_fail: "登录失败", admin_totp_fail: "动态码错误",
    admin_logout: "退出后台", ip_ban: "IP 封禁", ip_unblock: "IP 解封", ip_block: "IP 封禁",
    cards_create: "创建卡密", card_disable: "禁用卡密", card_enable: "启用卡密", card_delete: "删除卡密",
    numbers_import: "导入号码", number_disable: "禁用号码", number_enable: "启用号码", number_delete: "删除号码",
    settings_save: "修改设置", provider_test: "测试号源", blocked_hit: "拦截封禁 IP",
    redeem_fail: "卡密验证失败", redeem_invalid: "卡密格式错误", poll_error: "取码上游异常",
    admin_setup: "后台初始化", totp_bound: "绑定验证器", setup_fail: "初始化失败",
    allocate_error: "取号异常", unhandled_error: "系统异常", admin_login_step1: "账号验证通过",
    setup_totp_fail: "绑定验证失败", ip_note: "IP 备注", number_retry: "更换号码",
    relay_create: "生成直链卡", relay_from_pool: "号池转直链", relay_reset: "重置直链",
    relay_disable: "禁用直链", relay_enable: "启用直链", relay_delete: "删除直链",
    relay_error: "直链取码异常",
    shop_stock_recover: "回收卡密", shop_stock_order: "现货下单",
    shop_stock_remove: "移除库存", shop_stock_price: "库存改价",
  };
  async function pageDashboard() {
    content.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/overview");
    if (!d.ok) { content.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    const s = d.stats;
    $("providerPill").textContent = "号源 " + ({ pool: "本地号池", sms_activate: "SMS-Activate", fivesim: "5SIM", nexsms_premium: "NexSMS 优选", d1jiema: "d1jiema 国内" }[d.provider] || d.provider);
    const max = Math.max(1, ...d.series.map((x) => x.codes));
    const chart = d.series.map((x) => {
      const hgt = Math.round(x.codes / max * 100);
      return '<div class="col" title="' + esc(x.day) + ' 接码 ' + x.codes + ' / 兑换 ' + x.redemptions + '">' +
        '<span class="cnt">' + (x.codes || "") + "</span>" +
        '<div class="bar" style="height:' + Math.max(2, hgt) + '%"></div>' +
        '<span class="lbl">' + esc(x.day) + "</span></div>";
    }).join("");

    const stock = Object.entries(s.stock || {}).map(([k, v]) =>
      (k === "us" ? "美国" : k === "gb" ? "英国" : "香港") + " " + v).join(" · ");
    const events = (d.recent || []).map((e) =>
      "<tr><td class='mono'>" + fmt(e.ts) + "</td><td>" + badge(e.level, e.level === "alert" ? "violet" : e.level === "warn" ? "warn" : e.level === "error" ? "bad" : "info") +
      "</td><td>" + esc(KIND[e.kind] || e.kind) + "</td><td class='mono'>" + esc(e.ip || "—") + "</td><td class='mono'>" +
      esc((e.detail || "").slice(0, 70)) + "</td></tr>").join("");

    content.innerHTML =
      '<div class="stats">' +
      stat("卡密总数", s.cards_total, "今日新增 " + s.cards_today, "b") +
      stat("可用卡密", s.cards_active, "已核销 " + s.cards_used, "g") +
      stat("今日接码", s.codes_today + " / " + s.codes_total, "累计成功验证码", "g") +
      stat("今日兑换", s.redemptions_today, "取号 " + s.alloc_today + " 次", "b") +
      stat("号池库存", stock || "—", "按地区统计", "w") +
      stat("今日请求", s.events_today, "事件与操作审计", "b") +
      stat("封禁 IP", s.blocked_ips, "累计记录 " + s.ips_total + " 个 IP", s.blocked_ips ? "r" : "") +
      stat("已禁用卡密", s.cards_disabled, "可在卡密管理中恢复", "w") +
      "</div>" +
      '<div class="card"><div class="card-title"><h3>近 14 天接码趋势</h3><span class="hint">按北京时间自然日</span></div>' +
      '<div class="chart">' + chart + "</div></div>" +
      '<div class="card"><div class="card-title"><h3>最近安全事件</h3><a class="btn mini ghost" href="#/audit">查看全部</a></div>' +
      '<div class="tbl-wrap"><table><thead><tr><th>时间</th><th>级别</th><th>事件</th><th>IP</th><th>详情</th></tr></thead><tbody>' +
      (events || "<tr><td colspan=5 class='empty'>暂无记录</td></tr>") + "</tbody></table></div></div>";
  }
  function stat(k, v, s, cls) {
    return '<div class="stat ' + (cls || "") + '"><div class="k">' + esc(k) + '</div><div class="v">' + esc(v) +
      '</div><div class="s">' + esc(s) + "</div></div>";
  }

  async function pageCards() {
    const st = S.pageState.cards = S.pageState.cards || { status: "all", q: "", page: 1 };
    content.innerHTML =
      '<div class="card"><div class="card-title"><h3>生成卡密</h3><span class="hint">默认单次接码 · 链接即授权</span></div>' +
      '<form id="createForm" class="form-grid">' +
      '<label class="fld">数量<input name="count" type="number" min="1" max="500" value="10"></label>' +
      '<label class="fld">每卡可接码次数<input name="max_codes" type="number" min="1" max="20" value="1"></label>' +
      '<label class="fld">有效期（天，0 为永久）<input name="expires_days" type="number" min="0" max="3650" value="0"></label>' +
      '<label class="fld">号段（该链接只能接这一国号码）<select name="choice">' +
      opt("", "不限制（前台自选）") + opt("us", "美国实卡") + opt("gb", "英国") +
      opt("hk", "香港") + opt("cn_real", "国内实卡") + opt("cn_virtual", "国内虚拟卡") +
      "</select></label>" +
      '<label class="fld">备注<input name="note" maxlength="120" placeholder="例如：TG 渠道 / 批次 A"></label>' +
      '<div class="full"><button class="btn primary" type="submit">生成卡密链接</button>' +
      '<span class="help">生成后链接形如 <code id="linkSample">/c/XXXX-XXXX-XXXX-XXXX</code>，打开即自动授权。' +
      ' 需要 <b>卡号:手机号----接码地址</b>（可重复取码、隐藏上游）？<a href="#/relay" style="color:#34e0b0">去生成直链卡 →</a></span></div>' +
      "</form></div>" +
      '<div class="card"><div class="toolbar">' +
      '<select id="cardStatus">' + opt("all", "全部状态", st.status) + opt("active", "可用") + opt("used", "已核销") +
      opt("disabled", "已禁用") + opt("expired", "已过期") + "</select>" +
      '<input id="cardQ" placeholder="搜索卡密前缀 / 备注 / 批次" style="min-width:240px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="cardSearch">搜索</button><div class="spacer"></div>' +
      '<span class="hint" id="cardTotal"></span></div><div class="mt12" id="cardTable"></div></div>';

    $("cardStatus").addEventListener("change", () => { st.status = $("cardStatus").value; st.page = 1; loadCards(); });
    $("cardQ").addEventListener("keydown", (e) => { if (e.key === "Enter") { st.q = $("cardQ").value.trim(); st.page = 1; loadCards(); } });
    $("cardSearch").addEventListener("click", () => { st.q = $("cardQ").value.trim(); st.page = 1; loadCards(); });
    $("linkSample").textContent = location.origin + "/c/XXXX-XXXX-XXXX-XXXX";
    loadCards();
  }

  async function loadCards() {
    const st = S.pageState.cards;
    const box = $("cardTable");
    if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/cards?status=" + encodeURIComponent(st.status) + "&q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=50");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("cardTotal").textContent = "共 " + d.total + " 条";
    const now = d.now || Date.now() / 1000;
    const rows = d.items.map((c) => {
      let stt = c.status, cls = "ok", txt = "可用";
      if (c.used_codes >= c.max_codes) { stt = "used"; }
      if (stt === "used") { cls = "warn"; txt = "已核销"; }
      else if (stt === "disabled") { cls = "bad"; txt = "已禁用"; }
      else if (c.expires_at && c.expires_at < now) { cls = "bad"; txt = "已过期"; }
      const chCode = c.bound_choice || c.choice;
      const chName = ({ us: "美国", gb: "英国", hk: "香港", cn_real: "国内实卡",
        cn_virtual: "国内虚卡" }[chCode] || "未选择") +
        (c.bound_choice ? (c.choice ? " · 已取号" : " · 待取号") : "");
      return "<tr><td class='mono'>#" + c.id + "</td><td class='mono'>" + esc(c.key_prefix) + "…</td><td>" +
        badge(txt, cls) + "</td><td class='mono'>" + c.used_codes + " / " + c.max_codes + "</td><td>" +
        esc(chName) + "</td><td class='mono'>" +
        esc(c.batch_no || "—") + "</td><td>" + esc(c.note || "—") + "</td><td class='mono'>" + fmt(c.created_at) +
        "</td><td class='mono'>" + (c.last_ip ? esc(c.last_ip) : "—") + "</td><td class='mono'>" +
        (c.expires_at ? fmt(c.expires_at) : "永久") + "</td><td>" +
        (stt === "disabled" ? '<button class="btn mini" data-act="card-action" data-op="enable" data-id="' + c.id + '">启用</button> '
          : '<button class="btn mini" data-act="card-action" data-op="disable" data-id="' + c.id + '">禁用</button> ') +
        '<button class="btn mini" data-act="card-action" data-op="reset_choice" data-id="' + c.id + '">重置选择</button> ' +
        '<button class="btn mini danger" data-act="card-action" data-op="delete" data-id="' + c.id + '">删除</button></td></tr>';
    }).join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>卡密</th><th>状态</th><th>次数</th><th>号段</th><th>批次</th><th>备注</th><th>创建时间</th><th>最近 IP</th><th>有效期</th><th>操作</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=10 class='empty'>暂无卡密</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 50);
  }

  async function createCards() {
    const f = $("createForm");
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    const body = {
      count: Number(f.count.value), max_codes: Number(f.max_codes.value),
      expires_days: Number(f.expires_days.value), note: f.note.value.trim(),
      choice: f.choice.value,
    };
    const d = await api("/admin/api/cards/create", { method: "POST", body });
    btn.disabled = false;
    if (!d.ok) { toast(d.error || "生成失败", "bad"); return; }
    const rows = d.keys.map((k, i) => {
      const link = (d.links && d.links[i]) || (location.origin + "/c/" + k);
      return '<div class="keyrow"><span class="k">' + esc(k) + '</span><span class="lnk">' + esc(link) +
        '</span><button class="btn mini" data-act="copy" data-text="' + esc(link) + '">复制链接</button></div>';
    }).join("");
    const all = (d.links && d.links.length ? d.links : d.keys.map((k) => location.origin + "/c/" + k)).join("\n");
    openModal("已生成 " + d.count + " 张卡密 · " + d.batch,
      '<div class="ok-box">卡密仅在此处显示，请立即保存。每张卡默认只能接一次码。</div>' +
      '<div class="mt12"><textarea id="allLinks" style="width:100%;height:70px" readonly>' + esc(all) + "</textarea>" +
      '<button class="btn mini mt12" data-act="copy-all">复制全部链接</button></div>' +
      '<div class="keylist">' + rows + "</div>");
    loadCards();
  }

  async function cardAction(id, op) {
    if (op === "delete" && !confirm("删除后该卡密链接立即失效，确定删除？")) return;
    if (op === "reset_choice" && !confirm("重置后该卡密可重新选择号段并取号（原号码作废），确定？")) return;
    const d = await api("/admin/api/cards/" + id + "/action", { method: "POST", body: { action: op } });
    toast(d.ok ? "操作成功" : (d.error || "操作失败"), d.ok ? "good" : "bad");
    if (d.ok) loadCards();
  }
  async function pageRelay() {
    const st = S.pageState.relay = S.pageState.relay || { status: "all", q: "", page: 1 };
    let base = "https://api.12456.cfd";
    const cfg = await api("/admin/api/settings");
    if (cfg.ok && cfg.settings && cfg.settings.relay_base) base = cfg.settings.relay_base;
    const kwDef = (cfg.ok && cfg.settings && cfg.settings.d1jiema_keyword) || "豆包";
    const pxDef = (cfg.ok && cfg.settings && cfg.settings.d1jiema_sms_prefix) || "[豆包]";
    content.innerHTML =
      '<div class="card" id="relayRecent"><div class="card-title"><h3>最近批次 · 找回</h3>' +
      '<span class="hint">忘记复制也不丢 · 任何一批都能重新导出</span></div>' +
      '<div class="toolbar"><select id="relayBatch" style="min-width:360px"></select>' +
      '<button class="btn primary" data-act="relay-batch-copy">复制这一批</button>' +
      '<button class="btn" data-act="relay-batch-download">下载 txt</button>' +
      '<button class="btn" data-act="relay-batch-preview">查看明细</button>' +
      '<span class="hint" id="relayBatchHint">加载中…</span></div>' +
      '<div class="mt12" id="relayBatchPreview"></div></div>' +
      '<div class="card"><div class="card-title"><h3>生成号码直链卡</h3><span class="hint">系统自动购买号码 · 可反复取码</span></div>' +
      '<form id="relayAllocForm" class="form-grid">' +
      '<label class="fld">号段<select name="choice">' + opt("us", "美国实卡") + opt("gb", "英国") +
      opt("hk", "香港") + opt("cn_real", "国内实卡") + opt("cn_virtual", "国内虚拟卡") + "</select></label>" +
      '<label class="fld">数量<input name="count" type="number" min="1" max="50" value="1"></label>' +
      '<label class="fld">有效期（天，0 为永久）<input name="expires_days" type="number" min="0" max="3650" value="0"></label>' +
      '<label class="fld">项目关键词<input name="sms_keyword" placeholder="豆包 / TikTok / 平台名" value="' + esc(kwDef) + '"></label>' +
      '<label class="fld">短信前缀（只收以此开头，可空）<input name="sms_prefix" placeholder="[豆包]" value="' + esc(pxDef) + '"></label>' +
      '<label class="fld">备注<input name="note" maxlength="120" placeholder="渠道 / 批次"></label>' +
      '<div class="full"><button class="btn primary" type="submit">购买并生成直链卡</button>' +
      '<span class="help">会真实向上游购买号码（产生费用），生成 <code>卡号:+手机号----' + esc(base) +
      '/smsrecord?token=…</code>，买家看不到上游域名。</span></div>' +
      "</form></div>" +
      '<div class="card"><div class="card-title"><h3>已有号码导入（可选）</h3><span class="hint">不买号 · 只把上游地址换成自己的域名</span></div>' +
      '<form id="relayForm" class="form-grid">' +
      '<label class="fld full">粘贴上游号码（每行一条，必填）<textarea name="lines" rows="6" placeholder="每行一条，例如：&#10;卡号:+8613800000000----https://你的上游地址/smsrecord?token=你的token"></textarea></label>' +
      '<label class="fld">有效期（天，0 为永久）<input name="expires_days" type="number" min="0" max="3650" value="0"></label>' +
      '<label class="fld">项目关键词（可空）<input name="sms_keyword" placeholder="' + esc(kwDef) + '"></label>' +
      '<label class="fld">短信前缀（可空）<input name="sms_prefix" placeholder="' + esc(pxDef) + '"></label>' +
      '<label class="fld">备注<input name="note" maxlength="120" placeholder="渠道 / 批次"></label>' +
      '<div class="full"><button class="btn primary" type="submit">生成直链卡</button>' +
      '<span class="help">生成后统一替换为 <code>' + esc(base) + '/smsrecord?token=…</code>，买家看不到上游域名；同一链接可反复取码。</span></div>' +
      "</form></div>" +
      '<div class="card"><div class="card-title"><h3>从号池转直链</h3><span class="hint">把已购号码变成可重复取码的直链</span></div>' +
      '<form id="relayPoolForm" class="form-grid">' +
      '<label class="fld">数量<input name="count" type="number" min="1" max="200" value="10"></label>' +
      '<label class="fld">地区<select name="country"><option value="">全部</option><option value="us">美国</option><option value="gb">英国</option><option value="hk">香港</option><option value="cn">中国</option></select></label>' +
      '<div class="full"><button class="btn" type="submit">从号池生成</button></div></form></div>' +
            '<div class="card"><div class="toolbar">' +
      '<select id="relayStatus">' + opt("all", "全部状态", st.status) + opt("active", "可用") +
      opt("disabled", "已禁用") + opt("expired", "已过期") + "</select>" +
      '<input id="relayQ" placeholder="搜索手机号 / 备注 / token" style="min-width:240px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="relaySearch">搜索</button><div class="spacer"></div>' +
      '<button class="btn mini" data-act="relay-export">导出全部直链</button>' +
      '<span class="hint" id="relayTotal"></span></div><div class="mt12" id="relayTable"></div></div>';
    $("relayStatus").addEventListener("change", () => { st.status = $("relayStatus").value; st.page = 1; loadRelay(); });
    $("relayQ").addEventListener("keydown", (e) => { if (e.key === "Enter") { st.q = $("relayQ").value.trim(); st.page = 1; loadRelay(); } });
    $("relaySearch").addEventListener("click", () => { st.q = $("relayQ").value.trim(); st.page = 1; loadRelay(); });
    $("relayBatch").addEventListener("change", () => loadRelayBatches($("relayBatch").value));
    loadRelay();
    loadRelayBatches();
  }

  async function loadRelayBatches(keep) {
    const sel = $("relayBatch");
    if (!sel) return;
    const d = await api("/admin/api/relay/batches");
    if (!d.ok) { const h = $("relayBatchHint"); if (h) h.textContent = "批次列表加载失败"; return; }
    const items = d.items || [];
    const last = localStorage.getItem("relay_last_batch") || "";
    sel.innerHTML = items.length ? items.map((b) =>
      '<option value="' + b.id + '">' + (String(b.id) === last ? "★ 刚生成 · " : "") + esc(b.batch_no) +
      " · " + b.live + " 条 · " + fmt(b.created_at) + (b.note ? " · " + esc(b.note) : "") + "</option>").join("")
      : '<option value="">暂无批次</option>';
    const want = keep == null ? sel.value : String(keep);
    if (want && Array.from(sel.options).some((o) => o.value === want)) sel.value = want;
    const cur = items.find((b) => String(b.id) === sel.value);
    const hint = $("relayBatchHint");
    if (hint) hint.textContent = cur ? ("当前这批 " + cur.live + " 条，共 " + items.length + " 批") : "暂无批次";
  }

  async function relayBatchPreview() {
    const sel = $("relayBatch");
    const box = $("relayBatchPreview");
    const id = sel ? sel.value : "";
    if (!box) return;
    if (!id) { box.innerHTML = '<div class="empty">还没有可找回的批次</div>'; return; }
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/relay/export?batch=" + encodeURIComponent(id));
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error || "加载失败") + "</div>"; return; }
    const lines = String(d.text || "").split("\n").filter(Boolean);
    if (!lines.length) { box.innerHTML = '<div class="empty">这一批的卡已经全部删除了</div>'; return; }
    const all = lines.join("\n");
    box.innerHTML = '<textarea readonly style="width:100%;height:150px" spellcheck="false">' + esc(all) + "</textarea>" +
      '<div class="toolbar mt12"><button class="btn mini" data-act="copy" data-text="' + esc(all) + '">复制全部（' +
      lines.length + " 条）</button><button class=\"btn mini\" data-act=\"relay-batch-download\">下载 txt</button>" +
      '<span class="hint">关掉这个页面也能随时回来找回</span></div>';
  }

  async function relayBatchExport(mode) {
    const sel = $("relayBatch");
    const id = sel ? sel.value : "";
    if (!id) { toast("还没有可导出的批次", "bad"); return; }
    const d = await api("/admin/api/relay/export?batch=" + encodeURIComponent(id));
    if (!d.ok) { toast(d.error || "导出失败", "bad"); return; }
    if (!d.count) { toast("这一批的卡已经全部删除了", "bad"); return; }
    if (mode === "copy") copy(d.text || "");
    else { downloadText("relay-batch-" + id + ".txt", d.text || ""); toast("已下载 " + d.count + " 条", "good"); }
  }

  async function loadRelay() {
    const st = S.pageState.relay;
    const box = $("relayTable");
    if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/relay?status=" + encodeURIComponent(st.status) + "&q=" +
      encodeURIComponent(st.q) + "&page=" + st.page + "&size=50");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("relayTotal").textContent = "共 " + d.total + " 条";
    const now = d.now || Date.now() / 1000;
    const rows = d.items.map((c) => {
      let cls = "ok", txt = "可用";
      if (c.status === "disabled") { cls = "bad"; txt = "已禁用"; }
      else if (c.expires_at && c.expires_at < now) { cls = "bad"; txt = "已过期"; }
      const tcls = c.card_type === "续费卡" ? "bad" : (c.card_type === "首次卡" ? "ok" : "warn");
      return "<tr><td class='mono'>#" + c.id + "</td><td class='mono'>" + esc(c.phone || "—") +
        "</td><td>" + (c.card_type ? badge(c.card_type, tcls) : '<span class="hint">—</span>') +
        "</td><td class='mono'>" + esc(c.token_prefix || "—") + "</td><td>" + badge(txt, cls) +
        "</td><td class='mono'>" + (c.reads || 0) + "</td><td class='mono'>" +
        (c.last_read_at ? fmt(c.last_read_at) : "—") + "</td><td class='mono'>" +
        (c.last_ip ? esc(c.last_ip) : "—") + "</td><td class='mono'>" + esc(c.upstream_host || "—") +
        "</td><td>" + esc(c.note || "—") + "</td><td class='mono'>" +
        (c.expires_at ? fmt(c.expires_at) : "永久") + "</td><td>" +
        '<button class="btn mini" data-act="copy" data-text="' + esc(c.line) + '">复制</button> ' +
        (c.status === "disabled"
          ? '<button class="btn mini" data-act="relay-action" data-op="enable" data-id="' + c.id + '">启用</button> '
          : '<button class="btn mini" data-act="relay-action" data-op="disable" data-id="' + c.id + '">禁用</button> ') +
        '<button class="btn mini" data-act="relay-action" data-op="reset" data-id="' + c.id + '">重置</button> ' +
        '<button class="btn mini danger" data-act="relay-action" data-op="delete" data-id="' + c.id + '">删除</button></td></tr>';
    }).join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>手机号</th><th>类型</th><th>Token</th><th>状态</th>' +
      "<th>读取</th><th>最近读取</th><th>最近 IP</th><th>上游</th><th>备注</th><th>有效期</th><th>操作</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan=12 class='empty'>暂无直链卡</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 50);
  }

  function relayResult(d) {
    const all = (d.lines || []).join("\n");
    S.lastRelay = { batch: d.batch || "", lines: all, count: d.count || 0 };
    try { if (all) setTimeout(() => copy(all), 150); } catch (e) {}
    openModal("已生成 " + d.count + " 条直链卡" + (d.batch ? " · " + d.batch : ""),
      '<div class="ok-box">买家拿到的是你自己的域名，上游地址只保存在服务器；同一链接可一直取码，除非禁用或过期。' +
      '<br>已自动复制到剪贴板；就算关掉这个窗口也不丢 —— 直链卡页顶部「最近批次 · 找回」可以随时把这一批重新导出来。</div>' +
      '<div class="mt12"><textarea id="allLinks" style="width:100%;height:150px" readonly>' + esc(all) + "</textarea>" +
      '<button class="btn mini mt12" data-act="copy-all">复制全部（' + (d.count || 0) + " 条）</button> " +
      '<button class="btn mini mt12" id="relayDownload">下载 txt</button> ' +
      '<button class="btn mini mt12" data-act="relay-batch-download">从批次再下载</button></div>' +
      (d.bad && d.bad.length ? '<div class="mt12 hint">跳过 ' + d.bad.length + " 行：" +
        esc(d.bad.map((x) => (x.line ? "第" + x.line + "行 " : (x.phone || "") + " ") + x.why).join("；")) + "</div>" : ""), true);
    const dl = $("relayDownload");
    if (dl) dl.addEventListener("click", () => downloadText("relay-cards.txt", all));
    loadRelayBatches().then(() => {
      const sel = $("relayBatch");
      if (sel && sel.value) localStorage.setItem("relay_last_batch", sel.value);
    });
  }

  async function allocateRelay() {
    const f = $("relayAllocForm");
    const n = Math.max(1, Math.min(50, Number(f.count.value) || 1));
    const name = ({ us: "美国", gb: "英国", hk: "香港", cn_real: "国内实卡", cn_virtual: "国内虚拟卡" }[f.choice.value]) || f.choice.value;
    if (!confirm("将真实购买 " + n + " 个" + name + "号码并生成直链卡，可能产生费用，确定？")) return;
    const btn = f.querySelector("button[type=submit]");
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "购买中…（最多 1 分钟）";
    const d = await api("/admin/api/relay/allocate", { method: "POST", body: {
      choice: f.choice.value, count: n,
      expires_days: Number(f.expires_days.value), note: f.note.value.trim(),
      sms_keyword: f.sms_keyword ? f.sms_keyword.value.trim() : "",
      sms_prefix: f.sms_prefix ? f.sms_prefix.value.trim() : "",
    } });
    btn.disabled = false;
    btn.textContent = old;
    if (!d.ok) { toast(d.error || "生成失败", "bad"); return; }
    relayResult(d);
    loadRelay();
  }

  async function createRelay() {
    const f = $("relayForm");
    const raw = (f.lines.value || "").trim();
    if (!raw) {
      toast("输入框是空的：请粘贴 卡号:手机号----接码地址（灰色文字只是示例）", "bad");
      f.lines.focus();
      return;
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    const d = await api("/admin/api/relay/create", { method: "POST", body: {
      lines: raw, note: f.note.value.trim(), expires_days: Number(f.expires_days.value),
      sms_keyword: f.sms_keyword ? f.sms_keyword.value.trim() : "",
      sms_prefix: f.sms_prefix ? f.sms_prefix.value.trim() : "",
    } });
    btn.disabled = false;
    if (!d.ok) { toast(d.error || "生成失败", "bad"); return; }
    relayResult(d);
    loadRelay();
  }

  async function relayFromPool(f) {
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    const d = await api("/admin/api/relay/from_pool", { method: "POST", body: {
      count: Number(f.count.value), country: f.country.value,
    } });
    btn.disabled = false;
    if (!d.ok) { toast(d.error || "生成失败", "bad"); return; }
    relayResult(d);
    loadRelay();
  }

  async function relayAction(id, op) {
    if (op === "delete" && !confirm("删除后该直链立即失效，确定删除？")) return;
    if (op === "reset" && !confirm("重置后旧 token 立刻失效，买家需要换新链接，确定？")) return;
    const d = await api("/admin/api/relay/" + id + "/action", { method: "POST", body: { action: op } });
    toast(d.ok ? "操作成功" : (d.error || "操作失败"), d.ok ? "good" : "bad");
    if (d.ok) { if (op === "reset" && d.line) copy(d.line); loadRelay(); }
  }

  async function relayExport() {
    const d = await api("/admin/api/relay/export");
    if (!d.ok) { toast(d.error || "导出失败", "bad"); return; }
    downloadText("relay-cards-all.txt", d.text || "");
    toast("已导出 " + d.count + " 条", "good");
  }

  function downloadText(name, text) {
    const blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  async function pageNumbers() {
    const st = S.pageState.numbers = S.pageState.numbers || { status: "all", country: "all", q: "", page: 1 };
    content.innerHTML =
      '<div class="card"><div class="card-title"><h3>导入号码</h3><span class="hint">支持 217api / 通用接码 URL</span></div>' +
      '<form id="importForm" class="form-grid">' +
      '<label class="fld">地区<select name="country"><option value="us">美国 +1</option><option value="gb">英国 +44</option><option value="hk">香港 +852</option></select></label>' +
      '<label class="fld">备注<input name="note" maxlength="80" placeholder="批次 / 渠道"></label>' +
      '<label class="fld full">号码与接码地址（每行一条）<textarea name="lines" rows="6" placeholder="+1 2025550123----http://217api.com:9900/api/sms/getcode?token=xxxxxxxx&#10;+44 7700900123|http://example.com/sms?token=yyyy"></textarea></label>' +
      '<div class="full"><button class="btn primary" type="submit">导入号码</button>' +
      '<span class="help">分隔符支持 <code>----</code>、<code>|</code>、逗号或制表符。接码地址按原样轮询，返回 <code>0|0</code> 或 <code>1|123456</code> 均可识别。</span></div>' +
      "</form></div>" +
      '<div class="card"><div class="toolbar">' +
      '<select id="numStatus">' + opt("all", "全部状态", st.status) + opt("available", "可用") + opt("allocated", "已分配") +
      opt("used", "已接码") + opt("expired", "已超时") + opt("disabled", "已禁用") + "</select>" +
      '<select id="numCountry">' + opt("all", "全部地区", st.country) + opt("us", "美国") + opt("gb", "英国") + opt("hk", "香港") + "</select>" +
      '<input id="numQ" placeholder="搜索号码 / 备注" style="min-width:200px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="numSearch">搜索</button><div class="spacer"></div><span class="hint" id="numTotal"></span></div>' +
      '<div class="mt12" id="numTable"></div></div>';

    $("importForm").addEventListener("submit", (e) => { e.preventDefault(); });
    ["numStatus", "numCountry"].forEach((id) => $(id).addEventListener("change", () => {
      st.status = $("numStatus").value; st.country = $("numCountry").value; st.page = 1; loadNumbers();
    }));
    $("numSearch").addEventListener("click", () => { st.q = $("numQ").value.trim(); st.page = 1; loadNumbers(); });
    loadNumbers();
  }

  async function importNumbers() {
    const f = $("importForm");
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    const d = await api("/admin/api/numbers/import", { method: "POST", body: {
      country: f.country.value, lines: f.lines.value, note: f.note.value.trim() } });
    btn.disabled = false;
    if (!d.ok) { toast(d.error || "导入失败", "bad"); return; }
    toast("导入成功 " + d.added + " 条，跳过 " + d.skipped + " 条", "good");
    f.lines.value = "";
    loadNumbers();
  }

  async function loadNumbers() {
    const st = S.pageState.numbers;
    const box = $("numTable");
    if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/numbers?status=" + encodeURIComponent(st.status) + "&country=" + encodeURIComponent(st.country) +
      "&q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=50");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("numTotal").textContent = "共 " + d.total + " 条";
    const rows = d.items.map((n) => {
      const map = { available: ["ok", "可用"], allocated: ["info", "已分配"], used: ["warn", "已接码"],
        expired: ["bad", "已超时"], disabled: ["bad", "已禁用"] };
      const m = map[n.status] || ["", n.status];
      const tcls = n.card_type === "续费卡" ? "bad" : (n.card_type === "首次卡" ? "ok" : "warn");
      return "<tr><td class='mono'>#" + n.id + "</td><td class='mono'>" + esc(n.phone) + "</td><td>" +
        (n.country === "us" ? "美国" : n.country === "gb" ? "英国" : "香港") + "</td><td>" +
        (n.card_type ? badge(n.card_type, tcls) : '<span class="hint">—</span>') + "</td><td>" +
        badge(m[1], m[0]) + "</td><td class='mono'>" + esc(n.provider) + "</td><td class='mono'>" +
        (n.card_id ? "#" + n.card_id : "—") + "</td><td class='mono'>" + (n.codes || 0) + "</td><td class='mono'>" +
        (n.allocated_at ? fmt(n.allocated_at) : "—") + "</td><td>" +
        (n.status === "disabled"
          ? '<button class="btn mini" data-act="num-action" data-op="enable" data-id="' + n.id + '">启用</button> '
          : '<button class="btn mini" data-act="num-action" data-op="disable" data-id="' + n.id + '">禁用</button> ') +
        '<button class="btn mini danger" data-act="num-action" data-op="delete" data-id="' + n.id + '">删除</button></td></tr>';
    }).join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>号码</th><th>地区</th><th>类型</th><th>状态</th><th>号源</th><th>绑定卡密</th><th>接码数</th><th>分配时间</th><th>操作</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=10 class='empty'>号码池为空，请先导入</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 50);
  }

  async function numAction(id, op) {
    if (op === "delete" && !confirm("确定删除该号码？")) return;
    const d = await api("/admin/api/numbers/" + id + "/action", { method: "POST", body: { action: op } });
    toast(d.ok ? "操作成功" : (d.error || "操作失败"), d.ok ? "good" : "bad");
    if (d.ok) loadNumbers();
  }

  async function pageRedemptions() {
    const st = S.pageState.redemptions = S.pageState.redemptions || { result: "all", q: "", page: 1 };
    content.innerHTML = '<div class="card"><div class="toolbar">' +
      '<select id="rdResult">' + opt("all", "全部结果", st.result) + opt("ok", "成功") + opt("fail", "失败") + "</select>" +
      '<input id="rdQ" placeholder="搜索 IP / 卡密前缀" style="min-width:220px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="rdSearch">搜索</button><div class="spacer"></div><span class="hint" id="rdTotal"></span></div>' +
      '<div class="mt12" id="rdTable"></div></div>';
    $("rdResult").addEventListener("change", () => { st.result = $("rdResult").value; st.page = 1; loadRedemptions(); });
    $("rdSearch").addEventListener("click", () => { st.q = $("rdQ").value.trim(); st.page = 1; loadRedemptions(); });
    loadRedemptions();
  }
  async function loadRedemptions() {
    const st = S.pageState.redemptions;
    const box = $("rdTable"); if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/redemptions?result=" + encodeURIComponent(st.result) + "&q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=50");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("rdTotal").textContent = "共 " + d.total + " 条";
    const rows = d.items.map((r) =>
      "<tr><td class='mono'>#" + r.id + "</td><td class='mono'>" + esc(r.key_prefix || "—") + "…</td><td>" +
      badge(r.result === "ok" ? "成功" : "失败", r.result === "ok" ? "ok" : "bad") + "</td><td class='mono'>" +
      esc(r.ip || "—") + "</td><td class='mono'>" + fmt(r.created_at) + "</td><td class='mono'>" +
      esc((r.detail || "").slice(0, 40)) + "</td><td class='mono small'>" + esc((r.ua || "").slice(0, 48)) + "</td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>卡密</th><th>结果</th><th>IP</th><th>时间</th><th>详情</th><th>UA</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=7 class='empty'>暂无记录</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 50);
  }

  async function pageCodes() {
    const st = S.pageState.codes = S.pageState.codes || { status: "all", q: "", page: 1 };
    content.innerHTML = '<div class="card"><div class="toolbar">' +
      '<select id="cdStatus">' + opt("all", "全部状态", st.status) + opt("received", "已收到") + opt("waiting", "等待中") +
      opt("expired", "已超时") + "</select>" +
      '<input id="cdQ" placeholder="搜索号码 / 验证码 / 卡密" style="min-width:240px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="cdSearch">搜索</button><div class="spacer"></div><span class="hint" id="cdTotal"></span></div>' +
      '<div class="mt12" id="cdTable"></div></div>';
    $("cdStatus").addEventListener("change", () => { st.status = $("cdStatus").value; st.page = 1; loadCodes(); });
    $("cdSearch").addEventListener("click", () => { st.q = $("cdQ").value.trim(); st.page = 1; loadCodes(); });
    loadCodes();
  }
  async function loadCodes() {
    const st = S.pageState.codes;
    const box = $("cdTable"); if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/codes?status=" + encodeURIComponent(st.status) + "&q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=50");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("cdTotal").textContent = "共 " + d.total + " 条";
    const rows = d.items.map((r) => {
      const map = { received: ["ok", "已收到"], waiting: ["info", "等待中"], expired: ["bad", "已超时"] };
      const m = map[r.status] || ["", r.status];
      return "<tr><td class='mono'>#" + r.id + "</td><td class='mono'>" + esc(r.phone) + "</td><td>" +
        (r.country === "us" ? "🇺🇸" : r.country === "gb" ? "🇬🇧" : "🇭🇰") + "</td><td>" + badge(m[1], m[0]) +
        "</td><td class='mono'>" + (r.code ? '<b style="color:#8dffdd;font-size:13px">' + esc(r.code) + "</b> " +
          '<button class="btn mini" data-act="copy" data-text="' + esc(r.code) + '">复制</button>' : "—") +
        "</td><td class='mono'>" + esc(r.key_prefix || "—") + "…</td><td class='mono'>" + (r.polls || 0) +
        "</td><td class='mono'>" + esc(r.provider || "pool") + "</td><td class='mono'>" + fmt(r.created_at) +
        "</td><td class='mono'>" + (r.got_at ? fmt(r.got_at) : "—") + "</td></tr>";
    }).join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>号码</th><th>地区</th><th>状态</th><th>验证码</th><th>卡密</th><th>轮询</th><th>号源</th><th>创建</th><th>收到</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=10 class='empty'>暂无接码记录</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 50);
  }
  async function pageUsage() {
    const st = S.pageState.usage = S.pageState.usage || { q: "", page: 1 };
    content.innerHTML = '<div class="card"><div class="toolbar">' +
      '<input id="usQ" placeholder="搜索 IP / 卡密前缀" style="min-width:240px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="usSearch">搜索</button><div class="spacer"></div><span class="hint" id="usTotal"></span></div>' +
      '<div class="mt12" id="usTable"></div></div>';
    $("usSearch").addEventListener("click", () => { st.q = $("usQ").value.trim(); st.page = 1; loadUsage(); });
    loadUsage();
  }
  async function loadUsage() {
    const st = S.pageState.usage;
    const box = $("usTable"); if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/usage?q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=50");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("usTotal").textContent = "共 " + d.total + " 条";
    const now = d.now || Date.now() / 1000;
    const rows = d.items.map((r) =>
      "<tr><td class='mono'>" + esc(r.key_prefix) + "…</td><td class='mono'>" + esc(r.phone || "—") +
      "</td><td>" + (r.used_codes >= r.max_codes ? badge("已核销", "warn") : badge("进行中", "ok")) +
      "</td><td class='mono'>" + esc(r.ip || "—") + "</td><td class='mono'>" + fmt(r.created_at) +
      "</td><td class='mono'>" + fmt(r.last_seen) + "</td><td class='mono'>" +
      (r.expires_at > now ? Math.ceil((r.expires_at - now) / 60) + " 分钟" : "已过期") +
      "</td><td class='mono small'>" + esc((r.ua || "").slice(0, 46)) + "</td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>卡密</th><th>号码</th><th>状态</th><th>IP</th><th>会话建立</th><th>最后活动</th><th>剩余</th><th>UA</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=8 class='empty'>暂无会话</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 50);
  }

  async function pageAudit() {
    const st = S.pageState.audit = S.pageState.audit || { level: "all", kind: "", q: "", page: 1 };
    content.innerHTML = '<div class="card"><div class="toolbar">' +
      '<select id="auLevel">' + opt("all", "全部级别", st.level) + opt("info", "信息") + opt("warn", "警告") +
      opt("alert", "重要") + opt("error", "异常") + "</select>" +
      '<input id="auKind" placeholder="事件类型，如 login" style="width:170px" value="' + esc(st.kind) + '">' +
      '<input id="auQ" placeholder="搜索 IP / 操作者 / 详情" style="min-width:220px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="auSearch">筛选</button><div class="spacer"></div><span class="hint" id="auTotal"></span></div>' +
      '<div class="mt12" id="auTable"></div></div>';
    $("auLevel").addEventListener("change", () => { st.level = $("auLevel").value; st.page = 1; loadAudit(); });
    $("auSearch").addEventListener("click", () => {
      st.kind = $("auKind").value.trim(); st.q = $("auQ").value.trim(); st.page = 1; loadAudit();
    });
    loadAudit();
  }
  async function loadAudit() {
    const st = S.pageState.audit;
    const box = $("auTable"); if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/audit?level=" + encodeURIComponent(st.level) + "&kind=" + encodeURIComponent(st.kind) +
      "&q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=60");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("auTotal").textContent = "共 " + d.total + " 条";
    const rows = d.items.map((e) =>
      "<tr><td class='mono'>" + fmt(e.ts) + "</td><td>" + badge(e.level, e.level === "alert" ? "violet" : e.level === "warn" ? "warn" : e.level === "error" ? "bad" : "info") +
      "</td><td>" + esc(KIND[e.kind] || e.kind) + "</td><td class='mono'>" + esc(e.ip || "—") + "</td><td class='mono'>" +
      esc(e.actor || "—") + "</td><td class='mono'>" + (e.card_id ? "#" + e.card_id : "—") + "</td><td class='mono small'>" +
      esc((e.detail || "").slice(0, 110)) + "</td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>时间</th><th>级别</th><th>事件</th><th>IP</th><th>操作者</th><th>卡密</th><th>详情</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=7 class='empty'>暂无事件</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 60);
  }

  async function pageIps() {
    const st = S.pageState.ips = S.pageState.ips || { state: "all", q: "", page: 1 };
    content.innerHTML =
      '<div class="card"><div class="card-title"><h3>手动封禁</h3><span class="hint">支持单个 IP 或 CIDR 段</span></div>' +
      '<form id="blockForm" class="toolbar">' +
      '<input name="ip" placeholder="1.2.3.4 或 1.2.3.0/24" style="min-width:210px">' +
      '<input name="minutes" type="number" min="1" value="60" style="width:100px" title="封禁分钟数">' +
      '<input name="reason" placeholder="原因" style="min-width:180px">' +
      '<button class="btn danger" type="submit">封禁</button><div class="spacer"></div>' +
      '<span class="help">自动风控：登录失败 5 次 / 10 分钟 → 封禁 30 分钟。</span></form></div>' +
      '<div class="card"><div class="toolbar">' +
      '<select id="ipState">' + opt("all", "全部 IP", st.state) + opt("blocked", "已封禁") + opt("clean", "正常") + "</select>" +
      '<input id="ipQ" placeholder="搜索 IP" style="min-width:190px" value="' + esc(st.q) + '">' +
      '<button class="btn" id="ipSearch">搜索</button><div class="spacer"></div><span class="hint" id="ipTotal"></span></div>' +
      '<div class="mt12" id="ipTable"></div></div>';
    $("ipState").addEventListener("change", () => { st.state = $("ipState").value; st.page = 1; loadIps(); });
    $("ipSearch").addEventListener("click", () => { st.q = $("ipQ").value.trim(); st.page = 1; loadIps(); });
    loadIps();
  }
  async function loadIps() {
    const st = S.pageState.ips;
    const box = $("ipTable"); if (!box) return;
    box.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/ips?state=" + encodeURIComponent(st.state) + "&q=" + encodeURIComponent(st.q) + "&page=" + st.page + "&size=60");
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    $("ipTotal").textContent = "共 " + d.total + " 个 IP";
    const rows = d.items.map((i) =>
      "<tr><td class='mono'>" + esc(i.ip) + "</td><td>" + (i.blocked ? badge("封禁中", "bad") : badge("正常", "ok")) +
      "</td><td class='mono'>" + i.req_count + "</td><td class='mono'>" + i.fail_count + "</td><td class='mono'>" + fmt(i.last_seen) +
      "</td><td class='mono'>" + (i.blocked ? Math.ceil(i.blocked_for / 60) + " 分钟" : "—") + "</td><td class='mono'>" +
      esc(i.block_reason || "—") + "</td><td>" +
      (i.blocked ? '<button class="btn mini" data-act="unblock-ip" data-ip="' + esc(i.ip) + '">解封</button>'
        : '<button class="btn mini danger" data-act="block-ip" data-ip="' + esc(i.ip) + '">封禁</button>') + "</td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>IP</th><th>状态</th><th>请求</th><th>失败</th><th>最近访问</th><th>剩余</th><th>原因</th><th>操作</th></tr></thead><tbody>' +
      (rows || "<tr><td colspan=8 class='empty'>暂无 IP 记录</td></tr>") + "</tbody></table></div>" + pager(d.total, st.page, 60);
  }
  function blockIp(ip) {
    openModal("封禁 IP", '<div class="kv"><span class="k">目标</span><span class="v">' + esc(ip) + "</span></div>" +
      '<div class="form-grid mt12"><label class="fld">时长（分钟）<input id="biMin" type="number" min="1" value="60"></label>' +
      '<label class="fld">原因<input id="biReason" value="manual"></label></div>' +
      '<button class="btn danger mt12" id="biGo">确认封禁</button>');
    $("biGo").addEventListener("click", async () => {
      const d = await api("/admin/api/ips/action", { method: "POST", body: {
        ip, action: "block", minutes: Number($("biMin").value), reason: $("biReason").value } });
      toast(d.ok ? "已封禁 " + ip : (d.error || "失败"), d.ok ? "good" : "bad");
      if (d.ok) { closeModal(); loadIps(); }
    });
  }
  async function blockIpSubmit() {
    const f = $("blockForm");
    const d = await api("/admin/api/ips/action", { method: "POST", body: {
      ip: f.ip.value.trim(), action: "block", minutes: Number(f.minutes.value), reason: f.reason.value.trim() } });
    toast(d.ok ? "已封禁" : (d.error || "失败"), d.ok ? "good" : "bad");
    if (d.ok) { f.reset(); loadIps(); }
  }
  async function ipAction(ip, action) {
    const d = await api("/admin/api/ips/action", { method: "POST", body: { ip, action } });
    toast(d.ok ? "操作成功" : (d.error || "失败"), d.ok ? "good" : "bad");
    if (d.ok) loadIps();
  }

  async function pageSettings() {
    content.innerHTML = '<div class="loading">加载中…</div>';
    const d = await api("/admin/api/settings");
    if (!d.ok) { content.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    const s = d.settings;
    const sel = (v, cur) => (v === cur ? " selected" : "");
    content.innerHTML =
      '<form id="settingsForm" class="card">' +
      '<div class="card-title"><h3>站点信息</h3><span class="hint">展示在前台与卡密链接中</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">站点名称<input name="site_name" value="' + esc(s.site_name) + '"></label>' +
      '<label class="fld">站点地址（生成卡密链接用）<input name="site_url" value="' + esc(s.site_url || "") + '" placeholder="https://12456.cfd"></label>' +
      '<label class="fld">购买号码链接（官网按钮跳转）<input name="shop_url" value="' + esc(s.shop_url || "https://wzyp.cn/shop/63EZ1CJK") + '" placeholder="https://wzyp.cn/shop/..."></label>' +
      '<label class="fld">直链卡域名（生成 卡号:手机号----接码地址 用）<input name="relay_base" value="' + esc(s.relay_base || "https://api.12456.cfd") + '" placeholder="https://api.12456.cfd"></label>' +
      '<label class="fld">直链每 token 每分钟上限<input name="relay_rate" type="number" min="1" max="6000" value="' + esc(s.relay_rate || "60") + '"></label>' +
      '<label class="fld">直链每 IP 每分钟上限<input name="relay_ip_rate" type="number" min="1" max="20000" value="' + esc(s.relay_ip_rate || "600") + '"></label>' +
      '<label class="fld">无效 token 每 IP 每分钟上限<input name="relay_bad_rate" type="number" min="1" max="20000" value="' + esc(s.relay_bad_rate || "300") + '"></label>' +
      '<label class="fld full">前台提示文案<input name="site_note" value="' + esc(s.site_note) + '"></label>' +
      "</div>" +
      '<div class="card-title" style="margin-top:20px"><h3>号源配置</h3><span class="hint">决定用户取号来源</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">号源类型<select name="provider">' +
      '<option value="pool"' + sel("pool", s.provider) + ">本地号池（217api / 通用 URL）</option>" +
      '<option value="sms_activate"' + sel("sms_activate", s.provider) + ">SMS-Activate（按需购号）</option>" +
      '<option value="fivesim"' + sel("fivesim", s.provider) + ">5SIM（按需购号）</option>" +
      '<option value="nexsms_premium"' + sel("nexsms_premium", s.provider) + ">NexSMS 优选（豆包 / 按需购号）</option>" +
      "</select></label>" +
      '<label class="fld">开放地区（国际）<select name="countries" multiple size="3">' +
      ["us", "gb", "hk"].map((c) => '<option value="' + c + '"' +
        ((s.allow_countries || "").split(",").includes(c) ? " selected" : "") + ">" +
        (c === "us" ? "美国 +1" : c === "gb" ? "英国 +44" : "香港 +852") + "</option>").join("") +
      "</select></label>" +
      '<label class="fld">开放号段（前台可选，选定即锁）<select name="choices" multiple size="5">' +
      [["us", "美国 +1"], ["gb", "英国 +44"], ["hk", "香港 +852"],
       ["cn_real", "国内 · 实卡"], ["cn_virtual", "国内 · 虚卡"]].map((x) => '<option value="' + x[0] + '"' +
        ((s.allow_choices || "").split(",").includes(x[0]) ? " selected" : "") + ">" + x[1] + "</option>").join("") +
      "</select></label>" +
      '<label class="fld">短信轮询间隔（秒）<input name="poll_interval" type="number" min="5" max="120" value="' + esc(s.poll_interval) + '"></label>' +
      '<label class="fld">号码有效期（分钟）<input name="number_ttl_min" type="number" min="2" max="180" value="' + esc(s.number_ttl_min) + '"></label>' +
      '<label class="fld">每卡最多换号次数（含首次，建议 3）<input name="max_alloc_attempts" type="number" min="1" max="10" value="' + esc(s.max_alloc_attempts || "3") + '"></label>' +
      '<label class="fld">自动封禁<select name="auto_ban_enabled"><option value="1"' + (s.auto_ban_enabled === "1" ? " selected" : "") + ">开启</option><option value=\"0\"" + (s.auto_ban_enabled === "0" ? " selected" : "") + ">关闭</option></select></label>" +
      '<label class="fld">核销后退出倒计时（秒，0=立即）<input name="exit_grace_sec" type="number" min="0" max="60" value="' + esc(s.exit_grace_sec || "8") + '"></label>' +
      '<label class="fld full">后台登录 IP 白名单（留空=不限制，逗号分隔，支持 CIDR）<input name="admin_ip_allow" value="' + esc(s.admin_ip_allow || "") + '" placeholder="例如 1.2.3.4,10.0.0.0/8"></label>' +
      "</div>" +
      '<div class="card-title" style="margin-top:20px"><h3>SMS-Activate</h3><span class="hint">留空表示不修改</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">API Key<input name="sms_activate_key" type="password" placeholder="' + (s.sms_activate_key_set ? "已设置：" + esc(s.sms_activate_key_hint) : "未设置") + '"></label>' +
      '<label class="fld">服务代码（go / wa / tg…）<input name="sms_activate_service" value="' + esc(s.sms_activate_service) + '"></label>' +
      "</div>" +
      '<div class="card-title" style="margin-top:20px"><h3>5SIM</h3><span class="hint">留空表示不修改</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">API Key<input name="fivesim_key" type="password" placeholder="' + (s.fivesim_key_set ? "已设置：" + esc(s.fivesim_key_hint) : "未设置") + '"></label>' +
      '<label class="fld">产品（any / google…）<input name="fivesim_product" value="' + esc(s.fivesim_product) + '"></label>' +
      '<label class="fld">运营商<input name="fivesim_operator" value="' + esc(s.fivesim_operator) + '"></label>' +
      "</div>" +
      '<div class="card-title" style="margin-top:20px"><h3>NexSMS 优选（豆包）</h3><span class="hint">从「优选号码」找项目，只接收指定前缀的短信</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">API Key<input name="nexsms_key" type="password" placeholder="' + (s.nexsms_key_set ? "已设置：" + esc(s.nexsms_key_hint) : "未设置") + '"></label>' +
      '<label class="fld">API 地址<input name="nexsms_base" value="' + esc(s.nexsms_base || "https://api.nexsms.net") + '"></label>' +
      '<label class="fld">项目关键词<input name="nexsms_project" value="' + esc(s.nexsms_project || "豆包") + '" placeholder="豆包"></label>' +
      '<label class="fld">短信前缀（只收以此开头的）<input name="nexsms_sms_prefix" value="' + esc(s.nexsms_sms_prefix || "[豆包]") + '" placeholder="[豆包]"></label>' +
      '<label class="fld">卡类型<select name="nexsms_type">' + opt("1", "1 · 首次卡", s.nexsms_type) + opt("2", "2 · 重启卡", s.nexsms_type) + opt("3", "3 · 续费卡", s.nexsms_type) + "</select></label>" +
      '<label class="fld">卡类型严格模式<select name="nexsms_type_strict">' + opt("1", "开启 · 缺货就报缺货（推荐）", s.nexsms_type_strict) + opt("0", "关闭 · 缺货时自动换其它类型", s.nexsms_type_strict) + "</select></label>" +
      '<label class="fld">有效期档位<select name="nexsms_expiry">' + [["0","0 · 随机 / 默认"],["1","1 · 5-30 天"],["2","2 · 30-45 天"],["3","3 · 45-60 天"],["4","4 · 60-80 天"],["5","5 · 80-90 天"],["6","6 · 90 天以上"]].map(function (x) { return opt(x[0], x[1], s.nexsms_expiry); }).join("") + "</select></label>" +
      '<label class="fld">号码前缀（可空）<input name="nexsms_prefix" value="' + esc(s.nexsms_prefix || "") + '"></label>' +
      '<label class="fld">手动 appId（可空）<input name="nexsms_app_id" value="' + esc(s.nexsms_app_id || "") + '" placeholder="留空自动搜索项目"></label>' +
      '<label class="fld">批量购买数量（每个号池批次）<input name="nexsms_batch_qty" type="number" min="1" max="20" value="' + esc(s.nexsms_batch_qty || "5") + '"></label>' +
      '<label class="fld">目标库存（每地区预购）<input name="nexsms_stock_target" type="number" min="0" max="200" value="' + esc(s.nexsms_stock_target || "5") + '"></label>' +
      '<div class="full help">已改为直接取号：库存不足时自动批量补货，用户不再进入排队；补货失败会立即返回提示。</div>' +
      '<label class="fld">限流冷却基数（秒）<input name="nexsms_cooldown_sec" type="number" min="5" max="300" value="' + esc(s.nexsms_cooldown_sec || "30") + '"></label>' +
      '<label class="fld">上游代理（可选）<input name="nexsms_proxy" type="password" placeholder="' + (s.nexsms_proxy_set ? "已设置：" + esc(s.nexsms_proxy_hint) : "留空直连，如 http://user:pass@host:3000") + '"></label>' +
      "</div>" +
      '<div class="card-title" style="margin-top:20px"><h3>d1jiema 国内接码</h3><span class="hint">实卡 / 虚卡，按需购号，只收关键词短信</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">API Token<input name="d1jiema_token" type="password" placeholder="' + (s.d1jiema_token_set ? "已设置：" + esc(s.d1jiema_token_hint) : "未设置") + '"></label>' +
      '<label class="fld">API 地址<input name="d1jiema_base" value="' + esc(s.d1jiema_base || "https://api.d1jiema.com/zc/data.php") + '"></label>' +
      '<label class="fld">短信关键词<input name="d1jiema_keyword" value="' + esc(s.d1jiema_keyword || "豆包") + '" placeholder="豆包"></label>' +
      '<label class="fld">短信前缀（可空）<input name="d1jiema_sms_prefix" value="' + esc(s.d1jiema_sms_prefix || "[豆包]") + '" placeholder="[豆包]"></label>' +
      '<label class="fld">通道开关<select name="d1jiema_enabled">' + opt("1", "开启", s.d1jiema_enabled) + opt("0", "关闭", s.d1jiema_enabled) + "</select></label>" +
      '<label class="fld">请求间隔（毫秒）<input name="d1jiema_gap_ms" type="number" min="120" max="5000" value="' + esc(s.d1jiema_gap_ms || "300") + '"></label>' +
      "</div>" +
      '<div class="help" id="queueStats" style="margin-top:10px">号源状态读取中…</div>' +
      '<div class="mt18" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">' +
      '<button class="btn primary" type="submit">保存设置</button>' +
      '<button class="btn" type="button" data-act="test-provider">测试号源连通性</button>' +
      '<button class="btn" type="button" data-act="test-d1jiema">测试 d1jiema</button>' +
      '<button class="btn" type="button" data-act="refill">立即补货</button>' +
      '<button class="btn ghost" type="button" data-act="cooldown-clear">解除限流冷却</button>' +
      '<button class="btn ghost" type="button" data-act="queue-stats">刷新号源状态</button>' +
      '<span class="help">NexSMS 优选只在短信内容以设定前缀（默认 [豆包]）开头时才返回验证码。</span></div>' +
      "</form>";
    $("settingsForm").addEventListener("submit", (e) => { e.preventDefault(); });
    refreshQueueStats();
  }

  async function saveSettings() {
    const f = $("settingsForm");
    const countries = [...f.countries.selectedOptions].map((o) => o.value).join(",");
    const choiceList = [...f.choices.selectedOptions].map((o) => o.value).join(",");
    const body = {
      site_name: f.site_name.value, site_url: f.site_url.value, site_note: f.site_note.value,
      shop_url: f.shop_url.value, relay_base: f.relay_base.value,
      relay_rate: f.relay_rate.value, relay_ip_rate: f.relay_ip_rate.value,
      relay_bad_rate: f.relay_bad_rate.value,
      provider: f.provider.value, allow_countries: countries || "us,gb",
      allow_choices: choiceList || "us,gb,hk",
      poll_interval: f.poll_interval.value, number_ttl_min: f.number_ttl_min.value,
      max_alloc_attempts: f.max_alloc_attempts.value, auto_ban_enabled: f.auto_ban_enabled.value,
      exit_grace_sec: f.exit_grace_sec.value, admin_ip_allow: f.admin_ip_allow.value,
      sms_activate_key: f.sms_activate_key.value, sms_activate_service: f.sms_activate_service.value,
      fivesim_key: f.fivesim_key.value, fivesim_product: f.fivesim_product.value,
      fivesim_operator: f.fivesim_operator.value,
      nexsms_key: f.nexsms_key.value, nexsms_base: f.nexsms_base.value,
      nexsms_project: f.nexsms_project.value, nexsms_sms_prefix: f.nexsms_sms_prefix.value,
      nexsms_type: f.nexsms_type.value, nexsms_type_strict: f.nexsms_type_strict.value,
      nexsms_expiry: f.nexsms_expiry.value,
      nexsms_prefix: f.nexsms_prefix.value, nexsms_app_id: f.nexsms_app_id.value,
      nexsms_batch_qty: f.nexsms_batch_qty.value, nexsms_stock_target: f.nexsms_stock_target.value,
      nexsms_cooldown_sec: f.nexsms_cooldown_sec.value, nexsms_proxy: f.nexsms_proxy.value,
      d1jiema_token: f.d1jiema_token.value, d1jiema_base: f.d1jiema_base.value,
      d1jiema_keyword: f.d1jiema_keyword.value, d1jiema_sms_prefix: f.d1jiema_sms_prefix.value,
      d1jiema_enabled: f.d1jiema_enabled.value, d1jiema_gap_ms: f.d1jiema_gap_ms.value,
    };
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    const d = await api("/admin/api/settings", { method: "POST", body });
    btn.disabled = false;
    toast(d.ok ? "设置已保存" : (d.error || "保存失败"), d.ok ? "good" : "bad");
  }

  async function refreshQueueStats() {
    const el = $("queueStats");
    if (!el) return;
    const d = await api("/admin/api/queue/stats");
    if (!d.ok) { el.textContent = "号源状态读取失败"; return; }
    const streak = Number(d.streak || 0);
    const block = Number(d.supply_block || 0);
    el.textContent = "直接取号 · 无排队 · " +
      (block > 0 ? "补货暂停 " + block + " 秒（" + (d.supply_msg || "上游限制") + "）" : "号源通道正常") +
      " · 冷却 " + (d.cooldown || 0) + " 秒" +
      (streak > 1 ? "（连续限流 x" + streak + "）" : "");
  }

  async function refillPool() {
    toast("正在补货，请稍候…", "");
    const d = await api("/admin/api/nexsms/refill", { method: "POST", body: {} });
    toast(d.ok ? "补货完成" : (d.error || "补货失败"), d.ok ? "good" : "bad");
    refreshQueueStats();
  }

  async function clearCooldown() {
    const d = await api("/admin/api/queue/cooldown", { method: "POST", body: { action: "clear" } });
    toast(d.ok ? "已解除限流冷却" : (d.error || "操作失败"), d.ok ? "good" : "bad");
    refreshQueueStats();
  }

  async function testProvider(prov) {
    const d = await api("/admin/api/provider/test", {
      method: "POST", body: prov ? { provider: prov } : {}
    });
    toast((d.ok ? "连通正常：" : "测试失败：") + (d.message || d.error || ""), d.ok ? "good" : "bad");
  }

  function opt(v, label, cur) {
    return '<option value="' + v + '"' + (String(cur) === v ? " selected" : "") + ">" + label + "</option>";
  }
  function pager(total, page, size) {
    const pages = Math.max(1, Math.ceil(total / size));
    if (pages <= 1) return "";
    return '<div class="pager"><span>第 <b>' + page + "</b> / " + pages + " 页</span>" +
      '<button class="btn mini" data-act="page" data-page="' + Math.max(1, page - 1) + '"' + (page <= 1 ? " disabled" : "") + ">上一页</button>" +
      '<button class="btn mini" data-act="page" data-page="' + Math.min(pages, page + 1) + '"' + (page >= pages ? " disabled" : "") + ">下一页</button></div>";
  }
  function goPage(p) {
    const st = S.pageState[S.route];
    if (!st) return;
    st.page = Number(p) || 1;
    if (S.route === "cards") loadCards();
    else if (S.route === "relay") loadRelay();
    else if (S.route === "numbers") loadNumbers();
    else if (S.route === "redemptions") loadRedemptions();
    else if (S.route === "codes") loadCodes();
    else if (S.route === "usage") loadUsage();
    else if (S.route === "audit") loadAudit();
    else if (S.route === "ips") loadIps();
    else if (S.route === "shop") loadShopProducts();
    else if (S.route === "shoporders") loadShopOrders();
    else if (S.route === "rcodes") loadRcodes();
    else if (S.route === "shopusers") loadShopUsers();
  }

  /* ============================================================ 号码商城 */
  function yuan(cents) { return (Number(cents || 0) / 100).toFixed(2); }

  async function pageShop() {
    const st = S.pageState.shop = S.pageState.shop || { q: "", status: "all", page: 1 };
    content.innerHTML = '<div class="loading">加载中…</div>';
    const ov = await api("/admin/api/shop/overview");
    const cf = await api("/admin/api/shop/settings");
    if (!ov.ok) { content.innerHTML = '<div class="empty">' + esc(ov.error) + "</div>"; return; }
    const s = cf.settings || {};
    S.shopRate = Number(s.shop_usd_cny || 7.2) || 7.2;
    const on = (v) => (String(v) === "1" ? " selected" : "");
    const sel = (val, cur) => (String(val) === String(cur) ? " selected" : "");
    content.innerHTML =
      '<div class="stats">' +
      stat("注册用户", ov.users, "今日新增 " + ov.users_today, "b") +
      stat("账户余额合计", "¥" + yuan(ov.balance_total), "待消费余额", "w") +
      stat("成交金额", "¥" + yuan(ov.revenue), "今日 ¥" + yuan(ov.revenue_today), "g") +
      stat("商城订单", ov.orders, "今日 " + ov.orders_today, "b") +
      stat("在售平台", ov.products_on + " / " + ov.products, "开通 / 总数", "g") +
      stat("充值码", ov.rcodes_active + " / " + ov.rcodes, "可用 / 总数", "w") +
      "</div>" +

      '<form id="shopCfgForm" class="card mt18">' +
      '<div class="card-title"><h3>商城设置</h3><span class="hint">人民币定价结算，美元单价仅作参考</span></div>' +
      '<div class="form-grid">' +
      '<label class="fld">商城开关<select name="shop_on"><option value="1"' + sel("1", s.shop_on || "1") + ">营业中</option><option value=\"0\"" + sel("0", s.shop_on) + ">维护中（前端只读）</option></select></label>" +
      '<label class="fld">开放注册<select name="shop_register_on"><option value="1"' + sel("1", s.shop_register_on || "1") + ">允许</option><option value=\"0\"" + sel("0", s.shop_register_on) + ">关闭</option></select></label>" +
      '<label class="fld">现货专区<select name="shop_stock_on"><option value="1"' + sel("1", s.shop_stock_on === "1" ? "1" : "x") + ">展示（先卖库存）</option><option value=\"0\"" + sel("0", s.shop_stock_on === "0" ? "0" : "x") + ">关闭（不显示）</option></select></label>" +
      '<label class="fld">注册必须充值码<select name="shop_register_need_code"><option value="1"' + sel("1", s.shop_register_need_code === "1" ? "1" : "x") + ">必须</option><option value=\"0\"" + sel("0", s.shop_register_need_code === "0" ? "0" : "x") + ">可免码注册</option></select></label>" +
      '<label class="fld">美元汇率（1 USD = ? CNY）<input name="shop_usd_cny" value="' + esc(s.shop_usd_cny || "7.20") + '"></label>' +
      '<label class="fld">允许缺货下单<select name="shop_allow_oos"><option value="0"' + sel("0", s.shop_allow_oos === "1" ? "x" : "0") + ">不允许（推荐）</option><option value=\"1\"" + sel("1", s.shop_allow_oos) + ">允许</option></select></label>" +
      '<label class="fld">卡密有效期（天，0=永久）<input name="shop_order_days" value="' + esc(s.shop_order_days || "0") + '"></label>' +
      '<label class="fld">每人每小时下单上限<input name="shop_buy_per_hour" value="' + esc(s.shop_buy_per_hour || "20") + '"></label>' +
      '<label class="fld">同步每个分类页数<input name="shop_sync_pages" value="' + esc(s.shop_sync_pages || "1") + '"></label>' +
      '<label class="fld full">只同步这些分类（留空=全部；可填分类 ID 或名称，逗号分隔）<input name="shop_sync_cates" value="' + esc(s.shop_sync_cates || "") + '" placeholder="例如 美国(实卡),英国卡"></label>' +
      '<div class="fld full"><button class="btn primary" type="submit">保存设置</button></div>' +
      "</div></form>" +

      '<div class="card mt18">' +
      '<div class="card-title"><h3>平台同步</h3><span class="hint">只收录上游「有成功率」的平台，已有商品保留你的定价与开关</span></div>' +
      '<div class="row gap10 wrap">' +
      '<button class="btn" type="button" data-act="shop-sync">立即同步平台列表</button>' +
      '<span class="help" id="syncStat">未开始</span></div>' +
      '<form id="shopAddForm" class="form-grid mt12">' +
      '<label class="fld">货源<select name="provider">' +
      '<option value="nexsms">NexSMS 上游（海外号）</option>' +
      '<option value="d1jiema">国内 d1jiema（实卡/虚卡）</option></select></label>' +
      '<label class="fld">appId<input name="app_id" placeholder="709；国内填 -1 实卡 / -2 虚卡"></label>' +
      '<label class="fld">平台名称<input name="name" placeholder="WhatsApp"></label>' +
      '<label class="fld">分类（运营商）<input name="cate_name" placeholder="美国(实卡)"></label>' +
      '<label class="fld">卡类型<select name="ptype"><option value="1">首次卡</option><option value="2">重启卡</option><option value="3">续费卡</option></select></label>' +
      '<label class="fld">售价（元）<input name="price_yuan" placeholder="12.00"></label>' +
      '<label class="fld">项目关键词<input name="sms_keyword" placeholder="豆包（短信里含此词）"></label>' +
      '<label class="fld">短信前缀<input name="sms_prefix" placeholder="[豆包]（可空）"></label>' +
      '<div class="fld"><button class="btn ghost" type="submit">手动添加商品</button></div>' +
      "</form></div>" +

      '<div class="card mt18">' +
      '<div class="card-title"><h3>商品与定价</h3><span class="hint">售价框里是<b>上次保存</b>的价格，框下小字是建议价（USD × 汇率）；改动会亮橙色边框，可单行保存或一键保存全部</span></div>' +
      '<div class="row gap10 wrap mt8">' +
      '<input id="shopQ" placeholder="搜索平台 / 分类" value="' + esc(st.q) + '" style="max-width:260px">' +
      '<select id="shopStatus">' + opt("all", "全部", st.status) + opt("on", "在售", st.status) +
      opt("off", "已下架", st.status) + opt("oos", "缺货", st.status) + "</select>" +
      '<button class="btn mini" type="button" data-act="shop-search">查询</button>' +
      '<button class="btn mini primary" type="button" data-act="shop-save-all" id="shopSaveAll"' +
      ' disabled>一键保存全部</button>' +
      '<span class="help" id="shopDirty"></span>' +
      '<span class="help" id="shopTotal"></span></div>' +
      '<div class="mt12" id="shopTable"></div></div>';
    $("shopCfgForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      const body = {};
      ["shop_on", "shop_register_on", "shop_usd_cny", "shop_markup_pct", "shop_allow_oos",
       "shop_order_days", "shop_buy_per_hour", "shop_sync_pages", "shop_sync_cates",
       "shop_stock_on", "shop_register_need_code"].forEach((k) => {
        if (f[k]) body[k] = f[k].value;
      });
      const d = await api("/admin/api/shop/settings", { method: "POST", body });
      toast(d.ok ? "商城设置已保存" : (d.error || "保存失败"), d.ok ? "good" : "bad");
    });
    $("shopAddForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      const d = await api("/admin/api/shop/product/add", { method: "POST", body: {
        app_id: f.app_id.value, name: f.name.value, cate_name: f.cate_name.value,
        ptype: f.ptype.value, price_yuan: f.price_yuan.value,
        sms_keyword: f.sms_keyword ? f.sms_keyword.value.trim() : "",
        sms_prefix: f.sms_prefix ? f.sms_prefix.value.trim() : "",
        provider: f.provider ? f.provider.value : "nexsms" } });
      toast(d.ok ? "商品已添加" : (d.error || "添加失败"), d.ok ? "good" : "bad");
      if (d.ok) { f.reset(); loadShopProducts(); }
    });
    loadShopProducts();
    refreshSync();
  }

  async function loadShopProducts() {
    const st = S.pageState.shop;
    const box = $("shopTable");
    if (!box || !st) return;
    const q = new URLSearchParams({ q: st.q || "", status: st.status || "all",
      page: String(st.page || 1), size: "50" });
    const d = await api("/admin/api/shop/products?" + q.toString());
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    if ($("shopTotal")) $("shopTotal").textContent = "共 " + d.total + " 个商品";
    const rate = Number(S.shopRate || 7.2) || 7.2;
    const rows = (d.products || []).map((p) => {
      const onoff = p.enabled ? badge("在售", "g") : badge("下架", "");
      const stock = p.stock > 0 ? p.stock : badge("缺货", "bad");
      const savedC = Number(p.price_cents || 0);          // 上次保存的售价（分）
      const suggest = Number(p.usd || 0) * rate;          // 建议价 = USD × 汇率
      const val = (savedC > 0 ? savedC / 100 : suggest).toFixed(2);
      const hasUsd = Number(p.usd || 0) > 0;      // 国内货源没有美元成本，不显示建议价
      const tip = "<span class='sl-saved'>" +
        (savedC > 0 ? "上次 ¥" + (savedC / 100).toFixed(2) : "尚未定价") + "</span>" +
        (hasUsd ? "<br><span class='sl-sug'>建议 ¥" + suggest.toFixed(2) + "</span>" : "");
      return "<tr><td class='mono'>" + p.id + "</td><td>" + icoHtml(p.icon) +
        "</td><td><b>" + esc(p.name) + "</b><div class='mono small'>appId " + p.app_id +
        " · type " + p.ptype +
        (p.provider === "d1jiema" ? " · <span class='prov-cn'>国内 d1</span>" : "") +
        "</div></td><td>" + esc(p.cate_name || "—") +
        "</td><td>" + esc(p.card_type || "—") +
        "</td><td class='mono small'>" + esc(p.sms_keyword || "豆包") +
        (p.sms_prefix ? "<br>" + esc(p.sms_prefix) : "") +
        " <button class='btn mini' data-act='shop-kw' data-id='" + p.id +
        "' data-kw='" + esc(p.sms_keyword || "") + "' data-pref='" + esc(p.sms_prefix || "") +
        "'>改</button></td><td class='mono'>" +
        (Number(p.success) > 0 ? p.success + "%" : "—") + "</td>" +
        "<td class='mono'>" + (Number(p.usd || 0) > 0
          ? "$" + Number(p.usd || 0).toFixed(3) : "—") + "</td>" +
        "<td class='shop-pcell'><input class='shop-price' data-id='" + p.id +
        "' data-saved='" + (savedC / 100).toFixed(2) +
        "' data-suggest='" + suggest.toFixed(2) + "' value='" + val + "'>" +
        "<div class='shop-last" + (savedC > 0 ? "" : " empty") + "' data-id='" + p.id +
        "'>" + tip + "</div>" +
        (hasUsd && savedC > 0 && Math.abs(suggest - savedC / 100) > 0.004
          ? "<button class='shop-adopt' type='button' data-act='shop-adopt' data-id='" + p.id +
            "'>用建议价</button>" : "") +
        "</td><td class='mono'>" + stock + "</td><td>" + onoff +
        "</td><td><button class='btn mini' data-act='shop-save' data-id='" + p.id +
        "'>保存价格</button> <button class='btn mini' data-act='shop-toggle' data-id='" + p.id +
        "' data-on='" + (p.enabled ? "0" : "1") + "'>" + (p.enabled ? "下架" : "上架") +
        "</button> <button class='btn mini danger' data-act='shop-del' data-id='" + p.id +
        "'>删除</button></td></tr>";
    }).join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th></th><th>平台</th>' +
      "<th>运营商</th><th>卡类型</th><th>短信关键词</th><th>成功率</th><th>USD 参考</th><th>售价 ¥</th><th>库存</th>" +
      "<th>状态</th><th>操作</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan=12 class='empty'>还没有商品，点上方「立即同步平台列表」</td></tr>") +
      "</tbody></table></div>" + pager(d.total, d.page, d.size);
    shopSyncDirty();
  }

  // ---- 售价：单行写回 / 改动高亮 / 未保存计数 ----
  function shopApplySaved(inp, price) {
    if (!inp) return;
    const v = Number(price || 0);
    inp.dataset.saved = v.toFixed(2);
    inp.classList.remove("dirty");
    inp.classList.add("saved");
    setTimeout(() => inp.classList.remove("saved"), 1200);
    const tip = document.querySelector(".shop-last[data-id='" + inp.dataset.id + "']");
    if (tip) {
      const sug = Number(inp.dataset.suggest || 0);
      tip.innerHTML = "<span class='sl-saved'>上次 ¥" + v.toFixed(2) + "</span>" +
        (sug > 0 ? "<br><span class='sl-sug'>建议 ¥" + sug.toFixed(2) + "</span>" : "");
      tip.classList.remove("empty");
      tip.classList.add("changed");
      setTimeout(() => tip.classList.remove("changed"), 1600);
    }
    shopSyncDirty();
  }

  function shopMarkDirty(inp) {
    if (!inp) return;
    const diff = Math.abs(Number(inp.value || 0) - Number(inp.dataset.saved || 0)) > 0.004;
    inp.classList.toggle("dirty", diff);
    shopSyncDirty();
  }

  function shopDirtyRows() {
    return Array.from(document.querySelectorAll(".shop-price")).filter((i) =>
      Math.abs(Number(i.value || 0) - Number(i.dataset.saved || 0)) > 0.004);
  }

  function shopSyncDirty() {
    const n = shopDirtyRows().length;
    const lab = $("shopDirty");
    if (lab) {
      lab.textContent = n ? "有 " + n + " 项改动还没保存" : (document.querySelector(".shop-price") ? "全部已保存" : "");
      lab.style.color = n ? "#ffc27a" : "";
    }
    const btn = $("shopSaveAll");
    if (btn) {
      btn.disabled = !n;
      btn.textContent = n ? "一键保存全部（" + n + "）" : "一键保存全部";
    }
  }

  async function refreshSync() {
    const el = $("syncStat");
    if (!el) return;
    const d = await api("/admin/api/shop/sync/status");
    if (!d.ok) return;
    el.textContent = d.running
      ? ("同步中 " + d.done + " / " + (d.total || "?") + " · " + (d.msg || ""))
      : (d.msg || "未开始");
    if (d.running) setTimeout(refreshSync, 1500);
    else if (S.route === "shop") loadShopProducts();
  }
  async function pageShopOrders() {
    const st = S.pageState.shoporders = S.pageState.shoporders || { status: "", q: "", page: 1 };
    content.innerHTML =
      '<div class="card"><div class="card-title"><h3>商城订单</h3>' +
      '<span class="hint">每单自动生成 卡号:手机号----接码地址 直链卡</span></div>' +
      '<div class="row gap10 wrap">' +
      '<input id="ordQ" placeholder="搜索用户 / 号码 / 平台" value="' + esc(st.q) + '" style="max-width:280px">' +
      '<select id="ordStatus">' + opt("", "全部状态", st.status) + opt("done", "已发货", st.status) +
      opt("pending", "处理中", st.status) + opt("failed", "失败", st.status) +
      opt("refunded", "已退款", st.status) + "</select>" +
      '<button class="btn mini" type="button" data-act="ord-search">查询</button>' +
      '<span class="help" id="ordTotal"></span></div>' +
      '<div class="mt12" id="ordTable"></div></div>';
    loadShopOrders();
  }

  async function loadShopOrders() {
    const st = S.pageState.shoporders;
    const box = $("ordTable");
    if (!box || !st) return;
    const q = new URLSearchParams({ q: st.q || "", status: st.status || "",
      page: String(st.page || 1), size: "50" });
    const d = await api("/admin/api/shop/orders?" + q.toString());
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    if ($("ordTotal")) $("ordTotal").textContent = "共 " + d.total + " 单";
    const cls = { done: "g", pending: "warn", failed: "bad", refunded: "violet" };
    const label = { done: "已发货", pending: "处理中", failed: "失败", refunded: "已退款" };
    const rows = (d.orders || []).map((o) =>
      "<tr><td class='mono'>" + o.id + "</td><td class='mono'>" + fmt(o.created_at) + "</td><td>" +
      esc(o.username) + "</td><td>" + esc(o.name) + (o.card_type ? " · " + esc(o.card_type) : "") +
      "</td><td class='mono'>" + esc(o.phone || "—") + "</td><td class='mono'>¥" + esc(o.price) +
      "</td><td>" + badge(label[o.status] || o.status, cls[o.status] || "") + "</td>" +
      "<td class='mono small'>" + esc((o.error || "").slice(0, 40)) + "</td>" +
      "<td>" + (o.link
        ? '<button class="btn mini" data-act="copy" data-text="' + esc(o.link) + '">复制卡密</button>'
        : "—") +
      (o.status === "done" || o.status === "failed"
        ? ' <button class="btn mini danger" data-act="ord-refund" data-id="' + o.id + '">退款</button>'
        : "") + "</td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>时间</th><th>用户</th>' +
      "<th>平台</th><th>号码</th><th>金额</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan=9 class='empty'>暂无订单</td></tr>") + "</tbody></table></div>" +
      pager(d.total, d.page, d.size);
  }

  async function pageRcodes() {
    const st = S.pageState.rcodes = S.pageState.rcodes || { batch: "", status: "", page: 1 };
    content.innerHTML =
      '<div class="card"><div class="card-title"><h3>生成充值码</h3>' +
      '<span class="hint">一次性核销 · 明文只在生成时显示一次</span></div>' +
      '<form id="rcGenForm" class="form-grid">' +
      '<label class="fld">数量<input name="count" type="number" min="1" max="500" value="10"></label>' +
      '<label class="fld">每张面额（元）<input name="amount_yuan" value="50"></label>' +
      '<label class="fld">有效期（天，0=永久）<input name="expires_days" value="0"></label>' +
      '<label class="fld">备注<input name="note" placeholder="例如 十月批次"></label>' +
      '<div class="fld"><button class="btn primary" type="submit">生成充值码</button></div>' +
      "</form></div>" +
      '<div class="card mt18"><div class="card-title"><h3>充值码列表</h3>' +
      '<span class="hint">只保存哈希，无法反查明文</span></div>' +
      '<div class="row gap10 wrap">' +
      '<input id="rcBatch" placeholder="批次号" value="' + esc(st.batch) + '" style="max-width:240px">' +
      '<select id="rcStatus">' + opt("", "全部状态", st.status) + opt("active", "未使用", st.status) +
      opt("used", "已使用", st.status) + opt("disabled", "已作废", st.status) + "</select>" +
      '<button class="btn mini" type="button" data-act="rc-search">查询</button>' +
      '<span class="help" id="rcTotal"></span></div>' +
      '<div class="mt12" id="rcTable"></div></div>';
    $("rcGenForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      const btn = f.querySelector("button[type=submit]");
      btn.disabled = true;
      const d = await api("/admin/api/shop/rcodes", { method: "POST", body: {
        count: f.count.value, amount_yuan: f.amount_yuan.value,
        expires_days: f.expires_days.value, note: f.note.value } });
      btn.disabled = false;
      if (!d.ok) { toast(d.error || "生成失败", "bad"); return; }
      toast("已生成 " + d.count + " 张 · 批次 " + d.batch, "good");
      showCodes(d);
      loadRcodes();
    });
    loadRcodes();
  }

  function showCodes(d) {
    const text = d.codes.join("\n");
    openModal("充值码 · 批次 " + d.batch,
      '<div class="hint">共 ' + d.count + " 张，每张 ¥" + esc(d.amount) +
      "，仅此一次显示，请立即复制或下载。</div>" +
      '<textarea id="allCodes" readonly style="width:100%;height:220px;margin-top:12px;font-family:var(--mono);' +
      'font-size:12px;background:#080d18;color:#cfe8ff;border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:10px">' +
      esc(text) + "</textarea>" +
      '<div class="row gap10 wrap mt12">' +
      '<button class="btn primary" type="button" data-act="copy-all-codes">复制全部</button>' +
      '<button class="btn" type="button" data-act="dl-codes">下载 txt</button></div>', true);
    S.lastCodes = { text: text, batch: d.batch };
  }

  async function loadRcodes() {
    const st = S.pageState.rcodes;
    const box = $("rcTable");
    if (!box || !st) return;
    const q = new URLSearchParams({ batch: st.batch || "", status: st.status || "",
      page: String(st.page || 1), size: "50" });
    const d = await api("/admin/api/shop/rcodes?" + q.toString());
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    if ($("rcTotal")) $("rcTotal").textContent = "共 " + d.total + " 张";
    const cls = { active: "g", used: "", disabled: "bad" };
    const rlabel = { active: "未使用", used: "已使用", disabled: "已作废" };
    const rows = (d.rcodes || []).map((r) =>
      "<tr><td class='mono'>" + r.id + "</td><td class='mono'>" + esc(r.prefix) + "…</td>" +
      "<td class='mono'>¥" + esc(r.amount) + "</td><td>" + badge(rlabel[r.status] || r.status, cls[r.status] || "") +
      "</td><td class='mono small'>" + esc(r.batch) + "</td><td>" + esc(r.used_by || "—") +
      "</td><td class='mono'>" + fmt(r.created_at) + "</td><td class='mono'>" +
      (r.used_at ? fmt(r.used_at) : "—") + "</td><td>" +
      (r.status === "active"
        ? '<button class="btn mini danger" data-act="rc-disable" data-id="' + r.id + '">作废</button>'
        : "—") + "</td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>前缀</th><th>面额</th>' +
      "<th>状态</th><th>批次</th><th>使用者</th><th>生成时间</th><th>使用时间</th><th>操作</th>" +
      "</tr></thead><tbody>" + (rows || "<tr><td colspan=9 class='empty'>暂无充值码</td></tr>") +
      "</tbody></table></div>" + pager(d.total, d.page, d.size);
  }

  async function pageShopUsers() {
    const st = S.pageState.shopusers = S.pageState.shopusers || { q: "", page: 1 };
    content.innerHTML =
      '<div class="card"><div class="card-title"><h3>商城用户</h3>' +
      '<span class="hint">余额可手动调整，所有变动都写入流水</span></div>' +
      '<div class="row gap10 wrap">' +
      '<input id="uQ" placeholder="搜索账号" value="' + esc(st.q) + '" style="max-width:260px">' +
      '<button class="btn mini" type="button" data-act="u-search">查询</button>' +
      '<span class="help" id="uTotal"></span></div>' +
      '<div class="mt12" id="uTable"></div></div>';
    loadShopUsers();
  }

  async function loadShopUsers() {
    const st = S.pageState.shopusers;
    const box = $("uTable");
    if (!box || !st) return;
    const q = new URLSearchParams({ q: st.q || "", page: String(st.page || 1), size: "50" });
    const d = await api("/admin/api/shop/users?" + q.toString());
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    if ($("uTotal")) $("uTotal").textContent = "共 " + d.total + " 位";
    const rows = (d.users || []).map((u) =>
      "<tr><td class='mono'>" + u.id + "</td><td><b>" + esc(u.username) + "</b></td>" +
      "<td class='mono'>¥" + esc(u.balance) + "</td><td class='mono'>" + u.orders +
      "</td><td class='mono'>¥" + esc(u.spent) + "</td><td>" +
      badge(u.status === "active" ? "正常" : "已禁用", u.status === "active" ? "g" : "bad") +
      "</td><td class='mono'>" + fmt(u.created_at) + "</td><td class='mono'>" +
      fmt(u.last_login_at) + "</td><td class='mono small'>" + esc(u.last_ip || "—") + "</td><td>" +
      '<button class="btn mini" data-act="u-balance" data-id="' + u.id + '" data-name="' +
      esc(u.username) + '">调余额</button> ' +
      '<button class="btn mini ' + (u.status === "active" ? "danger" : "") + '" data-act="u-status" ' +
      'data-id="' + u.id + '" data-status="' + (u.status === "active" ? "disabled" : "active") + '">' +
      (u.status === "active" ? "禁用" : "启用") + "</button></td></tr>").join("");
    box.innerHTML = '<div class="tbl-wrap"><table><thead><tr><th>ID</th><th>账号</th><th>余额</th>' +
      "<th>订单</th><th>累计消费</th><th>状态</th><th>注册时间</th><th>最近登录</th><th>IP</th>" +
      "<th>操作</th></tr></thead><tbody>" +
      (rows || "<tr><td colspan=10 class='empty'>暂无用户</td></tr>") + "</tbody></table></div>" +
      pager(d.total, d.page, d.size);
  }

  function icoHtml(icon, size) {
    const s = String(icon == null ? "" : icon);
    const px = size || 18;
    if (s.indexOf("i:") === 0) {
      return '<img src="/static/icons/' + esc(s.slice(2)) + '" alt="" loading="lazy" style="width:' +
        px + "px;height:" + px + 'px;object-fit:contain;vertical-align:middle">';
    }
    return esc(s || "🔑");
  }

  async function pageStock() {
    const st = S.pageState.stock = S.pageState.stock || { status: "available", cate: "", page: 1 };
    content.innerHTML = '<div class="loading">加载中…</div>';
    const q = new URLSearchParams({ status: st.status || "", cate: st.cate || "",
      page: String(st.page || 1), size: "50" });
    const d = await api("/admin/api/shop/stock?" + q.toString());
    if (!d.ok) { content.innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
    const groups = d.groups || [];
    const available = groups.reduce((a, g) => a + Number(g.count || 0), 0);
    const gRows = groups.map((g) =>
      "<tr><td>" + esc(g.flag || "🌐") + " <b>" + esc(g.cate_name) + "</b></td><td>" +
      esc(g.card_type || "不限") + "</td><td class='mono'>" + g.count + "</td><td class='mono'>¥" +
      esc(g.price) + "</td><td><input class='stock-price' data-cate='" + esc(g.cate_name) +
      "' data-ctype='" + esc(g.card_type || "") + "' value='" + esc(g.price) +
      "' style='width:86px'></td><td><button class='btn mini' data-act='stock-price' data-cate='" +
      esc(g.cate_name) + "' data-ctype='" + esc(g.card_type || "") + "'>保存售价</button></td></tr>").join("");
    const rows = (d.stock || []).map((s) =>
      "<tr><td class='mono'>" + s.id + "</td><td>" + badge(s.kind === "relay" ? "直链" : "卡密", "info") +
      "</td><td class='mono small' style='max-width:340px;word-break:break-all'>" + esc(s.link) +
      "</td><td>" + esc(s.cate_name || "—") + "</td><td>" + esc(s.card_type || "—") +
      "</td><td class='mono'>¥" + esc(s.price) + "</td><td>" + stockBadge(s.status) +
      "</td><td class='mono small'>" + esc(s.batch || "—") + "</td><td class='mono small'>" +
      fmt(s.created_at) + "</td><td>" + (s.status === "available"
        ? '<button class="btn mini danger" data-act="stock-remove" data-id="' + s.id + '">移除</button>'
        : "—") + "</td></tr>").join("");
    content.innerHTML =
      '<div class="stats">' +
      stat("在库现货", available, "可立即发货", available ? "g" : "w") +
      stat("库存分组", groups.length, "运营商 + 卡类型", "b") +
      stat("当前筛选", (d.stock || []).length + " / " + d.total, "本页 / 全部", "b") +
      "</div>" +

      '<div class="card mt18"><div class="card-title"><h3>回收卡密链接</h3>' +
      '<span class="hint">已售出的卡密继续有效；回收只针对未售出的卡，入库后优先于上游进货发货</span></div>' +
      '<form id="stockForm" class="form-grid">' +
      '<label class="fld">回收方式<select name="mode">' +
      '<option value="relay">自动回收：未读直链卡（需填运营商）</option>' +
      '<option value="links">粘贴卡密链接</option></select></label>' +
      '<label class="fld">运营商 / 分类<input name="cate_name" placeholder="美国(实卡) / 英国卡 / 香港卡"></label>' +
      '<label class="fld">卡类型<select name="card_type"><option value="">不限 / 按原卡</option>' +
      '<option value="首次卡">首次卡</option><option value="重启卡">重启卡</option>' +
      '<option value="续费卡">续费卡</option>' +
      '<option value="实卡">实卡（国内）</option>' +
      '<option value="虚拟卡">虚拟卡（国内）</option></select></label>' +
      '<label class="fld">现货售价（元）<input name="price_yuan" placeholder="5.00"></label>' +
      '<label class="fld">本次最多回收<input name="limit" value="50"></label>' +
      '<label class="fld">只回收 N 天前创建（0=不限）<input name="days" value="0"></label>' +
      '<label class="fld">备注<input name="note" placeholder="可选，例如 10 月回收批次"></label>' +
      '<label class="fld full">粘贴卡密链接（仅「粘贴」模式需要；整段复制也行，时间/已售出等文字会自动忽略）' +
      '<textarea name="text" rows="5" placeholder="https://12456.cfd/c/XXXX-XXXX-XXXX-XXXX&#10;卡号:+17798001455----https://api.12456.cfd/smsrecord?token=...&#10;（可整段粘贴，垃圾文字不用删）"></textarea></label>' +
      '<div class="fld full"><button class="btn primary" type="submit">开始回收</button>' +
      '<button class="btn ghost" type="button" data-act="stock-preview" style="margin-left:8px">预览可回收数量</button>' +
      '<div class="help" id="stockHint">' + esc(st.flash || "注意：粘贴了链接会自动切到「粘贴卡密链接」模式；同「运营商 + 卡类型」统一售价；卡密链接必须粘贴上传（库里只存哈希，明文无法还原）。") + '</div></div>' +
      "</form></div>" +

      '<div class="card mt18"><div class="card-title"><h3>库存分组</h3>' +
      '<span class="hint">现货专区按分组展示，售完自动隐藏</span></div>' +
      '<div class="tbl-wrap"><table><thead><tr><th>运营商</th><th>卡类型</th><th>数量</th>' +
      "<th>售价 ¥</th><th>改价</th><th></th></tr></thead><tbody>" +
      (gRows || "<tr><td colspan=6 class='empty'>还没有回收的现货，先用上方表单回收一批</td></tr>") +
      "</tbody></table></div></div>" +

      '<div class="card mt18"><div class="card-title"><h3>库存明细</h3>' +
      '<span class="hint">链接即发货内容，移除后不会再发给买家</span></div>' +
      '<div class="row gap10 wrap">' +
      '<input id="stockCate" placeholder="筛选运营商 / 分类" value="' + esc(st.cate || "") + '" style="max-width:240px">' +
      '<select id="stockStatus">' + opt("available", "在库", st.status) + opt("sold", "已售", st.status) +
      opt("removed", "已移除", st.status) + opt("", "全部", st.status) + "</select>" +
      '<button class="btn mini" type="button" data-act="stock-search">查询</button>' +
      '<span class="help">共 ' + d.total + " 条</span></div>" +
      '<div class="mt12"><div class="tbl-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>卡密链接</th>' +
      "<th>运营商</th><th>卡类型</th><th>售价 ¥</th><th>状态</th><th>批次</th><th>入库时间</th><th>操作</th>" +
      "</tr></thead><tbody>" +
      (rows || "<tr><td colspan=10 class='empty'>暂无库存记录</td></tr>") + "</tbody></table></div>" +
      pager(d.total, d.page, d.size) + "</div></div>";
  }

  function stockBadge(s) {
    return s === "available" ? badge("在库", "g") : s === "sold" ? badge("已售", "info")
      : s === "removed" ? badge("已移除", "") : badge(s || "—", "");
  }

  async function stockRecover(form) {
    const fd = new FormData(form);
    const body = {};
    fd.forEach((v, k) => { body[k] = v; });
    const hint = $("stockHint");
    const pasted0 = String(body.text || "").trim();
    if (pasted0 && (body.mode || "relay") === "relay") {
      body.mode = "links";
      if (form.elements && form.elements["mode"]) form.elements["mode"].value = "links";
      toast("检测到粘贴内容，已自动切换为「粘贴卡密链接」回收", "good");
    }
    if ((body.mode || "links") === "relay") {
      const pv = await api("/admin/api/shop/stock/preview?" + new URLSearchParams({
        cate_name: body.cate_name || "", card_type: body.card_type || "",
        days: body.days || "0" }).toString());
      if (!pv.ok) { toast(pv.error || "预览失败", "bad"); return; }
      if (!pv.total) {
        toast("没有符合条件的未读直链卡", "bad");
        if (hint) hint.textContent = "没有符合条件的未读直链卡（或都已被读取过）";
        return;
      }
      if (!confirm("将回收 " + pv.total + " 张直链卡进库存（运营商 " + (body.cate_name || "—") +
                   "），回收后库存优先发货，确定？")) return;
    }
    if (hint) hint.textContent = "正在回收…";
    const d = await api("/admin/api/shop/stock/recover", { method: "POST", body });
    const stk = S.pageState.stock = S.pageState.stock || {};
    if (!d.ok) {
      toast(d.error || "回收失败", "bad");
      if (hint) hint.textContent = (d.bad && d.bad.length)
        ? "跳过：" + d.bad.join(" / ") : (d.error || "回收失败");
      stk.flash = hint ? hint.textContent : "";
      return;
    }
    const skip = (d.bad && d.bad.length) ? ("，跳过 " + d.bad.length + " 条") : "";
    toast("识别 " + (d.parsed || (d.added + d.dup)) + " 条，已回收 " + d.added + " 个" +
      (d.dup ? "（重复 " + d.dup + "）" : "") + skip, "good");
    if (hint) hint.textContent = (d.bad && d.bad.length)
      ? ("入库 " + d.added + " 个" + skip + "：" + d.bad.slice(0, 6).join(" / "))
      : ("入库 " + d.added + " 个，批次 " + d.batch);
    stk.flash = (hint ? hint.textContent : "") +
      (d.available != null ? ("；当前在库 " + d.available + " 张") : "");
    pageStock();
  }

  async function shopAction(act, el) {
    const id = el.dataset.id;
    if (act === "stock-preview") {
      const f = $("stockForm");
      const get = (n) => (f && f.elements[n] ? f.elements[n].value : "");
      const pasted1 = String(get("text") || "").trim();
      if (pasted1) {
        const n = cleanCardText(pasted1).split("\n").filter(Boolean).length;
        if (f && f.elements["mode"] && f.elements["mode"].value === "relay") {
          f.elements["mode"].value = "links";
        }
        const hint0 = $("stockHint");
        toast(n ? ("识别到 " + n + " 条链接，点「开始回收」即可入库")
                : "没识别到卡密链接，检查一下格式", n ? "good" : "bad");
        if (hint0) hint0.textContent = n
          ? ("已将回收方式切为「粘贴卡密链接」，识别到 " + n + " 条，点「开始回收」入库")
          : "没识别到卡密链接：支持 /c/XXXX-XXXX-XXXX-XXXX 与 token=... 两种格式";
        return;
      }
      const d = await api("/admin/api/shop/stock/preview?" + new URLSearchParams({
        cate_name: get("cate_name"), card_type: get("card_type"),
        days: get("days") || "0" }).toString());
      const hint = $("stockHint");
      if (!d.ok) { toast(d.error || "预览失败", "bad"); return; }
      const detail = (d.groups || []).map((g) => (g.country || "?") + "/" +
        (g.card_type || "不限") + " " + g.count).join("，");
      toast("可回收 " + d.total + " 张", d.total ? "good" : "bad");
      if (hint) hint.textContent = d.total
        ? ("可回收 " + d.total + " 张：" + detail) : "没有符合条件的未读直链卡";
      return;
    }
    if (act === "stock-search") {
      const st = S.pageState.stock;
      st.cate = $("stockCate").value.trim(); st.status = $("stockStatus").value; st.page = 1;
      return pageStock();
    }
    if (act === "stock-price") {
      const tr = el.closest("tr");
      const inp = tr ? tr.querySelector("input") : null;
      const d = await api("/admin/api/shop/stock/price", { method: "POST", body: {
        cate_name: el.dataset.cate || "", card_type: el.dataset.ctype || "",
        price_yuan: inp ? inp.value : "" } });
      toast(d.ok ? ("售价已改为 ¥" + d.price) : (d.error || "改价失败"), d.ok ? "good" : "bad");
      return d.ok && pageStock();
    }
    if (act === "stock-remove") {
      if (!confirm("移除后这条卡密不再发给买家（已售出的不受影响），确定？")) return;
      const d = await api("/admin/api/shop/stock/remove", { method: "POST", body: { id } });
      toast(d.ok ? "已移除" : (d.error || "操作失败"), d.ok ? "good" : "bad");
      return d.ok && pageStock();
    }
    if (act === "shop-kw") {
      const kw = el.dataset.kw || "";
      const pref = el.dataset.pref || "";
      openModal("短信关键词 · 商品 #" + id,
        '<div class="form-grid">' +
        '<label class="fld">项目关键词（短信内容含此词才算这个平台，例如 豆包）' +
        '<input id="kwEditInput" value="' + esc(kw) + '" placeholder="豆包"></label>' +
        '<label class="fld">短信前缀（只收以此开头的短信，留空不限）' +
        '<input id="pxEditInput" value="' + esc(pref) + '" placeholder="[豆包]"></label>' +
        '<div class="full"><button class="btn primary" type="button" data-act="shop-kw-save" data-id="' + id +
        '">保存</button> <button class="btn ghost" type="button" data-act="modal-close">取消</button></div>' +
        "</div>", true);
      return;
    }
    if (act === "shop-kw-save") {
      const kwInp = $("kwEditInput"), pxInp = $("pxEditInput");
      const d = await api("/admin/api/shop/product", { method: "POST", body: {
        id, sms_keyword: kwInp ? kwInp.value.trim() : "",
        sms_prefix: pxInp ? pxInp.value.trim() : "" } });
      if (!d.ok) { toast(d.error || "保存失败", "bad"); return; }
      toast("已保存短信关键词", "good");
      closeModal();
      return loadShopProducts();
    }
    if (act === "shop-adopt") {
      const inp = document.querySelector(".shop-price[data-id='" + id + "']");
      if (!inp) return;
      inp.value = Number(inp.dataset.suggest || 0).toFixed(2);
      shopMarkDirty(inp);
      return;
    }
    if (act === "shop-save-all") {
      const list = Array.from(document.querySelectorAll(".shop-price")).filter((i) =>
        Math.abs(Number(i.value || 0) - Number(i.dataset.saved || 0)) > 0.004);
      if (!list.length) { toast("没有需要保存的改动"); return; }
      if (!confirm("将保存 " + list.length + " 个改动的售价，确定？")) return;
      const btn = el;
      btn.disabled = true;
      let okN = 0, badN = 0, k = 0;
      for (const inp of list) {
        k++;
        btn.textContent = "保存中 " + k + "/" + list.length;
        let d;
        try {
          d = await api("/admin/api/shop/product", { method: "POST",
            body: { id: inp.dataset.id, price_yuan: inp.value } });
        } catch (e) { d = { ok: false, error: "网络异常" }; }
        if (d && d.ok) { okN++; shopApplySaved(inp, d.price); }
        else { badN++; inp.classList.add("dirty"); }
        shopSyncDirty();
      }
      btn.disabled = false;
      shopSyncDirty();
      toast(badN ? ("已保存 " + okN + " 个，" + badN + " 个失败")
                 : ("已保存 " + okN + " 个价格"), badN ? "bad" : "good");
      return;
    }
    if (act === "shop-save") {
      const inp = document.querySelector(".shop-price[data-id='" + id + "']");
      const d = await api("/admin/api/shop/product", { method: "POST",
        body: { id, price_yuan: inp ? inp.value : "" } });
      if (!d.ok) { toast(d.error || "保存失败", "bad"); return; }
      toast("已保存 ¥" + d.price, "good");
      // 只回写当前行，不重建表格：其它输入框里没保存的修改保持原样
      shopApplySaved(inp, d.price);
      const old = el.textContent;
      el.textContent = "已保存";
      el.disabled = true;
      setTimeout(() => { el.textContent = old; el.disabled = false; }, 1200);
      return;
    }
    if (act === "shop-toggle") {
      const d = await api("/admin/api/shop/product", { method: "POST",
        body: { id, enabled: el.dataset.on } });
      toast(d.ok ? (el.dataset.on === "1" ? "已上架" : "已下架") : (d.error || "操作失败"),
        d.ok ? "good" : "bad");
      return d.ok && loadShopProducts();
    }
    if (act === "shop-del") {
      if (!confirm("删除后该商品从商城移除（已售卡密不受影响），确定？")) return;
      const d = await api("/admin/api/shop/product/delete", { method: "POST", body: { id } });
      toast(d.ok ? "已删除" : (d.error || "删除失败"), d.ok ? "good" : "bad");
      return d.ok && loadShopProducts();
    }
    if (act === "shop-sync") {
      const d = await api("/admin/api/shop/sync", { method: "POST", body: {} });
      toast(d.ok ? "同步已开始，请稍候…" : (d.error || "同步失败"), d.ok ? "good" : "bad");
      refreshSync();
      return;
    }
    if (act === "ord-refund") {
      if (!confirm("退款会把金额退回用户余额并停用该直链卡，确定？")) return;
      const d = await api("/admin/api/shop/order/refund", { method: "POST", body: { id } });
      toast(d.ok ? ("已退款，用户余额 ¥" + d.balance) : (d.error || "退款失败"), d.ok ? "good" : "bad");
      return d.ok && loadShopOrders();
    }
    if (act === "rc-disable") {
      if (!confirm("作废后该充值码无法再使用，确定？")) return;
      const d = await api("/admin/api/shop/rcodes/disable", { method: "POST", body: { id } });
      toast(d.ok ? "已作废" : (d.error || "操作失败"), d.ok ? "good" : "bad");
      return d.ok && loadRcodes();
    }
    if (act === "u-balance") {
      const v = prompt("调整 " + (el.dataset.name || "") + " 的余额（元，正数加、负数减，例如 50 或 -20）", "");
      if (v === null) return;
      const d = await api("/admin/api/shop/user/balance", { method: "POST",
        body: { id, delta_yuan: v, note: "后台手动调整" } });
      toast(d.ok ? ("已调整，余额 ¥" + d.balance) : (d.error || "调整失败"), d.ok ? "good" : "bad");
      return d.ok && loadShopUsers();
    }
    if (act === "u-status") {
      const off = el.dataset.status !== "active";
      if (off && !confirm("禁用后该用户会立即退出登录且无法下单，确定？")) return;
      const d = await api("/admin/api/shop/user/status", { method: "POST",
        body: { id, status: el.dataset.status } });
      toast(d.ok ? (off ? "已禁用" : "已启用") : (d.error || "操作失败"), d.ok ? "good" : "bad");
      return d.ok && loadShopUsers();
    }
    if (act === "shop-search") {
      const st = S.pageState.shop;
      st.q = $("shopQ").value.trim(); st.status = $("shopStatus").value; st.page = 1;
      return loadShopProducts();
    }
    if (act === "ord-search") {
      const st = S.pageState.shoporders;
      st.q = $("ordQ").value.trim(); st.status = $("ordStatus").value; st.page = 1;
      return loadShopOrders();
    }
    if (act === "rc-search") {
      const st = S.pageState.rcodes;
      st.batch = $("rcBatch").value.trim(); st.status = $("rcStatus").value; st.page = 1;
      return loadRcodes();
    }
    if (act === "u-search") {
      const st = S.pageState.shopusers;
      st.q = $("uQ").value.trim(); st.page = 1;
      return loadShopUsers();
    }
    if (act === "copy-all-codes") {
      copy((S.lastCodes && S.lastCodes.text) || "", "已复制全部充值码");
      return;
    }
    if (act === "dl-codes") {
      const t = (S.lastCodes && S.lastCodes.text) || "";
      const blob = new Blob([t], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "rcodes-" + ((S.lastCodes && S.lastCodes.batch) || "batch") + ".txt";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 3000);
      return;
    }
  }
  // 粘贴卡密链接时自动剔除垃圾文字（时间、已售出、备注…），只留识别到的链接
  function cleanCardText(text) {
    const t = String(text || "").replace(/[\u200b-\u200f\u2028\u2029\ufeff]/g, "");
    const out = [], seen = {};
    const host = location.host || "12456.cfd";
    let m;
    const reC = /\/c\/([0-9A-Za-z][0-9A-Za-z-]{7,63})/gi;
    while ((m = reC.exec(t))) {
      const k = m[1].replace(/^-+|-+$/g, "").toUpperCase();
      if (!seen["c" + k]) { seen["c" + k] = 1; out.push("https://" + host + "/c/" + k); }
    }
    const reT = /token=([0-9a-fA-F]{16,64})/gi;
    while ((m = reT.exec(t))) {
      const v = m[1].toLowerCase();
      if (!seen["t" + v]) { seen["t" + v] = 1; out.push("https://api." + host + "/smsrecord?token=" + v); }
    }
    return out.join("\n");
  }

  document.addEventListener("paste", (e) => {
    const ta = e.target;
    if (!ta || ta.tagName !== "TEXTAREA" || ta.name !== "text" || !ta.closest("#stockForm")) return;
    const raw = ((e.clipboardData || window.clipboardData) || { getData: () => "" })
      .getData("text") || "";
    const cleaned = cleanCardText(raw);
    if (!cleaned) return;                 // 没识别到链接就按普通粘贴处理
    e.preventDefault();
    const s0 = ta.selectionStart, s1 = ta.selectionEnd;
    ta.value = ta.value.slice(0, s0) + cleaned + ta.value.slice(s1);
    ta.selectionStart = ta.selectionEnd = s0 + cleaned.length;
    const n = cleaned.split("\n").filter(Boolean).length;
    const sf = ta.closest("#stockForm");
    if (sf && sf.elements["mode"] && sf.elements["mode"].value === "relay") {
      sf.elements["mode"].value = "links";
    }
    toast("已自动剔除无关文字，保留 " + n + " 条链接", "good");
  });

  // 售价框输入即时高亮（橙色边框）并刷新「未保存 n 项」
  document.addEventListener("input", (e) => {
    const inp = e.target;
    if (!inp || !inp.classList || !inp.classList.contains("shop-price")) return;
    shopMarkDirty(inp);
  });
  boot();
})();