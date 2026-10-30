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
        .then(function (d) { d._status = r.status; return d; });
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
      nav.innerHTML = u
        ? '<a class="btn ghost sm" href="/shop/me">' + esc(u.username) + " · ¥" + esc(u.balance) +
          '</a><a class="btn primary sm" href="/shop">继续选购</a>'
        : '<a class="btn ghost sm" href="/shop/login">登录</a>' +
          '<a class="btn primary sm" href="/shop/login#reg">注册</a>';
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

  function cardHtml(p) {
    var oos = p.stock <= 0;
    var rate = Math.max(2, Math.min(100, Number(p.success) || 0));
    return '<article class="sh-card' + (oos ? " off" : "") + '">' +
      '<div class="sh-ico">' + esc(p.icon || "\u{1F511}") + "</div>" +
      '<div class="sh-cbody"><h3>' + esc(p.name) +
      (p.card_type ? '<span>' + esc(p.card_type) + "</span>" : "") + "</h3>" +
      '<div class="sh-tags">' +
      '<span class="sh-tag">' + esc(p.flag) + " " + esc(p.cate_name || "全球") + "</span>" +
      (oos ? '<span class="sh-tag warn">缺货</span>'
           : '<span class="sh-tag idle">库存 ' + esc(p.stock) + "</span>") +
      "</div>" +
      '<div class="sh-meter"><span>成功率 ' + esc(p.success) + "%</span>" +
      '<span class="sh-bar"><i class="' + rateClass(p.success) + '" style="width:' +
      rate + '%"></i></span></div></div>' +
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
      renderChips();
      renderProducts(d.products || []);
      setNavUser(d.user || null);
    });
  }

  function buy(pid, btn) {
    if (!STATE.user) {
      toast("请先登录再购买", "bad");
      setTimeout(function () { location.href = "/shop/login?next=/shop"; }, 700);
      return;
    }
    if (STATE.busy) return;
    STATE.busy = true;
    if (btn) { btn.disabled = true; btn.textContent = "发货中…"; }
    var idem = randomIdem();
    api("/shop/api/buy", { method: "POST", body: { product_id: Number(pid), idem: idem } })
      .then(function (d) {
        STATE.busy = false;
        if (!d.ok) {
          if (d._status === 401) { location.href = "/shop/login"; return; }
          toast(d.error || "下单失败", "bad");
          if (d._status === 402) {
            setTimeout(function () { location.href = "/shop/me"; }, 900);
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

  function showLink(o) {
    var old = $("linkBox");
    if (old) old.remove();
    var div = document.createElement("div");
    div.id = "linkBox";
    div.className = "sh-notice";
    div.style.borderColor = "#35e0b055";
    div.style.background = "#35e0b010";
    div.style.color = "#b9f7e6";
    div.innerHTML = "下单成功 · " + esc(o.name || "") + " · " + esc(o.phone || "") +
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
        username: $("rgUser").value.trim(), password: $("rgPass").value } }).then(function (d) {
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
      body.innerHTML = '<tr><td colspan="7" class="mono">还没有购买记录，去商城选一个平台试试。</td></tr>';
      return;
    }
    body.innerHTML = list.map(function (o) {
      var link = o.link || "";
      var cell = link
        ? '<div class="sh-link"><code title="' + esc(link) + '">' + esc(link) + "</code></div>"
        : '<span class="mono">' + esc(o.error || "—") + "</span>";
      var op = link
        ? '<button class="btn tiny" data-copy="' + esc(link) + '">复制</button>'
        : '<span class="mono">—</span>';
      return "<tr><td class='mono'>" + fmt(o.created_at) + "</td><td>" + esc(o.name) +
        (o.card_type ? ' <span class="sh-tag">' + esc(o.card_type) + "</span>" : "") +
        "</td><td class='mono'>" + esc(o.phone || "—") + "</td><td class='mono'>¥" +
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
    var b = e.target.closest("[data-buy]");
    if (b && !b.disabled) { buy(b.dataset.buy, b); }
  });

  var timer = null;
  document.addEventListener("input", function (e) {
    if (e.target.id !== "shQ") return;
    clearTimeout(timer);
    timer = setTimeout(function () { STATE.q = e.target.value.trim(); loadShop(); }, 280);
  });

  if (page === "shop") loadShop();
  else if (page === "login") bindLogin();
  else if (page === "me") { bindMe(); loadMe(); }
})();