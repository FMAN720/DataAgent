"""Real image/PDF decoders and mocked SiliconFlow transport, no paid requests."""

import asyncio
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from app.config import Settings
from app.vision.images import filename, inspect_source, page_png
from app.vision.provider import VisionProvider
from app.vision.types import PageResult, VisionError
from tests.conftest import run
from tests.test_phase5_documents import pdf_bytes

RESULT = {
    "text": "合成表：金额单位元",
    "tables": [
        {
            "title": "合成金额",
            "columns": ["编号", "金额（元）"],
            "rows": [["001", "12.30"], ["002", None]],
            "notes": "空白不是零",
        }
    ],
    "warnings": ["仅为转录，请人工核对"],
}


def png(size=(120, 80), format="PNG"):
    stream = io.BytesIO()
    with Image.new("RGB", size, "white") as image:
        image.save(stream, format=format)
    return stream.getvalue()


def configured(**changes):
    return Settings(
        _env_file=None,
        vision_enabled=True,
        vision_api_key="synthetic-test-key",
        vision_model="synthetic-vision",
        **changes,
    )


@pytest.mark.parametrize(
    "suffix,mime,format",
    [("png", "image/png", "PNG"), ("jpg", "image/jpeg", "JPEG"), ("webp", "image/webp", "WEBP")],
)
def test_images_are_verified_and_normalized_without_metadata(suffix, mime, format):
    settings = Settings(_env_file=None)
    name, _ = filename("C:/fakepath/合成." + suffix, mime)
    raw = png(format=format)
    assert inspect_source(raw, name, settings) == 1
    normalized = page_png(raw, name, 1, settings)
    with Image.open(io.BytesIO(normalized)) as image:
        assert image.format == "PNG" and image.mode == "RGB" and not image.getexif()


@pytest.mark.parametrize(
    "name,mime,raw",
    [
        ("a.svg", "image/svg+xml", b"<svg/>"),
        ("a.png", "image/png", b"<script>bad</script>"),
        ("a.jpg", "image/jpeg", png()),
        ("a.pdf", "application/pdf", b"bad"),
    ],
)
def test_fake_or_unsupported_images_are_rejected(name, mime, raw):
    with pytest.raises(VisionError):
        clean, _ = filename(name, mime)
        inspect_source(raw, clean, Settings(_env_file=None))


def test_image_pixel_and_upload_limits_are_enforced():
    with pytest.raises(VisionError):
        inspect_source(png((101, 101)), "x.png", Settings(_env_file=None, vision_max_pixels=10000))
    with pytest.raises(VisionError):
        inspect_source(png(), "x.png", Settings(_env_file=None, vision_max_bytes=10))


def test_pdf_render_retains_real_page_order_and_rejects_invalid_ranges():
    settings = Settings(_env_file=None)
    raw = pdf_bytes("Page One", "Page Two")
    assert inspect_source(raw, "scan.pdf", settings) == 2
    first, second = (page_png(raw, "scan.pdf", n, settings) for n in (1, 2))
    assert first != second and first.startswith(b"\x89PNG")
    with pytest.raises(VisionError):
        page_png(raw, "scan.pdf", 3, settings)
    with pytest.raises(VisionError):
        inspect_source(raw, "scan.pdf", Settings(_env_file=None, vision_max_pdf_pages=1))
    with pytest.raises(VisionError):
        inspect_source(pdf_bytes("x", encrypted=True), "scan.pdf", settings)


def test_real_image_only_scan_has_no_text_but_can_be_rendered():
    from pypdf import PdfReader

    stream = io.BytesIO()
    with Image.new("RGB", (500, 700), "white") as image:
        image.save(stream, format="PDF")
    raw = stream.getvalue()
    assert not PdfReader(io.BytesIO(raw)).pages[0].extract_text()
    assert page_png(raw, "scan.pdf", 1, Settings(_env_file=None)).startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "change",
    [
        {"text": "", "tables": []},
        {"tables": [{"title": "x", "columns": ["a", "a"], "rows": []}]},
        {"tables": [{"title": "x", "columns": ["a"], "rows": [["x", "y"]]}]},
        {"tables": [{"title": "x", "columns": ["a"], "rows": [[12.3]]}]},
        {"unknown": "execute"},
    ],
)
def test_extraction_schema_rejects_ambiguous_numeric_or_malformed_results(change):
    with pytest.raises(ValidationError):
        PageResult.model_validate({**RESULT, **change})


def test_transport_sends_only_one_image_without_tools_and_preserves_exact_cells():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(RESULT)}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 20},
            },
        )

    async def scenario():
        provider = VisionProvider(configured(), transport=httpx.MockTransport(handler))
        try:
            value, usage = await provider.extract(png())
            assert (
                value["tables"][0]["rows"][0] == ["001", "12.30"]
                and value["tables"][0]["rows"][1][1] is None
            )
            assert usage["usage_complete"] and usage["requests"] == 1
        finally:
            await provider.aclose()

    run(scenario())
    assert len(calls) == 1
    assert "tools" not in calls[0] and "response_format" not in calls[0]
    assert calls[0]["messages"][0]["content"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.parametrize("status", [302, 401, 402, 429, 500])
def test_remote_errors_do_not_retry_or_expose_provider_details(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "sensitive-provider-body"})

    async def scenario():
        provider = VisionProvider(configured(), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(VisionError) as error:
                await provider.extract(png())
            assert "sensitive-provider-body" not in str(error.value)
        finally:
            await provider.aclose()

    run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "reason,content",
    [("length", json.dumps(RESULT)), ("stop", "not json"), ("stop", '{"text":"ok","tables":[{}]}')],
)
def test_truncated_or_invalid_output_is_never_adopted(reason, content):
    async def scenario():
        provider = VisionProvider(
            configured(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"choices": [{"finish_reason": reason, "message": {"content": content}}]},
                )
            ),
        )
        try:
            with pytest.raises(VisionError):
                await provider.extract(png())
        finally:
            await provider.aclose()

    run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"vision_enabled": False},
        {"vision_api_base": "http://api.siliconflow.cn/v1"},
        {"vision_api_base": "https://name:secret@another.example/v1"},
        {"vision_api_key": ""},
        {"vision_model": ""},
    ],
)
def test_incomplete_or_unsafe_endpoint_cannot_create_client(changes):
    values = {"vision_enabled": True, "vision_api_key": "test-key", "vision_model": "fake"}
    values.update(changes)
    with pytest.raises(VisionError):
        VisionProvider(Settings(_env_file=None, **values))


def test_total_request_deadline_does_not_resubmit_after_an_unknown_billable_result():
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={})

    async def scenario():
        provider = VisionProvider(
            configured(vision_timeout_seconds=0.01), transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(VisionError, match="未自动重试"):
                await provider.extract(png())
        finally:
            await provider.aclose()

    run(scenario())
    assert len(calls) == 1
