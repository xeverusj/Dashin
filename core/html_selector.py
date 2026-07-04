"""
core/html_selector.py — Inhouse HTML parsing with CSS selector support.

Implements a Parsel-compatible API using lxml and cssselect directly —
the same underlying libraries that Scrapling itself uses, with no
external scraping framework dependency.

Supported:
  Selector(html_string)              — parse HTML
  .css('selector')                   — CSS selection · SelectorList of Selector objects
  .css('selector::text')             — text content of each match
  .css('selector::attr(name)')       — attribute value of each match
  .css('*::text')                    — all text nodes in subtree
  .find_all('tag')                   — iterate all elements with tag
  .find_by_text(text, exact=False)   — find elements containing / matching text
  .containing_text(text)             — alias for find_by_text(exact=False)
  .get(default=None)                 — first result or default
  .getall()                          — all results as list of strings
  .html                              — outer HTML of element (property)
  .text                              — all text content of element (property)
  .tag                               — element tag name (property)
  .attrib                            — element attributes dict (property)
  .parent                            — parent Selector (property)
  .children                          — direct child Selectors (property)
  .siblings                          — sibling Selectors excl. self (property)
  .next                              — next sibling Selector (property)
  .previous                          — previous sibling Selector (property)
  .to_dict()                         — parse <table> element into list of row dicts
  .generate_selector()               — produce a unique CSS path for this element
  .get_best_match(candidates)        — return the most structurally similar Selector
"""

import re
from collections import Counter

try:
    import lxml.html
    import lxml.etree
    _LXML_AVAILABLE = True
except ImportError:
    _LXML_AVAILABLE = False


class SelectorList(list):
    """List of results with Parsel-compatible .get() / .getall() helpers."""

    def get(self, default=None):
        """Return first item or default if empty."""
        return self[0] if self else default

    def getall(self):
        """Return all items as a plain list."""
        return list(self)


class Selector:
    """
    CSS-selector wrapper around lxml.html with Scrapling-inspired extras.

    Mirrors the Scrapling / Parsel Selector API so scrapers can be written
    once and remain portable whether lxml is available or not.

    Extra features beyond basic Parsel compatibility:
      - DOM traversal  : .parent, .children, .siblings, .next, .previous
      - Text search    : .find_by_text(), .containing_text()
      - Table parsing  : .to_dict()
      - Selector gen   : .generate_selector()
      - Fuzzy matching : .get_best_match()
    """

    # Compile once — match e.g. "div.card::text"  or  "a::attr(href)"
    _RE_TEXT = re.compile(r'^(.*?)::text$', re.DOTALL)
    _RE_ATTR = re.compile(r'^(.*?)::attr\(([^)]+)\)$', re.DOTALL)

    def __init__(self, source):
        """
        Accept either an HTML string or a raw lxml element.
        If lxml is not installed the object is inert (all queries return empty).
        """
        self._el = None
        if not _LXML_AVAILABLE:
            return
        if isinstance(source, str):
            try:
                self._el = lxml.html.fromstring(source)
            except Exception:
                pass
        elif source is not None:
            # Already an lxml element — wrap directly
            self._el = source

    # ── Core API ──────────────────────────────────────────────────────────────

    @property
    def html(self) -> str:
        """Outer HTML of the wrapped element, or empty string."""
        if self._el is None:
            return ''
        try:
            return lxml.html.tostring(self._el, encoding='unicode')
        except Exception:
            return ''

    @property
    def text(self) -> str:
        """All text content of the element, stripped."""
        return self._text(self._el) if self._el is not None else ''

    @property
    def tag(self) -> str:
        """Tag name of the element (e.g. 'div', 'a', 'span')."""
        if self._el is None:
            return ''
        try:
            return self._el.tag if isinstance(self._el.tag, str) else ''
        except Exception:
            return ''

    @property
    def attrib(self) -> dict:
        """Dict of all attributes on this element."""
        if self._el is None:
            return {}
        try:
            return dict(self._el.attrib)
        except Exception:
            return {}

    def css(self, query: str) -> SelectorList:
        """
        Run a CSS selector query with optional ::text / ::attr() pseudo-elements.

        Returns a SelectorList of:
          - strings  when ::text or ::attr() is used
          - Selector objects  otherwise (each wrapping a matched element)
        """
        if self._el is None:
            return SelectorList()

        text_m = self._RE_TEXT.match(query)
        attr_m = self._RE_ATTR.match(query)

        if text_m:
            base = text_m.group(1).strip()
            elements = self._select(base)
            results = []
            for el in elements:
                t = self._text(el)
                if t:
                    results.append(t)
            return SelectorList(results)

        if attr_m:
            base = attr_m.group(1).strip()
            attr_name = attr_m.group(2).strip()
            elements = self._select(base)
            results = []
            for el in elements:
                val = el.get(attr_name, '')
                if val:
                    results.append(val)
            return SelectorList(results)

        # Plain selector — return Selector wrappers
        elements = self._select(query)
        return SelectorList([Selector(el) for el in elements])

    def find_all(self, tag: str) -> SelectorList:
        """
        Iterate all descendant elements with the given tag name.
        Equivalent to lxml element.iter(tag).
        """
        if self._el is None:
            return SelectorList()
        try:
            return SelectorList([Selector(el) for el in self._el.iter(tag)])
        except Exception:
            return SelectorList()

    def get(self, default=None):
        """Return outer HTML of this element, or default."""
        h = self.html
        return h if h else default

    def getall(self) -> list:
        """Return [outer_html] for this single element (list for API compat)."""
        h = self.html
        return [h] if h else []

    # ── DOM Traversal ─────────────────────────────────────────────────────────

    @property
    def parent(self) -> 'Selector':
        """Parent element as a Selector, or an empty Selector if at root."""
        if self._el is None:
            return Selector(None)
        try:
            p = self._el.getparent()
            return Selector(p) if p is not None else Selector(None)
        except Exception:
            return Selector(None)

    @property
    def children(self) -> SelectorList:
        """Direct child elements as a SelectorList."""
        if self._el is None:
            return SelectorList()
        try:
            return SelectorList([Selector(c) for c in self._el
                                 if isinstance(c.tag, str)])
        except Exception:
            return SelectorList()

    @property
    def siblings(self) -> SelectorList:
        """All sibling elements (same parent, excluding self) as a SelectorList."""
        if self._el is None:
            return SelectorList()
        try:
            parent = self._el.getparent()
            if parent is None:
                return SelectorList()
            return SelectorList([
                Selector(s) for s in parent
                if s is not self._el and isinstance(s.tag, str)
            ])
        except Exception:
            return SelectorList()

    @property
    def next(self) -> 'Selector':
        """Next sibling element, or an empty Selector if none."""
        if self._el is None:
            return Selector(None)
        try:
            sib = self._el.getnext()
            # Skip non-element nodes (comments, processing instructions)
            while sib is not None and not isinstance(sib.tag, str):
                sib = sib.getnext()
            return Selector(sib) if sib is not None else Selector(None)
        except Exception:
            return Selector(None)

    @property
    def previous(self) -> 'Selector':
        """Previous sibling element, or an empty Selector if none."""
        if self._el is None:
            return Selector(None)
        try:
            sib = self._el.getprevious()
            while sib is not None and not isinstance(sib.tag, str):
                sib = sib.getprevious()
            return Selector(sib) if sib is not None else Selector(None)
        except Exception:
            return Selector(None)

    # ── Text-Based Finding ────────────────────────────────────────────────────

    def find_by_text(self, text: str, exact: bool = False,
                     case_sensitive: bool = False) -> SelectorList:
        """
        Find all descendant elements whose text content matches *text*.

        Args:
            text:           The string to search for.
            exact:          If True, the full text_content() must equal text
                            (after stripping). If False (default), a substring
                            match is used.
            case_sensitive: If False (default), comparison is case-insensitive.

        Returns:
            SelectorList of matching Selector objects.
        """
        if self._el is None or not text:
            return SelectorList()

        needle = text if case_sensitive else text.lower()
        results = []
        try:
            for el in self._el.iter():
                if not isinstance(el.tag, str):
                    continue
                content = (el.text_content() or '').strip()
                haystack = content if case_sensitive else content.lower()
                if exact:
                    if haystack == needle:
                        results.append(Selector(el))
                else:
                    if needle in haystack:
                        results.append(Selector(el))
        except Exception:
            pass
        return SelectorList(results)

    def containing_text(self, text: str,
                        case_sensitive: bool = False) -> SelectorList:
        """
        Alias for find_by_text(text, exact=False).
        Returns all descendants whose text content contains *text*.
        """
        return self.find_by_text(text, exact=False, case_sensitive=case_sensitive)

    # ── Table Extraction ──────────────────────────────────────────────────────

    def to_dict(self) -> list:
        """
        Parse an HTML <table> element (or the first <table> inside this element)
        into a list of row dicts keyed by the header row.

        Returns an empty list if no <table> is found or it has no header row.

        Example:
            sel.css('table').get()   # or sel itself if it IS a <table>
            sel.to_dict()
            # · [{"Name": "Alice", "Company": "Acme"}, ...]
        """
        if self._el is None:
            return []

        # Find the target table element
        target = None
        if isinstance(self._el.tag, str) and self._el.tag.lower() == 'table':
            target = self._el
        else:
            found = self._el.cssselect('table')
            if found:
                target = found[0]

        if target is None:
            return []

        try:
            return self._parse_table(target)
        except Exception:
            return []

    # ── Selector Generation ───────────────────────────────────────────────────

    def generate_selector(self) -> str:
        """
        Generate a unique CSS selector path that identifies this element
        within its document tree.

        Strategy (most-specific to least):
          1. If element has a unique id · '#id'
          2. Otherwise build a path of 'tag.class:nth-child(n)' steps
             walking up the tree to the root.

        Returns an empty string if the element is None.
        """
        if self._el is None:
            return ''
        try:
            return self._build_selector_path(self._el)
        except Exception:
            return ''

    # ── Fuzzy / Similarity Matching ───────────────────────────────────────────

    def get_best_match(self, candidates) -> 'Selector':
        """
        Return the Selector from *candidates* that is most structurally
        similar to *self*.

        Similarity is scored by comparing:
          - tag name                (weight 3)
          - CSS class overlap       (weight 4)
          - attribute name overlap  (weight 2)
          - child count bucket      (weight 1)
          - text length bucket      (weight 1)
          - depth in tree           (weight 1)

        Args:
            candidates: iterable of Selector objects or an HTML string.
                        If a string is passed it is parsed and all descendant
                        elements are used as candidates.

        Returns:
            The best-matching Selector, or an empty Selector if candidates
            is empty or self is None.
        """
        if self._el is None:
            return Selector(None)

        # Accept raw HTML string as candidates source
        if isinstance(candidates, str):
            root = Selector(candidates)
            candidate_list = [Selector(el) for el in root._el.iter()
                              if root._el is not None
                              and isinstance(el.tag, str)]
        else:
            candidate_list = list(candidates)

        if not candidate_list:
            return Selector(None)

        ref_fp = self._fingerprint(self._el)
        best_sel = None
        best_score = -1.0

        for cand in candidate_list:
            if cand._el is None:
                continue
            score = self._similarity(ref_fp, self._fingerprint(cand._el))
            if score > best_score:
                best_score = score
                best_sel = cand

        return best_sel if best_sel is not None else Selector(None)

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _select(self, selector: str) -> list:
        """Run a CSS selector and return a list of lxml elements."""
        if self._el is None:
            return []
        # Empty / wildcard — return all descendants
        if not selector or selector.strip() == '*':
            try:
                return list(self._el.iter())
            except Exception:
                return []
        try:
            return self._el.cssselect(selector)
        except Exception:
            return []

    @staticmethod
    def _text(element) -> str:
        """Return all text content of an lxml element, stripped."""
        try:
            return (element.text_content() or '').strip()
        except Exception:
            return ''

    # ── Table Helper ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_table(table_el) -> list:
        """Convert an lxml <table> element to a list of row dicts."""
        rows = table_el.cssselect('tr')
        if not rows:
            return []

        # Collect headers from the first <th> row; fall back to first <td> row
        headers = []
        header_row_idx = 0
        for idx, row in enumerate(rows):
            ths = row.cssselect('th')
            if ths:
                headers = [(th.text_content() or '').strip() for th in ths]
                header_row_idx = idx
                break

        if not headers:
            # Use first row tds as headers
            tds = rows[0].cssselect('td')
            headers = [(td.text_content() or '').strip() for td in tds]
            header_row_idx = 0

        if not headers:
            return []

        result = []
        for row in rows[header_row_idx + 1:]:
            cells = row.cssselect('td')
            if not cells:
                continue
            row_dict = {}
            for i, cell in enumerate(cells):
                key = headers[i] if i < len(headers) else f'col_{i}'
                row_dict[key] = (cell.text_content() or '').strip()
            if any(v for v in row_dict.values()):
                result.append(row_dict)
        return result

    # ── Selector Generation Helper ────────────────────────────────────────────

    @staticmethod
    def _build_selector_path(el) -> str:
        """Walk up the tree building a CSS path for *el*."""
        # If element has a unique id, that's sufficient
        el_id = el.get('id', '').strip()
        if el_id and ' ' not in el_id:
            return f'#{el_id}'

        parts = []
        current = el
        while current is not None and isinstance(current.tag, str):
            tag = current.tag.lower()
            classes = current.get('class', '').split()
            # Use at most 2 classes to keep selector readable
            class_str = ''.join(f'.{c}' for c in classes[:2] if c.isidentifier())

            parent = current.getparent()
            if parent is not None:
                # nth-child position among same-tag siblings
                same_tag = [s for s in parent if s.tag == current.tag]
                if len(same_tag) > 1:
                    pos = same_tag.index(current) + 1
                    parts.append(f'{tag}{class_str}:nth-child({pos})')
                else:
                    parts.append(f'{tag}{class_str}')
            else:
                parts.append(f'{tag}{class_str}')
            current = parent

        parts.reverse()
        return ' > '.join(parts)

    # ── Fingerprint / Similarity Helpers ──────────────────────────────────────

    @staticmethod
    def _fingerprint(el) -> dict:
        """Extract structural features of an lxml element for comparison."""
        tag = el.tag.lower() if isinstance(el.tag, str) else ''
        classes = set((el.get('class') or '').split())
        attrs = set(el.attrib.keys())
        child_count = sum(1 for c in el if isinstance(c.tag, str))
        text_len = len((el.text_content() or '').strip())
        # Tree depth
        depth = 0
        p = el.getparent()
        while p is not None:
            depth += 1
            p = p.getparent()
        return {
            'tag': tag,
            'classes': classes,
            'attrs': attrs,
            'child_bucket': min(child_count // 3, 10),   # bucket: 0,1,2…10
            'text_bucket': min(text_len // 50, 20),       # bucket per 50 chars
            'depth_bucket': min(depth // 2, 15),          # bucket per 2 levels
        }

    @staticmethod
    def _similarity(fp_a: dict, fp_b: dict) -> float:
        """
        Score structural similarity between two fingerprint dicts.
        Returns a float; higher is more similar.
        """
        score = 0.0

        # Tag match — highest single signal
        if fp_a['tag'] and fp_a['tag'] == fp_b['tag']:
            score += 3.0

        # Class overlap: Jaccard similarity × 4
        cls_a, cls_b = fp_a['classes'], fp_b['classes']
        if cls_a or cls_b:
            union = cls_a | cls_b
            inter = cls_a & cls_b
            score += 4.0 * len(inter) / len(union)

        # Attribute name overlap: Jaccard × 2
        att_a, att_b = fp_a['attrs'], fp_b['attrs']
        if att_a or att_b:
            union = att_a | att_b
            inter = att_a & att_b
            score += 2.0 * len(inter) / len(union)

        # Child count bucket match
        if fp_a['child_bucket'] == fp_b['child_bucket']:
            score += 1.0

        # Text length bucket match
        if fp_a['text_bucket'] == fp_b['text_bucket']:
            score += 1.0

        # Depth bucket match
        if fp_a['depth_bucket'] == fp_b['depth_bucket']:
            score += 1.0

        return score
