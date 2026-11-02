
"""Seller-facing PDF drive reports and optional CUPS printing.

This module never accesses or writes block devices. PDFs use saved DriveBench
metadata and explicitly supplied test results; no missing test is marked passed.
Report generation is repeatable: calling generate_pdf(..., replace=True) builds
an updated PDF at the same deterministic filename.

Optional Ubuntu components: python3-reportlab and cups-client.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.sax.saxutils import escape

from . import __version__


PROJECT_URL = "https://github.com/ericburnsonline/DriveBench"


class ReportError(RuntimeError):
    """A PDF generation or printing action was refused or failed."""


@dataclass(frozen=True)
class ReportSection:
    """An extensible collection of seller-safe rows for a future feature."""

    title: str
    rows: tuple[tuple[str, str], ...]


def _display(value: Any, missing: str = "Not recorded") -> str:
    if value is None or value == "":
        return missing
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip() or missing


def _slug(value: Any, default: str = "Unknown", max_length: int = 60) -> str:
    """Make disk-supplied strings safe as ONE filename component."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-")
    return value[:max_length].strip("-") or default


def _size_label(num_bytes: Any) -> str:
    try:
        size = int(num_bytes)
    except (TypeError, ValueError, OverflowError):
        size = 0
    if size <= 0:
        return "UnknownSize"
    gb = size / 1_000_000_000
    if gb >= 950:
        tb = gb / 1000
        return (f"{tb:.2f}".rstrip("0").rstrip(".") + "TB")
    if gb >= 1:
        return f"{round(gb):.0f}GB" if abs(gb - round(gb)) < 0.5 else f"{gb:.1f}GB"
    return f"{round(size / 1_000_000):.0f}MB"


def _bytes_label(value: Any) -> str:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return "Not recorded"
    if num < 0:
        return "Not recorded"
    if num >= 1_073_741_824:
        return f"{num:,} bytes ({num / 1_073_741_824:.2f} GiB)"
    return f"{num:,} bytes"


def _hardware_serial(disk: Mapping[str, Any], record: Mapping[str, Any] | None) -> str:
    # Never label an operator serial as hardware verified.
    return _display(disk.get("serial") or (record or {}).get("last_device", {}).get("serial"), "")


def report_filename(disk: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> str:
    """Stable name: brand, interface, model, capacity, and serial/asset ID.

    If hardware serial is missing, an explicitly tagged operator identifier or
    record ID is used; reports for anonymous drives cannot silently collide.
    """
    record = record or {}
    maker = disk.get("vendor") or record.get("last_device", {}).get("vendor") or "UnknownBrand"
    interface = disk.get("tran") or record.get("last_device", {}).get("tran") or "UnknownInterface"
    model = disk.get("model") or record.get("last_device", {}).get("model") or "UnknownModel"
    capacity = disk.get("size") or record.get("last_device", {}).get("size")
    serial = _hardware_serial(disk, record)
    if serial:
        identity = "SN-" + _slug(serial, max_length=72)
    elif record.get("manual_serial"):
        identity = "OperatorSN-" + _slug(record["manual_serial"], max_length=72)
    elif record.get("asset_id"):
        identity = "Asset-" + _slug(record["asset_id"], max_length=72)
    elif record.get("id"):
        identity = "Record-" + _slug(record["id"])
    else:
        raise ReportError("Provide a verified hardware serial or saved record/asset ID to name the PDF safely.")
    stem = "-".join([_slug(maker, max_length=32), _slug(interface, max_length=20),
                     _slug(model, max_length=48),
                     _size_label(capacity), identity])
    return stem + ".pdf"


def _latest_full_scan(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Use the latest completed full scan only; ignore incomplete attempts."""
    entries = [event for event in record.get("raw_analysis", [])
               if isinstance(event, dict) and event.get("kind") == "full_scan"
               and event.get("status") == "COMPLETED"
               and isinstance(event.get("result"), dict)]
    return max(entries, key=lambda e: (str(e.get("finished_at") or ""),
                                       str(e.get("event_id") or "")), default=None)


def _test_sections(record: Mapping[str, Any], health: Mapping[str, Any] | None) -> list[ReportSection]:
    sections: list[ReportSection] = []
    recent = _latest_full_scan(record)
    if recent:
        result = recent["result"]
        fully_scanned = result.get("fully_scanned") is True
        uniform = result.get("uniform_byte_pattern") or {}
        rows = [
            ("Latest full scan", _display(recent.get("finished_at"))),
            ("Entire logical surface read", "Completed" if fully_scanned else "Not confirmed"),
            ("Data examined", _bytes_label(result.get("bytes_examined"))),
        ]
        if uniform.get("status") == "VERIFIED_FULL_SURFACE" and fully_scanned:
            rows.append(("Uniform byte pattern", f"{_display(uniform.get('byte_hex'))} - VERIFIED_FULL_SURFACE"))
        elif uniform.get("status") == "NOT_UNIFORM":
            rows.append(("Uniform byte pattern", "NOT UNIFORM in examined data"))
        elif uniform.get("status") == "SAMPLED_OR_PARTIAL_ONLY":
            rows.append(("Uniform byte pattern", f"{_display(uniform.get('byte_hex'))} - sample/partial only"))
        elif uniform.get("status"):
            rows.append(("Uniform byte pattern", _display(uniform.get("status"))))
        if result.get("assessment"):
            rows.append(("File signature assessment", _display(result["assessment"]).replace("_", " ")))
        markers = result.get("structural_markers")
        if isinstance(markers, list):
            rows.append(("Filesystem/partition markers", ", ".join(str(x) for x in markers[:6]) or "None recognized"))
        sections.append(ReportSection("Read-only media analysis", tuple(rows)))
    else:
        sections.append(ReportSection("Read-only media analysis", (("Full scan", "No completed scan in saved record"),)))

    if isinstance(health, Mapping) and health:
        smart = health.get("smart_status")
        rows: list[tuple[str, str]] = []
        if isinstance(smart, Mapping) and isinstance(smart.get("passed"), bool):
            rows.append(("SMART overall assessment", "Passed (device-reported)" if smart["passed"] else "FAILED (device-reported)"))
        if health.get("scsi_grown_defect_list") is not None:
            rows.append(("SCSI grown defect count", str(health["scsi_grown_defect_list"])))
        if isinstance(health.get("power_on_time"), Mapping) and health["power_on_time"].get("hours") is not None:
            rows.append(("Power-on time (reported)", f"{health['power_on_time']['hours']} hours; lifetime may be unreliable"))
        if health.get("self_test_result"):
            rows.append(("SMART self-test", str(health["self_test_result"])))
        if rows:
            sections.append(ReportSection("Drive health (device-reported)", tuple(rows)))
    return sections


def build_sections(disk: Mapping[str, Any], record: Mapping[str, Any] | None = None,
                   *, health: Mapping[str, Any] | None = None,
                   extra_sections: Iterable[ReportSection] = ()) -> list[ReportSection]:
    """Build a seller-facing report from saved evidence and explicit extras.

    Future features can pass ReportSection instances, leaving the PDF renderer
    unchanged. Discovery/recovery file paths and raw history are not copied.
    """
    record = record or {}
    serial = _hardware_serial(disk, record)
    rows = [
        ("Manufacturer", _display(disk.get("vendor") or record.get("last_device", {}).get("vendor"))),
        ("Model", _display(disk.get("model") or record.get("last_device", {}).get("model"))),
        ("Interface", _display(disk.get("tran") or record.get("last_device", {}).get("tran")).upper()),
        ("Capacity", _bytes_label(disk.get("size") or record.get("last_device", {}).get("size"))),
        ("Hardware serial", _display(serial, "Unavailable")),
    ]
    if record.get("manual_serial"):
        rows.append(("Operator-supplied serial (not verified)", _display(record["manual_serial"])))
    if record.get("asset_id"):
        rows.append(("Asset ID", _display(record["asset_id"])))
    if record.get("id"):
        rows.append(("DriveBench inventory ID", _display(record["id"])))
    sections = [ReportSection("Drive identification", tuple(rows))]
    sections.extend(_test_sections(record, health))
    # Strictly whitelist extra sections from explicitly requested reporting
    # features; callers decide what is seller-safe to publish.
    for section in extra_sections:
        if not isinstance(section, ReportSection):
            raise ReportError("Each extra section must be a ReportSection")
        if section.rows:
            sections.append(section)
    return sections


def _pdf_styles():
    try:
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError as exc:
        raise ReportError("PDF support missing. On Ubuntu: sudo apt install python3-reportlab") from exc
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DBTitle", parent=styles["Heading1"], fontName="Helvetica-Bold",
                              fontSize=18, leading=22, spaceAfter=5, textColor=colors.HexColor("#171717")))
    styles.add(ParagraphStyle(name="DBSub", parent=styles["Normal"], fontSize=9, leading=13,
                              textColor=colors.HexColor("#5A5A5A"), spaceAfter=12))
    styles.add(ParagraphStyle(name="DBSection", parent=styles["Heading2"], fontName="Helvetica-Bold",
                              fontSize=11, leading=14, spaceBefore=13, spaceAfter=6,
                              textColor=colors.HexColor("#202020")))
    styles.add(ParagraphStyle(name="DBLabel", parent=styles["Normal"], fontSize=8.5,
                              leading=11, textColor=colors.HexColor("#454545")))
    styles.add(ParagraphStyle(name="DBValue", parent=styles["Normal"], fontSize=8.6,
                              leading=12, textColor=colors.HexColor("#151515"), wordWrap="CJK"))
    styles.add(ParagraphStyle(name="DBFine", parent=styles["Normal"], fontSize=8,
                              leading=11, textColor=colors.HexColor("#5A5A5A"), spaceBefore=10))
    return styles


def render_pdf(path: Path, *, disk: Mapping[str, Any], record: Mapping[str, Any] | None = None,
               health: Mapping[str, Any] | None = None,
               extra_sections: Iterable[ReportSection] = (),
               generated_at: datetime | None = None) -> None:
    """Write an independent PDF to `path` (not to the attached storage drive)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table,
                                       TableStyle, KeepTogether, Spacer)
    except ImportError as exc:
        raise ReportError("PDF support missing. On Ubuntu: sudo apt install python3-reportlab") from exc
    styles = _pdf_styles()
    when = generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.66*inch,
                            rightMargin=0.66*inch, topMargin=0.6*inch,
                            bottomMargin=0.64*inch, title="DriveBench Storage Drive Test Report",
                            author="DriveBench", pageCompression=1)
    content = [Paragraph("DRIVEBENCH", styles["DBTitle"]),
               Paragraph("STORAGE DRIVE TEST REPORT  |  Generated " +
                         escape(when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")), styles["DBSub"])]
    for section in build_sections(disk, record, health=health, extra_sections=extra_sections):
        heading = Paragraph(escape(section.title), styles["DBSection"])
        rows = [[Paragraph(escape(_display(k)), styles["DBLabel"]),
                 Paragraph(escape(_display(v)), styles["DBValue"])] for k, v in section.rows]
        table = Table(rows, colWidths=[2.05*inch, 5.02*inch], hAlign="LEFT", repeatRows=0)
        table.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("LEFTPADDING", (0,0), (0,-1), 8),
            ("RIGHTPADDING", (0,0), (-1,-1), 8),
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor("#F6F6F6")]),
            ("LINEBELOW", (0,-1), (-1,-1), 0.5, colors.HexColor("#DADADA")),
        ]))
        content.append(KeepTogether([heading, table]))
        content.append(Spacer(1, 4))
    content.append(Paragraph(
        "Report limitations: Results refer only to the tests actually recorded above. "
        "A completed logical-surface read is not a certified secure erase. "
        "A uniform-byte pattern does not establish factory-new condition or why the data was written. "
        "Device-reported counters may not reflect lifetime usage. "
        "No data recovery or personal-file details are included.", styles["DBFine"]))

    def footer(canvas, report):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(0.66*inch, 0.40*inch,
                          f"DriveBench v{__version__} | Report from recorded drive tests")
        canvas.drawRightString(letter[0] - 0.66*inch, 0.40*inch, f"Page {report.page}")
        canvas.drawString(0.66*inch, 0.25*inch, PROJECT_URL)
        canvas.linkURL(PROJECT_URL,
                       (0.66*inch, 0.23*inch, 3.25*inch, 0.37*inch),
                       relative=0, thickness=0)
        canvas.restoreState()

    try:
        doc.build(content, onFirstPage=footer, onLaterPages=footer)
    except OSError as exc:
        raise ReportError(f"Unable to create report PDF: {exc}") from exc


def generate_pdf(disk: Mapping[str, Any], record: Mapping[str, Any] | None = None,
                 *, output_dir: Path | str | None = None, replace: bool = False,
                 health: Mapping[str, Any] | None = None,
                 extra_sections: Iterable[ReportSection] = ()) -> Path:
    """Atomically create a report; explicit replace permits future regeneration.

    Default directory: ~/DriveBench-Reports. Never modifies the source disk.
    No automatic printing. Caller should confirm the report before invoking lp.
    """
    directory = Path(output_dir).expanduser() if output_dir else Path.home() / "DriveBench-Reports"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.is_dir():
        raise ReportError(f"Not an output directory: {directory}")
    target = directory / report_filename(disk, record)
    if target.exists() and not replace:
        raise ReportError(f"Report already exists: {target}. Confirm regeneration before replacing it.")
    fd, temp_name = tempfile.mkstemp(prefix=".drivebench-report-", suffix=".pdf", dir=directory)
    os.close(fd)
    temp = Path(temp_name)
    try:
        render_pdf(temp, disk=disk, record=record, health=health,
                   extra_sections=extra_sections)
        os.chmod(temp, 0o600)
        if target.exists() and not replace:
            raise ReportError(f"Report appeared during generation: {target}")
        os.replace(temp, target)
        return target
    finally:
        temp.unlink(missing_ok=True)


def list_printers() -> list[str]:
    """Return installed CUPS destinations; no printing performed."""
    if not shutil.which("lpstat"):
        raise ReportError("Printer discovery requires cups-client: sudo apt install cups-client")
    try:
        result = subprocess.run(["lpstat", "-e"], capture_output=True,
                                text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReportError(f"Unable to query CUPS printers: {exc}") from exc
    if result.returncode:
        raise ReportError("Unable to list CUPS printers: " + (result.stderr or result.stdout).strip()[:200])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def print_pdf(path: Path | str, *, printer: str | None = None, copies: int = 1) -> str:
    """Submit a previously generated PDF to CUPS. Never called implicitly."""
    try:
        document = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReportError(f"PDF does not exist or cannot be accessed: {path}") from exc
    if not document.is_file() or document.suffix.lower() != ".pdf":
        raise ReportError("Only an existing PDF file can be printed")
    with document.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ReportError("Selected file does not have a PDF header")
    if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= 20:
        raise ReportError("Copies must be between 1 and 20")
    if not shutil.which("lp"):
        raise ReportError("Printing requires cups-client: sudo apt install cups-client")
    args = ["lp"]
    if printer:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,126}", printer):
            raise ReportError("Invalid CUPS printer name")
        args += ["-d", printer]
    args += ["-n", str(copies), "-o", "media=Letter", "-o", "sides=one-sided",
             "--", str(document)]
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=35, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReportError(f"CUPS submission failed: {exc}") from exc
    if result.returncode:
        raise ReportError("CUPS rejected job: " + (result.stderr or result.stdout).strip()[:250])
    return (result.stdout or "Print job accepted by CUPS").strip()
