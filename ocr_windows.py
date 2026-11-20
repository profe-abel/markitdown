#!/usr/bin/env python3
"""
OCR local usando el motor de Windows (Windows.Media.Ocr).

No usa internet ni servicios externos: aprovecha los paquetes de idioma OCR
que Windows ya tiene instalados (aqui: es-ES, es-MX, zh-Hans-CN).

Uso:
    python ocr_windows.py archivo.pdf
    python ocr_windows.py foto.jpg --lang es-ES --dpi 300

Salida: Markdown por stdout (UTF-8).
Codigos de salida: 0 = ok, 1 = error, 2 = no hay motor OCR disponible.
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata

try:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapPixelFormat,
        SoftwareBitmap,
    )
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
except ImportError as exc:  # pragma: no cover
    print(f"No se pudieron cargar los bindings de Windows OCR: {exc}", file=sys.stderr)
    sys.exit(2)

try:
    import pypdfium2 as pdfium
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    print(f"Faltan dependencias (pypdfium2 / pillow): {exc}", file=sys.stderr)
    sys.exit(2)


PREFERRED_LANGS = ("es-ES", "es-MX", "en-US", "en-GB")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}


def pick_engine(lang: str | None):
    """Devuelve un OcrEngine en el idioma pedido o el mejor disponible."""
    if lang:
        engine = OcrEngine.try_create_from_language(Language(lang))
        if engine:
            return engine, lang
    for tag in PREFERRED_LANGS:
        engine = OcrEngine.try_create_from_language(Language(tag))
        if engine:
            return engine, tag
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine:
        return engine, str(engine.recognizer_language.language_tag)
    return None, None


def image_to_bitmap(img: Image.Image) -> SoftwareBitmap:
    """Convierte una imagen PIL a SoftwareBitmap BGRA8."""
    rgba = img.convert("RGBA")
    raw = rgba.tobytes("raw", "BGRA")
    writer = DataWriter()
    writer.write_bytes(raw)
    buffer = writer.detach_buffer()
    return SoftwareBitmap.create_copy_from_buffer(
        buffer,
        BitmapPixelFormat.BGRA8,
        rgba.width,
        rgba.height,
        BitmapAlphaMode.PREMULTIPLIED,
    )


def ocr_image(engine, img: Image.Image, max_side: int) -> list[str]:
    """OCR de una imagen; devuelve las lineas detectadas."""
    if img.width > max_side or img.height > max_side:
        ratio = max_side / max(img.width, img.height)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    bitmap = image_to_bitmap(img)
    result = engine.recognize_async(bitmap).get()
    return [line.text for line in result.lines if line.text.strip()]


def pdf_pages(path: str, dpi: int):
    """Genera imagenes PIL de cada pagina del PDF."""
    doc = pdfium.PdfDocument(path)
    scale = dpi / 72.0
    try:
        for page in doc:
            bitmap = page.render(scale=scale)
            yield bitmap.to_pil()
    finally:
        doc.close()


def join_lines(lines: list[str]) -> str:
    """Une lineas OCR en parrafos legibles."""
    paragraphs: list[str] = []
    current: list[str] = []
    for raw in lines:
        line = " ".join(raw.split())
        if not line:
            continue
        if current:
            prev = current[-1]
            ends_sentence = prev.endswith((".", "!", "?", ":", ";"))
            starts_upper = line[:1].isupper() or line[:1].isdigit()
            if ends_sentence or (starts_upper and len(prev) < 45):
                paragraphs.append(" ".join(current))
                current = []
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def clean(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR local con el motor de Windows")
    parser.add_argument("path", nargs="?", help="PDF o imagen a procesar")
    parser.add_argument("--lang", default=None, help="codigo de idioma, p.ej. es-ES")
    parser.add_argument("--dpi", type=int, default=250, help="resolucion para PDF (por defecto 250)")
    parser.add_argument("--no-page-markers", action="store_true", help="no insertar <!-- pagina N -->")
    parser.add_argument("--list-langs", action="store_true", help="listar idiomas OCR disponibles")
    args = parser.parse_args()

    if args.list_langs:
        langs = OcrEngine.available_recognizer_languages
        for lang in langs:
            sys.stdout.buffer.write((str(lang.language_tag) + "\n").encode("utf-8"))
        return 0

    if not args.path:
        parser.error("falta el archivo a procesar")
        return 2

    if not os.path.isfile(args.path):
        print(f"No existe el archivo: {args.path}", file=sys.stderr)
        return 1

    engine, used_lang = pick_engine(args.lang)
    if engine is None:
        print(
            "Windows no tiene ningun motor OCR instalado.\n"
            "Anade un idioma en Configuracion > Hora e idioma > Idioma.",
            file=sys.stderr,
        )
        return 2
    print(f"OCR: motor de Windows, idioma {used_lang}", file=sys.stderr)

    max_side = int(getattr(OcrEngine, "max_image_dimension", 4000) or 4000)
    ext = os.path.splitext(args.path)[1].lower()

    blocks: list[str] = []
    if ext in IMAGE_EXTS:
        with Image.open(args.path) as img:
            text = clean(join_lines(ocr_image(engine, img, max_side)))
        blocks.append(text)
    else:
        for index, page_img in enumerate(pdf_pages(args.path, args.dpi), start=1):
            lines = ocr_image(engine, page_img, max_side)
            text = clean(join_lines(lines))
            if not text:
                continue
            if args.no_page_markers:
                blocks.append(text)
            else:
                blocks.append(f"<!-- pagina {index} -->\n\n{text}")

    body = "\n\n".join(b for b in blocks if b).strip()
    if not body:
        print("El OCR no detecto texto.", file=sys.stderr)
        return 1

    sys.stdout.buffer.write((body + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
