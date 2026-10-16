#!/usr/bin/env python3
"""Render the small fictional PDF corpus used by the Sourcebound walkthrough."""

from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "evals" / "demo_corpus" / "source"
OUTPUT_DIR = ROOT / "evals" / "demo_corpus" / "generated"

OUTPUTS = {
    "incident-v1.json": "incident-v1",
    "incident-v2.json": "incident-v2",
    "retention.json": "retention",
    "vendor-guide.json": "vendor",
    "restricted-appendix.json": "restricted",
}


def render(source_path: Path, output_path: Path) -> None:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SourceboundTitle", parent=styles["Title"], alignment=TA_CENTER,
        textColor=colors.HexColor("#18324b"), fontSize=18, leading=22, spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "SourceboundSubtitle", parent=styles["Normal"], alignment=TA_CENTER,
        textColor=colors.HexColor("#526477"), fontSize=9, leading=12, spaceAfter=16,
    )
    heading_style = ParagraphStyle(
        "SourceboundHeading", parent=styles["Heading2"], textColor=colors.HexColor("#18324b"),
        fontSize=11, leading=14, spaceBefore=8, spaceAfter=3,
    )
    body_style = ParagraphStyle(
        "SourceboundBody", parent=styles["BodyText"], fontSize=9.5, leading=13.5,
        textColor=colors.HexColor("#263442"), spaceAfter=4,
    )
    story = [
        Paragraph(escape(source["title"]), title_style),
        Paragraph(escape(source["subtitle"]), subtitle_style),
    ]
    for heading, body in source["sections"]:
        story.extend((Paragraph(escape(heading), heading_style), Paragraph(escape(body), body_style)))
    story.extend((Spacer(1, 0.15 * inch), Paragraph(
        "Synthetic demo content. Not a real policy or operational instruction.",
        ParagraphStyle("SourceboundFooter", parent=styles["Italic"], fontSize=8, textColor=colors.grey),
    )))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path), pagesize=letter, rightMargin=0.72 * inch, leftMargin=0.72 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=source["title"], author="Sourcebound fictional demo corpus",
    )
    document.build(story)


def main() -> None:
    for source_name, output_folder in OUTPUTS.items():
        if output_folder.startswith("incident-"):
            filename = "Northstar Incident Runbook.pdf"
        elif output_folder == "retention":
            filename = "Northstar Data Retention Standard.pdf"
        elif output_folder == "restricted":
            filename = "Restricted Northstar Operations Appendix.pdf"
        else:
            filename = "Northstar Vendor Operations Guide.pdf"
        output_path = OUTPUT_DIR / output_folder / filename
        render(SOURCE_DIR / source_name, output_path)
        print(output_path.relative_to(ROOT))


if __name__ == "__main__":
    main()
