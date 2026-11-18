#!/usr/bin/env python3
"""Build the deterministic synthetic PDF paired with the local HTML fixture."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas


OUTPUT = Path(__file__).with_name("sample-site") / "synthetic-accessibility-review.pdf"


def build_fixture(output: Path = OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(str(output), pagesize=LETTER, invariant=1, pageCompression=0)
    document.setAuthor("FusionTech Strategies")
    document.setCreator("WCAG 2.2 Site and PDF Scanner synthetic fixture builder")
    document.setSubject("Synthetic document for accessibility scanner evaluation")
    document.setTitle("Community services accessibility review")

    width, height = LETTER
    navy = HexColor("#17324D")
    blue = HexColor("#146C94")
    gray = HexColor("#4A5560")

    document.setFillColor(navy)
    document.rect(0, height - 108, width, 108, fill=1, stroke=0)
    document.setFillColor(HexColor("#FFFFFF"))
    document.setFont("Helvetica-Bold", 23)
    document.drawString(54, height - 66, "Community services accessibility review")
    document.setFont("Helvetica", 10)
    document.drawString(54, height - 88, "Synthetic training fixture - no customer or live-scan data")

    document.setFillColor(navy)
    document.setFont("Helvetica-Bold", 15)
    document.drawString(54, height - 150, "Purpose")
    document.setFillColor(gray)
    document.setFont("Helvetica", 10.5)
    text = document.beginText(54, height - 171)
    text.setLeading(15)
    text.textLines(
        "This one-page document is paired with the local HTML training page.\n"
        "It gives the PDF scanner a stable, inspectable input and gives reviewers\n"
        "a concrete artifact for comparing automated evidence with manual review."
    )
    document.drawText(text)

    document.setFillColor(navy)
    document.setFont("Helvetica-Bold", 15)
    document.drawString(54, height - 250, "Manual review queue")
    document.setFillColor(gray)
    document.setFont("Helvetica", 10.5)
    items = (
        "Confirm the document reading order with a keyboard and screen reader.",
        "Verify that headings and lists are represented in the structure tree.",
        "Check color, zoom, reflow, document language, title, and link purpose.",
        "Record limitations; do not treat an automated pass as conformance.",
    )
    y = height - 274
    for item in items:
        document.setFillColor(blue)
        document.circle(61, y + 3, 2.2, fill=1, stroke=0)
        document.setFillColor(gray)
        document.drawString(72, y, item)
        y -= 27

    document.setStrokeColor(HexColor("#D5DEE6"))
    document.line(54, 122, width - 54, 122)
    document.setFillColor(gray)
    document.setFont("Helvetica", 9)
    document.drawString(54, 96, "Expected boundary: automated inspection supplies evidence, not certification.")
    document.drawRightString(width - 54, 72, "Synthetic fixture | Page 1 of 1")
    document.showPage()
    document.save()
    return output


if __name__ == "__main__":
    print(build_fixture())
