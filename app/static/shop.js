/* SMS Gate - 号码商城前端（商城 / 登录注册 / 个人中心） */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var page = document.body.dataset.page || "";
  var STATE = { csrf: "", user: null, products: [], all: [], shown: 24,
                region: "", type: "", q: "", busy: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toast(msg, kind) {
    var el = $("toast");
    if (!el) return;
    el.textContent = msg;
    el.className = "toast show " + (kind || "");
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.className = "toast " + (kind || ""); }, 3200);
  }
  function copy(text, okMsg) {
    var s = String(text == null ? "" : text);
    if (!s) { toast("没有可复制的内容", "bad"); return; }
    var good = function () { toast(okMsg || "已复制", "good"); };
    var legacy = function () {
      try {
        var ta = document.createElement("textarea");
        ta.value = s;
        ta.setAttribute("readonly", "");
        ta.style.cssText = "position:fixed;top:-2000px;left:0;opacity:0";
        document.body.appendChild(ta);
        ta.focus(); ta.select(); ta.setSelectionRange(0, s.length);
        document.execCommand("copy") ? good() : toast("复制失败，请长按选择", "bad");
        ta.remove();
      } catch (e) { toast("复制失败，请长按选择", "bad"); }
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(s).then(good, legacy);
    } else legacy();
  }

  var csrfWait = null;
  /* 会话里的 csrf 可能因为刷新/换标签页/会话轮换而过期：重新拉一次，用户无感 */
  function refreshCsrf() {
    if (csrfWait) return csrfWait;
    csrfWait = fetch("/shop/api/me", { credentials: "same-origin",
                                       headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        csrfWait = null;
        if (d && d.ok && d.csrf) {
          STATE.csrf = d.csrf;
          if (d.user) STATE.user = d.user;
          return true;
        }
        return false;
      }, function () { csrfWait = null; return false; });
    return csrfWait;
  }

  function api(path, opt) {
    opt = opt || {};
    var init = { method: opt.method || "GET", credentials: "same-origin",
                 headers: { "Accept": "application/json" } };
    if (opt.body) {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRF"] = STATE.csrf;
      init.body = JSON.stringify(opt.body);
    }
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "响应异常" }; })
        .then(function (d) {
          d._status = r.status;
          // 后端可能直接抛 HTTPException({"detail": ...})，统一成 error 文案
          if (!d.error && d.detail) d.error = typeof d.detail === "string" ? d.detail : "请求失败";
          // 403（CSRF/会话失效）：刷新 token 后自动重试一次，不让用户白发一次下单
          if (opt.body && r.status === 403 && !opt._retried) {
            return refreshCsrf().then(function (got) {
              if (!got) return d;
              return api(path, { method: opt.method, body: opt.body, _retried: true });
            });
          }
          return d;
        });
    }, function () { return { ok: false, error: "网络异常，请重试", _status: 0 }; });
  }

  function fmt(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000), p = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
      p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function randomIdem() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "idem-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
  }

  function setNavUser(u) {
    STATE.user = u;
    var nav = $("navUser");
    if (nav) {
      var rc = document.body.dataset.rcodeUrl || "";
      var rcBtn = rc ? ('<a class="btn ghost sm" href="' + esc(rc) +
                        '" target="_blank" rel="noopener">购买充值码</a>') : "";
      nav.innerHTML = rcBtn + (u
        ? '<a class="btn ghost sm" href="/shop/me">' + esc(u.username) + " · ¥" + esc(u.balance) +
          '</a><a class="btn primary sm" href="/shop">继续选购</a>'
        : '<a class="btn ghost sm" href="/shop/login">登录</a>' +
          '<a class="btn primary sm" href="/shop/login#reg">注册</a>');
    }
    var wv = $("walletVal"), wn = $("walletNote");
    if (wv) wv.textContent = u ? u.balance : "—";
    if (wn) wn.textContent = u ? ("账号 " + u.username + " · 余额可用于任意平台下单")
                               : "登录后即可充值下单";
    if ($("walletLogin")) $("walletLogin").style.display = u ? "none" : "";
    if ($("walletMe")) $("walletMe").style.display = u ? "" : "none";
  }

  /* ------------------------------------------------------------ 商城页 */
  var REGIONS = [], TYPES = [];

  function chipHtml(key, label, flag, active) {
    return '<button type="button" class="sh-chip' + (active ? " on" : "") +
      '" data-k="' + esc(key) + '"><span class="em">' + esc(flag || "") + "</span>" +
      esc(label) + "</button>";
  }

  function renderChips() {
    var r = $("regionChips"), t = $("typeChips");
    if (r) {
      r.innerHTML = chipHtml("", "全部", "🌐", STATE.region === "") +
        REGIONS.map(function (x) {
          return chipHtml(x.key, x.label, x.flag, STATE.region === x.key);
        }).join("");
    }
    if (t) {
      t.innerHTML = chipHtml("", "全部", "🧩", STATE.type === "") +
        TYPES.map(function (x) { return chipHtml(x, x, "▫", STATE.type === x); }).join("");
    }
  }

  function rateClass(v) { return v >= 50 ? "" : "low"; }

  function icoHtml(icon) {
    var s = String(icon == null ? "" : icon);
    if (s.indexOf("i:") === 0) {
      return '<img alt="" loading="lazy" src="/static/icons/' + esc(s.slice(2)) + '">';
    }
    return esc(s || "\u{1F511}");
  }

  function cardHtml(p) {
    var oos = p.stock <= 0;
    var rate = Math.max(2, Math.min(100, Number(p.success) || 0));
    return '<article class="sh-card' + (oos ? " off" : "") + '">' +
      '<div class="sh-ico">' + icoHtml(p.icon) + "</div>" +
      '<div class="sh-cbody"><h3>' + esc(p.name) +
      (p.card_type ? '<span>' + esc(p.card_type) + "</span>" : "") + "</h3>" +
      '<div class="sh-tags">' +
      '<span class="sh-tag">' + esc(p.flag) + " " + esc(p.cate_name || "全球") + "</span>" +
      (oos ? '<span class="sh-tag warn">缺货</span>'
           : (Number(p.stock) >= 9999
              ? '<span class="sh-tag idle">现货充足</span>'
              : '<span class="sh-tag idle">库存 ' + esc(p.stock) + "</span>")) +
      "</div>" +
      (Number(p.success) > 0
        ? '<div class="sh-meter"><span>成功率 ' + esc(p.success) + "%</span>" +
          '<span class="sh-bar"><i class="' + rateClass(p.success) + '" style="width:' +
          rate + '%"></i></span></div>'
        : '<div class="sh-meter"><span class="sh-direct">' +
          esc(p.note || "直连上游通道") + "</span></div>") +
      "</div>" +
      '<div class="sh-buy"><div class="sh-price">¥<b>' + esc(p.price) + "</b></div>" +
      '<button class="btn primary" data-buy="' + p.id + '"' + (oos ? " disabled" : "") + ">" +
      (oos ? "暂时缺货" : "购买") + "</button></div></article>";
  }

  function paintProducts() {
    var box = $("prodGrid");
    if (!box) return;
    var list = STATE.all || [];
    if (!list.length) {
      box.innerHTML = '<div class="sh-empty">没有符合条件的平台，换个运营商或卡类型试试。</div>';
      return;
    }
    var n = Math.min(list.length, STATE.shown || 24);
    var html = list.slice(0, n).map(cardHtml).join("");
    if (list.length > n) {
      html += '<div class="sh-more"><button class="btn ghost" type="button" data-more="1">' +
        "显示更多（还有 " + (list.length - n) + " 个）</button></div>";
    }
    box.innerHTML = html;
  }

  function renderProducts(list) {
    STATE.all = list || [];
    STATE.shown = 24;
    paintProducts();
    var c = $("statCount");
    if (c) c.textContent = String(STATE.all.length);
  }

  function loadShop() {
    var qs = [];
    if (STATE.region) qs.push("region=" + encodeURIComponent(STATE.region));
    if (STATE.type) qs.push("type=" + encodeURIComponent(STATE.type));
    if (STATE.q) qs.push("q=" + encodeURIComponent(STATE.q));
    api("/shop/api/products" + (qs.length ? "?" + qs.join("&") : "")).then(function (d) {
      if (!d.ok) {
        $("prodGrid").innerHTML = '<div class="sh-empty">' + esc(d.error || "加载失败") + "</div>";
        return;
      }
      REGIONS = d.regions || [];
      TYPES = d.types || [];
      if (d.csrf) STATE.csrf = d.csrf;
      renderChips();
      renderProducts(d.products || []);
      setNavUser(d.user || null);
      loadStock();
    });
  }

  /* ------------------------------------------------------------ 购买二次确认（6 秒冷静期） */
  function ttlText(exp) {
    var e = Number(exp || 0);
    if (!e) return "";
    var now = Math.floor(Date.now() / 1000);
    var left = e - now;
    var mm = Math.floor(Math.abs(left) / 60), ss = Math.abs(left) % 60;
    var d = new Date(e * 1000);
    var hh = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    var span = mm >= 60 ? Math.floor(mm / 60) + " 小时 " + (mm % 60) + " 分钟" : mm + " 分 " + ss + " 秒";
    // 时间只是「预估」：上游并不告诉我们真实回收时刻，所以到期也绝不判死刑，
    // 只提示可能已回收、建议直接试一次取码。号码还有没有效，只由上游说了算。
    if (left <= 0) {
      return "⏱ 预估有效期已过（" + hh + "，已超 " + span + "）· 号码未必失效，可直接打开接码地址试一次";
    }
    if (left <= 180) {
      return "⏱ 预计还能收 " + span + "（约至 " + hh + "）· 快到期了，建议马上用";
    }
    return "⏱ 预计可用至 " + hh + "（约剩 " + span + "）· 以实际能否收码为准";
  }

  var CFM_PRODUCT = [
    "<b>号码是实时向上游买的。</b>你点确认的那一刻，上游就已经扣了我们一个号的钱。",
    "<b>号码会立刻显示给你，等于已经交付。</b>就算你一次都没用、平台也没发验证码，也<b>无法退款</b>。",
    "<b>不能收回再卖。</b>上游的号码是一次性的，我们没办法把它退给上游再卖给下一个人，所以「没用过」不构成退款理由。",
    "<b>唯一会退的是差价：</b>如果实际扣费低于页面价格，差额会原路退回你的余额。",
    "<b>拿到号请马上用。</b>有效期是预估的（下单后会显示），到时上游可能回收；能不能继续收码以实际为准。"
  ];
  var CFM_STOCK = [
    "<b>这是提前买好的现货。</b>上游的钱已经花出去了，卡密一发出去就等于交付。",
    "<b>发出后无法退款，</b>也没办法再卖给别人——号码是一次性的，不能收回。",
    "<b>唯一会退的是差价：</b>如果实际扣费低于页面价格，差额会原路退回你的余额。",
    "<b>拿到号请马上用。</b>有效期是预估的（下单后会显示），到时上游可能回收；能不能继续收码以实际为准。"
  ];

  function confirmBuy(opt) {
    opt = opt || {};
    return new Promise(function (resolve) {
      var old = document.getElementById("shCfm");
      if (old) old.remove();
      var list = opt.kind === "stock" ? CFM_STOCK : CFM_PRODUCT;
      var wrap = document.createElement("div");
      wrap.id = "shCfm";
      wrap.className = "sh-cfm";
      wrap.innerHTML =
        '<div class="sh-cfm-card" role="dialog" aria-modal="true" aria-labelledby="shCfmTitle">' +
          '<div class="sh-cfm-head"><h3 id="shCfmTitle">确认购买 · 号码一经发出不能退</h3>' +
          '<button type="button" class="sh-cfm-x" data-cfm="no" aria-label="关闭">×</button></div>' +
          '<div class="sh-cfm-prod"><b>' + esc(opt.name || "号码") + "</b>" +
            (opt.meta ? "<span>" + esc(opt.meta) + "</span>" : "") +
            (opt.price ? '<i class="sh-cfm-price">¥' + esc(opt.price) + "</i>" : "") + "</div>" +
          '<p class="sh-cfm-tip"><b>温馨提示：</b>上游号池有可能把别人用过的号码再分配给你（撞号），这是上游决定的、我们控制不了，想好了再买。<b>只要还没点「确认购买」，随时可以退出；一旦点了，不管你用没用，都退不了。</b></p>' +
          '<ul class="sh-cfm-list">' + list.map(function (x) { return "<li>" + x + "</li>"; }).join("") +
          "</ul>" +
          '<p class="sh-cfm-q">确认平台、国家都选对了吗？点错就没法退了。</p>' +
          '<div class="sh-cfm-acts">' +
            '<button type="button" class="btn ghost" data-cfm="no">再想想</button>' +
            '<button type="button" class="btn primary" data-cfm="yes" id="shCfmYes" disabled>请先阅读（6）</button>' +
          "</div>" +
        "</div>";
      document.body.appendChild(wrap);
      var btn = wrap.querySelector("#shCfmYes");
      var left = 6;
      btn.textContent = "请先阅读（" + left + "）";
      var timer = setInterval(function () {
        left--;
        if (left <= 0) {
          clearInterval(timer);
          btn.disabled = false;
          btn.textContent = "确认购买";
        } else {
          btn.textContent = "请先阅读（" + left + "）";
        }
      }, 1000);
      function onKey(e) { if (e.key === "Escape") close(false); }
      function close(v) {
        clearInterval(timer);
        document.removeEventListener("keydown", onKey);
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
        resolve(!!v);
      }
      document.addEventListener("keydown", onKey);
      wrap.addEventListener("click", function (e) {
        var t = e.target.closest("[data-cfm]");
        if (t) { close(t.dataset.cfm === "yes"); return; }
        if (e.target === wrap) close(false);
      });
    });
  }

  async function buy(pid, btn) {
    if (!STATE.user) {
      toast("请先登录再购买", "bad");
      setTimeout(function () { location.href = "/shop/login?next=/shop"; }, 700);
      return;
    }
    if (STATE.busy) return;
    var p = (STATE.all || []).filter(function (x) { return String(x.id) === String(pid); })[0] || {};
    var meta = [(p.flag || ""), (p.cate_name || ""), (p.card_type || "")].filter(Boolean).join(" · ");
    if (Number(p.success) > 0) meta += " · 成功率 " + p.success + "%";
    var yes = await confirmBuy({ name: p.name || "号码", price: p.price || "", meta: meta });
    if (!yes) return;
    if (STATE.busy) return;
    STATE.busy = true;
    if (btn) { btn.disabled = true; btn.textContent = "发货中…"; }
    var idem = randomIdem();
    api("/shop/api/buy", { method: "POST", body: { product_id: Number(pid), idem: idem } })
      .then(function (d) {
        STATE.busy = false;
        if (!d.ok) {
          if (d._status === 401) { location.href = "/shop/login"; return; }
          toast(d._status === 402
            ? "余额不足，请先购买充值码（最低 ¥5 起充）"
            : (d.error || "下单失败"), "bad");
          if (d._status === 402) {
            setTimeout(function () { location.href = "/shop/me"; }, 1100);
          }
          if (btn) { btn.disabled = false; btn.textContent = "购买"; }
          return;
        }
        if (d.user === undefined && d.balance !== undefined) {
          STATE.user.balance = d.balance;
          setNavUser(STATE.user);
        }
        var o = d.order || {};
        if (o.link) {
          copy(o.link, "已复制卡密，可直接发给客户");
          showLink(o);
        } else {
          toast("订单已提交", "good");
        }
        loadShop();
      });
  }

  /* ------------------------------------------------------------ 现货专区 */
  function stockCardHtml(g) {
    return '<article class="sh-stock-card">' +
      '<div class="sh-stock-flag">' + esc(g.flag || "\u{1F310}") + "</div>" +
      '<div class="sh-stock-main"><b>' + esc(g.cate_name) +
      (g.card_type ? "<span>" + esc(g.card_type) + "</span>" : "") + "</b>" +
      "<small>剩余 <b>" + esc(g.count) + "</b> 个 · 售罄后请到上方商城按平台选购</small></div>" +
      '<div class="sh-stock-buy"><div class="sh-price">¥<b>' + esc(g.price) + "</b></div>" +
      '<button class="btn primary sm" type="button" data-stock="1" data-cate="' + esc(g.cate_name) +
      '" data-ctype="' + esc(g.card_type || "") + '">现货直发</button></div></article>';
  }

  function renderStock(groups) {
    var zone = $("stockZone"), grid = $("stockGrid");
    if (!zone || !grid) return;
    var list = groups || [];
    STATE.stock = list;
    if (!list.length) { zone.classList.add("hidden"); grid.innerHTML = ""; return; }
    zone.classList.remove("hidden");
    grid.innerHTML = list.map(stockCardHtml).join("");
  }

  function loadStock() {
    api("/shop/api/stock").then(function (d) {
      if (d && d.ok) renderStock(d.groups || []);
    });
  }

  async function buyStock(btn) {
    if (!STATE.user) {
      toast("请先登录再购买", "bad");
      setTimeout(function () { location.href = "/shop/login?next=/shop"; }, 700);
      return;
    }
    if (STATE.busy) return;
    var g = null;
    for (var i = 0; i < (STATE.stock || []).length; i++) {
      if (STATE.stock[i].cate_name === btn.dataset.cate &&
          (STATE.stock[i].card_type || "") === (btn.dataset.ctype || "")) { g = STATE.stock[i]; break; }
    }
    var yes = await confirmBuy({
      kind: "stock",
      name: (btn.dataset.cate || "现货卡密") + (btn.dataset.ctype ? " · " + btn.dataset.ctype : ""),
      price: g ? g.price : "",
      meta: "现货直发 · 售出不退"
    });
    if (!yes) return;
    if (STATE.busy) return;
    STATE.busy = true;
    var old = btn.textContent;
    btn.disabled = true; btn.textContent = "发货中…";
    api("/shop/api/stock/buy", { method: "POST", body: {
      cate_name: btn.dataset.cate, card_type: btn.dataset.ctype || "", idem: randomIdem() } })
      .then(function (d) {
        STATE.busy = false; btn.disabled = false; btn.textContent = old;
        if (!d.ok) {
          if (d._status === 401) { location.href = "/shop/login"; return; }
          toast(d.error || "下单失败", "bad");
          if (d._status === 402) { setTimeout(function () { location.href = "/shop/me"; }, 900); }
          if (d._status === 409) loadStock();
          return;
        }
        if (d.balance !== undefined && STATE.user) {
          STATE.user.balance = d.balance;
          setNavUser(STATE.user);
        }
        var o = d.order || {};
        if (o.link) { copy(o.link, "已复制卡密，可直接发给客户"); showLink(o); }
        else { toast("订单已提交", "good"); }
        loadStock();
      });
  }

  function showLink(o) {
    var old = $("linkBox");
    if (old) old.remove();
    var div = document.createElement("div");
    div.id = "linkBox";
    div.className = "sh-notice";
    div.style.borderColor = "#35e0b055";
    div.style.background = "#35e0b010";
    div.style.color = "#b9f7e6";
    var expTxt = ttlText(o.expire_at);
    div.innerHTML = "下单成功 · " + esc(o.name || "") + " · " + esc(o.phone || "") +
      (expTxt ? '<div class="sh-exp">' + esc(expTxt) + "</div>" : "") +
      '<div style="margin-top:8px;font-family:var(--mono);font-size:12px;word-break:break-all">' +
      esc(o.link || "") + "</div>" +
      '<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">' +
      '<button class="btn tiny" data-copy="' + esc(o.link) + '">复制卡密</button>' +
      '<a class="btn tiny" href="/shop/me">去个人中心查看</a></div>';
    $("prodGrid").parentNode.insertBefore(div, $("prodGrid"));
    div.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  /* ------------------------------------------------------------ 登录页 */
  function setTab(reg) {
    if (!$("loginForm")) return;
    $("loginForm").style.display = reg ? "none" : "";
    $("regForm").style.display = reg ? "" : "none";
    $("tabLogin").classList.toggle("on", !reg);
    $("tabReg").classList.toggle("on", reg);
  }

  function bindLogin() {
    setTab(location.hash === "#reg");
    $("tabLogin").addEventListener("click", function () { setTab(false); });
    $("tabReg").addEventListener("click", function () { setTab(true); });
    $("loginForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var err = $("lgErr");
      err.classList.remove("show");
      var btn = $("lgBtn");
      btn.disabled = true;
      api("/shop/api/login", { method: "POST", body: {
        username: $("lgUser").value.trim(), password: $("lgPass").value } }).then(function (d) {
        btn.disabled = false;
        if (!d.ok) { err.textContent = d.error || "登录失败"; err.classList.add("show"); return; }
        STATE.csrf = d.csrf || "";
        goNext();
      });
    });
    $("regForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var err = $("rgErr");
      err.classList.remove("show");
      if ($("rgPass").value !== $("rgPass2").value) {
        err.textContent = "两次输入的密码不一致";
        err.classList.add("show");
        return;
      }
      var btn = $("rgBtn");
      btn.disabled = true;
      api("/shop/api/register", { method: "POST", body: {
        username: $("rgUser").value.trim(), password: $("rgPass").value,
        code: ($("rgCode").value || "").trim() } }).then(function (d) {
        btn.disabled = false;
        if (!d.ok) { err.textContent = d.error || "注册失败"; err.classList.add("show"); return; }
        STATE.csrf = d.csrf || "";
        toast("注册成功，已自动登录", "good");
        goNext();
      });
    });
    api("/shop/api/me").then(function (d) {
      if (d.ok && d.user) { STATE.csrf = d.csrf; goNext(); }
    });
  }

  function goNext() {
    var m = location.search.match(/[?&]next=([^&]+)/);
    var next = m ? decodeURIComponent(m[1]) : "/shop";
    if (next.indexOf("/shop") !== 0) next = "/shop";
    location.href = next;
  }

  /* ------------------------------------------------------------ 个人中心 */
  function statusHtml(s) {
    var map = { done: ["已发货", "done"], pending: ["处理中", "pending"],
                failed: ["失败退款", "failed"], refunded: ["已退款", "refunded"] };
    var x = map[s] || [s || "—", ""];
    return '<span class="sh-st ' + x[1] + '">' + esc(x[0]) + "</span>";
  }

  function renderOrders(list) {
    var body = $("orderBody");
    if (!body) return;
    if (!list.length) {
      body.innerHTML = '<tr><td colspan="8" class="mono">还没有购买记录，去商城选一个平台试试。</td></tr>';
      return;
    }
    body.innerHTML = list.map(function (o) {
      var link = o.link || "";
      var kw = o.keyword || "";
      var cell = link
        ? '<div class="sh-link"><code title="' + esc(link) + '">' + esc(link) + "</code></div>" +
          (kw ? '<div class="sh-kw">只收「' + esc(kw) + '」短信 · 收不到就点右边改</div>'
              : '<div class="sh-kw">不过滤关键词（任何短信都放行）</div>')
        : '<span class="mono">' + esc(o.error || "—") + "</span>";
      var op = link
        ? '<button class="btn tiny" data-copy="' + esc(link) + '">复制</button>' +
          '<button class="btn tiny ghost" data-kw="' + esc(o.id) + '" data-cur="' + esc(kw) +
          '">关键词</button>'
        : '<span class="mono">—</span>';
      var expTxt2 = ttlText(o.expire_at);
      return "<tr><td class='mono'>" + fmt(o.created_at) + "</td><td>" + esc(o.name) +
        (o.card_type ? ' <span class="sh-tag">' + esc(o.card_type) + "</span>" : "") +
        "</td><td class='mono'>" + esc(o.phone || "—") + "</td><td class='mono sh-expcell'>" +
        (expTxt2 ? esc(expTxt2) : "—") + "</td><td class='mono'>¥" +
        esc(o.price) + "</td><td>" + statusHtml(o.status) + "</td><td>" + cell +
        "</td><td>" + op + "</td></tr>";
    }).join("");
  }

  function loadMe() {
    api("/shop/api/me").then(function (d) {
      if (!d.ok || !d.user) { location.href = "/shop/login?next=/shop/me"; return; }
      STATE.csrf = d.csrf || "";
      STATE.user = d.user;
      $("meName").textContent = d.user.username;
      $("balVal").textContent = d.user.balance;
      renderOrders(d.orders || []);
    });
  }

  function bindMe() {
    var ob = $("orderBody");
    if (ob) {
      ob.addEventListener("click", function (e) {
        var b = e.target.closest("[data-kw]");
        if (!b) return;
        var cur = b.dataset.cur || "";
        var kw = window.prompt("这条卡密要接收哪个平台的短信？\n\n填平台名或短信里的字词，例如：豆包、日淘市集、验证码\n（填「验证码」最通用，但可能被同号上其它短信抢先）", cur);
        if (kw === null) return;
        kw = (kw || "").trim();
        if (!kw) { toast("关键词不能为空", "bad"); return; }
        api("/shop/api/orders/" + b.dataset.kw + "/keyword", { method: "POST", body: { keyword: kw } })
          .then(function (d) {
            if (!d.ok) { toast(d.error || "修改失败", "bad"); return; }
            toast("关键词已改为「" + kw + "」，重新打开接码地址即可", "good");
            loadMe();
          });
      });
    }
    $("btnLogout").addEventListener("click", function () {
      api("/shop/api/logout", { method: "POST", body: {} }).then(function () {
        location.href = "/shop/login";
      });
    });
    $("rcForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var err = $("rcErr"), btn = $("rcBtn");
      err.classList.remove("show");
      btn.disabled = true;
      api("/shop/api/recharge", { method: "POST", body: { code: $("rcCode").value.trim() } })
        .then(function (d) {
          btn.disabled = false;
          if (!d.ok) { err.textContent = d.error || "充值失败"; err.classList.add("show"); return; }
          $("rcCode").value = "";
          $("balVal").textContent = d.balance;
          $("balNote").textContent = "本次充值 ¥" + d.amount + "，余额可用于任意平台下单";
          toast("充值成功 +¥" + d.amount, "good");
          loadMe();
        });
    });
    $("pwForm").addEventListener("submit", function (e) {
      e.preventDefault();
      var err = $("pwErr");
      err.classList.remove("show");
      api("/shop/api/password", { method: "POST", body: {
        old: $("pwOld").value, new: $("pwNew").value } }).then(function (d) {
          if (!d.ok) { err.textContent = d.error || "修改失败"; err.classList.add("show"); return; }
          $("pwOld").value = ""; $("pwNew").value = "";
          toast("密码已更新", "good");
        });
    });
  }

  /* ------------------------------------------------------------ 绑定 */
  document.addEventListener("click", function (e) {
    var c = e.target.closest("[data-copy]");
    if (c) { copy(c.dataset.copy, "已复制卡密"); return; }
    var chip = e.target.closest(".sh-chip");
    if (chip) {
      var k = chip.dataset.k || "";
      var inRegion = chip.parentNode && chip.parentNode.id === "regionChips";
      if (inRegion) STATE.region = k; else STATE.type = k;
      renderChips();
      loadShop();
      return;
    }
    var more = e.target.closest("[data-more]");
    if (more) {
      STATE.shown = (STATE.shown || 24) + 24;
      paintProducts();
      return;
    }
    var sb = e.target.closest("[data-stock]");
    if (sb && !sb.disabled) { buyStock(sb); return; }
    var b = e.target.closest("[data-buy]");
    if (b && !b.disabled) { buy(b.dataset.buy, b); }
  });

  var timer = null;
  document.addEventListener("input", function (e) {
    if (e.target.id !== "shQ") return;
    clearTimeout(timer);
    timer = setTimeout(function () { STATE.q = e.target.value.trim(); loadShop(); }, 280);
  });

  if (page === "shop") {
    loadShop(); loadStock();
    if (!STATE.csrf) refreshCsrf();
  }
  else if (page === "login") bindLogin();
  else if (page === "me") { bindMe(); loadMe(); }
})();