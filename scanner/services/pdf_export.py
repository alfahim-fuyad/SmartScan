import io
from typing import Iterable, List

import cv2
import numpy as np
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from django.conf import settings

MAX_PAGE_POINTS = 2000.0


def bgr_to_pil(image: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def _page_size(pil_image: Image.Image) -> tuple:
    dpi = max(36, settings.SCANNER_PDF_DPI)
    width_pt = pil_image.width * 72.0 / dpi
    height_pt = pil_image.height * 72.0 / dpi
    longest = max(width_pt, height_pt)
    if longest > MAX_PAGE_POINTS:
        scale = MAX_PAGE_POINTS / longest
        width_pt, height_pt = width_pt * scale, height_pt * scale
    return width_pt, height_pt


def images_to_pdf(pil_images: Iterable[Image.Image]) -> bytes:
    pages: List[Image.Image] = [image.convert("RGB") for image in pil_images]
    if not pages:
        raise ValueError("No pages supplied to the PDF exporter.")

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    pdf.setTitle("Smart Document Scan")
    pdf.setAuthor("Smart Document Scanner — CSE 438 Lab Project")

    for page_image in pages:
        width_pt, height_pt = _page_size(page_image)
        pdf.setPageSize((width_pt, height_pt))
        pdf.drawImage(
            ImageReader(page_image),
            x=0,
            y=0,
            width=width_pt,
            height=height_pt,
            preserveAspectRatio=False,
            mask=None,
        )
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def arrays_to_pdf(bgr_images: Iterable[np.ndarray]) -> bytes:
    return images_to_pdf(bgr_to_pil(image) for image in bgr_images)
