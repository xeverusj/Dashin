"""
core/icons.py — monochrome line icons (Lucide-style) for Dashin.

Replaces decorative emoji in custom-HTML areas (page headers, section titles,
cards, sidebar). Each icon is an inline SVG that strokes with `currentColor`, so
it inherits the surrounding text colour and adapts to the dark/light theme
automatically.

Usage:
    from core.icons import icon
    st.markdown(f'{icon("inventory")} Inventory', unsafe_allow_html=True)
    st.markdown(icon("users", size=18, color="var(--accent)"), unsafe_allow_html=True)

Streamlit's native widgets (st.radio nav, st.button, st.tabs) take plain strings
and cannot embed SVG — use clean text labels there.
"""

# Lucide-style 24x24 path data. Keep the visual weight consistent (stroke 1.75).
_PATHS = {
    # nav / sections
    "platform":   '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    "scraper":    '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "search":     '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "inventory":  '<path d="M21 8 12 3 3 8l9 5 9-5Z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>',
    "research":   '<path d="M9 3h6"/><path d="M10 3v6l-4 8a2 2 0 0 0 2 3h8a2 2 0 0 0 2-3l-4-8V3"/>',
    "clipboard":  '<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>',
    "campaign":   '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/>',
    "chart":      '<path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="4" width="3" height="14"/>',
    "estimator":  '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h2M12 10h4M8 14h2M12 14h4M8 18h2"/>',
    "target":     '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
    "scoring":    '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="7" cy="18" r="2"/>',
    "link":       '<path d="M9 15l6-6"/><path d="M11 6l1-1a4 4 0 0 1 6 6l-1 1"/><path d="M13 18l-1 1a4 4 0 0 1-6-6l1-1"/>',
    "mail":       '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "outreach":   '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/>',
    "report":     '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    "settings":   '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.4l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2.4-1.4L13.7 2h-3.4l-.4 2.6a7 7 0 0 0-2.4 1.4l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.8l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2.4 1.4l.4 2.6h3.4l.4-2.6a7 7 0 0 0 2.4-1.4l2.4 1 2-3.4-2-1.6c.06-.46.1-.93.1-1.4Z"/>',
    "users":      '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 21a6.5 6.5 0 0 1 13 0"/><path d="M16 5.5a3.5 3.5 0 0 1 0 6"/><path d="M17.5 21a6.5 6.5 0 0 0-3-5.5"/>',
    "user":       '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "building":   '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M9 8h.01M15 8h.01M9 12h.01M15 12h.01M9 16h.01M15 16h.01"/>',
    "folder":     '<path d="M4 5a2 2 0 0 1 2-2h4l2 3h6a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5Z"/>',
    "calendar":   '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>',
    "bell":       '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    "plus":       '<path d="M12 5v14M5 12h14"/>',
    "edit":       '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    "trash":      '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
    "download":   '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 21h16"/>',
    "upload":     '<path d="M12 21V9M7 14l5-5 5 5"/><path d="M4 3h16"/>',
    "refresh":    '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "save":       '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
    "check":      '<path d="M20 6 9 17l-5-5"/>',
    "warning":    '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>',
    "block":      '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
    "eye":        '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "brain":      '<path d="M9 3a3 3 0 0 0-3 3 3 3 0 0 0-1 5 3 3 0 0 0 1 5 3 3 0 0 0 3 3 2.5 2.5 0 0 0 3-2.5V5.5A2.5 2.5 0 0 0 9 3Z"/><path d="M15 3a3 3 0 0 1 3 3 3 3 0 0 1 1 5 3 3 0 0 1-1 5 3 3 0 0 1-3 3 2.5 2.5 0 0 1-3-2.5V5.5A2.5 2.5 0 0 1 15 3Z"/>',
    "message":    '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>',
    "attach":     '<path d="M21 8.5 12.8 16.7a4 4 0 0 1-5.6-5.6l8.1-8.1a2.5 2.5 0 0 1 3.5 3.5l-8.1 8.1a1 1 0 0 1-1.4-1.4l7.4-7.4"/>',
    "arrow-right":'<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-up":   '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "flag":       '<path d="M4 22V4h13l-2 4 2 4H4"/>',
}


def icon(name: str, size: int = 16, color: str = "currentColor",
         stroke: float = 1.75, cls: str = "dsh-ico") -> str:
    """Return an inline SVG for the named icon, or empty string if unknown."""
    body = _PATHS.get(name)
    if not body:
        return ""
    return (
        f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round" style="vertical-align:-0.18em;flex:none">{body}</svg>'
    )


def mask_uri(name: str) -> str:
    """
    Return a `data:` URI for the named icon, for use as a CSS mask-image. The
    element it's applied to sets `background-color: currentColor`, so the icon
    takes the surrounding text colour — this is how we get monochrome nav icons
    into Streamlit's radio nav (whose labels can't hold inline SVG).
    """
    import urllib.parse
    body = _PATHS.get(name)
    if not body:
        return ""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
           'viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.9" '
           'stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>')
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


def nav_icon_css(page_keys: list, selector_prefix: str) -> str:
    """
    Build a <style> block that puts a monochrome icon before each nav item, keyed
    to the *actual* render order (page_keys). Uses ::before + mask-image so the
    icon inherits the item's text colour and theme.
    """
    css = ["<style>"]
    for i, key in enumerate(page_keys, start=1):
        name = _NAV_ICON.get(key)
        if not name:
            continue
        uri = mask_uri(name)
        css.append(
            f'{selector_prefix} label[data-baseweb="radio"]:nth-of-type({i})::before '
            f'{{content:"";display:inline-block;width:16px;height:16px;margin-right:10px;'
            f'flex:none;background-color:currentColor;'
            f'-webkit-mask:url("{uri}") no-repeat center / contain;'
            f'mask:url("{uri}") no-repeat center / contain;vertical-align:-0.18em;}}')
    css.append("</style>")
    return "\n".join(css)


# page_key -> icon name, used by the sidebar nav.
_NAV_ICON = {
    "superadmin": "platform", "scraper": "search", "inventory": "inventory",
    "research": "research", "res_manager": "clipboard", "campaigns": "campaign",
    "camp_manager": "chart", "estimator": "estimator", "enrichment": "target",
    "scoring": "scoring", "enricher": "link", "email_match": "mail",
    "outreach": "outreach", "report": "report", "reports": "report",
    "report_builder": "report", "admin": "settings",
    # client portal
    "client_home": "building", "client_leads": "users", "client_campaigns": "campaign",
    "client_report": "report", "client_files": "folder", "client_notes": "edit",
}


# Small solid status dot (replaces 🔴🟢🟡) — colour via a semantic var.
def dot(color: str = "var(--text-3)", size: int = 8) -> str:
    return (f'<span style="display:inline-block;width:{size}px;height:{size}px;'
            f'border-radius:50%;background:{color};vertical-align:middle"></span>')
