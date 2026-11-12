"""File parsing acceptance checks; no API calls or external files."""

import io
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from zipfile import ZipFile

import pytest

from app.config import Settings
from app.datasets.parser import (
    DatasetError,
    ExcelDependencyMissing,
    canonical_digest,
    clean_filename,
    parse_table,
)


def settings(**kwargs):
    return Settings(_env_file=None, **kwargs)


def csv(value, **kwargs):
    return parse_table(value.encode("utf-8-sig"), "test.csv", settings(), **kwargs)


def xlsx(rows, *, second=None):
    Workbook = pytest.importorskip("openpyxl").Workbook
    book = Workbook()
    book.active.title = "主表"
    for row in rows:
        book.active.append(row)
    if second:
        sheet = book.create_sheet("第二表")
        for row in second:
            sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def test_conservative_inference_and_source_record_numbers():
    parsed = csv(
        'id,amount,count,paid,day,memo,unknown\n001,12.30,4,true,2026-10-22," 保留空格 ",NULL\n\n002,0.10,,false,2026-10-23,"多行\n内容",NULL\n'
    )
    assert [col["type"] for col in parsed.columns] == [
        "text",
        "decimal",
        "integer",
        "boolean",
        "date",
        "text",
        "text",
    ]
    assert parsed.rows == [
        (2, ["001", Decimal("12.30"), 4, True, date(2026, 10, 22), " 保留空格 ", "NULL"]),
        (4, ["002", Decimal("0.10"), None, False, date(2026, 10, 23), "多行\n内容", "NULL"]),
    ]
    assert parsed.columns[2]["nullable"]
    assert parsed.preview[0][0] == 2 and parsed.preview[0][2] == "12.30"


def test_large_identifiers_mixed_types_and_decimal_precision():
    parsed = csv(
        "huge,mixed,precise\n9223372036854775808,1,12345678901234567890.12345678901234567890\n99999999999999999999,hello,0.100\n"
    )
    assert [col["type"] for col in parsed.columns] == ["text", "text", "decimal"]
    assert parsed.rows[0][1][2] == Decimal("12345678901234567890.12345678901234567890")
    assert parsed.rows[0][1][1] == "1" and parsed.warnings
    # Digest must not round through the default Decimal context.
    assert canonical_digest([[parsed.rows[0][1][2]]]) != canonical_digest(
        [[Decimal("12345678901234567890.12345678901234567891")]]
    )
    assert canonical_digest([[Decimal("1.00")]]) == canonical_digest([[Decimal("1.0")]])


def test_dates_timezones_and_empty_values():
    parsed = csv("day,ts,tz,empty\n2026-10-22,2026-10-22T12:30:00,2026-10-22T12:30:00+08:00,\n")
    assert [col["type"] for col in parsed.columns] == ["date", "datetime", "datetime_tz", "text"]
    assert parsed.rows[0][1][-1] is None
    aware = datetime(2026, 10, 22, 12, 30, tzinfo=UTC)
    assert canonical_digest([[aware]]) == canonical_digest([[aware.astimezone()]])


def test_column_mapping_is_unique_and_only_generated_names_are_overridden():
    parsed = csv("金额,id!,id?,1id,_row_number,column_001\n1,2,3,4,5,6\n")
    names = [col["name"] for col in parsed.columns]
    assert names == ["column_001", "id", "column_003", "column_004", "row_number", "column_006"]
    overridden = csv("id,amount\n001,12.30\n", type_overrides={"amount": "text"})
    assert overridden.rows[0][1] == ["001", "12.30"]
    for overrides in ({"id": "integer"}, {"missing": "text"}, {"id": "execute"}):
        with pytest.raises(DatasetError):
            csv("id\n001\n", type_overrides=overrides)


@pytest.mark.parametrize(
    "value", ["a,a\n1,2", "Ａ,a\n1,2", "a,\n1,2", "a,b\n1", "a\n", "a\n\x00", 'a\n"broken']
)
def test_invalid_csv_rejected(value):
    with pytest.raises(DatasetError):
        csv(value)


@pytest.mark.parametrize(
    "changes,value",
    [
        ({"import_max_rows": 1}, "a\n1\n2"),
        ({"import_max_columns": 1}, "a,b\n1,2"),
        ({"import_max_bytes": 3}, "a\n123"),
        ({"import_max_cell_chars": 3}, "a\n1234"),
    ],
)
def test_limits_are_applied(changes, value):
    with pytest.raises(DatasetError):
        parse_table(value.encode(), "a.csv", settings(**changes))


def test_encoding_delimiter_formula_text_and_preview_clipping():
    parsed = parse_table(
        "姓名;备注\n张三;=SUM(A1:A2)".encode("gb18030"),
        "a.csv",
        settings(),
        encoding="gb18030",
        delimiter=";",
    )
    assert parsed.rows[0][1] == ["张三", "=SUM(A1:A2)"]
    long = csv("a\n" + "字" * 300)
    assert len(long.preview[0][1]) == 201 and len(long.rows[0][1][0]) == 300
    with pytest.raises(DatasetError):
        parse_table(b"a\n1", "a.csv", settings(), encoding="utf-16")
    assert clean_filename("C:\\temp\\test.csv") == "test.csv"
    with pytest.raises(DatasetError):
        clean_filename("old.xls")


def test_xlsx_sheet_selection_and_native_types():
    raw = xlsx(
        [
            ["id", "amount", "flag", "day", "note"],
            ["001", 12.3, True, date(2026, 10, 22), None],
            ["002", 0.1, False, date(2026, 10, 23), "text"],
        ],
        second=[["region", "value"], ["east", 5]],
    )
    parsed = parse_table(raw, "table.xlsx", settings())
    assert parsed.sheet_names == ["主表", "第二表"] and parsed.selected_sheet == "主表"
    assert parsed.rows[0][1][:3] == ["001", Decimal("12.3"), True]
    assert parsed.rows[0][1][-1] is None
    other = parse_table(raw, "table.xlsx", settings(), sheet_name="第二表")
    assert other.rows[0][1] == ["east", 5]
    with pytest.raises(DatasetError):
        parse_table(raw, "table.xlsx", settings(), sheet_name="不存在")


def test_xlsx_formulas_errors_zip_size_and_xml_entities_are_rejected():
    for value in ("=SUM(A1:A2)", "#DIV/0!"):
        with pytest.raises(DatasetError):
            parse_table(xlsx([["a"], [value]]), "a.xlsx", settings())
    raw = xlsx([["a"], [1]])
    with pytest.raises(DatasetError):
        parse_table(raw, "a.xlsx", settings(import_xlsx_max_uncompressed_bytes=1))
    with pytest.raises(DatasetError):
        parse_table(b"not zip", "a.xlsx", settings())
    archive = io.BytesIO()
    with ZipFile(archive, "w") as book:
        book.writestr("xl/workbook.xml", b'<!DOCTYPE root [<!ENTITY x "bad">]><root/>')
    with pytest.raises(DatasetError):
        parse_table(archive.getvalue(), "a.xlsx", settings())


def test_missing_optional_excel_dependency_keeps_csv_available(monkeypatch):
    raw = xlsx([["a"], [1]])
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(ExcelDependencyMissing):
        parse_table(raw, "a.xlsx", settings())
    assert csv("a\n1").rows == [(2, [1])]
