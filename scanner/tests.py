import cv2
import numpy as np
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from unittest import mock
import os

from .models import ScanHistory
from .services.detection import (
    corners_valid,
    detect_document_corners,
    is_convex_quad,
    order_points,
    quad_area,
)
from .services.enhancement import (
    EnhancementParams,
    apply_enhancement,
    apply_filter,
)
from .services.pipeline import PipelineError, run_pipeline
from .services.perspective import destination_size, warp_document
from .services.quality import assess_quality
from .services.validators import (
    ImageValidationError,
    decode_image,
    sanitize_filename,
)

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def make_document_photo(width=900, height=700, angle=4.0, seed=7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    paper_w, paper_h = int(width * 0.62), int(height * 0.74)
    paper = np.full((paper_h, paper_w, 3), 236, dtype=np.uint8)
    for i in range(9):
        y = 40 + i * int((paper_h - 80) / 9)
        length = rng.integers(paper_w // 3, int(paper_w * 0.8))
        cv2.line(paper, (40, y), (40 + length, y), (60, 60, 65), 3)

    scene = np.full((height, width, 3), 38, dtype=np.uint8)
    scene += rng.integers(0, 14, scene.shape, dtype=np.uint8)
    x0, y0 = (width - paper_w) // 2, (height - paper_h) // 2

    matrix = cv2.getRotationMatrix2D((paper_w / 2, paper_h / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        paper, matrix, (paper_w, paper_h),
        borderValue=(38, 38, 38), flags=cv2.INTER_LINEAR,
    )
    mask = np.any(rotated != (38, 38, 38), axis=2)
    region = scene[y0 : y0 + paper_h, x0 : x0 + paper_w]
    region[mask] = rotated[mask]
    scene[y0 : y0 + paper_h, x0 : x0 + paper_w] = region
    return scene


def make_flat_image(width=640, height=480, level=180) -> np.ndarray:
    image = np.full((height, width, 3), level, dtype=np.uint8)
    return cv2.GaussianBlur(image, (9, 9), 0)


def png_upload(image: np.ndarray, name="document.png") -> SimpleUploadedFile:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return SimpleUploadedFile(name, buffer.tobytes(), content_type="image/png")


def jpg_upload(image: np.ndarray, name="photo.jpg") -> SimpleUploadedFile:
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return SimpleUploadedFile(name, buffer.tobytes(), content_type="image/jpeg")


def upload_image(client: Client, image, name="document.png") -> dict:
    if name.endswith(".jpg"):
        f = jpg_upload(image, name)
    else:
        f = png_upload(image, name)
    response = client.post(reverse("scanner:upload"), {"image": f}, **AJAX)
    return response


class PageTests(TestCase):
    def test_homepage_renders(self):
        response = self.client.get(reverse("scanner:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turn Any Document Into a Perfect Scan")
        self.assertContains(response, "How It Works")

    def test_scanner_page_renders(self):
        response = self.client.get(reverse("scanner:scanner_view"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scanner Workspace")

    def test_about_renders(self):
        response = self.client.get(reverse("scanner:about"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CSE 438")

    def test_history_renders_empty_for_anonymous(self):
        response = self.client.get(reverse("scanner:history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No scans yet")

    def test_health_endpoint(self):
        response = self.client.get(reverse("scanner:health"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database"], "ok")

    def test_404_page(self):
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)


class UploadTests(TestCase):
    def test_valid_png_upload_creates_record(self):
        response = upload_image(self.client, make_document_photo())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("id", data)
        self.assertTrue(ScanHistory.objects.filter(pk=data["id"]).exists())
        record = ScanHistory.objects.get(pk=data["id"])
        self.assertEqual(record.original_width, 900)
        self.assertEqual(record.original_height, 700)
        self.assertGreater(record.original_file_size, 0)
        self.assertEqual(record.status, ScanHistory.STATUS_COMPLETED)
        self.assertTrue(record.session_key)

    def test_valid_jpg_upload(self):
        response = upload_image(self.client, make_document_photo(600, 500), name="doc.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_homepage_form_post_redirects_to_scanner(self):
        f = png_upload(make_document_photo())
        response = self.client.post(reverse("scanner:upload"), {"image": f})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/scanner/?scan=", response["Location"])

    def test_invalid_extension_rejected(self):
        fake = SimpleUploadedFile("notes.txt", b"just text, not an image", content_type="text/plain")
        response = self.client.post(reverse("scanner:upload"), {"image": fake}, **AJAX)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_corrupt_image_rejected(self):
        fake = SimpleUploadedFile("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")
        response = self.client.post(reverse("scanner:upload"), {"image": fake}, **AJAX)
        self.assertEqual(response.status_code, 400)

    def test_fake_extension_mismatch_rejected(self):

        fake = SimpleUploadedFile("sneaky.png", b"definitely not a png", content_type="image/png")
        response = self.client.post(reverse("scanner:upload"), {"image": fake}, **AJAX)
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_rejected(self):
        image = make_document_photo()
        ok, buffer = cv2.imencode(".png", image)
        payload = buffer.tobytes() + b"\x00" * (300 * 1024)
        with override_settings(SCANNER_MAX_UPLOAD_SIZE=128 * 1024):
            f = SimpleUploadedFile("big.png", payload, content_type="image/png")
            response = self.client.post(reverse("scanner:upload"), {"image": f}, **AJAX)
        self.assertEqual(response.status_code, 400)

    def test_missing_file_rejected(self):
        response = self.client.post(reverse("scanner:upload"), {}, **AJAX)
        self.assertEqual(response.status_code, 400)

    def test_no_file_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        f = png_upload(make_document_photo())
        response = client.post(reverse("scanner:upload"), {"image": f}, **AJAX)
        self.assertEqual(response.status_code, 403)

    def test_page_limit_enforced(self):
        with override_settings(SCANNER_MAX_PAGES=2):
            for _ in range(2):
                response = upload_image(self.client, make_document_photo())
                self.assertEqual(response.status_code, 200)
            response = upload_image(self.client, make_document_photo())
            self.assertEqual(response.status_code, 400)
            self.assertIn("limit", response.json()["errors"][0].lower())


class DetectionTests(TestCase):
    def test_detects_document_on_dark_background(self):
        image = make_document_photo(angle=4.0)
        result = detect_document_corners(image)
        self.assertIsNotNone(result["corners"], result["message"])
        corners = result["corners"]
        self.assertEqual(corners.shape, (4, 2))
        h, w = image.shape[:2]
        for x, y in corners:
            self.assertGreaterEqual(x, -2)
            self.assertLessEqual(x, w + 2)
            self.assertGreaterEqual(y, -2)
            self.assertLessEqual(y, h + 2)

        self.assertGreater(quad_area(corners), 0.2 * w * h)

    def test_flat_image_detection_fails_gracefully(self):
        image = make_flat_image()
        result = detect_document_corners(image)
        self.assertIsNone(result["corners"])
        self.assertTrue(result["message"])

    def test_order_points_assigns_expected_corners(self):
        pts = np.array([[2, 3], [8, 1], [9, 7], [3, 9]], dtype=np.float32)
        ordered = order_points(pts)
        np.testing.assert_allclose(ordered[0], [2, 3])
        np.testing.assert_allclose(ordered[1], [8, 1])
        np.testing.assert_allclose(ordered[2], [9, 7])
        np.testing.assert_allclose(ordered[3], [3, 9])

    def test_is_convex_quad(self):
        self.assertTrue(is_convex_quad(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)))
        self.assertFalse(is_convex_quad(np.array([[0, 0], [10, 0], [2, 2], [0, 10]], dtype=float)))

    def test_corners_valid_rejects_degenerate(self):
        degenerate = np.array([[0, 0], [0, 0], [5, 5], [0, 5]], dtype=float)
        valid, _reason = corners_valid(degenerate, 100, 100)
        self.assertFalse(valid)

    def test_corners_valid_accepts_reasonable_quad(self):
        quad = np.array([[10, 10], [90, 8], [92, 92], [8, 90]], dtype=float)
        valid, _reason = corners_valid(quad, 100, 100, min_side_px=10)
        self.assertTrue(valid)


class PerspectiveTests(TestCase):
    def test_warp_size_matches_quad(self):
        image = np.full((400, 400, 3), 0, dtype=np.uint8)
        corners = np.array([[50, 40], [350, 60], [330, 340], [70, 320]], dtype=np.float32)
        warped = warp_document(image, corners)
        expected = destination_size(corners)
        self.assertEqual(warped.shape[1], max(48, expected[0]))
        self.assertEqual(warped.shape[0], max(48, expected[1]))

    def test_warp_straightens_content(self):
        image = make_document_photo(angle=9.0, seed=3)
        result = detect_document_corners(image)
        corners = result["corners"]
        ordered = order_points(corners)

        diffs = ordered[:, 0] - ordered[:, 1]
        self.assertEqual(int(np.argmax(diffs)), 1)
        self.assertEqual(int(np.argmin(diffs)), 3)
        warped = warp_document(image, corners)
        h, w = warped.shape[:2]

        self.assertGreater(w, h * 1.05, "warp output aspect looks twisted")

        center = warped[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3]
        self.assertGreater(float(center.mean()), 120)

        self.assertGreater(float(warped[5:25, 5:25].mean()), 80)
        self.assertGreater(float(warped[-25:-5, -25:-5].mean()), 80)


class EnhancementTests(TestCase):
    def setUp(self):
        self.image = make_document_photo(500, 400, angle=2)

    def test_brightness_changes_mean(self):
        params = EnhancementParams(brightness=1.4)
        out = apply_enhancement(self.image, params)
        self.assertGreater(float(out.mean()), float(self.image.mean()) + 5)

    def test_contrast_increases_std(self):
        flat = np.full((100, 100, 3), 110, dtype=np.uint8)
        cv2.circle(flat, (50, 50), 30, (150, 150, 150), -1)
        out = apply_enhancement(flat, EnhancementParams(contrast=1.6))
        self.assertGreater(float(out.std()), float(flat.std()) * 1.3)

    def test_sharpness_increases_edge_energy(self):
        blurred = cv2.GaussianBlur(self.image, (7, 7), 0)
        out = apply_enhancement(blurred, EnhancementParams(sharpness=1.5))
        lap_before = cv2.Laplacian(blurred, cv2.CV_64F).var()
        lap_after = cv2.Laplacian(out, cv2.CV_64F).var()
        self.assertGreater(lap_after, lap_before * 1.5)

    def test_denoise_and_clahe_run(self):
        out = apply_enhancement(self.image, EnhancementParams(denoise=True, clahe=True))
        self.assertEqual(out.shape, self.image.shape)

    def test_params_are_clamped(self):
        params = EnhancementParams.from_dict({"brightness": 99, "contrast": -5, "sharpness": 100})
        self.assertEqual(params.brightness, 1.8)
        self.assertEqual(params.contrast, 0.4)
        self.assertEqual(params.sharpness, 3.0)


class FilterTests(TestCase):
    def setUp(self):
        self.image = make_document_photo(400, 320, angle=3)

    def test_original_returns_copy(self):
        out = apply_filter(self.image, "original")
        self.assertFalse(np.shares_memory(out, self.image))

    def test_grayscale_channels_equal(self):
        out = apply_filter(self.image, "grayscale")
        np.testing.assert_array_equal(out[:, :, 0], out[:, :, 2])

    def test_bw_is_binary_after_otsu(self):
        out = apply_filter(self.image, "bw")
        unique = np.unique(out)
        self.assertTrue(set(unique.tolist()).issubset({0, 255}))

    def test_adaptive_threshold_is_binary(self):
        out = apply_filter(self.image, "adaptive")
        unique = np.unique(out)
        self.assertTrue(set(unique.tolist()).issubset({0, 255}))

    def test_enhanced_differs_from_original(self):
        out = apply_filter(self.image, "enhanced")
        self.assertFalse(np.array_equal(out, self.image))

    def test_unknown_filter_raises(self):
        with self.assertRaises(ValueError):
            apply_filter(self.image, "magic-filter")


class PipelineTests(TestCase):
    def test_full_pipeline_with_corners(self):
        image = make_document_photo(800, 600, angle=5)
        detection = detect_document_corners(image)
        out = run_pipeline(
            image,
            corners=detection["corners"],
            params=EnhancementParams(brightness=1.1, sharpness=0.5),
            filter_name="grayscale",
            max_dim=600,
        )
        self.assertLessEqual(max(out.shape[:2]), 600)
        np.testing.assert_array_equal(out[:, :, 0], out[:, :, 2])

    def test_invalid_corners_raise(self):
        image = np.zeros((200, 200, 3), dtype=np.uint8)
        bad = np.array([[0, 0], [1, 1], [2, 2], [0, 5]], dtype=np.float32)
        with self.assertRaises(PipelineError):
            run_pipeline(image, corners=bad, perspective=True)

    def test_quality_assessment_flags_dark_image(self):
        dark = (make_flat_image(level=25)).copy()
        report = assess_quality(dark)
        levels = [a["level"] for a in report["assessments"]]
        self.assertIn("bad", levels)


class ScannerApiFlowTests(TestCase):
    def setUp(self):
        response = upload_image(self.client, make_document_photo())
        self.assertEqual(response.status_code, 200)
        self.scan_id = response.json()["id"]

    def test_detect_api(self):
        response = self.client.post(
            reverse("scanner:api_detect", args=[self.scan_id]), **AJAX
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["detected"])
        self.assertEqual(len(data["corners"]), 4)

    def test_process_api_creates_processed_image(self):
        corners = [[10, 10], [880, 10], [880, 680], [10, 680]]
        response = self.client.post(
            reverse("scanner:api_process", args=[self.scan_id]),
            data='{"corners": %s, "filter": "grayscale", "params": {"brightness": 1.1}}' % corners,
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        record = ScanHistory.objects.get(pk=self.scan_id)
        self.assertTrue(record.has_processed)
        self.assertEqual(record.filter_used, "grayscale")
        self.assertEqual(record.params_json["brightness"], 1.1)
        self.assertEqual(record.corners_json, [[10, 10], [880, 10], [880, 680], [10, 680]])

    def test_process_rejects_bad_corners(self):
        response = self.client.post(
            reverse("scanner:api_process", args=[self.scan_id]),
            data='{"corners": [[0,0],[1,1],[2,2],[3,3]], "filter": "bw"}',
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 400)

    def test_export_jpg(self):
        response = self.client.post(
            reverse("scanner:api_export", args=[self.scan_id]),
            data='{"format": "jpg"}',
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("attachment", response["Content-Disposition"])
        image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(image)

    def test_export_png(self):
        response = self.client.post(
            reverse("scanner:api_export", args=[self.scan_id]),
            data='{"format": "png"}',
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response.content[:8], b"\x89PNG\r\n\x1a\n")

    def test_export_pdf(self):
        response = self.client.post(
            reverse("scanner:api_export", args=[self.scan_id]),
            data='{"format": "pdf"}',
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(response.content.rstrip().endswith(b"%%EOF"), True)

    def test_export_unknown_format_rejected(self):
        response = self.client.post(
            reverse("scanner:api_export", args=[self.scan_id]),
            data='{"format": "gif"}',
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 400)

    def test_multi_page_pdf(self):
        second = upload_image(self.client, make_document_photo(700, 520, angle=-3))
        second_id = second.json()["id"]
        response = self.client.post(
            reverse("scanner:api_pdf_multi"),
            data='{"scan_ids": [%d, %d]}' % (self.scan_id, second_id),
            content_type="application/json",
            **AJAX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        page_markers = response.content.count(b"/Type /Page\n")
        page_markers_alt = response.content.count(b"/Type /Page ")
        self.assertGreaterEqual(max(page_markers, page_markers_alt), 2)

    def test_pages_list_and_reorder(self):
        second = upload_image(self.client, make_document_photo(700, 520, angle=-3))
        second_id = second.json()["id"]
        response = self.client.get(reverse("scanner:api_pages"))
        pages = response.json()["pages"]
        self.assertEqual(len(pages), 2)
        response = self.client.post(
            reverse("scanner:api_pages_reorder"),
            data='{"order": [%d, %d]}' % (second_id, self.scan_id),
            content_type="application/json",
            **AJAX,
        )
        self.assertTrue(response.json()["ok"])
        pages = self.client.get(reverse("scanner:api_pages")).json()["pages"]
        self.assertEqual(pages[0]["id"], second_id)


class HistoryAccessTests(TestCase):
    def test_history_hidden_between_sessions(self):
        upload_image(self.client, make_document_photo())
        self.assertContains(self.client.get(reverse("scanner:history")), "document.png")
        other = Client()
        response = other.get(reverse("scanner:history"))
        self.assertNotContains(response, "document.png")

    def test_other_session_cannot_touch_scan(self):
        response = upload_image(self.client, make_document_photo())
        scan_id = response.json()["id"]
        other = Client()
        for url in (
            reverse("scanner:api_detect", args=[scan_id]),
            reverse("scanner:api_process", args=[scan_id]),
            reverse("scanner:api_export", args=[scan_id]),
            reverse("scanner:api_scan_delete", args=[scan_id]),
            reverse("scanner:history_delete", args=[scan_id]),
        ):
            response = other.post(url, **AJAX)
            self.assertEqual(response.status_code, 404, url)
        self.assertTrue(ScanHistory.objects.filter(pk=scan_id).exists())

    def test_delete_own_scan(self):
        response = upload_image(self.client, make_document_photo())
        scan_id = response.json()["id"]
        response = self.client.post(reverse("scanner:api_scan_delete", args=[scan_id]), **AJAX)
        self.assertTrue(response.json()["ok"])
        self.assertFalse(ScanHistory.objects.filter(pk=scan_id).exists())


class AuthTests(TestCase):
    def test_register_login_and_history_linkage(self):
        upload_image(self.client, make_document_photo())
        response = self.client.post(
            reverse("scanner:register"),
            {
                "username": "student",
                "email": "student@example.com",
                "password1": "Strong-Pass-2024!",
                "password2": "Strong-Pass-2024!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get("_auth_user_id"))
        record = ScanHistory.objects.get(user__username="student")
        self.assertEqual(record.session_key, "")

    def test_login_page_renders(self):
        response = self.client.get(reverse("scanner:login"))
        self.assertEqual(response.status_code, 200)

    def test_weak_password_rejected(self):
        response = self.client.post(
            reverse("scanner:register"),
            {
                "username": "weak",
                "email": "",
                "password1": "123",
                "password2": "123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())


class ValidatorTests(TestCase):
    def test_decode_rejects_text(self):
        with self.assertRaises(ImageValidationError):
            decode_image(b"hello world, not an image at all")

    def test_decode_accepts_png(self):
        data = png_upload(make_document_photo(300, 240)).read()
        decoded = decode_image(data)
        self.assertEqual(decoded["format"], "PNG")
        self.assertEqual(decoded["width"], 300)

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("my scan (final).jpg"), "my scan (final).jpg")
        self.assertEqual(sanitize_filename(""), "upload")
        self.assertEqual(sanitize_filename("a" * 500), "a" * 120)


class CloudinaryStorageTests(TestCase):

    def _patched(self, **overrides):
        from .services.media_storage import CloudinaryMediaStorage

        sdk = mock.MagicMock()
        sdk.uploader.upload.return_value = {"public_id": "scans/x/orig"}
        sdk.utils.cloudinary_url.return_value = (
            "https://res.cloudinary.com/demo/image/upload/scans/x/orig.jpg",
            {},
        )
        sdk.api.resource.return_value = {"bytes": 2048}
        for key, value in overrides.items():
            setattr(sdk, key, value)
        patcher = mock.patch("scanner.services.media_storage.cloudinary", sdk)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(
            os.environ, {"CLOUDINARY_URL": "cloudinary://key:secret@demo"}
        )
        env.start()
        self.addCleanup(env.stop)
        return CloudinaryMediaStorage(), sdk

    def test_save_uploads_without_extension_in_public_id(self):
        storage, sdk = self._patched()
        name = storage.save("scans/x/orig.jpg", ContentFile(b"jpeg-bytes"))
        self.assertEqual(name, "scans/x/orig.jpg")
        sdk.uploader.upload.assert_called_once()
        args, kwargs = sdk.uploader.upload.call_args
        self.assertTrue(args[0].startswith("data:image/jpeg;base64,"))
        self.assertEqual(kwargs["public_id"], "scans/x/orig")
        self.assertTrue(kwargs["overwrite"])

    def test_url_builds_delivery_link(self):
        storage, sdk = self._patched()
        url = storage.url("scans/x/orig.jpg")
        self.assertEqual(
            url, "https://res.cloudinary.com/demo/image/upload/scans/x/orig.jpg"
        )
        public_id, options = sdk.utils.cloudinary_url.call_args
        self.assertEqual(public_id[0], "scans/x/orig")
        self.assertEqual(options["format"], "jpg")

    def test_exists_and_size_use_api_metadata(self):
        storage, sdk = self._patched()
        self.assertTrue(storage.exists("scans/x/orig.jpg"))
        self.assertEqual(storage.size("scans/x/orig.jpg"), 2048)
        self.assertEqual(sdk.api.resource.call_count, 2)

    def test_exists_returns_false_on_not_found(self):
        not_found = type("NotFound", (Exception,), {})
        storage, sdk = self._patched()
        sdk.api.NotFound = not_found
        sdk.api.resource.side_effect = not_found
        self.assertFalse(storage.exists("scans/x/missing.jpg"))

    def test_delete_destroys_resource(self):
        storage, sdk = self._patched()
        storage.delete("scans/x/orig.jpg")
        sdk.uploader.destroy.assert_called_once()

    def test_config_from_environment_url(self):
        from .services.media_storage import CloudinaryMediaStorage

        with mock.patch.dict(os.environ, {"CLOUDINARY_URL": "cloudinary://k:s@c"}):
            storage = CloudinaryMediaStorage()
            storage._ensure_config()
            self.assertTrue(storage._configured)

    def test_missing_credentials_raise_improperly_configured(self):
        from django.conf import settings as dj_settings
        from django.core.exceptions import ImproperlyConfigured

        from .services.media_storage import CloudinaryMediaStorage

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                dj_settings,
                "CLOUDINARY",
                {"CLOUD_NAME": "", "API_KEY": "", "API_SECRET": ""},
            ):
                storage = CloudinaryMediaStorage()
                with self.assertRaises(ImproperlyConfigured):
                    storage._ensure_config()
