#!/usr/bin/env python3
"""Build the PUBLIC static dashboard (docs/) from the local app (web/).

docs/ is served by GitHub Pages off a public repo, so it is deliberately a
SUBSET of web/: the monthly + hourly scanners only, reading committed JSON
snapshots instead of the local API. Everything that needs the backend — the
Positions tab, the IBKR tab, Settings, and the whole trade-execution flow — is
stripped here rather than merely hidden.

Why a script: docs/ was hand-copied once in May and then drifted four months
behind web/, because a manual copy is a step nobody repeats. This makes the
sync one command, and refuses to emit a build that fails its own safety check.

    python3 scripts/build_dashboard.py [--check]

--check verifies docs/ matches what a build would produce, and writes nothing.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, DOCS = ROOT / "web", ROOT / "docs"

# Execution client. arb/execute.py is deliberately NOT in the public repo, so
# its UI must not be either. (positions/paired/ticket-preview code already ships
# in origin/main:web/app.js, so stripping those too would be theatre — but these
# genuinely have never been published.)
STRIP_FUNCS = [
    # trade execution (arb/execute.py is not public, so its UI must not be)
    "postExec", "dryRun", "liveConfirm", "placeLive",
    "postPolyExec", "polyConfirm", "polyPlace", "execHTML",
    # ticket preview — reaches /api/prepare, and is the entry point to the above
    "openTicket", "closeTicket", "refreshTicket", "ticketError", "ticketHTML",
    # Positions tab and everything it renders. The static page has no tab for
    # it; shipping the renderer anyway would leave /api/positions fetches and
    # the secrets.json setup card in a public bundle for no benefit.
    "renderPositions", "venuePanel", "posThumb", "expBadge",
    "historyCard", "historyView",
    "pairedView", "renderPairedScan", "assignBanner",
    "openAssign", "saveAssign", "resetAssign",
    # settings modal
    "loadSettings",
]

# Backend-only UI. Removed outright, not hidden, so there is nothing to reveal.
STRIP_IDS = ["settingsBtn", "settingsModal", "ticketModal", "ibkrSetup",
             "positions", "pairedWrap", "pairedBtn"]
STRIP_TABS = ["daily", "positions"]

# If any of these survive into the built output, the build is wrong. Checked
# against the emitted bytes, not the inputs.
FORBIDDEN = ["/api/execute", "/api/positions", "/api/settings",
             "/api/pair-assignments", "liveConfirm", "placeLive", "postExec",
             "polyPlace", "execHTML", "secrets.json", "private_key",
             "polymarket_wallet", "kalshi_key_id"]

STATIC_ENDPOINT = '''// Static snapshot URLs — no backend; cache-bust on each load so returning
// users see fresh data after a GHA cron commit.
const endpoint = () => {
  const f = MODE === "hourly" ? "hourly" : "scan";
  return `./data/${f}.json?t=${Date.now()}`;
};
const STATIC_MODE = true;
// Backend-only elements are stripped from the static page, so $() hands back an
// inert stub rather than null. `hidden: true` is the right default — anything
// that asks whether a modal is open gets "closed".
const _NULLEL = {
  hidden: true, innerHTML: "", value: "", textContent: "", dataset: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {}, focus() {}, click() {},
};
'''


def strip_js_function(src, name):
    """Remove `function NAME(...) { ... }` by brace matching from its opener."""
    m = re.search(r"^(?:async\s+)?function\s+%s\s*\(" % re.escape(name),
                  src, re.M)
    if not m:
        return src, False
    i = src.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    start = src.rfind("\n", 0, m.start()) + 1
    # take any comment block sitting directly above the function with it
    while True:
        prev_start = src.rfind("\n", 0, start - 1) + 1
        line = src[prev_start:start].strip()
        if line.startswith("//"):
            start = prev_start
        else:
            break
    end = src.find("\n", j) + 1
    while src[end:end + 1] == "\n":
        end += 1
    return src[:start] + src[end:], True


def strip_js_statement(src, prefix):
    """Remove a top-level statement beginning with `prefix`, brace-matched.

    Regex can't do this: `$("saveSettings").onclick = async () => { ... }` has a
    nested `const body = { ... };` whose closing brace a non-greedy pattern stops
    on, orphaning the rest of the handler into the enclosing scope.
    """
    i = src.find(prefix)
    if i < 0:
        return src, False
    start = src.rfind("\n", 0, i) + 1
    eol = src.find("\n", i)
    brace = src.find("{", i)
    if brace < 0 or brace > eol:                 # single-line statement
        return src[:start] + src[eol + 1:], True
    depth, j = 0, brace
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    end = src.find("\n", j) + 1
    return src[:start] + src[end:], True


def strip_html_element(src, el_id):
    """Remove the element carrying id="el_id", children included."""
    m = re.search(r'<(\w+)[^>]*\sid="%s"' % re.escape(el_id), src)
    if not m:
        return src, False
    tag = m.group(1)
    if re.match(r"<%s[^>]*/>" % tag, src[m.start():]):
        end = src.index(">", m.start()) + 1
    else:
        depth, pos = 0, m.start()
        pat = re.compile(r"</?%s\b" % tag)
        while True:
            mm = pat.search(src, pos)
            if not mm:
                return src, False
            depth += -1 if mm.group(0).startswith("</") else 1
            pos = mm.end()
            if depth == 0:
                end = src.index(">", pos) + 1
                break
    start = src.rfind("\n", 0, m.start()) + 1
    nxt = src.find("\n", end) + 1
    return src[:start] + src[nxt:], True


def build():
    app = (WEB / "app.js").read_text()
    html = (WEB / "index.html").read_text()
    css = (WEB / "style.css").read_text()
    log = []

    for fn in STRIP_FUNCS:
        app, hit = strip_js_function(app, fn)
        log.append("  js  %-14s %s" % (fn, "stripped" if hit else "NOT FOUND"))
    # the three init lines that wired the ticket's exec buttons
    before = app
    app = re.sub(r'^\s*\$\("tk(?:Dry|Confirm|Poly)"\)\.onclick = \w+;\n', "",
                 app, flags=re.M)
    log.append("  js  %-14s %s" % ("exec wiring",
               "stripped" if app != before else "NOT FOUND"))

    # Dead init wiring + state left behind by the stripped renderers. These are
    # unreachable on the static page (no buttons), but they would still ship
    # live /api/ URLs in a public bundle, so they go.
    # Call sites that are NOT inside a stripped function. Every other reference
    # lives within one and goes with it; these three are reachable code.
    for label, pat, repl in [
        # "Prepare trade" is the entry point to the execution flow. The public
        # page must not offer it at all, rather than fail when clicked.
        ("prep-row", r'\$\{MODE === "monthly" && r\.best_side[\s\S]*?: ""\}', '""'),
        ("prep-btn wiring",
         r'^  document\.querySelectorAll\("#body \.prep-btn"\)[\s\S]*?^  \}\);\n', ""),
        # MODE can never be "positions" (no tab), but the branch would still
        # ship a call to a function that no longer exists.
        ("positions branch",
         r'^    if \(MODE === "positions"\) \{\n[\s\S]*?^    \} else if ', '    if '),
        ("escape handler",
         r'^    if \(!\$\("ticketModal"\)\.hidden\) closeTicket\(\);\n', ""),
        ("paired branch",
         r'^  if \(activeView\(\) === "paired"\) \{ renderPairedScan\(\); return; \}\n', ""),
    ]:
        before = app
        app = re.sub(pat, repl, app, count=1, flags=re.M)
        log.append("  js  %-14s %s" % (label,
                   "stripped" if app != before else "NOT FOUND"))

    for stmt in ['$("settingsBtn").onclick', '$("closeSettings").onclick',
                 '$("saveSettings").onclick', '$("tkClose").onclick',
                 '$("ticketModal").addEventListener',
                 '$("tkMax").addEventListener', '$("tkBuf").addEventListener',
                 '$("asgSave").onclick', '$("asgReset").onclick']:
        app, hit = strip_js_statement(app, stmt)
        log.append("  js  %-14s %s" % (stmt.split('"')[1],
                   "stripped" if hit else "NOT FOUND"))

    for label, pat in [
        ("paired cache",
         r'^// Cached /api/positions[\s\S]*?^let SCAN_POS = null;\n'),
        ("paired invalidate",
         r'^\s*// A forced load[\s\S]*?^\s*if \(force && activeView\(\) === "paired"\) SCAN_POS = null;\n'),
    ]:
        before = app
        app = re.sub(pat, "", app, flags=re.M)
        log.append("  js  %-14s %s" % (label,
                   "stripped" if app != before else "NOT FOUND"))

    old_ep = re.search(
        r"const endpoint = \(\) =>.*?;\n", app, re.S)
    assert old_ep, "endpoint() not found in web/app.js"
    app = app[:old_ep.start()] + STATIC_ENDPOINT + app[old_ep.end():]
    app = app.replace("const $ = (id) => document.getElementById(id);",
                      "const $ = (id) => document.getElementById(id) || _NULLEL;")
    log.append("  js  %-14s rewritten for ./data/*.json" % "endpoint()")

    for el in STRIP_IDS:
        html, hit = strip_html_element(html, el)
        log.append("  html %-13s %s" % (el, "stripped" if hit else "NOT FOUND"))
    for tab in STRIP_TABS:
        before = html
        html = re.sub(r'^\s*<button data-m="%s".*?</button>\n' % tab, "",
                      html, flags=re.M | re.S)
        log.append("  html %-13s %s" % ("tab:" + tab,
                   "stripped" if html != before else "NOT FOUND"))

    bad = sorted({t for t in FORBIDDEN if t in app or t in html})
    return app, html, css, log, bad


def main():
    app, html, css, log, bad = build()
    print("\n".join(log))
    print()
    if bad:
        print("REFUSING TO BUILD — forbidden tokens survived into the output:",
              file=sys.stderr)
        for t in bad:
            print("    %s" % t, file=sys.stderr)
        return 1
    print("Safety check: none of %d forbidden tokens present." % len(FORBIDDEN))

    check = "--check" in sys.argv
    changed = []
    for name, text in (("app.js", app), ("index.html", html),
                       ("style.css", css)):
        cur = (DOCS / name).read_text() if (DOCS / name).exists() else None
        if cur != text:
            changed.append(name)
            if not check:
                (DOCS / name).write_text(text)
    if check:
        print("--check: %s" % (", ".join(changed) + " would change"
                               if changed else "docs/ is up to date"))
        return 1 if changed else 0
    print("Wrote: %s" % (", ".join("docs/" + c for c in changed) or "nothing (already current)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
