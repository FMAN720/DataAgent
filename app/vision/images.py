"""Bounded local decoding; normalized PNG previews also form the API input."""

import io
import math
import threading
import warnings
from pathlib import Path, PurePosixPath

from app.vision.types import VisionError

MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}
_pdf_lock = threading.Lock()  # PDFium objects must not be accessed concurrently across threads.


def filename(value, mime=None):
    name = PurePosixPath((value or "").replace("\\", "/")).name
    name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127)
    suffix = Path(name).suffix.lower()
    if not name or len(name) > 255 or suffix not in MIMES:
        raise VisionError("仅支持 PNG、JPG、WEBP 和 PDF，文件名不超过 255 字符", 415)
    if mime and mime.split(";", 1)[0].lower() not in (MIMES[suffix], "application/octet-stream"):
        raise VisionError("文件类型与扩展名不匹配", 415)
    return name, MIMES[suffix]


def _image(raw, name, settings):
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
            try:
                expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[
                    Path(name).suffix.lower()
                ]
                if image.format != expected:
                    raise VisionError("图片实际格式与扩展名不符")
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width * height > settings.vision_max_pixels
                    or max(width, height) > 16000
                ):
                    raise VisionError("图片像素或边长超过限制，请缩小后上传")
                if getattr(image, "n_frames", 1) != 1:
                    raise VisionError("仅支持静态单帧图片")
                image.verify()
            finally:
                image.close()
            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
        return width, height
    except ImportError:
        raise VisionError(
            "图片解析库未安装，请先安装已批准的 requirements-vision.txt", 503
        ) from None
    except VisionError:
        raise
    except Exception:
        raise VisionError("图片损坏或无法安全读取") from None


def inspect_source(raw, name, settings):
    if not raw or len(raw) > settings.vision_max_bytes:
        raise VisionError("文件为空或超过图片/扫描件大小上限", 413)
    if name.lower().endswith(".pdf"):
        if not raw.startswith(b"%PDF-"):
            raise VisionError("不是有效的 PDF")
        try:
            import pypdfium2 as pdfium

            with _pdf_lock, pdfium.PdfDocument(raw) as document:
                count = len(document)
                if not 1 <= count <= settings.vision_max_pdf_pages:
                    raise VisionError("PDF 页数超过识别上限，请拆分文件")
                return count
        except ImportError:
            raise VisionError(
                "PDF 渲染库未安装，请先安装已批准的 requirements-vision.txt", 503
            ) from None
        except VisionError:
            raise
        except Exception:
            raise VisionError("PDF 损坏、加密或无法读取") from None
    _image(raw, name, settings)
    return 1


def page_png(raw, name, number, settings):
    count = inspect_source(raw, name, settings)
    if number < 1 or number > count:
        raise VisionError("页码超出范围")
    try:
        from PIL import Image, ImageOps

        if name.lower().endswith(".pdf"):
            import pypdfium2 as pdfium

            with _pdf_lock, pdfium.PdfDocument(raw) as document:
                page = document[number - 1]
                try:
                    width, height = page.get_size()
                    if not all(math.isfinite(v) and 0 < v <= 20000 for v in (width, height)):
                        raise VisionError("PDF 页面尺寸异常")
                    scale = min(2, 2048 / max(width, height))
                    bitmap = page.render(scale=scale, may_draw_forms=False)
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        else:
            with Image.open(io.BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGBA")
        try:
            image.thumbnail((2048, 2048))
            if image.mode in ("RGBA", "LA"):
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image.close()
                image = background
            elif image.mode != "RGB":
                converted = image.convert("RGB")
                image.close()
                image = converted
            output = io.BytesIO()
            image.save(output, format="PNG")
            content = output.getvalue()
            if len(content) > 8 * 1024 * 1024:
                raise VisionError("渲染后的图片过大，请拆分或降低分辨率")
            return content
        finally:
            image.close()
    except ImportError:
        raise VisionError("图片或 PDF 渲染依赖未安装", 503) from None
    except VisionError:
        raise
    except Exception:
        raise VisionError("页面渲染失败，请检查源文件") from None
