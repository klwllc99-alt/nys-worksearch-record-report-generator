from __future__ import annotations

import datetime as dt
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from parser import WorkSearchEntry

EMPLOYER_ROWS_PER_FORM = 5
OTHER_ACTIVITY_ROWS_PER_FORM = 5


@dataclass(slots=True)
class ClaimantInfo:
    first_name: str
    last_name: str
    nys_id: str
    ssn_last4: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def build_blank_form_pdf() -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    pdf.setTitle("WS5 Blank Placeholder")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(40, height - 60, "Work Search Record (WS5) Placeholder Form")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(40, height - 84, "Replace this page with the official blank WS5 PDF when it becomes available.")
    pdf.setStrokeColor(colors.HexColor("#94A3B8"))
    pdf.rect(32, 40, width - 64, height - 120, stroke=1, fill=0)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _safe_text(value: str, max_length: int = 80) -> str:
    return (value or "").strip()[:max_length]


def _split_entries(entries: list[WorkSearchEntry], generate_all: bool) -> list[tuple[list[WorkSearchEntry], list[WorkSearchEntry]]]:
    employer_contacts = [entry for entry in entries if entry.record_type != "other_activity"]
    other_activities = [entry for entry in entries if entry.record_type == "other_activity"]

    if not generate_all:
        return [(employer_contacts[:EMPLOYER_ROWS_PER_FORM], other_activities[:OTHER_ACTIVITY_ROWS_PER_FORM])]

    pages: list[tuple[list[WorkSearchEntry], list[WorkSearchEntry]]] = []
    while employer_contacts or other_activities:
        pages.append((
            employer_contacts[:EMPLOYER_ROWS_PER_FORM],
            other_activities[:OTHER_ACTIVITY_ROWS_PER_FORM],
        ))
        employer_contacts = employer_contacts[EMPLOYER_ROWS_PER_FORM:]
        other_activities = other_activities[OTHER_ACTIVITY_ROWS_PER_FORM:]

    return pages or [([], [])]


def _wrap_text_to_width(pdf: canvas.Canvas, text: str, max_width: float, font_name: str, font_size: float) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if pdf.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_field_value(pdf: canvas.Canvas, rect: list[float], value: str) -> None:
    text = (value or "").strip()
    if not text:
        return

    x1, y1, x2, y2 = rect
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    pdf.setFillColor(colors.black)

    if width <= 22 or len(text) <= 2:
        font_size = min(10.0, max(7.0, height * 0.72))
        pdf.setFont("Helvetica", font_size)
        text_width = pdf.stringWidth(text, "Helvetica", font_size)
        x = x1 + max(0.5, (width - text_width) / 2)
        y = y1 + max(1.0, (height - font_size) / 2)
        pdf.drawString(x, y, text)
        return

    font_size = min(9.0, max(6.5, height * 0.64))
    pdf.setFont("Helvetica", font_size)
    lines = _wrap_text_to_width(pdf, text, width - 4, "Helvetica", font_size)
    max_lines = max(1, min(3, int(height // (font_size + 1))))
    lines = lines[:max_lines]
    start_y = y2 - font_size - 1
    for index, line in enumerate(lines):
        pdf.drawString(x1 + 2, start_y - (index * (font_size + 1)), line)


def _fill_date_fields(field_values: dict[str, str], prefix: str, value: dt.date) -> None:
    field_values[f"{prefix}MM"] = value.strftime("%m")
    field_values[f"{prefix}DD"] = value.strftime("%d")
    field_values[f"{prefix}YY"] = value.strftime("%y")


def _build_ws5_field_values(
    claimant: ClaimantInfo,
    week_end: dt.date,
    employer_rows: list[WorkSearchEntry],
    other_rows: list[WorkSearchEntry],
) -> dict[str, str]:
    field_values: dict[str, str] = {
        "Contact_FirstName": _safe_text(claimant.first_name, 32),
        "Contact_LastName": _safe_text(claimant.last_name, 32),
    }

    id_chars = [char for char in claimant.nys_id.upper() if char.isalnum()][:9]
    for index in range(1, 10):
        field_values[f"Contact_NYIDNumber{index}"] = id_chars[index - 1] if index <= len(id_chars) else ""

    ssn_digits = [char for char in claimant.ssn_last4 if char.isdigit()][:4]
    for offset, field_suffix in enumerate(range(6, 10), start=0):
        field_values[f"Contact_SSN{field_suffix}"] = ssn_digits[offset] if offset < len(ssn_digits) else ""

    _fill_date_fields(field_values, "CurrentDate_Current", week_end)

    for index in range(1, EMPLOYER_ROWS_PER_FORM + 1):
        if index <= len(employer_rows):
            entry = employer_rows[index - 1]
            _fill_date_fields(field_values, f"Grievance_DateList_Row{index}Date", entry.date)
            field_values[f"Employment_JobSearchRecord_Row{index}PositionApplied"] = _safe_text(entry.position_or_activity, 60)
            field_values[f"Employment_JobSearchRecord_Row{index}BusinessName"] = _safe_text(entry.business_name, 60)
            field_values[f"Employment_JobSearchRecord_Row{index}PersonContactedNameTitle"] = _safe_text(entry.person_contacted, 60)
            field_values[f"Employment_JobSearchRecord_Row{index}MethodOfContact"] = _safe_text(entry.method_of_contact, 40)
            field_values[f"Employment_JobSearchRecord_Row{index}ContactInformation"] = _safe_text(entry.contact_information, 90)
            field_values[f"Employment_JobSearchRecord_Row{index}Result"] = _safe_text(entry.result, 50)

    for index in range(1, OTHER_ACTIVITY_ROWS_PER_FORM + 1):
        if index <= len(other_rows):
            entry = other_rows[index - 1]
            row_number = EMPLOYER_ROWS_PER_FORM + index
            _fill_date_fields(field_values, f"Grievance_DateList_Row{row_number}Date", entry.date)
            field_values[f"Grievance_ActionList_Row{index}Action"] = _safe_text(entry.position_or_activity, 90)

    return field_values


def _fill_official_ws5_pdf(
    claimant: ClaimantInfo,
    week_end: dt.date,
    employer_rows: list[WorkSearchEntry],
    other_rows: list[WorkSearchEntry],
    blank_form_path: Path,
) -> bytes:
    reader = PdfReader(str(blank_form_path))
    field_values = _build_ws5_field_values(claimant, week_end, employer_rows, other_rows)

    overlay_buffer = BytesIO()
    overlay_canvas = None
    for page in reader.pages:
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        if overlay_canvas is None:
            overlay_canvas = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
        else:
            overlay_canvas.setPageSize((page_width, page_height))

        for annot_ref in page.get("/Annots") or []:
            annot = annot_ref.get_object()
            field_name = annot.get("/T")
            if not field_name or field_name not in field_values:
                continue
            rect = [float(value) for value in annot.get("/Rect")]
            _draw_field_value(overlay_canvas, rect, field_values[field_name])
        overlay_canvas.showPage()

    if overlay_canvas is None:
        return build_blank_form_pdf()

    overlay_canvas.save()
    overlay_reader = PdfReader(BytesIO(overlay_buffer.getvalue()))

    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        page.merge_page(overlay_reader.pages[index])
        writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _draw_fallback_page(
    pdf: canvas.Canvas,
    claimant: ClaimantInfo,
    week_end: dt.date,
    employer_rows: list[WorkSearchEntry],
    other_rows: list[WorkSearchEntry],
) -> None:
    width, height = letter
    margin_x = 36
    pdf.setTitle(f"WS5 Record {week_end.isoformat()}")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(margin_x, height - 44, "NYS Work Search Record")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(margin_x, height - 66, f"Claimant: {claimant.full_name}")
    pdf.drawString(300, height - 66, f"Week ending: {week_end.strftime('%m/%d/%Y')}")
    pdf.drawString(margin_x, height - 82, f"NYS ID: {claimant.nys_id}")
    pdf.drawString(300, height - 82, f"SSN last 4: {claimant.ssn_last4}")

    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin_x, height - 114, "Business / Employer Contacts")
    pdf.setFont("Helvetica", 9)
    y = height - 132
    for entry in employer_rows:
        pdf.drawString(margin_x, y, f"{entry.date:%m/%d/%y} | {entry.position_or_activity[:26]} | {entry.business_name[:28]} | {entry.result[:20]}")
        y -= 16

    y -= 12
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(margin_x, y, "Other Work Search Activities")
    pdf.setFont("Helvetica", 9)
    y -= 18
    for entry in other_rows:
        pdf.drawString(margin_x, y, f"{entry.date:%m/%d/%y} | {entry.position_or_activity[:80]}")
        y -= 16

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.setFillColor(colors.HexColor("#475569"))
    pdf.drawString(margin_x, 40, "Fallback layout in use because an official fillable WS5 form was not detected.")


def _render_page_pdf(
    claimant: ClaimantInfo,
    week_end: dt.date,
    employer_rows: list[WorkSearchEntry],
    other_rows: list[WorkSearchEntry],
    blank_form_path: Path | None,
) -> bytes:
    if blank_form_path and blank_form_path.exists():
        try:
            if (PdfReader(str(blank_form_path)).get_fields() or {}):
                return _fill_official_ws5_pdf(claimant, week_end, employer_rows, other_rows, blank_form_path)
        except Exception:
            pass

    fallback_buffer = BytesIO()
    pdf = canvas.Canvas(fallback_buffer, pagesize=letter)
    _draw_fallback_page(pdf, claimant, week_end, employer_rows, other_rows)
    pdf.showPage()
    pdf.save()
    return fallback_buffer.getvalue()


def _build_week_pdf(
    claimant: ClaimantInfo,
    week_end: dt.date,
    entries: list[WorkSearchEntry],
    generate_all: bool,
    blank_form_path: Path | None,
) -> bytes:
    writer = PdfWriter()
    form_pages = _split_entries(entries, generate_all=generate_all)

    for employer_rows, other_rows in form_pages:
        page_pdf = _render_page_pdf(
            claimant=claimant,
            week_end=week_end,
            employer_rows=employer_rows,
            other_rows=other_rows,
            blank_form_path=blank_form_path,
        )
        reader = PdfReader(BytesIO(page_pdf))
        for page in reader.pages:
            writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _build_download_name(week_ends: list[dt.date], extension: str) -> str:
    sorted_dates = sorted(week_ends)
    if len(sorted_dates) == 1:
        return f"ws5_week_ending_{sorted_dates[0].isoformat()}.{extension}"
    return f"ws5_weeks_ending_{sorted_dates[0].isoformat()}_to_{sorted_dates[-1].isoformat()}.{extension}"


def create_output_document(
    grouped_records: dict[dt.date, list[WorkSearchEntry]],
    claimant: ClaimantInfo,
    output_mode: str,
    generate_all: bool,
    blank_form_path: Path | None = None,
) -> tuple[bytes, str, str]:
    weekly_pdfs: list[tuple[dt.date, bytes]] = []
    for week_end, entries in grouped_records.items():
        pdf_bytes = _build_week_pdf(
            claimant=claimant,
            week_end=week_end,
            entries=entries,
            generate_all=generate_all,
            blank_form_path=blank_form_path,
        )
        weekly_pdfs.append((week_end, pdf_bytes))

    week_endings = [week_end for week_end, _ in weekly_pdfs]

    if output_mode == "per_week":
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for week_end, pdf_bytes in weekly_pdfs:
                archive.writestr(f"ws5_week_ending_{week_end.isoformat()}.pdf", pdf_bytes)
        return archive_buffer.getvalue(), _build_download_name(week_endings, "zip"), "application/zip"

    writer = PdfWriter()
    for _, pdf_bytes in weekly_pdfs:
        reader = PdfReader(BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

    merged_buffer = BytesIO()
    writer.write(merged_buffer)
    return merged_buffer.getvalue(), _build_download_name(week_endings, "pdf"), "application/pdf"
