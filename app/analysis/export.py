"""导出已保存快照，不重新查询、不调用模型；防电子表格公式执行。"""

from __future__ import annotations

import csv
import html
import io
import json
import re


def spreadsheet_text(value):
    text = "" if value is None else str(value)
    # 数字负号合法；表达式及控制符前缀需要转为文本。
    if text.lstrip("\ufeff \t\r\n\v\f\x00").startswith(("=", "+", "-", "@")) and not re.fullmatch(
        r"[+-]?\d+(?:\.\d+)?", text
    ):
        return "'" + text
    return text


def _safe_xml(value):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))


def _md(value):
    text = html.escape("" if value is None else str(value)).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()!|])", r"\\\1", text)


def export_report(report, format, table_id=None):
    tables = report.get("tables", [])
    selected = (
        next((table for table in tables if table["id"] == table_id), None)
        if table_id
        else (tables[0] if tables else None)
    )
    if format == "csv":
        if selected is None:
            raise ValueError("没有该结果表，请选择其他表格或导出 Markdown 报告")
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(
            ["分析记录ID", "证据编号", "核验状态", "统计范围", "是否截断", *selected["columns"]]
        )
        metadata = [
            report["trace_id"],
            ",".join(selected["evidence_ids"]),
            report["reflection_status"],
            json.dumps(selected.get("scope", {}), ensure_ascii=False),
            str(selected.get("truncated", False)),
        ]
        for row in selected["rows"]:
            writer.writerow([spreadsheet_text(value) for value in [*metadata, *row]])
        return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8"), "text/csv; charset=utf-8"
    if format == "xlsx":
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
        except ImportError:
            raise ValueError(
                "XLSX 导出需要已批准安装的 openpyxl，请先使用 CSV 或 Markdown"
            ) from None
        book = Workbook()
        sheet = book.active
        sheet.title = "分析说明"
        metadata = [
            ["分析记录", report["trace_id"]],
            ["问题", report.get("question", "")],
            ["保存时间", report.get("created_at", "")],
            ["核验状态", report["reflection_status"]],
            ["答案", report["answer"]],
            [
                "说明",
                "历史快照；精确数值按文本保存，不重新查询。超长文本拆为续行，移除 XLSX 不支持的控制符。",
            ],
        ]
        metadata += [["限制", item] for item in report.get("analysis", {}).get("warnings", [])]
        _write_sheet(sheet, metadata, Font, PatternFill)
        for index, table in enumerate(tables, 1):
            sheet = book.create_sheet(f"结果{index}")
            rows = [
                ["结果", table["title"]],
                ["证据", ",".join(table["evidence_ids"])],
                ["统计范围", json.dumps(table.get("scope", {}), ensure_ascii=False)],
                ["是否截断", str(table.get("truncated", False))],
                [],
                table["columns"],
                *table["rows"],
            ]
            _write_sheet(sheet, rows, Font, PatternFill)
        evidence = book.create_sheet("查询证据")
        _write_sheet(
            evidence,
            [
                ["证据", "工具", "参数", "实际返回结果"],
                *[
                    [
                        f"E{index}",
                        step["tool"],
                        json.dumps(step.get("arguments", {}), ensure_ascii=False),
                        step.get("output", ""),
                    ]
                    for index, step in enumerate(report.get("steps", []), 1)
                ],
            ],
            Font,
            PatternFill,
        )
        output = io.BytesIO()
        book.save(output)
        return (
            output.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if format == "md":
        lines = [
            "# 数据分析记录",
            "",
            "- 记录：" + _md(report["trace_id"]),
            "- 保存时间：" + _md(report.get("created_at", "")),
            "- 核验状态：" + _md(report["reflection_status"]),
            "- 问题：" + _md(report.get("question", "")),
            "",
            "## 分析结论",
            "",
            _md(report["answer"]),
            "",
            "## 限制",
            "",
            "历史快照，不代表当前最新数据。未通过审查的内容需要核对。",
        ]
        lines.extend("- " + _md(item) for item in report.get("analysis", {}).get("warnings", []))
        for table in tables:
            lines += [
                "",
                "## " + _md(table["title"]),
                "",
                "证据：" + _md(",".join(table["evidence_ids"])),
                "",
                "统计范围：" + _md(json.dumps(table.get("scope", {}), ensure_ascii=False)),
                "",
                "结果截断：" + str(table.get("truncated", False)),
                "",
                "| " + " | ".join(map(_md, table["columns"])) + " |",
                "| " + " | ".join("---" for _ in table["columns"]) + " |",
            ]
            lines.extend("| " + " | ".join(map(_md, row)) + " |" for row in table["rows"])
        lines += ["", "## 查询与工具证据", ""]
        for index, step in enumerate(report.get("steps", []), 1):
            text = json.dumps(
                {"arguments": step.get("arguments", {}), "output": step.get("output", "")},
                ensure_ascii=False,
                indent=2,
            )
            fence = "`" * max(
                3, max((len(match) for match in re.findall(r"`+", text)), default=0) + 1
            )
            lines += [f"### E{index} · " + _md(step["tool"]), "", fence + "json", text, fence, ""]
        return "\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8"
    raise ValueError("不支持的导出格式")


def _write_sheet(sheet, rows, Font, PatternFill):
    for row in rows:
        values = [_safe_xml("" if value is None else value) for value in row]
        sheet.append([value[:32000] for value in values])
        for offset in range(32000, max(map(len, values), default=0), 32000):
            sheet.append([value[offset : offset + 32000] for value in values])
    for row in sheet:
        for cell in row:
            cell.data_type = "s"  # 包括 =、+ 等开头的来源文本，永不作为公式。
            cell.number_format = "@"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="175B48")
        cell.fill = PatternFill("solid", fgColor="E9F1EB")
    sheet.freeze_panes = "B2"
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(
            48, max(16, max(len(str(cell.value or "")) for cell in column) + 2)
        )
