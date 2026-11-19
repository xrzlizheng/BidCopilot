import json
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # type: ignore[import-untyped]
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # type: ignore[import-untyped]

from app.core.spreadsheet import active_sheet, excel_row
from app.modules.compliance.models import ComplianceReport, Finding
from app.modules.projects.export_branding import (
    ExportBranding,
    append_branding_row,
    branding_pdf_lines,
)


def build_report_xlsx(
    report: ComplianceReport,
    findings: list[Finding],
    project_name: str,
    *,
    branding: ExportBranding | None = None,
) -> bytes:
    workbook = Workbook()
    summary = active_sheet(workbook)
    summary.title = "Summary"
    summary["A1"] = "BidCopilot 合规预检报告"
    summary["A1"].font = Font(size=14, bold=True)
    # 品牌行紧随标题；branding=None 时不写任何内容，既有布局与断言不变。
    append_branding_row(summary, branding)
    summary.append(excel_row(["项目", project_name]))
    summary.append(excel_row(["租户项目 ID", str(report.project_id)]))
    summary.append(excel_row(["规则版本", report.rule_version]))
    summary.append(excel_row(["文件哈希", report.file_hash]))
    summary.append(excel_row(["生成时间", report.created_at.isoformat()]))
    summary.append(["HARD", report.summary_json.get("hard", 0)])
    summary.append(["SOFT", report.summary_json.get("soft", 0)])
    summary.append(["findings", report.summary_json.get("findings", 0)])
    summary.append(
        excel_row(
            ["分类统计", json.dumps(report.summary_json.get("by_category", {}), ensure_ascii=False)]
        )
    )
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 80

    sheet = workbook.create_sheet("Findings")
    headers = ["级别", "规则", "类别", "问题", "位置", "期望", "实际", "依据", "建议", "状态", "证据引用"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
    for finding in findings:
        location = finding.location or {}
        sheet.append(
            excel_row(
                [
                    finding.finding_type,
                    finding.rule_id,
                    finding.category,
                    finding.message,
                    str(location.get("section_title") or location.get("scope") or ""),
                    finding.expected or "",
                    finding.actual or "",
                    finding.basis or "",
                    finding.suggestion or "",
                    finding.status,
                    finding.evidence_ref or "",
                ]
            )
        )
    widths = [8, 22, 16, 40, 18, 24, 24, 30, 30, 10, 20]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_report_pdf(
    report: ComplianceReport,
    findings: list[Finding],
    project_name: str,
    *,
    branding: ExportBranding | None = None,
) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "ChineseNormal",
        parent=styles["Normal"],
        fontName="STSong-Light",
        fontSize=9,
        leading=13,
    )
    title_style = ParagraphStyle(
        "ChineseTitle",
        parent=normal,
        fontSize=16,
        leading=22,
        spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        "ChineseCell",
        parent=normal,
        fontSize=7.5,
        leading=10,
    )

    story: list[Any] = [
        Paragraph("BidCopilot 合规预检报告", title_style),
        *[Paragraph(escape(line), normal) for line in branding_pdf_lines(branding)],
        Paragraph(f"项目：{escape(project_name)}", normal),
        Paragraph(f"规则版本：{escape(report.rule_version)}", normal),
        Paragraph(f"文件哈希：{escape(report.file_hash)}", normal),
        Paragraph(f"生成时间：{escape(report.created_at.isoformat())}", normal),
        Paragraph(
            f"HARD：{report.summary_json.get('hard', 0)}　"
            f"SOFT：{report.summary_json.get('soft', 0)}　"
            f"findings：{report.summary_json.get('findings', 0)}",
            normal,
        ),
        Spacer(1, 6 * mm),
    ]

    headers = ["级别", "规则", "问题", "位置", "期望", "实际", "建议", "状态"]
    rows: list[list[Any]] = [
        [Paragraph(escape(header), cell_style) for header in headers]
    ]
    for finding in findings:
        location = finding.location or {}
        values = [
            finding.finding_type,
            finding.rule_id,
            finding.message,
            str(location.get("section_title") or location.get("scope") or ""),
            finding.expected or "",
            finding.actual or "",
            finding.suggestion or "",
            finding.status,
        ]
        rows.append([Paragraph(escape(str(value)), cell_style) for value in values])

    table = Table(
        rows,
        colWidths=[10 * mm, 22 * mm, 40 * mm, 20 * mm, 24 * mm, 24 * mm, 26 * mm, 10 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            "本报告由 BidCopilot 确定性规则引擎生成，仅用于投标自检，不替代官方评标。",
            normal,
        )
    )
    buffer = BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm).build(story)
    return buffer.getvalue()
