/* COPIED FROM atrium-intranet's static/smart_search.js on its main branch
 * (PR #1206, commit 2121c8b, 2026-11-06, the final engine): everything below
 * this comment is that file's, word for word, the same as Beacon's
 * styles/smart_search.js and Marty's static/smart_search.js. Change a rule
 * there first, then copy it to all four. Served as /static/smart_search.js to
 * widget.html, w.html and grid.html; tests/test_smart_search_browser.py types
 * into each box.
 *
 * One search for every box on the page: forgives typos, knows nicknames,
 * best match first. The same rules as smart_search.py, which has the why
 * (Alex, 2026-11-05: "a really, really sophisticated search engine like
 * AppFolio's") and lists what the Beacon and Marty copies added: phone
 * numbers as one run of digits, `literal`, and one way to fold letters.
 * tests/browser/test_smart_search.py checks the two agree.
 *
 *   SmartSearch.score("brittany", ["Brittney Moss", "PG3"])   -> 0.65
 *   SmartSearch.rank("dee", people, p => [p.name, p.nickname, p.title])
 *   SmartSearch.matches(q, fields)                             -> true/false
 *   SmartSearch.score("(407) 555-12", ["+14075551234"])        -> 0.9
 *
 * Fields go most important first: a hit in the first counts in full, a hit
 * in the rest at 0.9, so a name outranks the same word in a note.
 * opts.literal: typed words that get no spelling correction (see
 * smart_search.literal_terms on the server).
 */
(function () {
    "use strict";

    var NICKNAMES = {
        mike: ["michael"], mick: ["michael"], bob: ["robert"], rob: ["robert"],
        bobby: ["robert"], bill: ["william"], will: ["william"], billy: ["william"],
        liam: ["william"], rick: ["richard", "ricardo"], dick: ["richard"],
        rich: ["richard"], jim: ["james"], jimmy: ["james"], jack: ["john", "jackson"],
        johnny: ["john", "jonathan"], jon: ["jonathan", "john"], peggy: ["margaret"],
        maggie: ["margaret"], meg: ["margaret"], betty: ["elizabeth"],
        liz: ["elizabeth"], beth: ["elizabeth"], lisa: ["elizabeth"],
        chuck: ["charles"], charlie: ["charles"], hank: ["henry"],
        harry: ["henry", "harold"], greg: ["gregory"], ed: ["edward", "eduardo"],
        ted: ["edward", "theodore"], eddie: ["edward", "eduardo"],
        eddy: ["edward", "eduardo"], tony: ["anthony", "antonio"],
        gus: ["gustavo", "augustus"], nacho: ["ignacio"], pepe: ["jose"],
        paco: ["francisco"], pancho: ["francisco"], kay: ["katherine"],
        kathy: ["katherine"], katie: ["katherine", "kaitlin", "katelin"],
        cathy: ["catherine"], candy: ["candace"], sandy: ["sandra", "alexander"],
        alex: ["alexander", "alexandra", "alejandro"], sasha: ["alexander"],
        andy: ["andrew", "andrea"], drew: ["andrew"], dan: ["daniel"],
        danny: ["daniel"], dave: ["david"], steve: ["steven", "stephen"],
        chris: ["christopher", "christina", "christine", "cristina"],
        tina: ["christina", "cristina"], nick: ["nicholas"], matt: ["matthew"],
        pat: ["patrick", "patricia"], patty: ["patricia"], sam: ["samuel", "samantha"],
        joe: ["joseph"], joey: ["joseph"], tom: ["thomas"], tommy: ["thomas"],
        jen: ["jennifer", "jeniffer"], jenny: ["jennifer", "jeniffer"],
        becky: ["rebecca", "rebekah"], abby: ["abigail"], debbie: ["deborah"],
        deb: ["deborah"], sue: ["susan"], suzy: ["susan"], vicky: ["victoria"],
        tori: ["victoria"], val: ["valerie"], ron: ["ronald"], don: ["donald"],
        ken: ["kenneth"], larry: ["lawrence"], jerry: ["gerald", "gerardo"],
        manny: ["manuel"], lupe: ["guadalupe"], rafa: ["rafael"],
        beto: ["roberto", "alberto"], memo: ["guillermo"], chuy: ["jesus"],
        lalo: ["eduardo"], tono: ["antonio"], mel: ["melissa", "melanie", "melinda"]
    };

    // Both directions: "Mike" finds Michael and "Michael" finds Mike.
    var ALIASES = {};
    Object.keys(NICKNAMES).forEach(function (nick) {
        NICKNAMES[nick].forEach(function (form) {
            (ALIASES[nick] = ALIASES[nick] || {})[form] = true;
            (ALIASES[form] = ALIASES[form] || {})[nick] = true;
        });
    });

    var EXACT = 1.0, PREFIX = 0.9, NICK = 0.85, TYPO = 0.7, TYPO_PREFIX = 0.6, INSIDE = 0.5;
    var PHONE_MIN = 7;

    // NFKD splits "é" into "e" plus an accent; whatever is still not plain
    // ASCII after that ("ø", "ß") is dropped, exactly as smart_search.py's
    // encode("ascii", "ignore") does. (The Intranet copy turned those into a
    // space, so "Søren" was "s ren" on the page and "sren" on the server.)
    // Letters NFKD cannot take apart, spelled as a person types them without
    // the key ("Søren" is found by "soren"). smart_search.py has the same table.
    var LETTERS = {
        "\u00f8": "o", "\u00d8": "o", "\u00df": "ss", "\u00e6": "ae", "\u00c6": "ae",
        "\u0153": "oe", "\u0152": "oe", "\u0142": "l", "\u0141": "l", "\u0111": "d",
        "\u0110": "d", "\u00f0": "d", "\u00d0": "d", "\u00fe": "th", "\u00de": "th",
        "\u0131": "i"
    };
    var LETTERS_RE = /[\u00f8\u00d8\u00df\u00e6\u00c6\u0153\u0152\u0142\u0141\u0111\u0110\u00f0\u00d0\u00fe\u00de\u0131]/g;

    // Only accent marks are dropped; anything else that is not a letter or
    // digit (a curly apostrophe, a long dash) is a word break, like its
    // plain counterpart. The same steps as fold() in smart_search.py.
    function fold(s) {
        return String(s == null ? "" : s)
            .replace(LETTERS_RE, function (c) { return LETTERS[c]; })
            .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
            .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    }

    function words(s) {
        var f = fold(s);
        return f ? f.split(" ") : [];
    }

    function isDigits(w) { return /^[0-9]+$/.test(w); }

    // A phone number written in pieces, as the one word of digits it is:
    // "(407) 555-1234" is also 4075551234 and 14075551234, and +14075551234
    // is also 4075551234. Seven digits at least, so "PG 3, unit 104" stays apart.
    function phoneWords(ws) {
        var out = [], run = [], all = ws.concat([""]), i;
        for (i = 0; i < all.length; i++) {
            var w = all[i];
            if (w && isDigits(w)) { run.push(w); continue; }
            if (run.length >= 2) {
                var joined = run.join("");
                if (joined.length >= PHONE_MIN) out.push(joined);
            }
            run = [];
        }
        var more = ws.concat(out);
        for (i = 0; i < more.length; i++) {
            var x = more[i];
            if (x.length === 11 && x.charAt(0) === "1" && isDigits(x)) out.push(x.slice(1));
            else if (x.length === 10 && isDigits(x)) out.push("1" + x);
        }
        return out;
    }

    // The words typed with every run of all-digit words joined: "(407) 555-12"
    // is also 40755512. Both readings are scored; the better one counts.
    function joinedDigits(terms) {
        var out = [], run = [];
        terms.forEach(function (t) {
            if (isDigits(t)) { run.push(t); return; }
            if (run.length) { out.push(run.join("")); run = []; }
            out.push(t);
        });
        if (run.length) out.push(run.join(""));
        return out;
    }

    // One from 4 letters, two from 8; never in a number (PG3 is not PG8).
    function typosAllowed(term) {
        if (/[0-9]/.test(term)) return 0;
        return term.length >= 8 ? 2 : term.length >= 4 ? 1 : 0;
    }

    // Edits between a and b (a swap of neighbours counts as one), or
    // limit + 1 as soon as it is clearly more.
    function distance(a, b, limit) {
        if (Math.abs(a.length - b.length) > limit) return limit + 1;
        var prev2 = [], prev = [], cur, i, j;
        for (j = 0; j <= b.length; j++) prev.push(j);
        for (i = 1; i <= a.length; i++) {
            cur = [i];
            var rowMin = i;
            for (j = 1; j <= b.length; j++) {
                var cost = a[i - 1] === b[j - 1] ? 0 : 1;
                var v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
                if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
                    v = Math.min(v, prev2[j - 2] + 1);
                }
                cur.push(v);
                if (v < rowMin) rowMin = v;
            }
            if (rowMin > limit) return limit + 1;
            prev2 = prev;
            prev = cur;
        }
        return prev[b.length];
    }

    // typos === false is a word meant as typed (smart_search.literal_terms).
    function wordScore(term, word, typos) {
        if (!term || !word) return 0;
        if (word === term) return EXACT;
        if (word.indexOf(term) === 0) return PREFIX;
        var allowed = typos === false ? 0 : typosAllowed(term);
        if (allowed) {
            var d = distance(term, word, allowed);
            if (d <= allowed) return TYPO - 0.05 * (d - 1);
            // Still typing, with a slip: "britn" on its way to "brittney".
            if (term.length >= 5 && word.length > term.length) {
                var ns = [term.length, term.length + 1, term.length - 1];
                for (var k = 0; k < ns.length; k++) {
                    var n = ns[k];
                    if (n > 0 && n <= word.length &&
                        distance(term, word.slice(0, n), allowed) <= allowed) {
                        return TYPO_PREFIX;
                    }
                }
            }
        }
        if (term.length >= 3 && word.indexOf(term) !== -1) return INSIDE;
        return 0;
    }

    function prep(fields, weights) {
        if (!Array.isArray(fields)) fields = [fields];
        var fw = [], texts = [];
        fields.forEach(function (f, i) {
            var wt = weights && i < weights.length ? weights[i] : (i === 0 ? 1 : 0.9);
            var ws = words(f);
            texts.push(ws.join(" "));
            ws.forEach(function (w) { fw.push([w, wt]); });
            phoneWords(ws).forEach(function (w) { fw.push([w, wt]); });
        });
        return { fw: fw, texts: texts };
    }

    function scoreTerms(terms, p, extra, literal) {
        var total = 0;
        for (var t = 0; t < terms.length; t++) {
            var term = terms[t], best = 0;
            var typos = !(literal && literal.indexOf(term) !== -1);
            var alts = {};
            Object.keys(ALIASES[term] || {}).forEach(function (a) { alts[a] = true; });
            (extra[term] || []).forEach(function (a) { alts[fold(a)] = true; });
            for (var i = 0; i < p.fw.length; i++) {
                var w = p.fw[i][0], wt = p.fw[i][1];
                var s = wordScore(term, w, typos);
                if (s < EXACT && alts[w]) s = Math.max(s, NICK);
                if (s * wt > best) best = s * wt;
            }
            if (best <= 0) return 0;
            total += best;
        }
        var s2 = total / terms.length;
        var joined = terms.join(" ");
        if (terms.length > 1 && p.texts.some(function (x) { return x.indexOf(joined) !== -1; })) {
            s2 += 0.1;
        }
        return s2;
    }

    // opts: weights (per field), aliases ({word: [other names]}), literal
    // (typed words that get no spelling correction).
    function score(query, fields, opts) {
        opts = opts || {};
        var terms = words(query);
        if (!terms.length) return 1;
        var p = prep(fields, opts.weights);
        var extra = opts.aliases || {};
        var literal = opts.literal || null;
        var s = scoreTerms(terms, p, extra, literal);
        var joined = joinedDigits(terms);
        if (joined.length < terms.length) s = Math.max(s, scoreTerms(joined, p, extra, literal));
        return s;
    }

    function matches(query, fields, opts) {
        return score(query, fields, opts) > 0;
    }

    // The items that match, best first; ties keep the order they came in.
    function rank(query, items, fieldsOf, opts) {
        var scored = [];
        for (var i = 0; i < items.length; i++) {
            var s = score(query, fieldsOf(items[i]), opts);
            if (s > 0) scored.push([s, i, items[i]]);
        }
        scored.sort(function (a, b) { return b[0] - a[0] || a[1] - b[1]; });
        return scored.map(function (x) { return x[2]; });
    }

    window.SmartSearch = {
        fold: fold, words: words, score: score, matches: matches, rank: rank,
        distance: distance, wordScore: wordScore, phoneWords: phoneWords,
        nicknames: NICKNAMES
    };
})();
