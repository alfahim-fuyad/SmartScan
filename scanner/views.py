import json
import logging
import time
import uuid
from datetime import datetime

import cv2
import numpy as np
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.core.files.base import ContentFile
from django.db import connection
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .forms import RegisterForm, UploadForm
from .models import ScanHistory
from .services import quality as quality_service
from .services.detection import corners_valid, detect_document_corners
from .services.enhancement import (
    FILTER_LABELS,
    RESTORE_PRESET_LABELS,
    RESTORE_PRESETS,
    VALID_FILTERS,
    EnhancementParams,
    manual_enhance,
    restore_old_image,
)
from .services.pdf_export import arrays_to_pdf
from .services.pipeline import PipelineError, run_pipeline
from .services.validators import ImageValidationError, sanitize_filename, validate_upload_file

logger = logging.getLogger("scanner")


def _is_ajax(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _json_error(message: str, status: int = 400):
    return JsonResponse({"ok": False, "errors": [message]}, status=status)


def _ensure_session(request) -> str:
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _parse_json_body(request) -> dict:
    if request.content_type and "application/json" in request.content_type:
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Malformed JSON body.")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data
    return request.POST.dict()


def _parse_corners(raw) -> np.ndarray | None:
    if raw in (None, "", [], "null"):
        return None
    try:
        pts = np.array(json.loads(raw) if isinstance(raw, str) else raw, dtype=np.float64)
    except (ValueError, TypeError):
        raise ValueError("Corner data could not be parsed.")
    if pts.shape != (4, 2):
        raise ValueError("Exactly four corner points are required.")
    return pts.astype(np.float32)


def _load_source(scan: ScanHistory) -> np.ndarray:
    with scan.original_image.open("rb") as handle:
        data = handle.read()
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise PipelineError("The stored original image could not be decoded.")
    return image


def _encode(image: np.ndarray, fmt: str, quality: int) -> tuple[bytes, str, str]:
    if fmt == "png":
        _, buffer = cv2.imencode(".png", image)
        return buffer.tobytes(), "image/png", "png"
    if fmt == "pdf":
        return arrays_to_pdf([image]), "application/pdf", "pdf"
    _, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buffer.tobytes(), "image/jpeg", "jpg"


def _download_response(data: bytes, mime: str, extension: str, prefix: str = "smart-scan") -> HttpResponse:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}.{extension}"
    response = HttpResponse(data, content_type=mime)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(data))
    response["Cache-Control"] = "no-store"
    return response


def _save_preview(scan: ScanHistory, processed: np.ndarray) -> None:
    from .services.enhancement import _resize_if_needed

    def _replace(field_name: str, name: str, data: bytes) -> None:
        field = getattr(scan, field_name)
        if field and field.name:
            try:
                field.storage.delete(field.name)
            except (OSError, ValueError):
                pass
        field.save(name, ContentFile(data), save=False)

    preview = _resize_if_needed(processed, settings.SCANNER_PREVIEW_MAX_DIM)
    ok, buffer = cv2.imencode(
        ".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
    )
    if not ok:
        raise PipelineError("Could not encode the processed preview.")
    _replace(
        "processed_image",
        f"scan-{scan.pk}-preview-{uuid.uuid4().hex[:8]}.jpg",
        buffer.tobytes(),
    )
    scan.processed_width = int(processed.shape[1])
    scan.processed_height = int(processed.shape[0])

    thumb = _resize_if_needed(processed, 340)
    ok, tb = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    if ok:
        _replace(
            "thumb_image",
            f"scan-{scan.pk}-thumb-{uuid.uuid4().hex[:8]}.jpg",
            tb.tobytes(),
        )


def _scan_payload(scan: ScanHistory, include_state: bool = False) -> dict:
    payload = {
        "id": scan.pk,
        "filename": scan.display_name,
        "original_url": scan.original_image.url,
        "thumb_url": scan.thumb_image.url if scan.thumb_image else scan.original_image.url,
        "width": scan.original_width,
        "height": scan.original_height,
        "file_size": scan.original_file_size,
        "status": scan.status,
        "has_processed": scan.has_processed,
        "processed_url": scan.processed_image.url if scan.has_processed else "",
        "processed_width": scan.processed_width,
        "processed_height": scan.processed_height,
        "filter": scan.filter_used or "original",
    }
    if include_state:
        payload["corners"] = scan.corners_json
        payload["params"] = scan.params_json or {}
    return payload


@ensure_csrf_cookie
def home(request):
    form = UploadForm()
    max_mb = settings.SCANNER_MAX_UPLOAD_SIZE // (1024 * 1024)
    return render(request, "home.html", {"upload_form": form, "max_upload_mb": max_mb})


@ensure_csrf_cookie
def scanner_view(request):
    scans = list(ScanHistory.owned(request).order_by("page_order", "-created_at")[: settings.SCANNER_MAX_PAGES])
    initial = None
    requested_id = request.GET.get("scan")
    if requested_id and requested_id.isdigit():
        chosen = next((s for s in scans if s.pk == int(requested_id)), None)
        if chosen:
            initial = _scan_payload(chosen, include_state=True)
            initial["quality"] = None

    pages = [_scan_payload(scan, include_state=True) for scan in scans]
    config = {
        "filters": [{"id": f, "label": FILTER_LABELS[f]} for f in VALID_FILTERS],
        "restorePresets": [
            {"id": p, "label": RESTORE_PRESET_LABELS[p]} for p in RESTORE_PRESETS
        ],
        "maxUploadBytes": settings.SCANNER_MAX_UPLOAD_SIZE,
        "maxPages": settings.SCANNER_MAX_PAGES,
        "debounceMs": 420,
        "uploadUrl": reverse("scanner:upload"),
        "detectUrlTemplate": reverse("scanner:api_detect", args=[0]).replace("/0/", "/{0}/"),
        "processUrlTemplate": reverse("scanner:api_process", args=[0]).replace("/0/", "/{0}/"),
        "exportUrlTemplate": reverse("scanner:api_export", args=[0]).replace("/0/", "/{0}/"),
        "enhanceUrlTemplate": reverse("scanner:api_enhance", args=[0]).replace("/0/", "/{0}/"),
        "deleteUrlTemplate": reverse("scanner:api_scan_delete", args=[0]).replace("/0/", "/{0}/"),
        "pagesUrl": reverse("scanner:api_pages"),
        "reorderUrl": reverse("scanner:api_pages_reorder"),
        "pdfMultiUrl": reverse("scanner:api_pdf_multi"),
    }
    response = render(
        request,
        "scanner.html",
        {"initial": initial, "pages": pages, "config": config},
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@ensure_csrf_cookie
def enhancer_view(request):
    max_mb = settings.SCANNER_MAX_UPLOAD_SIZE // (1024 * 1024)
    scans = list(ScanHistory.owned(request).order_by("-created_at")[:12])
    initial = None
    requested_id = request.GET.get("scan")
    if requested_id and requested_id.isdigit():
        chosen = next((s for s in scans if s.pk == int(requested_id)), None)
        if chosen:
            initial = _scan_payload(chosen, include_state=False)

    config = {
        "enhancePresets": [
            {"id": p, "label": RESTORE_PRESET_LABELS[p]} for p in RESTORE_PRESETS
        ],
        "maxUploadBytes": settings.SCANNER_MAX_UPLOAD_SIZE,
        "uploadUrl": reverse("scanner:upload"),
        "enhanceUrlTemplate": reverse("scanner:api_enhance", args=[0]).replace("/0/", "/{0}/"),
        "deleteUrlTemplate": reverse("scanner:api_scan_delete", args=[0]).replace("/0/", "/{0}/"),
    }
    response = render(
        request,
        "enhancer.html",
        {
            "initial": initial,
            "config": config,
            "max_upload_mb": max_mb,
        },
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


def history_view(request):
    scans = list(
        ScanHistory.owned(request).order_by("-created_at")[:120]
    )
    items = []
    for scan in scans:
        item = _scan_payload(scan)
        item["created_at"] = scan.created_at
        item["export_format"] = scan.export_format
        item["download_url"] = reverse("scanner:processed_download", args=[scan.pk])
        items.append(item)
    return render(request, "history.html", {"items": items})


def about_view(request):
    return render(request, "about.html")


def handler404(request, exception=None):
    return render(request, "404.html", status=404)


def handler500(request):
    return render(request, "500.html", status=500)


@require_GET
def health(request):
    database = "ok"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        logger.warning("Health check DB failure: %s", exc.__class__.__name__)
        database = "error"
    payload = {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "app": "smart-document-scanner",
        "time": datetime.utcnow().isoformat() + "Z",
    }
    return JsonResponse(payload, status=200 if database == "ok" else 503)


@require_POST
@csrf_protect
def upload_view(request):
    _ensure_session(request)
    ajax = _is_ajax(request)
    uploaded = request.FILES.get("image")
    if uploaded is None:
        if ajax:
            return _json_error("No image file was received.")
        messages.error(request, "Please choose an image to scan.")
        return redirect("scanner:home")

    try:
        decoded = validate_upload_file(uploaded)
    except ImageValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        if ajax:
            return _json_error(message)
        messages.error(request, message)
        return redirect("scanner:home")

    existing = ScanHistory.owned(request).count()
    if existing >= settings.SCANNER_MAX_PAGES:
        note = f"Page limit reached ({settings.SCANNER_MAX_PAGES}). Delete a page first."
        if ajax:
            return _json_error(note, status=400)
        messages.error(request, note)
        return redirect("scanner:home")

    scan = ScanHistory(
        original_filename=sanitize_filename(uploaded.name or "upload"),
        original_width=decoded["width"],
        original_height=decoded["height"],
        user=request.user if request.user.is_authenticated else None,
        session_key=request.session.session_key,
        status=ScanHistory.STATUS_COMPLETED,
        page_order=existing,
    )
    uploaded.seek(0)
    raw_bytes = uploaded.read()
    scan.file_size = len(raw_bytes)
    extension = {"JPEG": ".jpg", "PNG": ".png", "WebP": ".webp"}.get(decoded["format"], ".jpg")
    scan.original_image.save(
        f"orig-{uuid.uuid4().hex}{extension}",
        ContentFile(raw_bytes),
        save=False,
    )

    report = quality_service.assess_quality(decoded["image"])
    scan.save()


    thumb = cv2.resize(
        decoded["image"],
        None,
        fx=min(1.0, 340 / max(decoded["image"].shape[:2])),
        fy=min(1.0, 340 / max(decoded["image"].shape[:2])),
        interpolation=cv2.INTER_AREA,
    )
    ok, tb = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    if ok:
        scan.thumb_image.save(f"thumb-{scan.pk}-{uuid.uuid4().hex[:8]}.jpg", ContentFile(tb.tobytes()), save=True)

    if ajax:
        payload = _scan_payload(scan)
        payload.update({"ok": True, "quality": report})
        return JsonResponse(payload)
    return redirect(f"{reverse('scanner:scanner_view')}?scan={scan.pk}")


@require_POST
@csrf_protect
def api_detect(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    try:
        image = _load_source(scan)
    except PipelineError as exc:
        logger.warning("detect: source decode failed for scan %s", scan.pk)
        return _json_error(str(exc), status=422)
    result = detect_document_corners(image)
    corners = None
    if result["corners"] is not None:
        corners = [[round(float(x), 2), round(float(y), 2)] for x, y in result["corners"]]
    return JsonResponse(
        {
            "ok": True,
            "detected": corners is not None,
            "corners": corners,
            "message": result["message"],
            "width": scan.original_width,
            "height": scan.original_height,
        }
    )


@require_POST
@csrf_protect
def api_process(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    try:
        body = _parse_json_body(request)
    except ValueError as exc:
        return _json_error(str(exc))

    try:
        corners = _parse_corners(body.get("corners"))
        perspective = bool(body.get("perspective", corners is not None))
    except ValueError as exc:
        return _json_error(str(exc))

    params = EnhancementParams.from_dict(body.get("params") or body)
    filter_name = body.get("filter", "original")
    if filter_name not in VALID_FILTERS:
        return _json_error(f"Unknown filter “{filter_name}”.")

    try:
        source = _load_source(scan)
    except PipelineError as exc:
        return _json_error(str(exc), status=422)

    height, width = source.shape[:2]
    if corners is not None:
        valid, reason = corners_valid(corners, width, height)
        if not valid:
            return _json_error(reason or "Invalid corners.")

    started = time.perf_counter()
    try:
        processed = run_pipeline(
            source,
            corners=corners,
            params=params,
            filter_name=filter_name,
            perspective=perspective,
            max_dim=settings.SCANNER_PROCESS_MAX_DIM,
        )
    except PipelineError as exc:
        scan.status = ScanHistory.STATUS_FAILED
        scan.save(update_fields=["status", "updated_at"])
        return _json_error(str(exc), status=400)

    try:
        _save_preview(scan, processed)
    except (OSError, PipelineError):
        logger.exception("process: preview persist failed for scan %s", scan.pk)
        return _json_error("Could not store the processed preview.", status=500)

    scan.filter_used = filter_name
    scan.params_json = params.as_dict()
    scan.corners_json = (
        [[round(float(x), 2), round(float(y), 2)] for x, y in corners]
        if corners is not None and perspective
        else None
    )
    scan.status = ScanHistory.STATUS_COMPLETED
    scan.save()

    payload = {
        "ok": True,
        "processed_url": f"{scan.processed_image.url}?v={int(time.time())}",
        "width": scan.processed_width,
        "height": scan.processed_height,
        "filter": scan.filter_used,
        "params": scan.params_json,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
    return JsonResponse(payload)


@require_POST
@csrf_protect
def api_export(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    try:
        body = _parse_json_body(request)
    except ValueError as exc:
        return _json_error(str(exc))

    fmt = str(body.get("format", "jpg")).lower()
    if fmt not in {"jpg", "png", "pdf"}:
        return _json_error("Export format must be JPG, PNG or PDF.")
    try:
        quality = int(body.get("quality", settings.SCANNER_JPEG_QUALITY))
    except (TypeError, ValueError):
        quality = settings.SCANNER_JPEG_QUALITY
    quality = max(60, min(96, quality))

    corners = None
    try:
        corners = _parse_corners(body.get("corners"))
    except ValueError as exc:
        return _json_error(str(exc))
    if corners is None and scan.corners_json:
        corners = np.array(scan.corners_json, dtype=np.float32)
    perspective = bool(body.get("perspective", corners is not None))

    params = EnhancementParams.from_dict(body.get("params") or scan.params_json)
    filter_name = body.get("filter") or scan.filter_used or "original"
    if filter_name not in VALID_FILTERS:
        return _json_error("Unknown filter for export.")

    try:
        source = _load_source(scan)
    except PipelineError as exc:
        return _json_error(str(exc), status=422)

    height, width = source.shape[:2]
    if corners is not None and perspective:
        valid, reason = corners_valid(corners, width, height)
        if not valid:
            return _json_error(reason or "Invalid corners.")

    try:
        processed = run_pipeline(
            source,
            corners=corners,
            params=params,
            filter_name=filter_name,
            perspective=perspective,
            max_dim=settings.SCANNER_EXPORT_MAX_DIM,
        )
        data, mime, extension = _encode(processed, "pdf" if fmt == "pdf" else fmt, quality)
    except PipelineError as exc:
        return _json_error(str(exc), status=400)
    except MemoryError:
        return _json_error("The image is too large to export with current settings.", status=400)

    scan.export_format = fmt
    scan.save(update_fields=["export_format", "updated_at"])
    return _download_response(data, mime, extension)


@require_POST
@csrf_protect
def api_enhance(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    try:
        body = _parse_json_body(request)
    except ValueError as exc:
        return _json_error(str(exc))

    mode = str(body.get("mode", "auto")).lower()
    if mode not in ("auto", "manual"):
        return _json_error("Unknown enhancement mode.")
    output_style = str(body.get("output_style", body.get("style", "colorful"))).lower()
    if output_style not in ("colorful", "grayscale"):
        return _json_error("Output style must be colorful or grayscale.")

    fmt = str(body.get("format", "jpg")).lower()
    if fmt not in {"jpg", "png"}:
        return _json_error("Enhanced export format must be JPG or PNG.")

    try:
        source = _load_source(scan)
    except PipelineError as exc:
        return _json_error(str(exc), status=422)

    max_dim = settings.SCANNER_PROCESS_MAX_DIM
    height, width = source.shape[:2]
    longest = max(height, width)
    if longest > max_dim:
        scale = max_dim / float(longest)
        source = cv2.resize(
            source,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )

    grayscale = output_style == "grayscale"

    try:
        if mode == "manual":
            enhanced = manual_enhance(
                source,
                brightness=float(body.get("brightness", 0.0)),
                contrast=float(body.get("contrast", 0.0)),
                sharpness=float(body.get("sharpness", 0.0)),
                noise=float(body.get("noise", 0.0)),
                saturation=float(body.get("saturation", 0.0)),
                strength=float(body.get("strength", 0.5)),
                color_correction=bool(body.get("color_correction", False)),
                clahe=bool(body.get("clahe", True)),
                grayscale=grayscale,
            )
        else:
            preset = str(body.get("preset", "auto")).lower()
            if preset not in RESTORE_PRESETS:
                return _json_error("Unknown enhancement preset.")
            try:
                strength = float(body.get("strength", 0.5))
            except (TypeError, ValueError):
                strength = 0.5
            strength = max(0.0, min(1.0, strength))
            enhanced = restore_old_image(source, preset, strength)
            if grayscale:
                enhanced = cv2.cvtColor(
                    cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR
                )
    except (ValueError, cv2.error) as exc:
        logger.warning("enhance: restore failed for scan %s: %s", scan.pk, exc)
        return _json_error("Could not enhance this image. Try different settings.", status=422)
    except MemoryError:
        return _json_error("The image is too large to enhance with current settings.", status=400)

    if min(enhanced.shape[:2]) < 24:
        return _json_error("The enhanced result is too small.", status=422)

    try:
        data, mime, extension = _encode(enhanced, fmt, settings.SCANNER_JPEG_QUALITY)
    except cv2.error:
        return _json_error("Could not encode the enhanced image.", status=500)

    prefix = f"enhanced-{mode}-{output_style}"
    return _download_response(data, mime, extension, prefix=prefix)


@require_POST
@csrf_protect
def api_pdf_multi(request):
    try:
        body = _parse_json_body(request)
    except ValueError as exc:
        return _json_error(str(exc))

    raw_ids = body.get("scan_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [part for part in raw_ids.split(",") if part.strip().isdigit()]
    try:
        ids = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        return _json_error("Invalid page list.")
    if not ids:
        return _json_error("Select at least one page to export.")
    if len(ids) > settings.SCANNER_MAX_PDF_PAGES:
        return _json_error(f"A PDF can contain at most {settings.SCANNER_MAX_PDF_PAGES} pages.")

    scans = list(
        ScanHistory.owned(request).filter(pk__in=ids).order_by("page_order", "-created_at")
    )
    found = {scan.pk for scan in scans}
    if len(found) != len(set(ids)):
        return _json_error("One or more pages could not be found.", status=404)

    by_id = {scan.pk: scan for scan in scans}
    ordered = [by_id[i] for i in ids if i in by_id]

    images = []
    for scan in ordered:
        try:
            source = _load_source(scan)
        except PipelineError:
            logger.warning("pdf-multi: skipping unreadable scan %s", scan.pk)
            continue
        corners = (
            np.array(scan.corners_json, dtype=np.float32)
            if scan.corners_json
            else None
        )
        params = EnhancementParams.from_dict(scan.params_json)
        try:
            processed = run_pipeline(
                source,
                corners=corners,
                params=params,
                filter_name=scan.filter_used or "original",
                perspective=corners is not None,
                max_dim=settings.SCANNER_MULTIPDF_MAX_DIM,
            )
        except PipelineError:
            logger.warning("pdf-multi: pipeline failed for scan %s", scan.pk)
            continue
        images.append(processed)

    if not images:
        return _json_error("No pages could be processed for the PDF.", status=422)

    try:
        pdf_bytes = arrays_to_pdf(images)
    except (ValueError, OSError):
        logger.exception("pdf-multi: reportlab failure")
        return _json_error("PDF generation failed. Please try again.", status=500)

    for scan in ordered:
        ScanHistory.objects.filter(pk=scan.pk).update(export_format="pdf")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"smart-scan-pages-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def processed_download(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    if not scan.has_processed:
        return _json_error("This scan has not been processed yet.", status=404)
    try:
        handle = scan.processed_image.open("rb")
    except (OSError, ValueError):
        return _json_error("The processed file is no longer available.", status=404)
    response = FileResponse(handle, content_type="image/jpeg")
    safe = f"scan-{scan.pk}.jpg"
    response["Content-Disposition"] = f'attachment; filename="{safe}"'
    return response


@require_GET
def api_pages(request):
    scans = list(
        ScanHistory.owned(request).order_by("page_order", "-created_at")[
            : settings.SCANNER_MAX_PAGES
        ]
    )
    return JsonResponse(
        {"ok": True, "pages": [_scan_payload(scan, include_state=True) for scan in scans]}
    )


@require_POST
@csrf_protect
def api_pages_reorder(request):
    try:
        body = _parse_json_body(request)
        ids = [int(value) for value in (body.get("order") or [])]
    except (ValueError, TypeError):
        return _json_error("Invalid order payload.")
    owned = ScanHistory.owned(request).filter(pk__in=ids)
    found = {scan.pk for scan in owned}
    if len(found) != len(set(ids)):
        return _json_error("Page list does not match your scans.", status=404)
    for index, scan_id in enumerate(ids):
        ScanHistory.objects.filter(pk=scan_id).update(page_order=index)
    return JsonResponse({"ok": True})


@require_POST
@csrf_protect
def api_scan_delete(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    scan.delete()
    return JsonResponse({"ok": True})


@require_POST
@csrf_protect
def history_delete(request, scan_id: int):
    scan = get_object_or_404(ScanHistory.owned(request), pk=scan_id)
    scan.delete()
    messages.success(request, "Scan deleted.")
    return redirect("scanner:history")


@require_POST
@csrf_protect
def history_clear_all(request):
    scans = list(ScanHistory.owned(request))
    for scan in scans:
        scan.delete()
    messages.success(request, f"Cleared {len(scans)} scan(s) from your history.")
    return redirect("scanner:history")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("scanner:scanner_view")
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            old_key = request.session.session_key
            user = form.save()
            auth_login(request, user)
            if old_key:
                ScanHistory.objects.filter(session_key=old_key, user__isnull=True).update(
                    user=user, session_key=""
                )
            messages.success(request, f"Welcome, {user.username}! Account created.")
            return redirect("scanner:scanner_view")
    else:
        form = RegisterForm()
    return render(request, "registration/register.html", {"form": form})


class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        old_key = self.request.session.session_key
        response = super().form_valid(form)
        if old_key:
            ScanHistory.objects.filter(session_key=old_key, user__isnull=True).update(
                user=self.request.user
            )
        return response
