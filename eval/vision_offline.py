"""Generate visible synthetic scan fixtures; --persist stores an explicitly prefilled draft."""

import argparse
import asyncio
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.session import init_engine
from app.vision.service import VisionService

ROOT = Path(__file__).resolve().parents[1]
RESULT = {
    "text": "合成演示：预置人工转录，非模型识别结果。金额单位元；第三行空白不能当作零。",
    "tables": [
        {
            "title": "合成区域金额（元）",
            "columns": ["external_id", "region_code", "amount_yuan"],
            "rows": [["001", "EAST", "12.30"], ["002", "NORTH", "0.10"], ["003", "WEST", None]],
            "notes": "金额单位元；外部编号保留前导零，空白为缺失值。",
        }
    ],
    "warnings": ["这是演示程序预置转录，不代表视觉模型准确率；发布前请核对原图。"],
}


def fixtures():
    target = ROOT / "tests/fixtures"
    target.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1300, 850), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = (
        ImageFont.truetype(str(font_path), 30)
        if font_path.exists()
        else ImageFont.load_default(size=30)
    )
    draw.text((60, 55), "SYNTHETIC DEMO - no real customer data", fill="#202e2a", font=font)
    draw.text((60, 120), "Region supplement | Amount unit: CNY / yuan", fill="#202e2a", font=font)
    rows = [
        ["external_id", "region_code", "amount_yuan"],
        ["001", "EAST", "12.30"],
        ["002", "NORTH", "0.10"],
        ["003", "WEST", ""],
    ]
    for i, row in enumerate(rows):
        y = 225 + i * 100
        draw.line((60, y - 20, 1230, y - 20), fill="#586963", width=2)
        for x, text in zip([70, 420, 850], row):
            draw.text((x, y), text, fill="#202e2a", font=font)
    draw.text(
        (60, 720), "Blank is missing, not zero. Preserve leading zeroes.", fill="#202e2a", font=font
    )
    png = target / "vision_synthetic.png"
    pdf = target / "vision_synthetic_scan.pdf"
    image.save(png, format="PNG")
    image.save(pdf, format="PDF", resolution=140)
    image.close()
    return png, pdf


async def main(persist):
    png, pdf = fixtures()
    output = {
        "model_api_calls": 0,
        "prefilled_transcription": True,
        "persisted": persist,
        "fixtures": [str(png.relative_to(ROOT)), str(pdf.relative_to(ROOT))],
    }
    if persist:
        values = (
            load_config()
        )  # Dedicated loopback database guard; never consult an arbitrary target.
        settings = Settings()
        engine = init_engine(values["DATABASE_URL"])
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            vision = VisionService(factory, settings, provider=None)
            draft = await vision.stage(png.read_bytes(), "合成演示-预置人工转录.png", "image/png")
            draft = await vision.edit(draft["id"], draft["revision"], 1, RESULT, manual=True)
            output["draft"] = draft
        finally:
            await engine.dispose()
    report = ROOT / "reports/vision-round3/demo.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"report": str(report.relative_to(ROOT)), "model_api_calls": 0, "persisted": persist}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.persist))
