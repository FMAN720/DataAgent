"""Strict recognition output: text and literal cells, never executable content."""

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VisionError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class LiteralTable(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    columns: list[str] = Field(min_length=1, max_length=50)
    rows: list[list[str | None]] = Field(max_length=500)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_shape(self):
        if any(not name.strip() or len(name) > 200 for name in self.columns):
            raise ValueError("表头为空或过长")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("表头重复，请人工改为不同名称")
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("表格列数不一致")
        if any(value is not None and len(value) > 2000 for row in self.rows for value in row):
            raise ValueError("单元格过长")
        return self


class PageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=50_000)
    tables: list[LiteralTable] = Field(default_factory=list, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def bounded(self):
        strings = [self.text, *self.warnings]
        for table in self.tables:
            strings.extend(
                [
                    table.title,
                    table.notes,
                    *table.columns,
                    *[value for row in table.rows for value in row if value is not None],
                ]
            )
        if any("\x00" in value for value in strings):
            raise ValueError("转录文字包含空字节，请移除后重试")
        if (
            any(len(value) > 2000 for value in self.warnings)
            or len(json.dumps(self.model_dump(), ensure_ascii=False)) > 120_000
        ):
            raise ValueError("识别结果过大，请拆分图片")
        if not self.text.strip() and not self.tables:
            raise ValueError("未识别到文字或表格")
        return self
