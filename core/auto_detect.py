"""
core/auto_detect.py — API-free page-structure detection.

Replaces Claude Vision (worker.py Tier 2) with a statistical repeat-pattern
finder that runs directly in the rendered DOM. No API key, no per-site cost,
works offline.

The core idea: every directory / attendee list / speaker grid is a *repeating
DOM pattern*. You don't need a model to find it — you need statistics. Walk the
tree, find the parent whose direct children share the same tag+class signature
many times over, score the candidate groups, and the winner is the card list.

Returns the SAME dict shape that the old AI path produced, so it drops straight
into run_worker()'s existing scrape loop:

    {
      "card_selector":       "div.speaker-card",   # required
      "name_selector":       ".name" | None,        # optional sub-selectors —
      "title_selector":      ".title" | None,       #   if None, worker falls
      "company_selector":    ".company" | None,     #   back to text-line parsing
      "profile_url_selector":"a" | None,
      "pagination_type":     "infinite_scroll" | "next_button" | "url_param",
      "next_button_selector":"a.next" | None,
      "confidence":          0.0 - 1.0,
      "card_count":          42,
      "reasoning":           "human-readable explanation",
      "detected_by":         "heuristic",
    }

Two entry points:
  identify_structure(page)          — run against a live Playwright page
  detect_in_page_js()               — the raw JS (for embedding/testing)

The detection self-heals via core.html_selector.get_best_match upstream in
worker.py; this module only concerns itself with first-pass detection.
"""

# The detector runs entirely in-page as JavaScript because it must see the
# *rendered* DOM (SPA sites build their cards with JS, so the raw HTML source
# has nothing to detect). Keeping it as one self-contained function means we can
# hand it to page.evaluate() and also unit-test it via page.set_content().
_DETECTOR_JS = r"""
() => {
  // ---- tunables -------------------------------------------------------------
  const MIN_REPEATS = 5;          // a card group must repeat at least this often
  const MIN_CARD_TEXT = 8;        // ignore groups whose items are near-empty
  const MAX_CARD_TEXT = 3500;     // ignore groups whose items are huge (page chrome).
                                  // Kept generous: some cards embed a full talk
                                  // abstract/bio. Whole-page wrappers don't repeat,
                                  // so scoring (below) protects us, not this cap.

  // ---- helpers --------------------------------------------------------------
  const signature = (el) => {
    // Structural identity of an element: tag + its sorted class list.
    // Two siblings with the same signature are almost certainly the same
    // "kind" of thing (two speaker cards, two company rows, ...).
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/)
                  .filter(Boolean).sort().join('.');
    return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  };

  const cleanText = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();

  const cssEscapeClass = (c) => {
    // Only keep classes that are safe as plain CSS identifiers. Utility
    // frameworks (Tailwind) and CSS-modules produce classes with ':' '/' '['
    // that break querySelectorAll unless escaped — we simply skip those and
    // lean on the remaining stable classes.
    return /^[a-zA-Z_][a-zA-Z0-9_-]*$/.test(c) ? c : null;
  };

  const sharedClasses = (els) => {
    // Intersection of class names across every element in the group — these
    // are the classes that define the group (per-item classes get dropped).
    let common = null;
    for (const el of els) {
      const set = new Set((el.getAttribute('class') || '').trim().split(/\s+/)
                    .filter(Boolean).map(cssEscapeClass).filter(Boolean));
      if (common === null) { common = set; }
      else { common = new Set([...common].filter(x => set.has(x))); }
      if (common.size === 0) break;
    }
    return common ? [...common] : [];
  };

  // ---- 1. gather candidate groups ------------------------------------------
  // For every element that has several children, bucket its *direct* children
  // by signature. Any bucket that repeats enough is a candidate card group.
  const candidates = [];
  const all = document.querySelectorAll('body *');

  for (const parent of all) {
    const kids = Array.from(parent.children);
    if (kids.length < MIN_REPEATS) continue;

    const buckets = {};
    for (const k of kids) {
      const sig = signature(k);
      (buckets[sig] = buckets[sig] || []).push(k);
    }

    for (const sig in buckets) {
      const group = buckets[sig];
      if (group.length < MIN_REPEATS) continue;

      // Average visible text length across the group — filters out nav bars
      // (tiny) and whole-page wrappers (huge).
      let totalText = 0, withLink = 0, withImg = 0, withHeading = 0;
      for (const g of group) {
        const t = cleanText(g);
        totalText += t.length;
        if (g.querySelector('a[href]') || g.tagName === 'A') withLink++;
        if (g.querySelector('img')) withImg++;
        if (g.querySelector('h1,h2,h3,h4,h5,strong,b')) withHeading++;
      }
      const avgText = totalText / group.length;
      if (avgText < MIN_CARD_TEXT || avgText > MAX_CARD_TEXT) continue;

      candidates.push({ parent, group, sig, avgText, withLink, withImg, withHeading });
    }
  }

  if (candidates.length === 0) return null;

  // ---- 2. score candidates --------------------------------------------------
  // We want the group that most looks like a list of people/companies:
  //   - repeats a lot (but with diminishing returns)
  //   - each item links somewhere (directory entries almost always do)
  //   - each item has a heading/name and often an avatar
  //   - sits in a sane text-length band
  const scoreOf = (c) => {
    const n = c.group.length;
    let s = 0;
    s += Math.min(n, 30) * 1.5;                       // repetition
    s += (c.withLink / n) * 20;                       // linked entries
    s += (c.withHeading / n) * 12;                    // has a name/heading
    s += (c.withImg / n) * 6;                          // has an avatar
    if (c.avgText >= 20 && c.avgText <= 400) s += 10;  // person-card text band
    return s;
  };

  candidates.sort((a, b) => scoreOf(b) - scoreOf(a));

  const countFor = (sel) => { try { return document.querySelectorAll(sel).length; } catch (e) { return -1; } };

  // Try to express one candidate group as a reliable CSS selector. Returns the
  // selector string, or null if the group's markup can't be pinned down (all
  // utility/hashed classes, no stable parent anchor, etc.).
  const buildCardSelector = (cand) => {
    const group = cand.group;
    const first = group[0];
    const tag = first.tagName.toLowerCase();
    const shared = sharedClasses(group);
    const closeEnough = (n) => n >= group.length * 0.7 && n <= group.length * 1.6;

    // A: tag + shared classes, globally (works when classes are stable)
    if (shared.length) {
      const sel = tag + '.' + shared.join('.');
      if (closeEnough(countFor(sel))) return sel;
    }
    // B: anchor to the parent's id or a stable parent class
    const p = cand.parent;
    const pid = p.getAttribute('id');
    const pcls = (p.getAttribute('class') || '').trim().split(/\s+/)
                   .filter(Boolean).map(cssEscapeClass).filter(Boolean);
    let parentSel = null;
    if (pid && /^[a-zA-Z_][a-zA-Z0-9_-]*$/.test(pid)) parentSel = '#' + pid;
    else if (pcls.length) parentSel = p.tagName.toLowerCase() + '.' + pcls.slice(0, 2).join('.');
    if (parentSel) {
      const childPart = shared.length ? (tag + '.' + shared.join('.')) : tag;
      const sel = parentSel + ' > ' + childPart;
      if (closeEnough(countFor(sel))) return sel;
    }
    // C: bare tag under parent id (last resort for class-less markup)
    if (shared.length === 0 && pid && /^[a-zA-Z_][a-zA-Z0-9_-]*$/.test(pid)) {
      const sel = '#' + pid + ' > ' + tag;
      if (closeEnough(countFor(sel))) return sel;
    }
    return null;
  };

  // Walk candidates in score order; the winner is the first one we can actually
  // express as a selector. This is the key robustness step — a high-scoring but
  // unaddressable group (hashed utility classes) must not block a slightly
  // lower-scoring group that we CAN pin down.
  let best = null, cardSelector = null;
  for (const cand of candidates) {
    const sel = buildCardSelector(cand);
    if (sel) { best = cand; cardSelector = sel; break; }
  }
  if (!cardSelector) return null;   // nothing expressible

  const group = best.group;
  const first = group[0];

  // ---- 4. detect sub-field selectors inside one card -----------------------
  // These are best-effort. If we can't find them the worker falls back to
  // its own text-line parser (smart_parse_lines), so leaving them null is safe.
  const TITLE_WORDS = ['manager','head','director','chief','officer','president',
    'vp','consultant','engineer','analyst','specialist','lead','advisor',
    'executive','ceo','cto','cfo','coo','founder','partner','scientist','principal'];

  const relSelector = (el) => {
    // Build a card-relative selector from an element's classes (or tag).
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/)
                  .filter(Boolean).map(cssEscapeClass).filter(Boolean);
    if (cls.length) return '.' + cls[0];
    return el.tagName.toLowerCase();
  };

  const classHints = (el, words) => {
    const c = (el.getAttribute('class') || '').toLowerCase();
    return words.some(w => c.includes(w));
  };

  let nameSel = null, titleSel = null, companySel = null, urlSel = null;

  // profile url
  if (first.tagName === 'A' && first.getAttribute('href')) urlSel = null; // card itself is the link
  else if (first.querySelector('a[href]')) urlSel = 'a';

  // Walk leaf-ish text elements in reading order.
  const leaves = Array.from(first.querySelectorAll('*')).filter(el => {
    const direct = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
    return direct && cleanText(el).length > 0 && cleanText(el).length < 120;
  });

  for (const el of leaves) {
    const t = cleanText(el);
    if (!nameSel && classHints(el, ['name','speaker','fullname'])) { nameSel = relSelector(el); continue; }
    if (!titleSel && (classHints(el, ['title','role','job','position']) ||
        TITLE_WORDS.some(w => t.toLowerCase().includes(w)))) { titleSel = relSelector(el); continue; }
    if (!companySel && classHints(el, ['company','org','organisation','organization','employer'])) {
      companySel = relSelector(el); continue;
    }
  }
  // If no class-based name found, assume the first heading/strong is the name.
  if (!nameSel) {
    const h = first.querySelector('h1,h2,h3,h4,h5,strong,b');
    if (h) nameSel = relSelector(h);
  }

  // ---- 5. detect pagination -------------------------------------------------
  let paginationType = 'infinite_scroll';
  let nextBtnSelector = null;

  // Explicit "next" control
  const nextCandidates = [
    'a[rel="next"]', '[aria-label*="next" i]', '[aria-label*="Next" i]',
    'a.next', 'button.next', '.pagination-next', 'li.next > a',
    '[class*="next"]'
  ];
  for (const sel of nextCandidates) {
    let el = null;
    try { el = document.querySelector(sel); } catch (e) { continue; }
    if (el) {
      const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
      if (t.includes('next') || el.matches('a[rel="next"], [class*="next"]')) {
        paginationType = 'next_button';
        nextBtnSelector = sel;
        break;
      }
    }
  }

  // Numbered pager (e.g. b2match / MUI "Go to page N")
  if (paginationType === 'infinite_scroll') {
    const numbered = document.querySelector(
      '[aria-label^="Go to page"], .pagination-item, .pagination a, nav[aria-label*="pagination" i] a');
    if (numbered) {
      paginationType = 'next_button';
      nextBtnSelector = '[aria-label*="next" i], .pagination-next, [aria-label^="Go to page"]';
    }
  }

  // URL-driven pagination (?page= already present)
  if (paginationType === 'infinite_scroll') {
    if (/[?&](page|pageNumber|pg|p)=/.test(location.search) ||
        document.querySelector('a[href*="page="], a[href*="pageNumber="]')) {
      paginationType = 'url_param';
    }
  }

  // ---- 6. confidence --------------------------------------------------------
  const n = group.length;
  const linkRatio = best.withLink / n;
  const headingRatio = best.withHeading / n;
  let confidence = 0.4;
  if (n >= 10) confidence += 0.25; else if (n >= MIN_REPEATS) confidence += 0.12;
  if (linkRatio >= 0.8) confidence += 0.15;
  if (headingRatio >= 0.6) confidence += 0.1;
  if (nameSel) confidence += 0.08;
  confidence = Math.min(confidence, 0.98);

  return {
    card_selector: cardSelector,
    name_selector: nameSel,
    title_selector: titleSel,
    company_selector: companySel,
    profile_url_selector: urlSel,
    pagination_type: paginationType,
    next_button_selector: nextBtnSelector,
    confidence: confidence,
    card_count: n,
    reasoning: `Repeat-pattern detector: ${n}× "${best.sig}" under <${best.parent.tagName.toLowerCase()}>, `
             + `link-ratio ${linkRatio.toFixed(2)}, heading-ratio ${headingRatio.toFixed(2)}.`,
    detected_by: 'heuristic',
  };
}
"""


def detect_in_page_js() -> str:
    """Return the raw detector JS (for embedding elsewhere or testing)."""
    return _DETECTOR_JS


def identify_structure(page, min_confidence: float = 0.4):
    """
    Run the heuristic detector against a live Playwright page.

    Nudges the page first (some SPA lists only render once scrolled into view),
    then evaluates the in-page detector. Returns the structure dict, or None if
    nothing repeated enough to be a card list — in which case the caller can
    fall back to the AI path (if an API key exists) or the generic Tier-3 scraper.
    """
    try:
        # Gentle nudge so virtual-DOM lists mount before we look.
        try:
            page.evaluate("window.scrollBy(0, 400)")
            page.wait_for_timeout(600)
            page.evaluate("window.scrollBy(0, -400)")
            page.wait_for_timeout(400)
        except Exception:
            pass

        result = page.evaluate(_DETECTOR_JS)
    except Exception as e:
        print(f"  [auto_detect] in-page detection error: {e}")
        return None

    if not result or not result.get("card_selector"):
        return None

    conf = float(result.get("confidence", 0))
    if conf < min_confidence:
        print(f"  [auto_detect] best group too weak (confidence={conf:.2f}) — no card pattern")
        return None

    print(f"  [auto_detect] {result['card_count']} cards, "
          f"selector={result['card_selector']!r}, confidence={conf:.2f}")
    print(f"  [auto_detect] {result.get('reasoning', '')}")
    return result
