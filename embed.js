/* Atrium listings widget: host-page companion script.
 *
 * Paste-in embed code (built in Beacon, Operations > Listing Widgets) is an <iframe> of
 * w.html plus this one <script src>. Hosting the logic here instead of inline means a fix
 * reaches every site on the next deploy, with nobody re-copying embed code.
 *
 * It does two jobs for every widget iframe on the page:
 *   1. Height: the widget reports its content height and the iframe grows to fit.
 *   2. Typography: an iframe cannot inherit the host's CSS, and cannot see the host's
 *      web fonts either (a Typekit kit or an @font-face lives in the HOST document only).
 *      So we read the fonts the page actually uses for body text and headings, collect
 *      the matching @font-face sources, and hand them to the widget, which registers
 *      them with the FontFace API. Font files on the host's own origin (usually served
 *      without CORS headers) are fetched here and sent as bytes; cross-origin ones are
 *      sent as URLs, and if the widget cannot load one it asks back and gets the bytes.
 *
 * The widget ignores all of this when the embed pins a font (?font=, ?gfont=, ?fonturl=).
 * Loaded twice (two widgets, two pasted snippets)? The second copy just rescans.
 */
(function () {
  'use strict';
  if (window.__atrListingsEmbed) { window.__atrListingsEmbed.scan(); return; }

  var script = document.currentScript;
  var ORIGIN = 'https://listings.meetatrium.com';
  try { if (script && script.src) ORIGIN = new URL(script.src, location.href).origin; } catch (e) {}

  var MAX_FACES = 24, MAX_BYTES = 3 * 1024 * 1024, MAX_FACE_BYTES = 1024 * 1024;
  // Stylesheets from these hosts contain nothing but @font-face, so the widget may link
  // them directly. Any other stylesheet is never handed over whole: it would restyle it.
  var FONT_CSS_HOSTS = /^(use\.typekit\.net|p\.typekit\.net|fonts\.googleapis\.com|fonts\.bunny\.net|fast\.fonts\.net|cloud\.typography\.com)$/i;
  var GENERIC = /^(serif|sans-serif|monospace|cursive|fantasy|system-ui|ui-serif|ui-sans-serif|ui-monospace|ui-rounded|emoji|math|fangsong|-apple-system|blinkmacsystemfont|inherit|initial|unset)$/i;

  var frames = [];

  function isWidget(f) {
    try {
      var u = new URL(f.getAttribute('src') || '', location.href);
      return u.origin === ORIGIN && /\/w(\.html)?$/.test(u.pathname);
    } catch (e) { return false; }
  }

  function scan() {
    var all = document.getElementsByTagName('iframe');
    for (var i = 0; i < all.length; i++) {
      var f = all[i];
      if (frames.indexOf(f) > -1 || !isWidget(f)) continue;
      frames.push(f);
      f.addEventListener('load', (function (fr) { return function () { send(fr); }; })(f));
      send(f);
    }
  }

  function frameFor(win) {
    for (var i = 0; i < frames.length; i++) if (frames[i].contentWindow === win) return frames[i];
    return null;
  }

  /* ---------------- which fonts does this page use? ---------------- */
  function unquote(s) { return s.trim().replace(/^["']|["']$/g, '').trim(); }
  function familyList(stack) { return String(stack || '').split(',').map(unquote).filter(Boolean); }
  function realFont(el) {
    if (!el || el.nodeType !== 1) return '';
    var s = '';
    try { s = getComputedStyle(el).fontFamily || ''; } catch (e) { return ''; }
    var fam = familyList(s);
    return fam.length && !GENERIC.test(fam[0]) ? s : '';
  }
  // Body text: many site builders (Squarespace included) never set a font on <body> and
  // style p/h tags instead, so body's computed font is plain "sans-serif". Prefer the
  // widget's own container, then the paragraphs nearest the widget, then any paragraph.
  function textFont(f) {
    var got = realFont(f.parentElement);
    for (var el = f.parentElement, n = 0; !got && el && n < 8; el = el.parentElement, n++) {
      got = realFont(el.querySelector && el.querySelector('p'));
    }
    return got || realFont(document.querySelector('main p, article p')) ||
      realFont(document.querySelector('p')) || realFont(document.body) || '';
  }
  function headFont() {
    return realFont(document.querySelector('h2')) || realFont(document.querySelector('h1')) ||
      realFont(document.querySelector('h3')) || '';
  }

  /* ---------------- where do those fonts come from? ---------------- */
  function coversLatin(range) {
    if (!range) return true;
    var parts = range.split(',');
    for (var i = 0; i < parts.length; i++) {
      var m = /u\+([0-9a-f?]+)(?:-([0-9a-f]+))?/i.exec(parts[i]);
      if (!m) continue;
      var lo, hi;
      if (m[1].indexOf('?') > -1) { lo = parseInt(m[1].replace(/\?/g, '0'), 16); hi = parseInt(m[1].replace(/\?/g, 'f'), 16); }
      else { lo = parseInt(m[1], 16); hi = m[2] ? parseInt(m[2], 16) : lo; }
      if (lo <= 0x61 && hi >= 0x61) return true;
    }
    return false;
  }

  function absolutize(src, base) {
    return src.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, function (m, q, u) {
      if (/^data:/i.test(u)) return 'url("' + u + '")';
      try { return 'url("' + new URL(u, base).href + '")'; } catch (e) { return m; }
    });
  }

  function firstUrl(src) {
    var m = /url\("([^"]+)"\)/.exec(src);
    return m ? m[1] : '';
  }

  function faceFrom(get, base, wanted) {
    var fam = unquote(get('font-family') || '');
    if (!fam || !wanted[fam.toLowerCase()]) return null;
    var style = (get('font-style') || 'normal').trim().toLowerCase();
    if (style !== 'normal') return null;                      // the widget sets no italics
    var range = (get('unicode-range') || '').trim();
    if (!coversLatin(range)) return null;
    var src = (get('src') || '').trim();
    if (!src) return null;
    return {
      family: fam, weight: (get('font-weight') || '400').trim(), style: 'normal',
      stretch: (get('font-stretch') || '').trim(), range: range, src: absolutize(src, base)
    };
  }

  function walkRules(rules, base, wanted, out) {
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      if (r.type === 5) {                                      // CSSFontFaceRule
        var face = faceFrom(function (p) { return r.style.getPropertyValue(p); }, base, wanted);
        if (face) out.push(face);
      } else if (r.type === 3 && r.styleSheet) {               // @import
        try { walkRules(r.styleSheet.cssRules, r.styleSheet.href || base, wanted, out); } catch (e) {}
      } else if (r.cssRules) {                                 // @media / @supports / @layer
        try { walkRules(r.cssRules, base, wanted, out); } catch (e) {}
      }
    }
  }

  function parseCssText(css, base, wanted, out) {
    var re = /@font-face\s*\{([^}]*)\}/gi, m;
    while ((m = re.exec(css))) {
      var body = m[1];
      var face = faceFrom(function (p) {
        var mm = new RegExp('(?:^|;)\\s*' + p + '\\s*:\\s*([^;]+)', 'i').exec(body);
        return mm ? mm[1] : '';
      }, base, wanted);
      if (face) out.push(face);
    }
  }

  var cssTextCache = {};
  function fetchCss(href) {
    if (!cssTextCache[href]) {
      cssTextCache[href] = fetch(href, { mode: 'cors', credentials: 'omit' })
        .then(function (r) { return r.ok ? r.text() : ''; })
        .catch(function () { return ''; });
    }
    return cssTextCache[href];
  }

  var bytesCache = {};
  function fetchBytes(url) {
    if (!bytesCache[url]) {
      bytesCache[url] = fetch(url, { mode: 'cors', credentials: 'omit' })
        .then(function (r) { return r.ok ? r.arrayBuffer() : null; })
        .then(function (b) { return b && b.byteLength <= MAX_FACE_BYTES ? b : null; })
        .catch(function () { return null; });
    }
    return bytesCache[url];
  }

  function harvest(f) {
    var font = textFont(f), head = headFont();
    var wanted = {};
    familyList(font).concat(familyList(head)).forEach(function (n) {
      if (!GENERIC.test(n)) wanted[n.toLowerCase()] = 1;
    });
    var faces = [], links = [], opaque = [];
    var sheets = document.styleSheets;
    for (var i = 0; i < sheets.length; i++) {
      var sh = sheets[i], rules = null;
      try { rules = sh.cssRules; } catch (e) { rules = null; }
      if (rules) { walkRules(rules, sh.href || document.baseURI, wanted, faces); continue; }
      if (!sh.href) continue;
      var host = '';
      try { host = new URL(sh.href).hostname; } catch (e) {}
      if (FONT_CSS_HOSTS.test(host)) links.push(sh.href); else opaque.push(sh.href);
    }
    // A cross-origin stylesheet we cannot read may still hold the @font-face we need.
    // Only go and fetch it when a wanted family is still unaccounted for.
    var have = {};
    faces.forEach(function (x) { have[x.family.toLowerCase()] = 1; });
    var missing = Object.keys(wanted).some(function (k) { return !have[k]; });
    var extra = missing && opaque.length
      ? Promise.all(opaque.slice(0, 8).map(function (href) {
          return fetchCss(href).then(function (css) { if (css) parseCssText(css, href, wanted, faces); });
        }))
      : Promise.resolve();

    return extra.then(function () {
      var seen = {}, uniq = [];
      faces.forEach(function (x) {
        var k = [x.family, x.weight, x.stretch, x.range, x.src].join('|');
        if (!seen[k] && uniq.length < MAX_FACES) { seen[k] = 1; uniq.push(x); }
      });
      // Same-origin font files rarely carry CORS headers, so the widget (another origin)
      // could never load them itself. We can: fetch the bytes and send those instead.
      var budget = MAX_BYTES;
      return Promise.all(uniq.map(function (x) {
        var u = firstUrl(x.src);
        var sameOrigin = false;
        try { sameOrigin = !!u && new URL(u).origin === location.origin; } catch (e) {}
        if (!sameOrigin) return x;
        return fetchBytes(u).then(function (b) {
          if (b && b.byteLength <= budget) { budget -= b.byteLength; x.bytes = b; }
          return x;
        });
      })).then(function (list) {
        return { type: 'atr-style', v: 2, font: font, head: head, faces: list, links: links };
      });
    });
  }

  function post(f, msg) {
    try { f.contentWindow.postMessage(msg, ORIGIN); } catch (e) {}
  }

  function send(f) {
    if (!f.contentWindow) return;
    harvest(f).then(function (msg) { post(f, msg); }).catch(function () {});
  }

  function sendAll() { for (var i = 0; i < frames.length; i++) send(frames[i]); }

  window.addEventListener('message', function (e) {
    if (e.origin !== ORIGIN) return;
    var f = frameFor(e.source);
    if (!f) { scan(); f = frameFor(e.source); }
    if (!f) return;
    var d = e.data;
    if (!d || typeof d !== 'object') return;
    if (d.type === 'atr-height' && typeof d.height === 'number' && d.height > 200 && d.height < 20000) {
      f.style.height = d.height + 'px';
    } else if (d.type === 'atr-hello') {
      send(f);
    } else if (d.type === 'atr-font-miss' && d.face && typeof d.face.src === 'string') {
      // The widget could not load a cross-origin font by URL (the font host's CORS
      // allows this site but not ours). Fetch it from here and send the bytes.
      var face = d.face, u = firstUrl(face.src);
      if (!/^(https:\/\/|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/)/i.test(u)) return;
      fetchBytes(u).then(function (b) {
        if (!b) return;
        post(f, { type: 'atr-style', v: 2, faces: [{
          family: face.family, weight: face.weight, style: 'normal', stretch: face.stretch,
          range: face.range, src: face.src, bytes: b }] });
      });
    }
  });

  window.__atrListingsEmbed = { scan: scan, send: sendAll };
  scan();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
  window.addEventListener('load', function () { scan(); sendAll(); });
  // Font kits (Typekit's JS kits, Google's loader) inject their @font-face late.
  try { document.fonts.ready.then(sendAll); } catch (e) {}
  setTimeout(function () { scan(); sendAll(); }, 2500);
  setTimeout(sendAll, 7000);
})();
