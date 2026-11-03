"""Printable identification labels for antistatic drive bags.

Creates a small 2.25 x 4 inch PDF (default), or a 4 x 6 inch PDF,
from the same saved DriveBench records used for letter-size reports.
No block devices are opened, no drives are formatted, and no jobs are
sent to a printer. Labels can be regenerated as records change.

Requires ReportLab only when rendering a PDF.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import __version__, reports

# Width across the label roll, then length in the feed direction.
LABEL_SIZES = {
    "2.25x4": (2.25, 4.0),
    "4x6": (4.0, 6.0),
}


@dataclass(frozen=True)
class LabelData:
    manufacturer: str
    model: str
    interface: str
    capacity: str
    serial_caption: str
    serial: str
    inventory_id: str
    scan_summary: str
    pattern_summary: str


def _clean(value: Any, fallback: str = "Not recorded") -> str:
    if value is None:
        return fallback
    result = " ".join("".join(ch for ch in str(value) if ch.isprintable()).split())
    return result or fallback


def _recent_scan(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    entries = [e for e in record.get("raw_analysis", [])
               if isinstance(e, dict) and e.get("kind") == "full_scan"
               and e.get("status") == "COMPLETED"
               and isinstance(e.get("result"), dict)]
    if not entries:
        return None
    return max(entries, key=lambda e: (str(e.get("finished_at") or ""),
                                       str(e.get("event_id") or "")))


def label_data(disk: Mapping[str, Any],
               record: Mapping[str, Any] | None = None) -> LabelData:
    """Extract only sale-safe identification and completed test metadata."""
    record = record or {}
    saved = record.get("last_device") or {}
    if not isinstance(saved, Mapping):
        saved = {}

    def field(name: str) -> Any:
        return disk.get(name) or saved.get(name)

    serial = field("serial")
    if serial:
        caption = "DRIVE SERIAL (DEVICE-REPORTED)"
    elif record.get("manual_serial"):
        serial = record["manual_serial"]
        caption = "SERIAL (OPERATOR LABEL)"
    elif record.get("asset_id"):
        serial = record["asset_id"]
        caption = "ASSET ID (OPERATOR LABEL)"
    else:
        serial = record.get("id")
        caption = "DRIVEBENCH RECORD ID"

    scan_summary = "Full surface scan: not recorded"
    pattern_summary = "Media pattern: not verified"
    event = _recent_scan(record)
    if event is not None:
        result = event["result"]
        size = field("size")
        try:
            capacity = int(size)
            scanned = int(result.get("bytes_examined"))
        except (ValueError, TypeError, OverflowError):
            capacity = 0
            scanned = 0

        complete = (result.get("fully_scanned") is True
                    and capacity > 0 and scanned == capacity)
        if complete:
            scan_summary = "Full logical surface: read complete"
            uniform = result.get("uniform_byte_pattern") or {}
            byte = str(uniform.get("byte_hex") or "") if isinstance(uniform, dict) else ""
            if (isinstance(uniform, dict)
                    and uniform.get("status") == "VERIFIED_FULL_SURFACE"
                    and re.fullmatch(r"0x[0-9a-fA-F]{2}", byte)):
                pattern_summary = f"Uniform byte {byte.upper().replace('0X', '0x')}: verified"
            elif isinstance(uniform, dict) and uniform.get("status") == "NOT_UNIFORM":
                pattern_summary = "Media pattern: not uniform"
        else:
            scan_summary = "Full surface read: not verified"

    interface = _clean(field("tran"), "Unknown").upper()
    if interface == "SPI":
        interface = "SCSI (SPI)"

    return LabelData(
        manufacturer=_clean(field("vendor"), "UNKNOWN BRAND"),
        model=_clean(field("model"), "Unknown model"),
        interface=interface,
        capacity=reports._size_label(field("size")),
        serial_caption=caption,
        serial=_clean(serial, "Unavailable"),
        inventory_id=_clean(record.get("id"), ""),
        scan_summary=scan_summary,
        pattern_summary=pattern_summary,
    )


def label_filename(disk: Mapping[str, Any],
                   record: Mapping[str, Any] | None = None,
                   *, size: str = "2.25x4") -> str:
    """Stable filename matching the letter report's disk identity."""
    if size not in LABEL_SIZES:
        raise reports.ReportError(f"Unknown label size: {size}")
    stem = Path(reports.report_filename(disk, record)).stem
    suffix = size.replace(".", "p")
    return f"{stem}-bag-label-{suffix}.pdf"


def _fit_text(canvas, value: str, x: float, y: float, width: float,
              *, font: str = "Helvetica", size: float = 9,
              min_size: float = 5.3) -> None:
    """Fit a line without truncating serial numbers or models."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    value = _clean(value, "")
    if not value:
        return

    while size > min_size and stringWidth(value, font, size) > width:
        size -= 0.2
    if stringWidth(value, font, size) > width:
        raise reports.ReportError(
            "Label field too long to fit without truncation: " + value[:45]
        )
    canvas.setFont(font, size)
    canvas.drawString(x, y, value)


def render_label(path: Path | str, *, disk: Mapping[str, Any],
                 record: Mapping[str, Any] | None = None,
                 size: str = "2.25x4") -> None:
    """Render exactly one appropriately sized page; do not submit a print job."""
    if size not in LABEL_SIZES:
        raise reports.ReportError(f"Unknown label size: {size}")

    try:
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.lib.units import inch
    except ImportError as exc:
        raise reports.ReportError(
            "Labels require ReportLab: sudo apt install python3-reportlab"
        ) from exc

    details = label_data(disk, record)
    width, height = (length * inch for length in LABEL_SIZES[size])
    margin = 10
    inner = width - 2 * margin

    canvas = Canvas(
        str(path),
        pagesize=(width, height),
        pageCompression=1,
        invariant=1,
    )
    canvas.setTitle("DriveBench Antistatic Bag Label")
    canvas.setAuthor("DriveBench")
    canvas.setLineWidth(0.7)

    y = height - 19
    _fit_text(
        canvas, "DRIVEBENCH", margin, y, inner,
        font="Helvetica-Bold", size=13
    )
    y -= 12
    _fit_text(
        canvas, "DRIVE IDENTIFICATION / TEST SUMMARY",
        margin, y, inner, size=6.7
    )
    y -= 8
    canvas.line(margin, y, width - margin, y)

    y -= 21
    _fit_text(
        canvas, details.manufacturer.upper(), margin, y, inner,
        font="Helvetica-Bold", size=12
    )
    y -= 17
    _fit_text(
        canvas, details.model, margin, y, inner,
        font="Helvetica-Bold", size=11
    )
    y -= 15
    _fit_text(
        canvas, f"{details.capacity}   |   {details.interface}",
        margin, y, inner, size=9
    )

    y -= 22
    _fit_text(
        canvas, details.serial_caption, margin, y, inner,
        font="Helvetica-Bold", size=6.8
    )
    y -= 13
    _fit_text(
        canvas, details.serial, margin, y, inner,
        font="Helvetica-Bold", size=9.2
    )

    if details.inventory_id:
        y -= 13
        _fit_text(
            canvas, "Record: " + details.inventory_id,
            margin, y, inner, size=7.5
        )

    y -= 15
    canvas.line(margin, y, width - margin, y)

    y -= 17
    _fit_text(
        canvas, details.scan_summary,
        margin, y, inner, size=8.0
    )
    y -= 15
    _fit_text(
        canvas, details.pattern_summary,
        margin, y, inner, size=8.0
    )

    # Leave a generous footer gap on larger labels and do not allow overlap.
    if y < 48:
        raise reports.ReportError("Label content would overlap the footer")

    canvas.setLineWidth(0.5)
    canvas.line(margin, 42, width - margin, 42)

    _fit_text(
        canvas, "See enclosed full test report",
        margin, 32, inner, size=7.2
    )
    _fit_text(
        canvas, reports.PROJECT_URL,
        margin, 21, inner, size=6.4, min_size=4.8
    )
    _fit_text(
        canvas,
        f"DriveBench v{__version__}  |  Not a sanitization certificate",
        margin, 11, inner, size=6.3, min_size=4.6
    )

    canvas.linkURL(
        reports.PROJECT_URL,
        (margin, 17, width - margin, 28),
        relative=0,
        thickness=0,
    )
    canvas.showPage()
    canvas.save()


def generate_label(disk: Mapping[str, Any],
                   record: Mapping[str, Any] | None = None,
                   *, size: str = "2.25x4",
                   output_dir: Path | str | None = None,
                   replace: bool = False) -> Path:
    """Save a PDF beside reports, atomically; never modify the source drive.

    The caller must explicitly approve replace=True to regenerate a label.
    Nothing is sent to a printer by this function.
    """
    filename = label_filename(disk, record, size=size)
    directory = (
        Path(output_dir).expanduser()
        if output_dir is not None
        else Path.home() / "DriveBench-Reports" / "labels"
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    if not directory.is_dir():
        raise reports.ReportError(f"Not an output directory: {directory}")

    target = directory / filename
    if target.exists() and not replace:
        raise reports.ReportError(
            f"Label already exists: {target}. "
            "Confirm regeneration before replacing it."
        )

    fd, temporary = tempfile.mkstemp(
        prefix=".drivebench-label-",
        suffix=".pdf",
        dir=directory,
    )
    os.close(fd)
    tmp = Path(temporary)

    try:
        render_label(tmp, disk=disk, record=record, size=size)
        os.chmod(tmp, 0o600)

        if target.exists() and not replace:
            raise reports.ReportError(
                f"Label appeared during generation: {target}"
            )

        os.replace(tmp, target)
        return target
    finally:
        tmp.unlink(missing_ok=True)
