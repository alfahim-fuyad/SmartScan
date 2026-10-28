from typing import Dict, Tuple

import cv2
import numpy as np
from django.conf import settings


def order_points(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = points.sum(axis=1)
    diffs = points[:, 0] - points[:, 1]
    top_left = points[np.argmin(sums)]
    bottom_right = points[np.argmax(sums)]

    top_right = points[np.argmax(diffs)]
    bottom_left = points[np.argmin(diffs)]
    return np.array(
        [top_left, top_right, bottom_right, bottom_left], dtype=np.float32
    )


def quad_area(points: np.ndarray) -> float:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    return abs(0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def quad_min_side(points: np.ndarray) -> float:
    pts = np.asarray(points, dtype=np.float64).reshape(4, 2)
    sides = [
        np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)
    ]
    return float(min(sides))


def is_convex_quad(points: np.ndarray, tolerance: float = 1e-6) -> bool:
    pts = np.asarray(points, dtype=np.float64).reshape(4, 2)
    signs = []
    for i in range(4):
        a, b, c = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        signs.append(cross)
    has_positive = any(s > tolerance for s in signs)
    has_negative = any(s < -tolerance for s in signs)
    return not (has_positive and has_negative)


def corners_valid(
    corners: np.ndarray,
    image_width: int,
    image_height: int,
    min_area_ratio: float = 0.02,
    min_side_px: float = 40.0,
) -> Tuple[bool, str]:
    try:
        pts = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    except (TypeError, ValueError):
        return False, "Corners must be four (x, y) points."
    if not np.all(np.isfinite(pts)):
        return False, "Corners contain invalid values."
    if np.any(pts < -2) or np.any(
        pts[:, 0] > image_width + 2
    ) or np.any(pts[:, 1] > image_height + 2):
        return False, "Corners fall outside the image."
    if not is_convex_quad(pts):
        return False, "The selected corners do not form a convex quadrilateral."
    area = quad_area(pts)
    image_area = float(image_width * image_height)
    if area < image_area * min_area_ratio:
        return False, "The selected region is too small to be a document."
    if quad_min_side(pts) < min_side_px:
        return False, "One side of the selected region is too short."
    return True, ""


def _auto_canny(gray: np.ndarray) -> np.ndarray:
    median = float(np.median(gray))
    lower = int(max(0, 0.66 * median))
    upper = int(min(255, 1.33 * median))
    if upper <= lower:
        lower, upper = 40, 120
    return cv2.Canny(gray, lower, upper)


def detect_document_corners(image: np.ndarray) -> Dict:
    height, width = image.shape[:2]
    max_dim = settings.SCANNER_DETECT_MAX_DIM
    scale = max(width, height) / float(max_dim)
    working = image if scale <= 1.0 else cv2.resize(
        image, (int(round(width / scale)), int(round(height / scale))),
        interpolation=cv2.INTER_AREA,
    )
    wh, ww = working.shape[:2]
    working_area = float(ww * wh)

    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = _auto_canny(blurred)

    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"corners": None, "message": "No edges found in the image."}

    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]

    best = None
    best_area = 0.0
    for epsilon_factor in (0.02, 0.012, 0.03, 0.008):
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < working_area * 0.08:
                continue
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(
                contour, epsilon_factor * perimeter, True
            )
            if len(approx) != 4:
                continue
            pts = approx.reshape(4, 2)
            if not is_convex_quad(pts):
                continue
            if quad_area(pts) < working_area * 0.08:
                continue
            if quad_min_side(pts) < 0.12 * min(ww, wh):
                continue
            candidate_area = quad_area(pts)
            if candidate_area > best_area:
                best_area = candidate_area
                best = pts
        if best is not None:
            break

    if best is None:
        return {
            "corners": None,
            "message": (
                "No clear document boundary detected. Try better lighting, "
                "or adjust the four corners manually."
            ),
        }

    corners = order_points(best.reshape(4, 2)) * scale
    corners = np.clip(corners, 0, [width - 1, height - 1]).astype(np.float32)
    return {
        "corners": corners,
        "message": "Document boundary detected.",
    }
