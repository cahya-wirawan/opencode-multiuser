/* codehover: hover explanations, definition brackets, search. No dependencies. */
(function () {
  "use strict";
  var body = document.body;
  var ROOT = body.getAttribute("data-root") || "";
  var INDEX = window.__CH_INDEX || { syms: [], files: {} };
  var dataEl = document.getElementById("ch-data");
  var DATA = dataEl ? JSON.parse(dataEl.textContent) : null;
  var tip = document.getElementById("tip");
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var UI = window.__CH_UI || {};
  var LANG = window.__CH_LANG || "en";
  var plurals = null;
  try { plurals = new Intl.PluralRules(LANG); } catch (e) { plurals = null; }

  // Interface text in the page's language: t("key", { n: 3 }). Plural entries are keyed by CLDR category.
  function t(key, vars) {
    var v = UI[key];
    if (v === undefined) return key;
    if (typeof v === "object") {
      var n = vars && vars.n !== undefined ? vars.n : 0;
      var cat = plurals ? plurals.select(n) : (n === 1 ? "one" : "other");
      v = v[cat] || v.other || v[Object.keys(v)[0]];
    }
    return String(v).replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] !== undefined ? vars[k] : m; });
  }
  // The same, with every value HTML-escaped first (for text that goes into innerHTML).
  function th(key, vars) {
    var safe = {};
    Object.keys(vars || {}).forEach(function (k) { safe[k] = esc(vars[k]); });
    return t(key, safe).replace(/&(?![a-z]+;|#\d+;)/g, "&amp;");
  }
  function kindLabel(kind) { return UI["kind_" + kind] !== undefined ? t("kind_" + kind) : kind; }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function store(key, val) {
    try {
      if (val === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, val);
    } catch (e) { return null; }
  }
  var CLASSY = { "class": 1, struct: 1, "interface": 1, trait: 1, "enum": 1, type: 1, impl: 1, module: 1, namespace: 1 };
  function kindClass(kind) { return "k-" + kind; }
  function fileName(path) { return path.split("/").pop(); }

  // ------------------------------------------------------------ card builders
  function locHtml(s) {
    var where = s.same ? t("loc_line", { n: s.line }) : t("loc_file_line", { file: fileName(s.path), n: s.line });
    return '<a class="tp-loc" href="' + esc(s.href) + '">' + esc(where) + "</a>";
  }

  function symCard(s, opts) {
    opts = opts || {};
    var c = s.card || {};
    var h = '<div class="tp-head"><span class="tp-kind">' + esc(kindLabel(s.kind)) + "</span>" +
      '<span class="tp-name">' + esc(s.q) + "</span>" + locHtml(s) + "</div>";
    if (s.sig) h += '<div class="tp-sig">' + esc(s.sig) + "</div>";
    if (c.summary) {
      h += '<div class="tp-sum">' + c.summary + "</div>";
      if (c.details) h += '<div class="tp-det">' + c.details + "</div>";
    } else {
      h += '<p class="tp-empty">' + th("no_explanation") + "</p>";
    }
    if (c.params && c.params.length) {
      h += '<div class="tp-sec"><span class="tp-label">' + th("parameters") + '</span><dl class="tp-params">';
      c.params.forEach(function (p) {
        h += '<dt class="' + (opts.param === p[0] ? "me" : "") + '">' + esc(p[0]) + "</dt><dd>" + p[1] + "</dd>";
      });
      h += "</dl></div>";
    }
    if (c.returns) h += '<div class="tp-sec"><span class="tp-label">' + th("returns") + "</span>" + c.returns + "</div>";
    if (c.notes) h += '<div class="tp-sec tp-note">' + c.notes + "</div>";
    if (s.used && s.used.length) {
      h += '<div class="tp-sec tp-alt"><span class="tp-label">' + th("used_by") + "</span>" + s.used.map(function (u) {
        return '<a href="' + esc(u[1]) + '">' + esc(u[0]) + "</a>" + (u[3] ? " " + th("in_file", { file: u[3] }) : "") +
          (u[2] > 1 ? " ×" + u[2] : "");
      }).join(th("list_sep")) + (s.usedMore ? th("list_sep") + th("and_more", { n: s.usedMore }) : "") +
        (s.usedByName ? ' <span class="tp-dim">' + th("some_by_name") + "</span>" : "") + "</div>";
    }
    if (s.uses && s.uses.length) {
      h += '<div class="tp-sec tp-alt"><span class="tp-label">' + th("uses") + "</span>" + s.uses.map(function (u) {
        return '<a href="' + esc(u[1]) + '">' + esc(u[0]) + "</a>";
      }).join(th("list_sep")) + "</div>";
    }
    if (s.guides && s.guides.length) {
      h += '<div class="tp-sec tp-alt">' + th("in_guide") + " " + s.guides.map(function (g) {
        return '<a class="tp-g" href="' + esc(g[1]) + '">' + esc(g[0]) + "</a>";
      }).join(th("list_sep")) + "</div>";
    }
    if (opts.alts && opts.alts.length) {
      h += '<div class="tp-sec tp-alt">' + th("other_defs", { n: opts.alts.length }) + " " + opts.alts.map(function (a) {
          return '<a href="' + esc(a.href) + '">' + esc(a.q) + "</a>" + (a.same ? "" : " " + th("in_file", { file: fileName(a.path) }));
        }).join(th("list_sep")) + "</div>";
    }
    h += foot(s.src, opts.byName);
    return h;
  }

  function paramCard(owner, name) {
    var desc = "";
    (owner.card && owner.card.params || []).forEach(function (p) { if (p[0] === name) desc = p[1]; });
    var h = '<div class="tp-head"><span class="tp-kind" style="--kc: var(--k-param)">' + th("kind_parameter") + "</span>" +
      '<span class="tp-name">' + esc(name) + "</span>" + locHtml(owner) + "</div>";
    h += '<div class="tp-sum">' + (desc || '<p class="tp-empty">' + th("no_description") + "</p>") + "</div>";
    var link = '<a href="' + esc(owner.href) + '">' + esc(owner.q) + "</a>";
    h += '<div class="tp-sec tp-alt">' + t("parameter_of_html", { owner: link });
    // English reads as one sentence ("Parameter of f, which adds..."); other languages get the
    // owner's summary on its own line, since the clause does not translate word for word.
    if (owner.card && owner.card.summary) {
      h += LANG === "en" ? ", which " + lowerFirst(stripP(owner.card.summary))
                         : '<div class="tp-dim">' + stripP(owner.card.summary) + "</div>";
    }
    h += "</div>" + foot(null);
    return h;
  }
  function stripP(h) { return h.replace(/^<p>/, "").replace(/<\/p>$/, "").split("</p><p>")[0]; }
  function lowerFirst(h) {
    // "Adds two values." -> "adds two values." (leave code/HTML starts alone)
    return /^[A-Z][a-z]/.test(h) ? h.charAt(0).toLowerCase() + h.slice(1) : h;
  }

  function extCard(x) {
    var h = '<div class="tp-head"><span class="tp-kind" style="--kc: var(--muted)">' + th("kind_external") + "</span>" +
      '<span class="tp-name">' + esc(x.name) + "</span>" +
      (x.pkg ? '<span class="tp-loc">' + esc(x.pkg + (x.ver ? " " + x.ver : "")) + "</span>" : "") + "</div>";
    if (x.sig) h += '<div class="tp-sig" style="--kc: var(--rule)">' + esc(x.sig) + "</div>";
    if (x.doc) h += '<div class="tp-det tp-ext">' + x.doc + "</div>";
    else if (!x.sig) h += '<p class="tp-empty">' + th("no_index_doc") + "</p>";
    return h + '<div class="tp-foot"><span class="hint">' + th("defined_outside") + "</span></div>";
  }

  function attrCard(a) {
    var where = a.same ? t("loc_line", { n: a.line }) : t("loc_file_line", { file: fileName(a.path), n: a.line });
    var h = '<div class="tp-head"><span class="tp-kind" style="--kc: var(--syn-builtin)">' + esc(kindLabel(a.kind)) + "</span>" +
      '<span class="tp-name">' + esc(a.q) + '</span><a class="tp-loc" href="' + esc(a.href) + '">' + esc(where) + "</a></div>";
    h += a.note ? '<p class="tp-lines">' + th("note_where_set") + "</p>" + a.note
                : '<p class="tp-alt">' + th("first_assigned", { where: where }) + "</p>";
    return h + foot(null);
  }

  function multiCard(name, list) {
    var h = '<div class="tp-head"><span class="tp-kind">' + th("n_definitions_named", { n: list.length }) + "</span>" +
      '<span class="tp-name">' + esc(name) + "</span></div>" +
      '<p class="tp-alt">' + th("matched_by_name") + "</p>";
    h += '<ul class="tp-multi">' + list.map(function (s) {
      var sum = s.card && s.card.summary ? stripP(s.card.summary) : '<span class="tp-empty">' + th("no_explanation_short") + "</span>";
      return '<li><a href="' + esc(s.href) + '">' + esc(s.q) + "</a>" +
        (s.same ? "" : ' <span class="tp-in">' + th("in_file", { file: fileName(s.path) }) + "</span>") +
        '<div class="tp-msum">' + sum + "</div></li>";
    }).join("") + "</ul>";
    return h + foot(null);
  }

  function citeCard(c) {
    var label = c.whole ? t("whole_file") : (c.a === c.b ? t("cite_line", { n: c.a }) : t("cite_lines", { a: c.a, b: c.b }));
    var h = '<div class="tp-head"><span class="tp-name">' + esc(c.path) + '</span><span class="tp-kind" style="--kc: var(--muted)">' +
      esc(label) + '</span><a class="tp-loc" href="' + esc(c.href) + '">' + th("open") + "</a></div>" +
      '<div class="tp-code">' + c.html + "</div>";
    if (c.more > 0) h += '<p class="tp-alt">' + th("more_lines", { n: c.more }) + "</p>";
    if (c.v) {
      var verdict = th("verdict_" + c.v.verdict);
      var how = th(c.v.by === "snippet" ? "by_snippet" : "by_model");
      h += '<div class="tp-sec tp-check ck-' + c.v.verdict + '"><span class="tp-label">' + verdict + "</span>" +
        (c.v.reason ? esc(c.v.reason) + " " : "") + '<span class="tp-dim">(' + how + ")</span></div>";
    }
    return h;
  }

  function noteCard(n) {
    var label = n.start === n.end ? th("note_line", { n: n.start }) : th("note_lines", { a: n.start, b: n.end });
    return '<div class="tp-lines">' + label + "</div>" + n.html + foot(null);
  }

  function fileCard(path) {
    var summary = INDEX.files[path];
    return '<div class="tp-file">' + esc(path) + "</div>" +
      (summary ? "<p>" + esc(summary).replace(/`([^`]+)`/g, "<code>$1</code>") + "</p>" : '<p class="tp-empty">' + th("no_summary") + "</p>");
  }

  function foot(src, byName) {
    var hint = th(state.pinned ? "esc_closes" : "click_to_keep");
    return '<div class="tp-foot"><span class="hint">' + hint + "</span>" +
      (byName ? '<span class="hint">' + th("linked_by_name") + "</span>" : "") +
      (src ? '<a class="sp" href="' + esc(src) + '">' + th("view_original") + "</a>" : "") + "</div>";
  }

  // ------------------------------------------------------------ highlight helpers
  var lit = [];   // elements we decorated, to undo
  function lineEl(n) { return document.getElementById("L" + n); }
  function decorate(el, cls) { el.classList.add.apply(el.classList, cls); lit.push([el, cls]); }
  function clearLit() {
    lit.forEach(function (pair) { pair[0].classList.remove.apply(pair[0].classList, pair[1]); });
    lit = [];
  }
  function bracket(s) {
    if (!s || !s.same) return;
    for (var n = s.line; n <= s.end; n++) {
      var el = lineEl(n);
      if (!el) continue;
      var cls = ["br", kindClass(s.kind)];
      if (n === s.line) cls.push("br-top");
      if (n === s.end) cls.push("br-bot");
      decorate(el, cls);
    }
  }
  function hotLines(n) {
    for (var i = n.start; i <= n.end; i++) { var el = lineEl(i); if (el) decorate(el, ["hot"]); }
  }
  function markParam(sidx, name) {
    document.querySelectorAll('.prm[data-s="' + sidx + '"]').forEach(function (el) {
      if (el.getAttribute("data-p") === name) decorate(el, ["on"]);
    });
  }

  // ------------------------------------------------------------ tooltip state machine
  var state = { target: null, pinned: false, showT: 0, hideT: 0, c: null };

  function contentFor(el) {
    if (!DATA && !el.hasAttribute("data-f")) return null;
    if (el.hasAttribute("data-f")) return { html: fileCard(el.getAttribute("data-f")), kc: null };
    if (el.hasAttribute("data-x")) return { html: extCard(DATA.ext[+el.getAttribute("data-x")]), kc: "var(--muted)" };
    if (el.hasAttribute("data-a")) return { html: attrCard(DATA.attrs[+el.getAttribute("data-a")]), kc: "var(--syn-builtin)" };
    if (el.hasAttribute("data-cite")) return { html: citeCard(DATA.cites[+el.getAttribute("data-cite")]), kc: "var(--muted)", wide: true };
    if (el.classList.contains("prm")) {
      var owner = DATA.syms[+el.getAttribute("data-s")];
      var name = el.getAttribute("data-p");
      return { html: paramCard(owner, name), kc: "var(--k-param)",
               after: function () { bracket(owner); markParam(el.getAttribute("data-s"), name); } };
    }
    if (el.hasAttribute("data-c")) {
      var ids = el.getAttribute("data-c").split(",").map(Number);
      var all = ids.map(function (i) { return DATA.syms[i]; });
      if (el.hasAttribute("data-amb")) return { html: multiCard(all[0].name, all), kind: all[0].kind };
      return { html: symCard(all[0], { alts: all.slice(1), byName: el.hasAttribute("data-how") }), kind: all[0].kind,
               after: function () { bracket(all[0]); } };
    }
    if (el.hasAttribute("data-s")) {
      var s = DATA.syms[+el.getAttribute("data-s")];
      return { html: symCard(s), kind: s.kind, after: function () { bracket(s); } };
    }
    if (el.hasAttribute("data-note")) {
      var n = DATA.notes[+el.getAttribute("data-note")];
      return { html: noteCard(n), kc: "var(--hl)", note: n, after: function () { hotLines(n); } };
    }
    return null;
  }

  function anchorRect(el, c) {
    if (c.note) {
      var first = lineEl(c.note.start), last = lineEl(c.note.end) || first;
      var code = first.querySelector(".c");
      var r1 = code.getBoundingClientRect(), r2 = last.getBoundingClientRect();
      return { left: r1.left, right: r1.left + 40, top: first.getBoundingClientRect().top, bottom: r2.bottom };
    }
    return el.getBoundingClientRect();
  }

  function place(el, c, still) {
    var r = anchorRect(el, c);
    tip.style.visibility = "hidden";
    tip.hidden = false;
    tip.style.left = "0px"; tip.style.top = "0px";
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var vw = document.documentElement.clientWidth, vh = window.innerHeight;
    var left = Math.min(Math.max(12, r.left - 4), vw - w - 12);
    var below = r.bottom + 8, above = r.top - h - 8;
    var top = (below + h <= vh - 8 || above < 60) ? below : above;
    top = Math.max(60, Math.min(top, vh - h - 8));
    tip.style.left = left + "px";
    tip.style.top = top + "px";
    tip.style.visibility = "";
    if (!reduceMotion && !still) {
      tip.style.opacity = "0";
      requestAnimationFrame(function () { tip.style.opacity = "1"; });
    }
  }

  function show(el, pin) {
    var c = contentFor(el);
    if (!c) return;
    clearTimeout(state.hideT);
    if (state.target && state.target !== el) state.target.classList.remove("on");
    clearLit();
    state.target = el;
    state.c = c;
    state.pinned = !!pin;
    tip.innerHTML = c.html;
    tip.className = (pin ? "pinned " : "") + (c.kind ? kindClass(c.kind) : "") + (c.wide ? " wide" : "");
    tip.style.removeProperty("--kc");
    if (c.kc) tip.style.setProperty("--kc", c.kc);
    if (el.classList.contains("sym") || el.classList.contains("o")) el.classList.add("on");
    if (c.after) c.after();
    place(el, c);
  }

  function hide() {
    clearTimeout(state.showT);
    tip.hidden = true;
    if (state.target) state.target.classList.remove("on");
    state.target = null;
    state.pinned = false;
    clearLit();
  }

  function hoverTarget(t) {
    if (!(t instanceof Element)) return null;
    if (tip.contains(t)) return null;
    var el = t.closest(".sym, .outline a.o, .tree a.file[data-f], a.cite");
    if (el) return el;
    if (body.classList.contains("mode-inline")) return null;
    var line = t.closest(".l.nr");
    if (line && (t.closest(".c") || t.closest(".g"))) return line;
    return null;
  }
  function delayFor(el) {
    if (el.classList.contains("sym")) return 110;
    if (el.classList.contains("cite")) return 200;
    if (el.classList.contains("l")) return 380;
    return 260;
  }

  document.addEventListener("pointerover", function (e) {
    if (e.pointerType === "touch") return;
    var el = hoverTarget(e.target);
    if (tip.contains(e.target)) { clearTimeout(state.hideT); clearTimeout(state.showT); return; }
    if (state.pinned) return;
    clearTimeout(state.showT);
    if (!el) {
      clearTimeout(state.hideT);
      state.hideT = setTimeout(hide, 160);
      return;
    }
    if (el === state.target) { clearTimeout(state.hideT); return; }
    // When a gloss is already open, wait a little so the pointer can travel into it.
    var d = tip.hidden ? delayFor(el) : 200;
    state.showT = setTimeout(function () { show(el, false); }, d);
  });

  document.addEventListener("click", function (e) {
    if (tip.contains(e.target)) return;
    var el = e.target.closest ? e.target.closest(".sym") : null;
    if (!el && e.target.closest) {
      var line = e.target.closest(".l.nr .c");
      var sel = window.getSelection && String(window.getSelection());
      if (line && !sel && !body.classList.contains("mode-inline")) el = line.parentNode;
    }
    if (el) {
      e.preventDefault();
      if (state.pinned && state.target === el) { hide(); return; }
      show(el, true);
      return;
    }
    if (state.pinned) hide();
  });

  document.addEventListener("focusin", function (e) {
    var el = e.target.closest && e.target.closest(".sym");
    if (el && !state.pinned) show(el, false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !tip.hidden) { hide(); return; }
    if (e.key === "Enter" && e.target.classList && e.target.classList.contains("sym")) {
      show(e.target, true); e.preventDefault();
    }
  });
  window.addEventListener("scroll", function () {
    if (tip.hidden) return;
    if (state.pinned) place(state.target, state.c, true); else hide();
  }, { passive: true });
  window.addEventListener("resize", function () { if (!tip.hidden) hide(); });

  // ------------------------------------------------------------ notes mode
  var modeButtons = document.querySelectorAll(".mode button");
  function setMode(m) {
    body.classList.toggle("mode-inline", m === "inline");
    modeButtons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-mode") === m)); });
    store("codehover-mode", m);
  }
  modeButtons.forEach(function (b) {
    b.addEventListener("click", function () { hide(); setMode(b.getAttribute("data-mode")); });
  });
  if (modeButtons.length) setMode(store("codehover-mode") === "inline" ? "inline" : "hover");

  // ------------------------------------------------------------ #L12 and #L12-L20
  var targeted = [];
  function applyHash() {
    targeted.forEach(function (el) { el.classList.remove("target"); });
    targeted = [];
    var m = /^#L(\d+)(?:-L?(\d+))?$/.exec(location.hash);
    if (!m) return;
    var a = +m[1], b = +(m[2] || m[1]);
    for (var n = a; n <= b; n++) { var el = lineEl(n); if (el) { el.classList.add("target"); targeted.push(el); } }
    if (targeted[0]) targeted[0].scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
  }
  window.addEventListener("hashchange", applyHash);
  if (DATA) applyHash();

  // ------------------------------------------------------------ diagrams (guide pages)
  var figs = Array.prototype.slice.call(document.querySelectorAll("figure.diagram"));
  var mermaidSrc = body.getAttribute("data-mermaid");
  function diagramFailed(fig, msg) {
    var cap = fig.querySelector(".dg-msg");
    cap.textContent = msg; cap.hidden = false;
    fig.classList.add("failed");
  }
  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function drawDiagrams() {
    var m = window.mermaid;
    if (!m) { figs.forEach(function (f) { diagramFailed(f, t("diagram_failed")); }); return; }
    var serif = cssVar("--serif");
    m.initialize({
      startOnLoad: false, securityLevel: "strict", theme: "base", fontFamily: serif,
      flowchart: { curve: "basis", padding: 14 },
      sequence: { actorFontSize: 15, messageFontSize: 15, noteFontSize: 14, mirrorActors: false,
                  actorMargin: 28, width: 118, boxMargin: 8 },
      themeVariables: {
        fontFamily: serif, fontSize: "15px", background: cssVar("--sheet"), textColor: cssVar("--ink"),
        primaryColor: cssVar("--sheet"), primaryTextColor: cssVar("--ink"), primaryBorderColor: cssVar("--k-class"),
        secondaryColor: cssVar("--tint"), secondaryTextColor: cssVar("--ink"), secondaryBorderColor: cssVar("--k-fn"),
        tertiaryColor: cssVar("--paper"), tertiaryTextColor: cssVar("--ink"), tertiaryBorderColor: cssVar("--rule"),
        mainBkg: cssVar("--sheet"), nodeBorder: cssVar("--k-class"), lineColor: cssVar("--muted"),
        clusterBkg: cssVar("--paper"), clusterBorder: cssVar("--rule"), edgeLabelBackground: cssVar("--sheet"),
        actorBkg: cssVar("--sheet"), actorBorder: cssVar("--k-class"), actorTextColor: cssVar("--ink"),
        signalColor: cssVar("--ink"), signalTextColor: cssVar("--ink"), labelBoxBkgColor: cssVar("--sheet"),
        noteBkgColor: cssVar("--tint"), noteBorderColor: cssVar("--hl"), noteTextColor: cssVar("--ink"),
        classText: cssVar("--ink")
      }
    });
    var i = 0;
    (function next() {
      var fig = figs[i++];
      if (!fig) return;
      var pre = fig.querySelector("pre.mermaid");
      var id = "ch-mmd-" + i;
      m.render(id, pre.textContent).then(function (r) {
        var div = document.createElement("div");
        div.className = "dg";
        div.innerHTML = r.svg;
        fig.insertBefore(div, pre);
        // Mermaid scales a wide diagram down to fit; past ~80% the text gets unreadable,
        // so stop shrinking there and let the figure scroll sideways instead.
        var svg = div.querySelector("svg");
        var natural = svg && parseFloat(svg.style.maxWidth);
        if (natural) svg.style.minWidth = Math.round(natural * 0.8) + "px";
        fig.classList.add("drawn");
        next();
      }).catch(function () {
        var stray = document.getElementById("d" + id);
        if (stray) stray.remove();
        diagramFailed(fig, t("diagram_syntax"));
        next();
      });
    })();
  }
  if (figs.length) {
    if (!mermaidSrc) figs.forEach(function (f) { diagramFailed(f, t("diagram_off")); });
    else {
      var sc = document.createElement("script");
      sc.src = mermaidSrc; sc.async = true;
      sc.onload = drawDiagrams;
      sc.onerror = function () {
        figs.forEach(function (f) { diagramFailed(f, t("diagram_offline")); });
      };
      document.head.appendChild(sc);
    }
  }

  // ------------------------------------------------------------ language and reader level
  // Every version of the site has the same pages, so switching keeps the page and the position.
  var variantInfo = window.__CH_VARIANTS;
  document.querySelectorAll("select[data-switch]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (!variantInfo) return;
      var cur = variantInfo.all[variantInfo.current];
      var want = { lang: cur.lang, level: cur.level };
      want[sel.getAttribute("data-switch")] = sel.value;
      var target = Object.keys(variantInfo.all).filter(function (k) {
        var v = variantInfo.all[k];
        return v.lang === want.lang && v.level === want.level;
      })[0];
      if (!target) return;
      var top = body.getAttribute("data-top") || "";
      var dir = variantInfo.all[target].dir;
      location.href = top + (dir ? dir + "/" : "") + (body.getAttribute("data-page") || "index.html") + location.hash;
    });
  });

  // ------------------------------------------------------------ search
  var q = document.getElementById("q");
  var list = document.getElementById("q-results");
  var results = [], sel = -1;

  function search(term) {
    term = term.trim().toLowerCase();
    if (!term) return [];
    var scored = [];
    INDEX.syms.forEach(function (row) {
      var qname = row[0].toLowerCase(), name = qname.split(".").pop();
      var score = name === term ? 0 : name.indexOf(term) === 0 ? 1 : qname.indexOf(term) !== -1 ? 2 :
        (row[4] || "").toLowerCase().indexOf(term) !== -1 ? 4 : -1;
      if (score >= 0) scored.push({ score: score, row: row, type: "sym" });
    });
    Object.keys(INDEX.files).forEach(function (p) {
      if (p.toLowerCase().indexOf(term) !== -1) scored.push({ score: 3, path: p, type: "file" });
    });
    (INDEX.guides || []).forEach(function (g) {
      var t = g[0].toLowerCase();
      var sc = t.indexOf(term) === 0 ? 1 : t.indexOf(term) !== -1 ? 2 : (g[2] || "").toLowerCase().indexOf(term) !== -1 ? 4 : -1;
      if (sc >= 0) scored.push({ score: sc, guide: g, type: "guide" });
    });
    scored.sort(function (a, b) {
      return a.score - b.score || (a.type === "guide" ? -1 : b.type === "guide" ? 1 : 0) ||
        (a.row ? a.row[0].length : 0) - (b.row ? b.row[0].length : 0);
    });
    return scored.slice(0, 14);
  }
  function hrefFor(r) {
    if (r.type === "guide") return ROOT + r.guide[1];
    return r.type === "file" ? ROOT + "src/" + r.path + ".html"
      : ROOT + "src/" + r.row[2] + ".html#L" + r.row[3];
  }
  function renderResults() {
    if (!results.length) {
      list.innerHTML = q.value.trim() ? '<li class="empty">' + th("no_match", { q: q.value.trim() }) + "</li>" : "";
      list.hidden = !q.value.trim();
      return;
    }
    list.innerHTML = results.map(function (r, i) {
      var inner = r.type === "guide"
        ? '<span class="qk">' + th("result_guide") + '</span><span class="qg">' + esc(r.guide[0]) + "</span>" +
          (r.guide[2] ? '<span class="qs">' + esc(r.guide[2].replace(/`/g, "")) + "</span>" : "")
        : r.type === "file"
        ? '<span class="qk">' + th("result_file") + '</span><span class="qn">' + esc(r.path) + "</span>"
        : '<span class="qk">' + esc(kindLabel(r.row[1])) + '</span><span class="qn">' + esc(r.row[0]) + '</span> <span class="qp">' +
          th("in_file", { file: fileName(r.row[2]) }) + "</span>" + (r.row[4] ? '<span class="qs">' + esc(r.row[4].replace(/`/g, "")) + "</span>" : "");
      return '<li role="option" id="qr' + i + '" aria-selected="' + (i === sel) + '" data-i="' + i + '">' + inner + "</li>";
    }).join("");
    list.hidden = false;
  }
  if (q) {
    q.addEventListener("input", function () { results = search(q.value); sel = results.length ? 0 : -1; renderResults(); });
    q.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!results.length) return;
        sel = (sel + (e.key === "ArrowDown" ? 1 : results.length - 1)) % results.length;
        renderResults(); e.preventDefault();
      } else if (e.key === "Enter" && results[sel]) {
        location.href = hrefFor(results[sel]);
      } else if (e.key === "Escape") {
        q.value = ""; results = []; renderResults(); q.blur();
      }
    });
    list.addEventListener("mousedown", function (e) {
      var li = e.target.closest("li[data-i]");
      if (li) { e.preventDefault(); location.href = hrefFor(results[+li.getAttribute("data-i")]); }
    });
    q.addEventListener("blur", function () { setTimeout(function () { list.hidden = true; }, 120); });
    q.addEventListener("focus", function () { if (results.length) list.hidden = false; });
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      if (e.key === "/" && !(t instanceof HTMLInputElement) && !(t instanceof HTMLTextAreaElement)) {
        e.preventDefault(); q.focus(); q.select();
      }
    });
  }
})();
