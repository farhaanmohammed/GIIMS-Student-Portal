"""
CSV and server-side PDF export utilities for GIIMS Student Portal.

Security & data integrity rules:
- Untrusted text exported to CSV is neutralized against spreadsheet formula
  injection (cells starting with ``=``, ``+``, ``-``, ``@``, ``\\t``, or ``\\r``
  are prefixed with ``'``). Numeric ``Decimal``, ``int``, and ``float`` values
  are formatted directly without altering negative numbers.
- Server-side PDF generation produces standards-compliant ``%PDF-1.4`` binary
  documents using built-in Type1 Helvetica/Helvetica-Bold fonts with zero
  external native dependencies.
"""

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.http import HttpResponse

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str, extension: str) -> str:
    """Return a clean attachment filename with the given extension."""
    cleaned = SAFE_FILENAME_RE.sub("_", (name or "export").strip()).strip("_.")
    if not cleaned:
        cleaned = "export"
    ext = extension.lstrip(".")
    if not cleaned.lower().endswith(f".{ext.lower()}"):
        cleaned = f"{cleaned}.{ext}"
    return cleaned


def sanitize_csv_cell(value: Any) -> str:
    """
    Format a value for CSV output and neutralize spreadsheet formula injection.

    Numeric types (``Decimal``, ``int``, ``float``) are converted directly so
    negative numbers remain numeric. String values whose raw or leading-trimmed
    text begins with a formula trigger character are prefixed with ``'``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()

    text = str(value)
    stripped = text.lstrip(" ")
    if text.startswith(FORMULA_PREFIXES) or stripped.startswith(FORMULA_PREFIXES):
        return f"'{text}"
    return text


def build_csv_response(
    filename: str,
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    metadata_rows: Sequence[Sequence[Any]] | None = None,
) -> HttpResponse:
    """Build a UTF-8 CSV attachment response with formula-injection protection."""
    safe_name = sanitize_filename(filename, "csv")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    if metadata_rows:
        for meta_row in metadata_rows:
            writer.writerow([sanitize_csv_cell(cell) for cell in meta_row])
        writer.writerow([])

    writer.writerow([sanitize_csv_cell(header) for header in headers])
    for row in rows:
        writer.writerow([sanitize_csv_cell(cell) for cell in row])

    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return response


@dataclass(frozen=True)
class PdfTableSection:
    """One titled table section in a server-generated PDF document."""

    title: str
    headers: Sequence[str]
    rows: Sequence[Sequence[Any]]
    col_widths: Sequence[int] | None = None
    empty_message: str = "No records found."
    summary_rows: Sequence[Sequence[Any]] = field(default_factory=tuple)


def _pdf_escape(text: str) -> str:
    """Escape text for a PDF literal string ``(...)`` in WinAnsi/ASCII."""
    ascii_text = (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u20b9", "INR ")
        .encode("latin-1", errors="replace")
        .decode("latin-1")
    )
    return (
        ascii_text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _format_pdf_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    text = str(value).strip()
    return text if text else "-"


def _truncate_cell(text: str, max_chars: int) -> str:
    if max_chars <= 3:
        return text[:max_chars]
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "~"


def build_pdf_document(
    *,
    title: str,
    subtitle: str = "GIIMS Student Portal",
    status_badge: str = "",
    metadata: Sequence[tuple[str, Any]] = (),
    sections: Sequence[PdfTableSection] = (),
    footer_note: str = "System-generated document from GIIMS Student Portal.",
    landscape: bool = False,
) -> bytes:
    """
    Generate a clean, multi-page ``%PDF-1.4`` binary document.

    Uses standard Type1 fonts (``/F1`` Helvetica and ``/F2`` Helvetica-Bold) so
    every PDF viewer renders it accurately without external font files.
    """
    page_width = 842 if landscape else 595
    page_height = 595 if landscape else 842
    margin_x = 36
    top_y = page_height - 40
    bottom_y = 48
    usable_width = page_width - (2 * margin_x)

    pages_ops: list[list[str]] = []
    current_ops: list[str] = []
    y = top_y

    def start_page(is_first: bool = False) -> None:
        nonlocal current_ops, y
        current_ops = []
        y = top_y
        # Header banner
        banner_h = 46 if is_first else 30
        current_ops.append("0.12 0.16 0.23 rg")
        current_ops.append(f"{margin_x} {y - banner_h} {usable_width} {banner_h} re f")
        current_ops.append("1 1 1 rg")
        if is_first:
            current_ops.append(
                f"BT /F2 14 Tf {margin_x + 12} {y - 20} Td ({_pdf_escape(title)}) Tj ET"
            )
            current_ops.append(
                f"BT /F1 9 Tf {margin_x + 12} {y - 35} Td ({_pdf_escape(subtitle)}) Tj ET"
            )
            if status_badge:
                badge_text = _pdf_escape(status_badge.upper())
                badge_x = page_width - margin_x - max(len(status_badge) * 6 + 24, 90)
                current_ops.append(f"BT /F2 10 Tf {badge_x} {y - 26} Td ([{badge_text}]) Tj ET")
        else:
            cont_title = f"{title} (Continued)"
            current_ops.append(
                f"BT /F2 10 Tf {margin_x + 10} {y - 19} Td ({_pdf_escape(cont_title)}) Tj ET"
            )
        current_ops.append("0 0 0 rg")
        y -= banner_h + 14

    def flush_page() -> None:
        pages_ops.append(current_ops)

    def ensure_space(needed: int) -> None:
        nonlocal y
        if y - needed < bottom_y:
            flush_page()
            start_page(is_first=False)

    start_page(is_first=True)

    # Render key-value metadata in a 2-column grid
    if metadata:
        col_w = usable_width // 2
        pairs = list(metadata)
        for idx in range(0, len(pairs), 2):
            ensure_space(16)
            left_label, left_val = pairs[idx]
            left_str = _format_pdf_value(left_val)
            current_ops.append(
                f"BT /F2 8.5 Tf {margin_x} {y} Td ({_pdf_escape(f'{left_label}:')}) Tj ET"
            )
            left_val_txt = _pdf_escape(_truncate_cell(left_str, 42))
            current_ops.append(f"BT /F1 8.5 Tf {margin_x + 95} {y} Td ({left_val_txt}) Tj ET")
            if idx + 1 < len(pairs):
                right_label, right_val = pairs[idx + 1]
                right_str = _format_pdf_value(right_val)
                rx = margin_x + col_w
                current_ops.append(
                    f"BT /F2 8.5 Tf {rx} {y} Td ({_pdf_escape(f'{right_label}:')}) Tj ET"
                )
                right_val_txt = _pdf_escape(_truncate_cell(right_str, 42))
                current_ops.append(f"BT /F1 8.5 Tf {rx + 95} {y} Td ({right_val_txt}) Tj ET")
            y -= 14
        # Divider line
        current_ops.append("0.82 0.84 0.88 RG 0.7 w")
        current_ops.append(f"{margin_x} {y - 2} m {page_width - margin_x} {y - 2} l S")
        y -= 14

    # Render table sections
    for section in sections:
        ensure_space(44)
        current_ops.append("0.12 0.16 0.23 rg")
        current_ops.append(f"BT /F2 10.5 Tf {margin_x} {y} Td ({_pdf_escape(section.title)}) Tj ET")
        current_ops.append("0 0 0 rg")
        y -= 16

        num_cols = max(len(section.headers), 1)
        if section.col_widths and len(section.col_widths) == num_cols:
            total_weight = sum(section.col_widths)
            col_widths = [int(usable_width * w / total_weight) for w in section.col_widths]
        else:
            col_widths = [usable_width // num_cols] * num_cols

        def draw_table_header(cols: Sequence[str], widths: Sequence[int]) -> None:
            nonlocal y
            row_h = 18
            current_ops.append("0.93 0.94 0.96 rg")
            current_ops.append(f"{margin_x} {y - row_h + 4} {usable_width} {row_h} re f")
            current_ops.append("0.15 0.20 0.28 rg")
            cx = margin_x + 4
            for header, width in zip(cols, widths, strict=False):
                max_chars = max(int(width / 4.8), 4)
                cell_text = _pdf_escape(_truncate_cell(str(header), max_chars))
                current_ops.append(f"BT /F2 8 Tf {cx} {y - 8} Td ({cell_text}) Tj ET")
                cx += width
            current_ops.append("0 0 0 rg")
            y -= row_h

        draw_table_header(section.headers, col_widths)

        if not section.rows:
            ensure_space(18)
            current_ops.append("0.40 0.45 0.52 rg")
            empty_txt = _pdf_escape(section.empty_message)
            current_ops.append(f"BT /F1 8.5 Tf {margin_x + 4} {y - 8} Td ({empty_txt}) Tj ET")
            current_ops.append("0 0 0 rg")
            y -= 18
        else:
            for row_idx, row in enumerate(section.rows):
                row_h = 16
                if y - row_h < bottom_y:
                    flush_page()
                    start_page(is_first=False)
                    draw_table_header(section.headers, col_widths)
                if row_idx % 2 == 1:
                    current_ops.append("0.97 0.98 0.99 rg")
                    current_ops.append(f"{margin_x} {y - row_h + 4} {usable_width} {row_h} re f")
                    current_ops.append("0 0 0 rg")
                cx = margin_x + 4
                for cell, width in zip(row, col_widths, strict=False):
                    max_chars = max(int(width / 4.6), 4)
                    cell_text = _pdf_escape(_truncate_cell(_format_pdf_value(cell), max_chars))
                    current_ops.append(f"BT /F1 8 Tf {cx} {y - 7} Td ({cell_text}) Tj ET")
                    cx += width
                y -= row_h

        for summary_row in section.summary_rows:
            row_h = 17
            ensure_space(row_h)
            current_ops.append("0.91 0.93 0.98 rg")
            current_ops.append(f"{margin_x} {y - row_h + 4} {usable_width} {row_h} re f")
            current_ops.append("0.10 0.14 0.22 rg")
            cx = margin_x + 4
            for cell, width in zip(summary_row, col_widths, strict=False):
                max_chars = max(int(width / 4.8), 4)
                cell_text = _pdf_escape(_truncate_cell(_format_pdf_value(cell), max_chars))
                current_ops.append(f"BT /F2 8 Tf {cx} {y - 7} Td ({cell_text}) Tj ET")
                cx += width
            current_ops.append("0 0 0 rg")
            y -= row_h

        y -= 10

    flush_page()

    total_pages = len(pages_ops)
    for page_num, ops in enumerate(pages_ops, start=1):
        ops.append("0.78 0.80 0.84 RG 0.5 w")
        ops.append(f"{margin_x} 36 m {page_width - margin_x} 36 l S")
        ops.append("0.40 0.45 0.52 rg")
        ops.append(f"BT /F1 7.5 Tf {margin_x} 24 Td ({_pdf_escape(footer_note)}) Tj ET")
        page_label = f"Page {page_num} of {total_pages}"
        ops.append(
            f"BT /F1 7.5 Tf {page_width - margin_x - 60} 24 Td ({_pdf_escape(page_label)}) Tj ET"
        )
        ops.append("0 0 0 rg")

    # Assemble PDF objects
    # 1: Catalog, 2: Pages, 3: Font F1 (Helvetica), 4: Font F2 (Helvetica-Bold)
    # Then for each page i: (5 + 2*i) Page object, (6 + 2*i) Content stream
    objects: list[bytes] = []
    page_obj_nums = [5 + (2 * idx) for idx in range(total_pages)]
    kids_refs = " ".join(f"{num} 0 R" for num in page_obj_nums)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids_refs}] /Count {total_pages} >>".encode("ascii"))
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )

    for idx, ops in enumerate(pages_ops):
        content_num = 6 + (2 * idx)
        stream_bytes = "\n".join(ops).encode("latin-1", errors="replace")
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_num} 0 R >>"
        ).encode("ascii")
        content_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        objects.append(page_dict)
        objects.append(content_obj)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for obj_num, obj_body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        out.extend(obj_body)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def build_pdf_response(filename: str, pdf_bytes: bytes, *, inline: bool = False) -> HttpResponse:
    """Return an ``application/pdf`` HTTP response."""
    safe_name = sanitize_filename(filename, "pdf")
    disposition = "inline" if inline else "attachment"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{safe_name}"'
    return response
