from typing import Dict, List

import cv2
import numpy as np
from django.conf import settings


def compute_metrics(image: np.ndarray) -> Dict:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return {
        "width": int(width),
        "height": int(height),
        "laplacian_variance": round(float(laplacian.var()), 1),
        "brightness": round(float(gray.mean()), 1),
        "contrast": round(float(gray.std()), 1),
    }


def _assessments(metrics: Dict, thresholds: Dict) -> List[Dict]:
    results: List[Dict] = []
    long_side = max(metrics["width"], metrics["height"])
    short_side = min(metrics["width"], metrics["height"])

    lap = metrics["laplacian_variance"]
    if lap < thresholds["blur_variance_bad"]:
        results.append(
            {
                "key": "blur",
                "level": "bad",
                "message": "Image may be blurry (sharpness score %.0f)." % lap,
            }
        )
    elif lap < thresholds["blur_variance_warn"]:
        results.append(
            {
                "key": "blur",
                "level": "warn",
                "message": "Image is slightly soft — hold the camera steady.",
            }
        )

    brightness = metrics["brightness"]
    if brightness < thresholds["brightness_bad_low"]:
        results.append(
            {
                "key": "brightness",
                "level": "bad",
                "message": "Image is very dark (%.0f/255). Rescan with more light."
                % brightness,
            }
        )
    elif brightness < thresholds["brightness_warn_low"]:
        results.append(
            {
                "key": "brightness",
                "level": "warn",
                "message": "Low brightness (%.0f/255)." % brightness,
            }
        )
    elif brightness > thresholds["brightness_warn_high"]:
        results.append(
            {
                "key": "brightness",
                "level": "warn",
                "message": "Very bright — highlights may be washed out.",
            }
        )

    if metrics["contrast"] < thresholds["contrast_warn_low"]:
        results.append(
            {
                "key": "contrast",
                "level": "warn",
                "message": "Low contrast — try the Enhanced Color filter.",
            }
        )

    if short_side < thresholds["min_dim"] * 0.5 or long_side < thresholds["min_dim"]:
        results.append(
            {
                "key": "resolution",
                "level": "warn",
                "message": "Resolution may be insufficient (%d×%dpx)."
                % (metrics["width"], metrics["height"]),
            }
        )

    if not results:
        results.append(
            {"key": "ok", "level": "good", "message": "Good quality image."}
        )
    return results


def assess_quality(image: np.ndarray, max_dim: int = 1000) -> Dict:
    height, width = image.shape[:2]
    longest = max(height, width)
    work = image
    if longest > max_dim:
        scale = max_dim / float(longest)
        work = cv2.resize(
            image,
            (int(round(width * scale)), int(round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    metrics = compute_metrics(work)

    metrics["width"], metrics["height"] = int(width), int(height)
    thresholds = settings.SCANNER_QUALITY_THRESHOLDS
    return {"metrics": metrics, "assessments": _assessments(metrics, thresholds)}
