"""
services/report_pdf.py — generate client report PDFs from Dashin report data.

Two outputs, both built from the SAME data already stored in the campaign_reports
/ report_campaigns / report_crm_contacts tables (so nothing is entered twice):

  build_full_report_pdf(report, campaigns, crm_contacts, insights) -> bytes
      The complete report: KPI band, campaign breakdown, per-campaign pipeline
      tables (named contacts), and the written insights with action lines.

  build_onepager_pdf(report, campaigns, insights) -> bytes
      A single-page executive summary: KPI band, campaign breakdown, and the top
      insights — the "funnel & roadmap" view.

Both take plain dicts/lists so they're testable without a database. Rendered with
reportlab Platypus (pure-Python, no native deps) in Dashin's brand palette
(cream/white page, near-black text, gold accent).
"""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                Spacer, KeepTogether)
from reportlab.lib.enums import TA_LEFT

# ── Brand palette ─────────────────────────────────────────────────────────────
GOLD = colors.HexColor("#C9A96E")
INK = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#6B6B6B")
LINE = colors.HexColor("#E3E1DB")
CREAM = colors.HexColor("#F7F6F3")
GREEN = colors.HexColor("#2E7D32")
ORANGE = colors.HexColor("#B26A00")
RED = colors.HexColor("#B3261E")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Kicker", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=7.5, textColor=GOLD, spaceAfter=2, leading=10))
    ss.add(ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold",
                          fontSize=22, textColor=INK, spaceAfter=2, leading=25, alignment=TA_LEFT))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], fontSize=9.5, textColor=MUTED,
                          spaceAfter=10, leading=13))
    ss.add(ParagraphStyle("SecH", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=11, textColor=INK, spaceBefore=12, spaceAfter=6, leading=13))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=9, textColor=INK, leading=13))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], fontSize=8, textColor=INK, leading=11))
    ss.add(ParagraphStyle("CellMuted", parent=ss["Normal"], fontSize=8, textColor=MUTED, leading=11))
    ss.add(ParagraphStyle("Action", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=8.5, textColor=GOLD, leading=12, spaceBefore=2))
    ss.add(ParagraphStyle("InsightT", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=9.5, textColor=INK, spaceBefore=6, spaceAfter=2, leading=12))
    return ss


def _num(v):
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v or 0)


def _kpi_band(report, ss):
    """A row of KPI cards rendered as a single-row table."""
    rate = (report.get("total_responses", 0) / report["total_emails"] * 100
            if report.get("total_emails") else 0)
    cells = [
        (_num(report.get("total_emails", 0)), "Total Emails",
         f"{_num(report.get('total_cold',0))} cold · {_num(report.get('total_followups',0))} follow-ups"),
        (_num(report.get("total_responses", 0)), "Responses", f"{rate:.1f}% overall"),
        (_num(report.get("total_interested", 0)), "Interested / Positive", "incl. 'not now'"),
        (_num(report.get("total_meetings", 0)), "Meetings Booked", "confirmed"),
        (f"{_num(report.get('crm_count',0))}", "CRM Leads", "real engagement"),
    ]
    row_val, row_lbl = [], []
    for val, lbl, sub in cells:
        row_val.append(Paragraph(f'<font size="17"><b>{val}</b></font>', ss["Body"]))
        row_lbl.append(Paragraph(f'<b>{lbl}</b><br/><font color="#6B6B6B" size="6.5">{sub}</font>',
                                 ss["Cell"]))
    t = Table([row_val, row_lbl], colWidths=[35*mm]*5)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CREAM),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _campaign_table(campaigns, ss):
    head = ["Campaign", "Cold", "Follow-ups", "Total", "Responses", "Booked", "Rate", "Status"]
    data = [[Paragraph(f'<b>{h}</b>', ss["Cell"]) for h in head]]
    for c in campaigns:
        total = c.get("total", 0)
        rate = c.get("rate") or (c.get("responses", 0) / total * 100 if total else 0)
        booked = c.get("booked", c.get("interested", 0))
        data.append([
            Paragraph(str(c.get("name", "")), ss["Cell"]),
            Paragraph(_num(c.get("cold", 0)), ss["Cell"]),
            Paragraph(_num(c.get("followups", 0)), ss["Cell"]),
            Paragraph(f'<b>{_num(total)}</b>', ss["Cell"]),
            Paragraph(_num(c.get("responses", 0)), ss["Cell"]),
            Paragraph(_num(booked), ss["Cell"]),
            Paragraph(f"{rate:.1f}%", ss["Cell"]),
            Paragraph(str(c.get("status", "")), ss["CellMuted"]),
        ])
    t = Table(data, colWidths=[34*mm, 15*mm, 20*mm, 16*mm, 20*mm, 20*mm, 14*mm, 36*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _pipeline_table(contacts, ss):
    head = ["Contact", "Company", "Role", "Status", "Notes"]
    data = [[Paragraph(f'<b>{h}</b>', ss["Cell"]) for h in head]]
    for ct in contacts:
        status = ct.get("status", "") or ""
        col = "#2E7D32" if ("book" in status.lower() or "met" in status.lower()) else \
              "#6B6B6B" if not status else "#B26A00"
        data.append([
            Paragraph(f'<b>{ct.get("contact_name","")}</b>', ss["Cell"]),
            Paragraph(str(ct.get("company", "")), ss["Cell"]),
            Paragraph(str(ct.get("role", "")), ss["CellMuted"]),
            Paragraph(f'<font color="{col}"><b>{status}</b></font>', ss["Cell"]),
            Paragraph(str(ct.get("notes", "")), ss["CellMuted"]),
        ])
    t = Table(data, colWidths=[30*mm, 32*mm, 28*mm, 30*mm, 55*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CREAM),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, GOLD),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _insight_flow(insights, ss, limit=None):
    flow = []
    items = insights[:limit] if limit else insights
    for s in items:
        block = [Paragraph(s.get("title", ""), ss["InsightT"])]
        if s.get("body"):
            block.append(Paragraph(s["body"].replace("\n", "<br/>"), ss["Body"]))
        if s.get("action"):
            block.append(Paragraph("ACTION — " + s["action"], ss["Action"]))
        flow.append(KeepTogether(block))
        flow.append(Spacer(1, 4))
    return flow


def _header(report, ss, subtitle):
    return [
        Paragraph((report.get("client_name", "") or "CLIENT").upper()
                  + " · B2B OUTREACH", ss["Kicker"]),
        Paragraph(report.get("title", "Outreach & Pipeline Report"), ss["H1"]),
        Paragraph(subtitle, ss["Sub"]),
    ]


def _footer(report):
    dr = report.get("date_range", "") or ""
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18*mm, 10*mm,
                          f"{report.get('title','Report')}  ·  {dr}".strip(" ·"))
        canvas.drawRightString(A4[0]-18*mm, 10*mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()
    return draw


def build_full_report_pdf(report: dict, campaigns: list, crm_contacts: list,
                          insights: list) -> bytes:
    buf = BytesIO()
    ss = _styles()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm,
                            title=report.get("title", "Report"))
    story = _header(report, ss, report.get("date_range", "") or "Campaign performance & pipeline")
    story += [_kpi_band(report, ss), Spacer(1, 12)]
    story += [Paragraph("Campaign Breakdown", ss["SecH"]), _campaign_table(campaigns, ss)]

    # Pipeline tables grouped by campaign
    by_campaign = {}
    for ct in crm_contacts:
        by_campaign.setdefault(ct.get("campaign_name", "Other"), []).append(ct)
    if by_campaign:
        story += [Paragraph("Pipeline by Campaign", ss["SecH"])]
        for camp, contacts in by_campaign.items():
            booked = sum(1 for x in contacts if "book" in (x.get("status", "") or "").lower())
            story.append(KeepTogether([
                Paragraph(f'{camp} &nbsp; <font color="#6B6B6B" size="8">'
                          f'({len(contacts)} leads · {booked} booked)</font>', ss["InsightT"]),
                Spacer(1, 2), _pipeline_table(contacts, ss), Spacer(1, 8)]))

    if insights:
        story += [Paragraph("Honest Insights", ss["SecH"])] + _insight_flow(insights, ss)

    draw = _footer(report)
    doc.build(story, onFirstPage=draw, onLaterPages=draw)
    return buf.getvalue()


def build_onepager_pdf(report: dict, campaigns: list, insights: list) -> bytes:
    buf = BytesIO()
    ss = _styles()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=14*mm, bottomMargin=14*mm,
                            title=report.get("title", "Report") + " — One Pager")
    story = _header(report, ss, "Outreach Funnel & Growth Roadmap")
    story += [_kpi_band(report, ss), Spacer(1, 10)]
    story += [Paragraph("Campaign Breakdown", ss["SecH"]), _campaign_table(campaigns, ss)]
    if insights:
        story += [Paragraph("Key Takeaways", ss["SecH"])] + _insight_flow(insights, ss, limit=3)
    draw = _footer(report)
    doc.build(story, onFirstPage=draw, onLaterPages=draw)
    return buf.getvalue()
