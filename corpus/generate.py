"""Generate the test corpus: broadcast advertising orders and invoices from
seven senders that share no format, plus their ground truth.

Why synthetic: this sandbox has no route to the FCC political file, SROIE or any
other real corpus (outbound network is allow-listed to the model API and package
registries only). The generator therefore stands in for the real thing, and is
built so the difficulty is real even though the documents are not: each sender
family is a separate layout with its own vocabulary and number style, the
degradations are physical, and several documents carry content traps whose
correct answer is "flag this", not any particular value.

Run:  python corpus/generate.py
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
INPUTS = HERE / "inputs"
TRUTH = HERE / "ground_truth"
WORK = HERE / ".work"

PAGE_W, PAGE_H = letter
SEED = 20260824

# --------------------------------------------------------------------------
# Sender families. Each has its own layout renderer, its own words for the same
# facts, and its own number and date conventions -- which is the whole problem.
# --------------------------------------------------------------------------

FAMILIES = {
    "meridian": dict(
        name="Meridian Broadcast Group",
        market="Cleveland, OH",
        style="Modern grid invoice, sans-serif, ruled table",
        currency="USD", number_style="us", date_style="us_slash",
        doc_type="invoice", total_label="Total Due",
    ),
    "kvue": dict(
        name="KTVR-TV Channel 9",
        market="Amarillo, TX",
        style="Typewriter order form, monospaced, dot leaders",
        currency="USD", number_style="us", date_style="us_slash",
        doc_type="order", total_label="GROSS AMOUNT",
    ),
    "natrep": dict(
        name="National Spot Reps LLC",
        market="New York, NY",
        style="Dense daypart rate table, small type",
        currency="USD", number_style="us", date_style="iso",
        doc_type="confirmation", total_label="Contract Total",
    ),
    "hollis": dict(
        name="Hollis & Reed Media Buying",
        market="Atlanta, GA",
        style="Letterhead prose; the facts live inside sentences",
        currency="USD", number_style="us", date_style="long",
        doc_type="order", total_label="total of",
    ),
    "nab": dict(
        name="WQRM Radio 88.3",
        market="Burlington, VT",
        style="Boxed two-column agency form, all caps labels",
        currency="USD", number_style="us", date_style="us_dash",
        doc_type="order", total_label="TOTAL GROSS",
    ),
    "quickspot": dict(
        name="QuickSpot Digital",
        market="Phoenix, AZ",
        style="Minimal receipt, narrow, almost no labels",
        currency="USD", number_style="us", date_style="us_slash",
        doc_type="invoice", total_label="TOTAL",
    ),
    "europa": dict(
        name="Europa Media Vertretung GmbH",
        market="Frankfurt, DE",
        style="International rep firm; EUR and 1.234,56 number style",
        currency="EUR", number_style="eu", date_style="eu_dot",
        doc_type="invoice", total_label="Rechnungsbetrag / Total",
    ),
}

ADVERTISERS = [
    ("Hillcrest Motors", "Brightline Media"),
    ("Val's Home & Garden", "Brightline Media"),
    ("Copperfield Insurance", "Anchor Media Partners"),
    ("Northgate Dental Group", None),
    ("Ridgeway Furniture Outlet", "Anchor Media Partners"),
    ("Summit Credit Union", "Brightline Media"),
    ("Delacroix Bistro", None),
    ("PaveRight Contracting", "Keystone Buying Svcs"),
    ("Lakeshore Veterinary", None),
    ("Ferber Appliance Depot", "Keystone Buying Svcs"),
]

DAYPARTS = [
    "Early News M-F 5-6:30p", "Prime Access M-F 7-8p", "Late News M-F 11-11:35p",
    "Weekend Sports Sa-Su 12-6p", "Morning Drive M-F 6-10a", "Midday Rotator M-Su 10a-3p",
    "Primetime Rotator M-Su 8-11p", "Overnight ROS 12-6a",
]


# --------------------------------------------------------------------------
# Money and date formatting, per sender convention
# --------------------------------------------------------------------------

def fmt_money(amount: Decimal, style: str, symbol: str = "$") -> str:
    q = f"{amount:,.2f}"
    if style == "eu":
        q = q.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
        return f"{q} EUR"
    return f"{symbol}{q}"


def fmt_date(d: date, style: str) -> str:
    return {
        "us_slash": d.strftime("%m/%d/%y"),
        "us_dash": d.strftime("%m-%d-%Y"),
        "iso": d.isoformat(),
        "long": d.strftime("%B %-d, %Y"),
        "eu_dot": d.strftime("%d.%m.%Y"),
    }[style]


# --------------------------------------------------------------------------
# Content model
# --------------------------------------------------------------------------

def make_facts(rng, family_key, trap=None):
    fam = FAMILIES[family_key]
    advertiser, agency = ADVERTISERS[rng.randrange(len(ADVERTISERS))]

    doc_date = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 200))
    flight_start = doc_date + timedelta(days=rng.randrange(3, 25))
    flight_end = flight_start + timedelta(days=rng.choice([6, 13, 20, 27]))

    prefix = {"meridian": "MBG", "kvue": "", "natrep": "NSR", "hollis": "HR",
              "nab": "WQ", "quickspot": "QS", "europa": "EMV"}[family_key]
    num = rng.randrange(10000, 99999)
    doc_id = f"{prefix}-{num}" if prefix else f"{rng.randrange(10,99)}-{num}"

    lines = []
    for _ in range(rng.randrange(2, 5)):
        qty = rng.randrange(4, 40)
        rate = Decimal(rng.randrange(4000, 95000)) / 100
        lines.append({
            "description": DAYPARTS[rng.randrange(len(DAYPARTS))],
            "quantity": qty,
            "unit_rate": rate,
            "amount": (rate * qty).quantize(Decimal("0.01")),
        })

    gross = sum((l["amount"] for l in lines), Decimal("0"))
    stated_gross = gross
    if trap == "inconsistent_total":
        # A discount mentioned only in prose: the printed total is lower than
        # the visible line items. Both numbers are on the page; a human has to
        # decide which one the system should book.
        stated_gross = (gross - Decimal(rng.randrange(20000, 60000)) / 100).quantize(Decimal("0.01"))

    net = (stated_gross * Decimal("0.85")).quantize(Decimal("0.01")) if agency else None

    return {
        "sender_name": fam["name"],
        "sender_market": fam["market"],
        "doc_type": fam["doc_type"],
        "doc_id": doc_id,
        "doc_date": doc_date,
        "advertiser": advertiser,
        "agency": agency,
        "flight_start": flight_start,
        "flight_end": flight_end,
        "line_items": lines,
        "line_sum": gross,
        "gross_total": stated_gross,
        "net_total": net,
        "currency": fam["currency"],
        "trap": trap,
    }


# --------------------------------------------------------------------------
# Renderers: one per family. These share no layout code on purpose.
# --------------------------------------------------------------------------

def _canvas():
    # invariant=1 stops reportlab stamping a creation timestamp, so the same
    # seed produces byte-identical PDFs on every run.
    buf = io.BytesIO()
    return buf, rl_canvas.Canvas(buf, pagesize=letter, invariant=1)


def _finish(buf, c):
    c.showPage()
    c.save()
    return buf.getvalue()


def render_meridian(f):
    """Modern grid invoice: helvetica, ruled table, right-aligned money."""
    buf, c = _canvas()
    m, y = 0.9 * inch, PAGE_H - 0.9 * inch
    c.setFont("Helvetica-Bold", 20)
    c.drawString(m, y, f["sender_name"])
    c.setFont("Helvetica", 9)
    c.drawString(m, y - 14, f"{f['sender_market']}  ·  Broadcast Sales Division")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(PAGE_W - m, y, "INVOICE")
    c.setFont("Helvetica", 9)
    c.drawRightString(PAGE_W - m, y - 14, f"Invoice No. {f['doc_id']}")
    c.drawRightString(PAGE_W - m, y - 26, f"Invoice Date  {fmt_date(f['doc_date'], 'us_slash')}")
    y -= 46
    c.setLineWidth(1.2)
    c.line(m, y, PAGE_W - m, y)
    y -= 24

    c.setFont("Helvetica-Bold", 9)
    c.drawString(m, y, "BILL TO")
    c.drawString(m + 3.1 * inch, y, "CAMPAIGN")
    c.setFont("Helvetica", 10)
    c.drawString(m, y - 15, f["agency"] or f["advertiser"])
    if f["agency"]:
        c.setFont("Helvetica", 9)
        c.drawString(m, y - 28, f"for advertiser: {f['advertiser']}")
    c.setFont("Helvetica", 10)
    c.drawString(m + 3.1 * inch, y - 15,
                 f"{fmt_date(f['flight_start'], 'us_slash')} – {fmt_date(f['flight_end'], 'us_slash')}")
    c.setFont("Helvetica", 9)
    c.drawString(m + 3.1 * inch, y - 28, "Flight dates")
    y -= 56

    cols = [m, m + 3.3 * inch, m + 4.2 * inch, m + 5.3 * inch]
    c.setFillGray(0.92)
    c.rect(m - 4, y - 5, PAGE_W - 2 * m + 8, 18, stroke=0, fill=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 8.5)
    for label, x in zip(["DESCRIPTION", "SPOTS", "RATE", "AMOUNT"], cols):
        (c.drawRightString if label in {"RATE", "AMOUNT"} else c.drawString)(
            x + (1.0 * inch if label in {"RATE", "AMOUNT"} else 0), y, label)
    y -= 20
    c.setFont("Helvetica", 9.5)
    for li in f["line_items"]:
        c.drawString(cols[0], y, li["description"])
        c.drawString(cols[1], y, str(li["quantity"]))
        c.drawRightString(cols[2] + 1.0 * inch, y, fmt_money(li["unit_rate"], "us"))
        c.drawRightString(cols[3] + 1.0 * inch, y, fmt_money(li["amount"], "us"))
        y -= 15
    y -= 6
    c.line(m + 3.9 * inch, y, PAGE_W - m, y)
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(m + 3.9 * inch, y, "Total Due")
    c.drawRightString(PAGE_W - m, y, fmt_money(f["gross_total"], "us"))
    if f["net_total"] is not None:
        y -= 16
        c.setFont("Helvetica", 9.5)
        c.drawString(m + 3.9 * inch, y, "Net after 15% agency comm.")
        c.drawRightString(PAGE_W - m, y, fmt_money(f["net_total"], "us"))
    c.setFont("Helvetica", 8)
    c.drawString(m, 0.8 * inch, "Remit within 30 days. Questions: billing@meridianbcg.example")
    return _finish(buf, c)


def render_kvue(f):
    """Typewriter order form: courier throughout, dot leaders, no table rules."""
    buf, c = _canvas()
    m, y = 1.0 * inch, PAGE_H - 1.0 * inch
    c.setFont("Courier-Bold", 14)
    c.drawCentredString(PAGE_W / 2, y, f["sender_name"].upper())
    c.setFont("Courier", 10)
    c.drawCentredString(PAGE_W / 2, y - 14, f["sender_market"].upper())
    c.drawCentredString(PAGE_W / 2, y - 34, "*** BROADCAST ADVERTISING ORDER ***")
    y -= 62

    def row(label, value):
        nonlocal y
        dots = "." * max(2, 34 - len(label))
        c.drawString(m, y, f"{label} {dots} {value}")
        y -= 15

    c.setFont("Courier", 10)
    row("ORDER NUMBER", f["doc_id"])
    row("ORDER DATE", fmt_date(f["doc_date"], "us_slash"))
    row("ADVERTISER", f["advertiser"].upper())
    row("AGENCY", (f["agency"] or "DIRECT").upper())
    row("START DATE", fmt_date(f["flight_start"], "us_slash"))
    row("END DATE", fmt_date(f["flight_end"], "us_slash"))
    y -= 10
    c.drawString(m, y, "-" * 62)
    y -= 16
    c.drawString(m, y, "QTY  DAYPART                          RATE       AMOUNT")
    y -= 6
    c.drawString(m, y, "-" * 62)
    y -= 16
    for li in f["line_items"]:
        desc = li["description"][:30].ljust(30)
        c.drawString(m, y, f"{str(li['quantity']).rjust(3)}  {desc} "
                           f"{fmt_money(li['unit_rate'], 'us').rjust(9)} "
                           f"{fmt_money(li['amount'], 'us').rjust(11)}")
        y -= 14
    y -= 4
    c.drawString(m, y, "-" * 62)
    y -= 18
    c.setFont("Courier-Bold", 11)
    c.drawString(m, y, f"GROSS AMOUNT {'.' * 22} {fmt_money(f['gross_total'], 'us')}")
    if f["net_total"] is not None:
        y -= 15
        c.setFont("Courier", 10)
        c.drawString(m, y, f"LESS AGY COMM 15%  {'.' * 16} {fmt_money(f['net_total'], 'us')}")
    y -= 40
    c.setFont("Courier", 9)
    c.drawString(m, y, "AUTHORIZED BY ______________________  DATE __________")
    return _finish(buf, c)


def render_natrep(f):
    """Dense rate confirmation: small type, many columns, distractor numbers."""
    buf, c = _canvas()
    m, y = 0.7 * inch, PAGE_H - 0.7 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(m, y, f["sender_name"])
    c.setFont("Helvetica", 7.5)
    c.drawString(m, y - 11, f"{f['sender_market']} | Station Representation | Ref {f['doc_id']}")
    c.drawRightString(PAGE_W - m, y, "SPOT BUY CONFIRMATION")
    c.drawRightString(PAGE_W - m, y - 11, f"Issued {fmt_date(f['doc_date'], 'iso')}")
    y -= 28
    c.setLineWidth(0.5)
    c.line(m, y, PAGE_W - m, y)
    y -= 16

    # Distractors: identifiers formatted like money, and a prior balance.
    c.setFont("Helvetica", 7.5)
    c.drawString(m, y, f"Client Code 4,412.00   |   Est. No. 1,088.75   |   "
                       f"Prior Balance Fwd {fmt_money(Decimal('2450.00'), 'us')}   |   "
                       f"Buyer {f['agency'] or 'direct'}")
    y -= 16
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y, f"ADVERTISER: {f['advertiser']}")
    c.drawString(m + 3.6 * inch, y, f"FLIGHT: {fmt_date(f['flight_start'], 'iso')} to "
                                    f"{fmt_date(f['flight_end'], 'iso')}")
    y -= 20

    xs = [m, m + 2.5 * inch, m + 3.4 * inch, m + 4.1 * inch, m + 5.0 * inch, m + 6.0 * inch]
    c.setFont("Helvetica-Bold", 7)
    for label, x in zip(["DAYPART", "LEN", "SPOTS", "WKS", "UNIT RATE", "EXTENDED"], xs):
        c.drawString(x, y, label)
    y -= 4
    c.line(m, y, PAGE_W - m, y)
    y -= 12
    c.setFont("Helvetica", 7.5)
    for li in f["line_items"]:
        c.drawString(xs[0], y, li["description"])
        c.drawString(xs[1], y, ":30")
        c.drawString(xs[2], y, str(li["quantity"]))
        c.drawString(xs[3], y, "4")
        c.drawRightString(xs[4] + 0.7 * inch, y, fmt_money(li["unit_rate"], "us"))
        c.drawRightString(xs[5] + 0.75 * inch, y, fmt_money(li["amount"], "us"))
        y -= 12
    y -= 4
    c.line(m + 4.6 * inch, y, PAGE_W - m, y)
    y -= 14
    c.setFont("Helvetica", 7.5)
    c.drawString(m + 4.6 * inch, y, "Total Spots")
    c.drawRightString(PAGE_W - m, y, str(sum(l["quantity"] for l in f["line_items"])))
    y -= 12
    if f["trap"] == "inconsistent_total":
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(m, y, "Note: negotiated rate adjustment applied at contract level; "
                           "see Contract Total below.")
        y -= 12
    c.setFont("Helvetica-Bold", 9)
    c.drawString(m + 4.6 * inch, y, "Contract Total")
    c.drawRightString(PAGE_W - m, y, fmt_money(f["gross_total"], "us"))
    if f["net_total"] is not None:
        y -= 12
        c.setFont("Helvetica", 7.5)
        c.drawString(m + 4.6 * inch, y, "Net to Station")
        c.drawRightString(PAGE_W - m, y, fmt_money(f["net_total"], "us"))
    return _finish(buf, c)


def render_hollis(f):
    """Letterhead prose: every fact is inside a sentence, no table at all."""
    buf, c = _canvas()
    m, y = 1.1 * inch, PAGE_H - 1.1 * inch
    c.setFont("Times-Bold", 16)
    c.drawCentredString(PAGE_W / 2, y, f["sender_name"])
    c.setFont("Times-Italic", 9.5)
    c.drawCentredString(PAGE_W / 2, y - 14, f"{f['sender_market']}  ·  Media Buying & Placement")
    c.setLineWidth(0.7)
    c.line(m, y - 24, PAGE_W - m, y - 24)
    y -= 52

    c.setFont("Times-Roman", 11)
    c.drawString(m, y, fmt_date(f["doc_date"], "long"))
    y -= 30
    c.drawString(m, y, "To the Traffic Department:")
    y -= 24

    spots = sum(l["quantity"] for l in f["line_items"])
    body = [
        f"Please accept this letter as our authorization number {f['doc_id']} to place",
        f"broadcast advertising on behalf of our client {f['advertiser']}. The schedule",
        f"is to begin on {fmt_date(f['flight_start'], 'long')} and to conclude on",
        f"{fmt_date(f['flight_end'], 'long')}, comprising {spots} spots in total across the",
        "dayparts we discussed by telephone last week.",
        "",
        f"We have agreed a total of {fmt_money(f['gross_total'], 'us')} gross for this flight."
        + (f" Our commission of" if f["net_total"] is not None else ""),
    ]
    if f["net_total"] is not None:
        body += [
            f"fifteen percent is deducted from that figure, leaving a net payable to the",
            f"station of {fmt_money(f['net_total'], 'us')}, which we will remit on the usual terms.",
        ]
    body += [
        "",
        "Kindly confirm receipt and advise if any of the requested inventory is",
        "unavailable, in which case we will discuss substitutions.",
    ]
    for ln in body:
        c.drawString(m, y, ln)
        y -= 17
    y -= 20
    c.drawString(m, y, "Yours faithfully,")
    y -= 40
    c.setFont("Times-Italic", 12)
    c.drawString(m, y, "M. Reed")
    c.setFont("Times-Roman", 10)
    c.drawString(m, y - 15, "Margaret Reed, Senior Buyer")
    return _finish(buf, c)


def render_hollis_offtopic(f):
    """Out-of-scope: a letter from a plausible sender that is not an order."""
    buf, c = _canvas()
    m, y = 1.1 * inch, PAGE_H - 1.1 * inch
    c.setFont("Times-Bold", 16)
    c.drawCentredString(PAGE_W / 2, y, f["sender_name"])
    c.setFont("Times-Italic", 9.5)
    c.drawCentredString(PAGE_W / 2, y - 14, f"{f['sender_market']}  ·  Media Buying & Placement")
    c.line(m, y - 24, PAGE_W - m, y - 24)
    y -= 52
    c.setFont("Times-Roman", 11)
    c.drawString(m, y, fmt_date(f["doc_date"], "long"))
    y -= 30
    for ln in [
        "To our station partners:",
        "",
        "Please note that our offices will relocate at the end of this quarter. From",
        "the first of next month, remittances should be directed to our new address at",
        "1180 Peachtree Court, Suite 400. Our banking details are unchanged.",
        "",
        "Invoices already in flight will be honoured as normal. We processed some",
        "$4,318,000.00 in placements last year and expect no interruption to service",
        "during the move. Our main line remains 404-555-0182.",
        "",
        "We thank you for your continued partnership.",
    ]:
        c.drawString(m, y, ln)
        y -= 17
    y -= 20
    c.drawString(m, y, "Yours faithfully,")
    y -= 40
    c.setFont("Times-Italic", 12)
    c.drawString(m, y, "M. Reed")
    c.setFont("Times-Roman", 10)
    c.drawString(m, y - 15, "Margaret Reed, Senior Buyer")
    return _finish(buf, c)


def render_nab(f):
    """Boxed two-column agency form, all-caps labels, values in ruled cells."""
    buf, c = _canvas()
    m = 0.85 * inch
    y = PAGE_H - 0.85 * inch
    c.setLineWidth(1.4)
    c.rect(m - 8, 1.0 * inch, PAGE_W - 2 * m + 16, y - 1.0 * inch + 22, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(PAGE_W / 2, y, "STATION ADVERTISING CONTRACT")
    c.setFont("Helvetica", 9)
    c.drawCentredString(PAGE_W / 2, y - 13, f"{f['sender_name']}  —  {f['sender_market']}")
    y -= 34
    c.setLineWidth(0.8)
    c.line(m - 8, y, PAGE_W - m + 8, y)
    y -= 6

    def cell(label, value, col, width):
        x = m + col * width
        c.setFont("Helvetica", 6.5)
        c.drawString(x + 3, y - 9, label)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 3, y - 23, value)
        c.setLineWidth(0.5)
        c.rect(x, y - 28, width, 28, stroke=1, fill=0)

    w = (PAGE_W - 2 * m) / 2
    pairs = [
        ("CONTRACT NO.", f["doc_id"], "CONTRACT DATE", fmt_date(f["doc_date"], "us_dash")),
        ("ADVERTISER", f["advertiser"], "AGENCY OF RECORD", f["agency"] or "NONE — DIRECT"),
        ("SCHEDULE FROM", fmt_date(f["flight_start"], "us_dash"),
         "SCHEDULE THROUGH", fmt_date(f["flight_end"], "us_dash")),
    ]
    for la, va, lb, vb in pairs:
        cell(la, va, 0, w)
        cell(lb, vb, 1, w)
        y -= 28
    y -= 20

    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y, "SCHEDULE OF ANNOUNCEMENTS")
    y -= 6
    c.line(m, y, PAGE_W - m, y)
    y -= 14
    c.setFont("Helvetica", 8.5)
    for li in f["line_items"]:
        c.drawString(m + 4, y, li["description"])
        c.drawString(m + 3.5 * inch, y, f"NO. OF ANNCTS {li['quantity']}")
        c.drawRightString(PAGE_W - m - 4, y, f"@ {fmt_money(li['unit_rate'], 'us')} = "
                                             f"{fmt_money(li['amount'], 'us')}")
        y -= 15
    y -= 8
    c.setLineWidth(1.0)
    c.line(m + 3.2 * inch, y, PAGE_W - m, y)
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(m + 3.2 * inch, y, "TOTAL GROSS")
    c.drawRightString(PAGE_W - m - 4, y, fmt_money(f["gross_total"], "us"))
    if f["net_total"] is not None:
        y -= 15
        c.setFont("Helvetica", 9)
        c.drawString(m + 3.2 * inch, y, "NET (LESS 15% AGENCY COMMISSION)")
        c.drawRightString(PAGE_W - m - 4, y, fmt_money(f["net_total"], "us"))
    y -= 44
    c.setFont("Helvetica", 8)
    c.drawString(m, y, "ACCEPTED FOR STATION ____________________________")
    c.drawString(m + 3.4 * inch, y, "DATE ______________")
    return _finish(buf, c)


def render_quickspot(f):
    """Minimal receipt: narrow column, almost no labels, cramped."""
    buf, c = _canvas()
    x, y = 1.6 * inch, PAGE_H - 1.3 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "QUICKSPOT DIGITAL")
    c.setFont("Helvetica", 8)
    c.drawString(x, y - 11, f["sender_market"])
    y -= 34
    c.setFont("Helvetica", 9)
    c.drawString(x, y, f["doc_id"])
    c.drawString(x, y - 12, fmt_date(f["doc_date"], "us_slash"))
    y -= 32
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x, y, f["advertiser"])
    y -= 12
    c.setFont("Helvetica", 8.5)
    if f["agency"]:
        c.drawString(x, y, f"c/o {f['agency']}")
        y -= 12
    c.drawString(x, y, f"{fmt_date(f['flight_start'], 'us_slash')}"
                       f"-{fmt_date(f['flight_end'], 'us_slash')}")
    y -= 24
    c.setLineWidth(0.4)
    c.line(x, y, x + 2.9 * inch, y)
    y -= 14
    for li in f["line_items"]:
        c.setFont("Helvetica", 8)
        c.drawString(x, y, f"{li['quantity']}x {li['description'][:24]}")
        c.drawRightString(x + 2.9 * inch, y, fmt_money(li["amount"], "us"))
        y -= 12
    c.line(x, y, x + 2.9 * inch, y)
    y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, "TOTAL")
    c.drawRightString(x + 2.9 * inch, y, fmt_money(f["gross_total"], "us"))
    if f["net_total"] is not None:
        y -= 14
        c.setFont("Helvetica", 8)
        c.drawString(x, y, "net -15%")
        c.drawRightString(x + 2.9 * inch, y, fmt_money(f["net_total"], "us"))
    y -= 26
    c.setFont("Helvetica", 7)
    c.drawString(x, y, "paid by ach · no signature required")
    return _finish(buf, c)


def render_europa(f):
    """International rep firm: EUR, 1.234,56 numbers, bilingual labels."""
    buf, c = _canvas()
    m, y = 0.95 * inch, PAGE_H - 0.95 * inch
    c.setFont("Helvetica-Bold", 15)
    c.drawString(m, y, f["sender_name"])
    c.setFont("Helvetica", 8.5)
    c.drawString(m, y - 13, f"{f['sender_market']}  ·  USt-IdNr. DE 812 447 903")
    c.drawRightString(PAGE_W - m, y, "RECHNUNG / INVOICE")
    c.drawRightString(PAGE_W - m, y - 13, f"Nr. {f['doc_id']}")
    c.drawRightString(PAGE_W - m, y - 25, f"Datum {fmt_date(f['doc_date'], 'eu_dot')}")
    y -= 44
    c.setLineWidth(0.9)
    c.line(m, y, PAGE_W - m, y)
    y -= 22
    c.setFont("Helvetica", 9.5)
    c.drawString(m, y, f"Kunde / Client:   {f['advertiser']}")
    y -= 14
    if f["agency"]:
        c.drawString(m, y, f"Agentur / Agency:   {f['agency']}")
        y -= 14
    c.drawString(m, y, f"Zeitraum / Period:   {fmt_date(f['flight_start'], 'eu_dot')} – "
                       f"{fmt_date(f['flight_end'], 'eu_dot')}")
    y -= 26

    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y, "POSITION / DESCRIPTION")
    c.drawString(m + 3.5 * inch, y, "ANZ.")
    c.drawRightString(m + 4.9 * inch, y, "EINZELPREIS")
    c.drawRightString(PAGE_W - m, y, "BETRAG")
    y -= 5
    c.setLineWidth(0.5)
    c.line(m, y, PAGE_W - m, y)
    y -= 14
    c.setFont("Helvetica", 9)
    for li in f["line_items"]:
        c.drawString(m, y, li["description"])
        c.drawString(m + 3.5 * inch, y, str(li["quantity"]))
        c.drawRightString(m + 4.9 * inch, y, fmt_money(li["unit_rate"], "eu"))
        c.drawRightString(PAGE_W - m, y, fmt_money(li["amount"], "eu"))
        y -= 14
    y -= 6
    c.line(m + 3.8 * inch, y, PAGE_W - m, y)
    y -= 18
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(m + 3.8 * inch, y, "Rechnungsbetrag / Total")
    c.drawRightString(PAGE_W - m, y, fmt_money(f["gross_total"], "eu"))
    if f["net_total"] is not None:
        y -= 15
        c.setFont("Helvetica", 9)
        c.drawString(m + 3.8 * inch, y, "Netto abzgl. 15% Provision")
        c.drawRightString(PAGE_W - m, y, fmt_money(f["net_total"], "eu"))
    y -= 30
    c.setFont("Helvetica", 7.5)
    c.drawString(m, y, "Zahlbar innerhalb 30 Tagen ohne Abzug. IBAN DE44 5001 0517 5407 3249 31")
    return _finish(buf, c)


RENDERERS = {
    "meridian": render_meridian, "kvue": render_kvue, "natrep": render_natrep,
    "hollis": render_hollis, "nab": render_nab, "quickspot": render_quickspot,
    "europa": render_europa,
}


# --------------------------------------------------------------------------
# The plan: which document is which. Reviewed as a set, not one at a time.
# --------------------------------------------------------------------------

DOC_PLAN = [
    # id      family        container    condition  sev  trap                   expected
    ("D01", "meridian",  "pdf",       "digital", 0, None,                 "green_ok"),
    ("D02", "meridian",  "pdf_image", "scan",    2, None,                 "green_ok"),
    ("D03", "kvue",      "jpg",       "scan",    2, None,                 "green_ok"),
    ("D04", "kvue",      "jpg",       "photo",   2, None,                 "green_ok"),
    ("D05", "natrep",    "pdf",       "digital", 0, "distractors",        "green_ok"),
    ("D06", "natrep",    "png",       "scan",    3, "distractors",        "green_ok"),
    ("D07", "hollis",    "pdf",       "digital", 0, "prose",              "green_ok"),
    ("D08", "hollis",    "jpg",       "photo",   2, "prose",              "green_ok"),
    ("D09", "nab",       "pdf_image", "scan",    1, None,                 "green_ok"),
    ("D10", "nab",       "jpg",       "fax",     2, None,                 "green_ok"),
    ("D11", "quickspot", "png",       "photo",   3, None,                 "green_ok"),
    ("D12", "quickspot", "pdf",       "digital", 0, None,                 "green_ok"),
    ("D13", "europa",    "pdf",       "digital", 0, "eu_numbers",         "green_ok"),
    ("D14", "europa",    "jpg",       "scan",    2, "eu_numbers",         "green_ok"),
    # Severity sweep: identical content, three degradation levels. If accuracy
    # does not fall as severity rises, the corpus is too easy to trust.
    ("D15", "meridian",  "jpg",       "scan",    1, "sweep",              "green_ok"),
    ("D16", "meridian",  "jpg",       "scan",    3, "sweep",              "green_ok"),
    # Planted traps: documents expected to be caught by the gate, not extracted.
    ("D17", "kvue",      "jpg",       "fax",     3, "illegible",          "review_expected"),
    ("D18", "quickspot", "jpg",       "photo",   2, "cropped_total",      "review_expected"),
    ("D19", "natrep",    "pdf",       "digital", 0, "inconsistent_total", "review_expected"),
    ("D20", "nab",       "jpg",       "scan",    2, "rotated_180",        "review_expected"),
    ("D21", "hollis",    "pdf",       "digital", 0, "out_of_scope",       "review_expected"),
]

TRAP_NOTES = {
    "distractors": "Identifiers formatted like money (client code, estimate no.) plus a "
                   "prior-balance figure that is not the contract total.",
    "prose": "No table anywhere; every fact is inside a sentence.",
    "eu_numbers": "EUR with 1.234,56 separators — a decimal misparse changes the total by 100x.",
    "sweep": "Severity sweep control: same content as its sibling at another degradation level.",
    "illegible": "Fax at worst severity: expected to be unreadable rather than misread.",
    "cropped_total": "Photographed with the bottom of the page out of frame, so the total "
                     "is genuinely absent. Correct behaviour is to report it missing.",
    "inconsistent_total": "Line items do not sum to the printed contract total; a prose note "
                          "mentions an adjustment. A human must decide which figure to book.",
    "rotated_180": "Scanned upside down. Modern readers often cope; if it passes, that is a "
                   "result worth reporting, not a failure of the test.",
    "out_of_scope": "An office-relocation letter from a real sender that mentions a large "
                    "dollar figure but is not an order at all.",
}


# --------------------------------------------------------------------------
# Containers and output
# --------------------------------------------------------------------------

def pdf_to_image(pdf_bytes, dpi=200):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def image_to_pdf_bytes(img):
    """Wrap a raster image in a PDF -- an image-only PDF with no text layer.

    Built with PyMuPDF rather than Pillow so the metadata can be cleared and the
    output stays byte-identical between runs.
    """
    # JPEG rather than PNG inside the PDF: that is what a scanner actually
    # produces, and it keeps the files a sane size for a repository.
    jpg = io.BytesIO()
    img.convert("RGB").save(jpg, "JPEG", quality=80)
    doc = fitz.open()
    width, height = img.width * 72 / 150, img.height * 72 / 150
    page = doc.new_page(width=width, height=height)
    page.insert_image(fitz.Rect(0, 0, width, height), stream=jpg.getvalue())
    doc.set_metadata({})
    out = doc.tobytes()
    doc.close()
    return out


def build():
    import random

    for d in (INPUTS, TRUTH, WORK):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    from degrade import apply as degrade_apply, crop_bottom, rotate_180

    manifest = []
    sweep_facts = {}

    for idx, (doc_id, family, container, condition, sev, trap, expected) in enumerate(DOC_PLAN):
        rng = random.Random(SEED + idx * 977)

        # Sweep siblings share content so only the degradation differs.
        if trap == "sweep":
            if "meridian_sweep" not in sweep_facts:
                sweep_facts["meridian_sweep"] = make_facts(random.Random(SEED + 1), family)
            facts = sweep_facts["meridian_sweep"]
        else:
            facts = make_facts(rng, family, trap=trap)

        renderer = render_hollis_offtopic if trap == "out_of_scope" else RENDERERS[family]
        pdf_bytes = renderer(facts)

        # Build the delivered artefact.
        if container == "pdf" and condition == "digital":
            path = INPUTS / f"{doc_id}.pdf"
            path.write_bytes(pdf_bytes)
        else:
            img = pdf_to_image(pdf_bytes)
            if condition != "digital":
                img = degrade_apply(condition, img, sev, SEED + idx, str(WORK / f"{doc_id}.tmp.jpg"))
            if trap == "rotated_180":
                img = rotate_180(img)
            if trap == "cropped_total":
                # The receipt layout puts everything in the top third of the
                # page, so the crop has to be aggressive to actually take the
                # total out of frame. Keeps the header, parties, dates and
                # line items; loses TOTAL and the net line below it.
                img = crop_bottom(img, 0.70)
            if container == "pdf_image":
                path = INPUTS / f"{doc_id}.pdf"
                path.write_bytes(image_to_pdf_bytes(img))
            else:
                path = INPUTS / f"{doc_id}.{container}"
                img.convert("RGB").save(path, quality=88) if container == "jpg" else img.save(path)

        # Ground truth. not_visible marks fields that the delivered artefact
        # genuinely does not show, where reporting them absent is correct.
        not_visible = []
        if trap == "cropped_total":
            not_visible = ["gross_total", "net_total"]

        if trap == "out_of_scope":
            truth = {f: None for f in
                     ["sender_name", "doc_id", "doc_date", "advertiser", "flight_start",
                      "flight_end", "gross_total", "net_total", "agency", "currency",
                      "line_item_count"]}
            truth["doc_type"] = "other"
            truth["sender_name"] = facts["sender_name"]
        else:
            truth = {
                "doc_type": facts["doc_type"],
                "sender_name": facts["sender_name"],
                "doc_id": facts["doc_id"],
                "doc_date": facts["doc_date"].isoformat(),
                "advertiser": facts["advertiser"],
                "agency": facts["agency"],
                "flight_start": facts["flight_start"].isoformat(),
                "flight_end": facts["flight_end"].isoformat(),
                "gross_total": str(facts["gross_total"]),
                "net_total": str(facts["net_total"]) if facts["net_total"] is not None else None,
                "currency": facts["currency"],
                "line_item_count": len(facts["line_items"]),
            }

        (TRUTH / f"{doc_id}.json").write_text(json.dumps({
            "doc_id": doc_id,
            "fields": truth,
            "not_visible_fields": not_visible,
            "line_sum": str(facts["line_sum"]),
        }, indent=2))

        manifest.append({
            "doc_id": doc_id,
            "file": path.name,
            "sender_family": family,
            "sender_name": facts["sender_name"],
            "layout": FAMILIES[family]["style"],
            "container": container,
            "condition": condition,
            "severity": sev,
            "trap": trap,
            "trap_note": TRAP_NOTES.get(trap),
            "expected_outcome": expected,
            "bytes": path.stat().st_size,
        })
        print(f"  {doc_id}  {family:<10} {container:<9} {condition:<8} "
              f"sev{sev}  {trap or '-'}")

    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2))
    write_manifest_md(manifest)
    shutil.rmtree(WORK, ignore_errors=True)

    n = len(manifest)
    non_clean = sum(1 for m in manifest if m["condition"] != "digital")
    traps = sum(1 for m in manifest if m["expected_outcome"] == "review_expected")
    print(f"\n{n} documents · {len(FAMILIES)} senders · "
          f"{non_clean} non-digital ({non_clean / n:.0%}) · {traps} planted for review")


def write_manifest_md(manifest):
    lines = [
        "# Corpus",
        "",
        f"{len(manifest)} documents from {len(FAMILIES)} senders. "
        f"{sum(1 for m in manifest if m['condition'] != 'digital')} of them are scans, "
        "photographs or faxes rather than clean digital files.",
        "",
        "Generated by `generate.py` with a fixed seed, so the corpus is reproducible. "
        "Ground truth in `ground_truth/` is written by the generator before any "
        "extraction happens.",
        "",
        "## Senders",
        "",
        "| Family | Name | Layout | Money / dates |",
        "|---|---|---|---|",
    ]
    for key, fam in FAMILIES.items():
        lines.append(f"| `{key}` | {fam['name']} | {fam['style']} | "
                     f"{fam['currency']}, {fam['number_style']}, {fam['date_style']} |")
    lines += [
        "",
        "## Documents",
        "",
        "| ID | Sender | Delivered as | Condition | Sev | Expected | Trap |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in manifest:
        lines.append(
            f"| {m['doc_id']} | `{m['sender_family']}` | {m['file']} | {m['condition']} | "
            f"{m['severity'] or '–'} | {m['expected_outcome']} | {m['trap'] or '–'} |")
    lines += ["", "## Traps", ""]
    for trap, note in TRAP_NOTES.items():
        ids = [m["doc_id"] for m in manifest if m["trap"] == trap]
        lines.append(f"- **{trap}** ({', '.join(ids)}) — {note}")
    (HERE / "MANIFEST.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    build()
