"""Bounded local tabular parsing and conservative, lossless type inference."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from app.config import Settings


class DatasetError(ValueError):
    pass


class ExcelDependencyMissing(DatasetError):
    pass


KINDS = {"text", "integer", "decimal", "boolean", "date", "datetime", "datetime_tz"}
_INTEGER = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
_DECIMAL = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)\.[0-9]+$")


def clean_filename(value: str) -> str:
    value = PurePosixPath(value.replace("\\", "/")).name
    value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127)
    if not value or len(value) > 255:
        raise DatasetError("文件名为空或过长")
    if Path(value).suffix.lower() not in (".csv", ".xlsx"):
        raise DatasetError("仅支持 CSV 和 XLSX；旧版 XLS 请先转换")
    return value


def json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def scalar_kind(value):
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, datetime):
        return "datetime_tz" if value.utcoffset() is not None else "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, int):
        return "integer" if -(2**63) <= value < 2**63 else "text"
    if isinstance(value, (float, Decimal)):
        if not math.isfinite(value):
            raise DatasetError("数值包含 NaN 或无穷大")
        return "decimal"
    if not isinstance(value, str):
        return "text"
    if value.casefold() in ("true", "false"):
        return "boolean"
    if _INTEGER.fullmatch(value):
        if len(value.lstrip("+-")) > 19:
            return "text"
        return "integer" if -(2**63) <= int(value) < 2**63 else "text"
    if _DECIMAL.fullmatch(value):
        return "decimal"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
            return "date"
        if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return "datetime_tz" if parsed.utcoffset() is not None else "datetime"
    except ValueError:
        pass
    return "text"


def convert(value, kind):
    if value is None:
        return None
    if kind == "text":
        return str(json_value(value))
    source = scalar_kind(value)
    if kind == "integer" and source == "integer":
        return int(value)
    if kind == "decimal" and source in ("integer", "decimal"):
        result = Decimal(str(value))
        if len(result.as_tuple().digits) > 1000 or abs(result.as_tuple().exponent) > 1000:
            raise DatasetError("小数精度超过导入限制，请按文本导入")
        return result
    if kind == "boolean" and source == "boolean":
        return value if isinstance(value, bool) else value.casefold() == "true"
    if kind == "date" and source == "date":
        return value if isinstance(value, date) else date.fromisoformat(value)
    if kind in ("datetime", "datetime_tz") and source == kind:
        return (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    raise DatasetError(f"值无法无损转换为 {kind}，请修改类型或源文件")


@dataclass
class ParsedTable:
    columns: list[dict]
    rows: list[tuple[int, list]]
    warnings: list[str]
    sheet_names: list[str]
    selected_sheet: str | None

    @property
    def preview(self):
        def render(value):
            result = json_value(value)
            return result[:200] + "…" if isinstance(result, str) and len(result) > 200 else result

        return [[number, *[render(value) for value in values]] for number, values in self.rows[:10]]


def _xlsx_rows(raw, sheet_name, settings):
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 2048
                or sum(item.file_size for item in entries)
                > settings.import_xlsx_max_uncompressed_bytes
            ):
                raise DatasetError("XLSX 解压大小或文件数量超过限制")
            if any(
                "vbaProject" in item.filename or ".." in PurePosixPath(item.filename).parts
                for item in entries
            ):
                raise DatasetError("XLSX 包含不支持的宏或异常路径")
            for item in entries:
                if item.filename.endswith((".xml", ".rels")):
                    with archive.open(item) as stream:
                        tail = b""
                        while block := stream.read(65536):
                            scan = tail + block
                            if (
                                b"\x00" in scan
                                or b"<!DOCTYPE" in scan.upper()
                                or b"<!ENTITY" in scan.upper()
                            ):
                                raise DatasetError("XLSX 包含不支持的 XML 编码或实体声明")
                            tail = scan[-32:]
    except BadZipFile:
        raise DatasetError("文件不是有效的 XLSX") from None
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ExcelDependencyMissing(
            "XLSX 需要 openpyxl；请先按授权流程安装，可先使用 CSV"
        ) from None
    try:
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    except Exception:
        raise DatasetError("无法解析 XLSX，请检查文件是否损坏或加密") from None
    try:
        sheets = workbook.sheetnames
        if len(sheets) > 100:
            raise DatasetError("工作表数量超过限制")
        if sheet_name is not None and sheet_name not in sheets:
            raise DatasetError("指定工作表不存在")
        visible = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
        sheet = (
            workbook[sheet_name]
            if sheet_name
            else (visible[0] if visible else workbook.worksheets[0])
        )
        if sheet.max_column and sheet.max_column > settings.import_max_columns:
            raise DatasetError("列数超过限制")
        # Read actual cells after checking the declared column bound.
        sheet.reset_dimensions()
        result = []
        for number, cells in enumerate(sheet.iter_rows(), 1):
            if number > settings.import_max_rows + 1:
                raise DatasetError("行数超过限制")
            if len(cells) > settings.import_max_columns:
                raise DatasetError("列数超过限制")
            if any(cell.data_type in ("f", "e") for cell in cells):
                raise DatasetError(f"第 {number} 行包含公式或错误值，请先粘贴为普通值")
            result.append((number, [cell.value for cell in cells]))
        return result, sheets, sheet.title
    finally:
        workbook.close()


def parse_table(
    raw: bytes,
    filename: str,
    settings: Settings,
    *,
    encoding="utf-8-sig",
    delimiter=",",
    sheet_name=None,
    type_overrides: dict[str, str] | None = None,
) -> ParsedTable:
    filename = clean_filename(filename)
    if not raw or len(raw) > settings.import_max_bytes:
        raise DatasetError("文件为空或超过大小限制")
    sheets, selected = [], None
    if filename.lower().endswith(".xlsx"):
        records, sheets, selected = _xlsx_rows(raw, sheet_name, settings)
    else:
        if encoding not in ("utf-8-sig", "gb18030") or delimiter not in (",", ";", "\t", "|"):
            raise DatasetError("不支持的 CSV 编码或分隔符")
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            raise DatasetError("CSV 编码不匹配，请选择 UTF-8 或 GB18030") from None
        if "\x00" in decoded:
            raise DatasetError("CSV 包含二进制内容")
        try:
            reader = csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter, strict=True)
            records = []
            for number, values in enumerate(reader, 1):
                if number > settings.import_max_rows + 1:
                    raise DatasetError("行数超过限制")
                if len(values) > settings.import_max_columns:
                    raise DatasetError("列数超过限制")
                records.append((number, values))
        except csv.Error:
            raise DatasetError("CSV 格式错误或单元格过长") from None
    if not records:
        raise DatasetError("文件没有表头")
    headers = [str(value).strip() if value is not None else "" for value in records[0][1]]
    if (
        not headers
        or any(not value for value in headers)
        or any(len(value) > 255 for value in headers)
    ):
        raise DatasetError("表头不能为空或超过 255 个字符")
    canonical = [unicodedata.normalize("NFKC", value).casefold() for value in headers]
    if len(set(canonical)) != len(canonical):
        raise DatasetError("表头重复，请给每列设置不同名称")
    rows: list[Any] = []
    kinds: list[set[str]] = [set() for _ in headers]
    for number, values in records[1:]:
        normalized = [
            None if isinstance(value, str) and not value.strip() else value for value in values
        ]
        # Some XLSX rows omit trailing empty cells; CSV rows must be explicit.
        if filename.lower().endswith(".xlsx") and len(normalized) < len(headers):
            normalized += [None] * (len(headers) - len(normalized))
        if all(value is None for value in normalized):
            continue
        if len(normalized) != len(headers):
            raise DatasetError(f"第 {number} 行列数与表头不一致")
        for i, value in enumerate(normalized):
            if isinstance(value, str) and (
                len(value) > settings.import_max_cell_chars or "\x00" in value
            ):
                raise DatasetError(f"第 {number} 行单元格过长或包含 NUL")
            kinds[i].add(scalar_kind(value))
        rows.append((number, normalized))
    if not rows:
        raise DatasetError("文件没有可导入的数据行")
    columns: list[dict[str, Any]] = []
    used = {"_row_number"}
    warnings: list[str] = []
    for i, (header, choices) in enumerate(zip(headers, kinds)):
        slug = re.sub(r"[^a-z0-9_]+", "_", unicodedata.normalize("NFKC", header).lower()).strip("_")
        if not slug or slug[0].isdigit() or len(slug) > 55 or slug in used:
            slug = f"column_{i + 1:03d}"
        while slug in used:
            slug += "_x"
        used.add(slug)
        choices = choices - {"empty"}
        kind = (
            next(iter(choices))
            if len(choices) == 1
            else "decimal"
            if choices and choices <= {"integer", "decimal"}
            else "text"
        )
        if len(choices) > 1 and kind == "text":
            warnings.append(f"{header} 含混合类型，按文本保留")
        columns.append(
            {"name": slug, "label": header, "type": kind, "nullable": "empty" in kinds[i]}
        )
    overrides: dict[str, str] = dict(type_overrides or {})
    if set(overrides) - {column["name"] for column in columns} or any(
        value not in KINDS for value in overrides.values()
    ):
        raise DatasetError("类型覆盖包含未知列或不支持的数据类型")
    for column in columns:
        column["type"] = overrides.get(column["name"], column["type"])
    converted = [
        (number, [convert(value, column["type"]) for value, column in zip(values, columns)])
        for number, values in rows
    ]
    return ParsedTable(columns, converted, warnings, sheets, selected)


def canonical_digest(rows) -> str:
    digest = hashlib.sha256()

    def normalize(value):
        if isinstance(value, Decimal):
            if value == 0:
                return "0"
            rendered = format(value, "f")
            return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
        if isinstance(value, datetime) and value.utcoffset() is not None:
            return value.astimezone(UTC).isoformat()
        return json_value(value)

    for row in rows:
        digest.update(
            json.dumps(
                [normalize(value) for value in row], ensure_ascii=False, separators=(",", ":")
            ).encode()
            + b"\n"
        )
    return digest.hexdigest()
