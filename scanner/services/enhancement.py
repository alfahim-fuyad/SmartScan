from dataclasses import dataclass
from typing import Dict

import cv2
import numpy as np

FILTER_ORIGINAL = "original"
FILTER_GRAYSCALE = "grayscale"
FILTER_BW = "bw"
FILTER_ADAPTIVE = "adaptive"
FILTER_ENHANCED = "enhanced"

VALID_FILTERS = (
    FILTER_ORIGINAL,
    FILTER_GRAYSCALE,
    FILTER_BW,
    FILTER_ADAPTIVE,
    FILTER_ENHANCED,
)

FILTER_LABELS = {
    FILTER_ORIGINAL: "Original Color",
    FILTER_GRAYSCALE: "Grayscale",
    FILTER_BW: "Black & White",
    FILTER_ADAPTIVE: "Adaptive Threshold",
    FILTER_ENHANCED: "Enhanced Color",
}

PARAM_RANGES = {
    "brightness": (0.4, 1.8, 1.0),
    "contrast": (0.4, 1.8, 1.0),
    "sharpness": (0.0, 3.0, 0.0),
}


@dataclass
class EnhancementParams:

    brightness: float = 1.0
    contrast: float = 1.0
    sharpness: float = 0.0
    denoise: bool = False
    clahe: bool = True

    @classmethod
    def from_dict(cls, data: Dict | None) -> "EnhancementParams":
        data = data or {}

        def clamped(key):
            low, high, default = PARAM_RANGES[key]
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        return cls(
            brightness=clamped("brightness"),
            contrast=clamped("contrast"),
            sharpness=clamped("sharpness"),
            denoise=bool(data.get("denoise", False)),
            clahe=bool(data.get("clahe", True)),
        )

    def as_dict(self) -> Dict:
        return {
            "brightness": round(self.brightness, 3),
            "contrast": round(self.contrast, 3),
            "sharpness": round(self.sharpness, 3),
            "denoise": self.denoise,
            "clahe": self.clahe,
        }


def _resize_if_needed(image: np.ndarray, max_dim: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if max_dim and longest > max_dim:
        scale = max_dim / float(longest)
        size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    return image


def _denoise(image: np.ndarray) -> np.ndarray:
    long_side = max(image.shape[:2])
    if long_side <= 1400:
        return cv2.fastNlMeansDenoisingColored(
            image, None, h=5, hColor=5, templateWindowSize=7, searchWindowSize=15
        )
    return cv2.bilateralFilter(image, d=7, sigmaColor=50, sigmaSpace=50)


def _apply_clahe(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    lightness = clahe.apply(lightness)
    return cv2.cvtColor(cv2.merge((lightness, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def _apply_brightness_contrast(image: np.ndarray, params: EnhancementParams) -> np.ndarray:
    alpha = params.contrast
    beta = (params.brightness - 1.0) * 90.0
    return cv2.convertScaleAbs(image, alpha=alpha, beta=beta)


def _unsharp(image: np.ndarray, strength: float) -> np.ndarray:
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=2.0)
    return cv2.addWeighted(image, 1.0 + strength, blurred, -strength, 0)


def apply_enhancement(image: np.ndarray, params: EnhancementParams) -> np.ndarray:
    result = image.copy()
    if params.denoise:
        result = _denoise(result)
    if params.clahe:
        result = _apply_clahe(result)
    if params.brightness != 1.0 or params.contrast != 1.0:
        result = _apply_brightness_contrast(result, params)
    if params.sharpness > 0.01:
        result = _unsharp(result, params.sharpness)
    return result


def _as_bgr(single: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(single, cv2.COLOR_GRAY2BGR)


def filter_grayscale(image: np.ndarray) -> np.ndarray:
    return _as_bgr(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))


def filter_bw(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    return _as_bgr(binary)


def filter_adaptive(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)
    block = 25 if min(gray.shape[:2]) >= 25 else (min(gray.shape[:2]) // 2) * 2 + 1
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 11
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
    return _as_bgr(binary)


def filter_enhanced(image: np.ndarray) -> np.ndarray:
    result = image.astype(np.float32)
    means = result.reshape(-1, 3).mean(axis=0)
    overall = float(means.mean())
    if overall > 1.0:
        scales = overall / np.maximum(means, 1.0)
        result = np.clip(result * scales[None, None, :], 0, 255).astype(np.uint8)
    else:
        result = image.copy()
    result = _apply_clahe(result)
    return _unsharp(result, 0.5)


def apply_filter(image: np.ndarray, filter_name: str) -> np.ndarray:
    if filter_name not in VALID_FILTERS:
        raise ValueError(f"Unknown filter: {filter_name!r}")
    if filter_name == FILTER_GRAYSCALE:
        return filter_grayscale(image)
    if filter_name == FILTER_BW:
        return filter_bw(image)
    if filter_name == FILTER_ADAPTIVE:
        return filter_adaptive(image)
    if filter_name == FILTER_ENHANCED:
        return filter_enhanced(image)
    return image.copy()


RESTORE_PRESET_AUTO = "auto"
RESTORE_PRESET_NATURAL = "natural"
RESTORE_PRESET_DOCUMENT = "document"
RESTORE_PRESET_DENOISE = "denoise"
RESTORE_PRESET_SHARPEN = "sharpen"
RESTORE_PRESET_COLOR = "color"
RESTORE_PRESET_SCRATCH = "scratch"

RESTORE_PRESETS = (
    RESTORE_PRESET_AUTO,
    RESTORE_PRESET_NATURAL,
    RESTORE_PRESET_DOCUMENT,
    RESTORE_PRESET_DENOISE,
    RESTORE_PRESET_SHARPEN,
    RESTORE_PRESET_COLOR,
    RESTORE_PRESET_SCRATCH,
)

RESTORE_PRESET_LABELS = {
    RESTORE_PRESET_AUTO: "Auto Enhance",
    RESTORE_PRESET_NATURAL: "Natural",
    RESTORE_PRESET_DOCUMENT: "Document",
    RESTORE_PRESET_DENOISE: "Noise Removal",
    RESTORE_PRESET_SHARPEN: "Sharpness",
    RESTORE_PRESET_COLOR: "Color Restoration",
    RESTORE_PRESET_SCRATCH: "Scratch & Dust",
}


def _gray_lab(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.shape[2] == 1:
        return image[:, :, 0]
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _balance_white_gray(image: np.ndarray) -> np.ndarray:
    gray = _gray_lab(image)
    mean_val = float(gray.mean()) if gray.size else 128.0
    if mean_val < 1.0:
        return image
    scale = 128.0 / mean_val
    return cv2.convertScaleAbs(image, alpha=scale, beta=0)


def _clahe_strength(image: np.ndarray, strength: float) -> np.ndarray:
    clip = max(1.0, min(5.0, 1.8 + strength * 1.8))
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    lightness = clahe.apply(lightness)
    return cv2.cvtColor(cv2.merge((lightness, a_channel, b_channel)), cv2.COLOR_LAB2BGR)


def _denoise_strength(image: np.ndarray, strength: float) -> np.ndarray:
    long_side = max(image.shape[:2])
    h = max(3, int(round(4 + strength * 8)))
    if long_side <= 1400:
        return cv2.fastNlMeansDenoisingColored(
            image, None, h=h, hColor=h, templateWindowSize=7, searchWindowSize=21
        )
    return cv2.bilateralFilter(image, d=7, sigmaColor=30 + strength * 40, sigmaSpace=30 + strength * 40)


def _sharpen_strength(image: np.ndarray, strength: float) -> np.ndarray:
    sigma = 1.5
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma)
    amount = 0.4 + strength * 1.2
    return cv2.addWeighted(image, 1.0 + amount, blurred, -amount, 0)


def _restore_color(image: np.ndarray, strength: float) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    l_chan, a_chan, b_chan = cv2.split(lab)
    a_mean = float(a_chan.mean()) - 128.0
    b_mean = float(b_chan.mean()) - 128.0
    factor = 0.4 + strength * 0.6
    a_chan = np.clip(a_chan - a_mean * factor, 0, 255)
    b_chan = np.clip(b_chan - b_mean * factor, 0, 255)
    l_mean = float(l_chan.mean())
    if l_mean > 1.0:
        target = 128.0
        scale = target / l_mean
        l_chan = np.clip(l_chan * scale, 0, 255)
    lab = cv2.merge((l_chan, a_chan, b_chan)).astype(np.uint8)
    result = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(result, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[:, :, 1]
    sat_mean = float(sat.mean())
    if sat_mean > 1.0:
        boost = 1.0 + strength * 0.3
        sat = np.clip(sat * boost, 0, 255)
    hsv[:, :, 1] = sat
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _remove_scratches(image: np.ndarray, strength: float) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    kernel_size = max(3, int(round(5 + strength * 4)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    black_hat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    top_hat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    thresh_val = max(15, int(round(40 - strength * 15)))
    _, dark_mask = cv2.threshold(black_hat, thresh_val, 255, cv2.THRESH_BINARY)
    _, bright_mask = cv2.threshold(top_hat, thresh_val, 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_or(dark_mask, bright_mask)
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.dilate(mask, kernel_close, iterations=1)
    radius = max(3, int(round(4 + strength * 6)))
    return cv2.inpaint(image, mask, radius, cv2.INPAINT_TELEA)


def restore_old_image(image: np.ndarray, preset: str, strength: float) -> np.ndarray:
    if preset not in RESTORE_PRESETS:
        raise ValueError(f"Unknown restore preset: {preset!r}")
    try:
        strength = max(0.0, min(1.0, float(strength)))
    except (TypeError, ValueError):
        strength = 0.5

    work = image.copy()

    if preset == RESTORE_PRESET_AUTO:
        gray = _gray_lab(work)
        brightness = float(gray.mean()) if gray.size else 128.0
        contrast = float(gray.std()) if gray.size else 50.0
        if brightness < 90.0:
            work = _balance_white_gray(work)
        elif brightness > 180.0:
            work = cv2.convertScaleAbs(work, alpha=0.85, beta=-20)
        clip = 2.0 + (max(0.0, 50.0 - contrast) / 50.0) * 2.0
        lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=min(4.0, clip), tileGridSize=(8, 8))
        l_chan = clahe.apply(l_chan)
        work = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)
        work = _sharpen_strength(work, 0.3)
        return work

    if preset == RESTORE_PRESET_DOCUMENT:
        work = _balance_white_gray(work)
        work = _clahe_strength(work, 0.6 + strength * 0.6)
        work = _denoise_strength(work, 0.4 + strength * 0.4)
        work = _sharpen_strength(work, 0.3 + strength * 0.5)
        return work

    if preset == RESTORE_PRESET_DENOISE:
        work = _denoise_strength(work, 0.5 + strength * 0.5)
        work = _clahe_strength(work, 0.4 + strength * 0.4)
        return work

    if preset == RESTORE_PRESET_SHARPEN:
        work = _clahe_strength(work, 0.4 + strength * 0.3)
        work = _sharpen_strength(work, 0.6 + strength * 0.8)
        return work

    if preset == RESTORE_PRESET_COLOR:
        work = _restore_color(work, strength)
        work = _clahe_strength(work, 0.3 + strength * 0.3)
        return work

    if preset == RESTORE_PRESET_SCRATCH:
        work = _remove_scratches(work, strength)
        work = _denoise_strength(work, 0.3 + strength * 0.3)
        work = _clahe_strength(work, 0.3 + strength * 0.2)
        return work

    work = _clahe_strength(work, 0.5 + strength * 0.5)
    if strength > 0.05:
        work = _denoise_strength(work, 0.3 + strength * 0.3)
    work = _sharpen_strength(work, 0.2 + strength * 0.4)
    alpha = 0.85 + strength * 0.15
    beta = (1.0 - alpha) * 8.0
    return cv2.convertScaleAbs(work, alpha=alpha, beta=beta)


def _clamp(value: float, low: float, high: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = default
    return max(low, min(high, v))


def _to_grayscale_bgr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def manual_enhance(
    image: np.ndarray,
    brightness: float = 0.0,
    contrast: float = 0.0,
    sharpness: float = 0.0,
    noise: float = 0.0,
    saturation: float = 0.0,
    strength: float = 0.5,
    color_correction: bool = False,
    clahe: bool = True,
    grayscale: bool = False,
) -> np.ndarray:
    brightness = _clamp(brightness, -1.0, 1.0, 0.0)
    contrast = _clamp(contrast, -1.0, 1.0, 0.0)
    sharpness = _clamp(sharpness, 0.0, 1.0, 0.0)
    noise = _clamp(noise, 0.0, 1.0, 0.0)
    saturation = _clamp(saturation, -1.0, 1.0, 0.0)
    strength = _clamp(strength, 0.0, 1.0, 0.5)

    work = image.copy()

    if noise > 0.02:
        work = _denoise_strength(work, noise)

    if clahe:
        clip = 1.8 + strength * 1.8 + contrast * 1.0
        lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)
        clahe_op = cv2.createCLAHE(clipLimit=max(1.0, min(5.0, clip)), tileGridSize=(8, 8))
        l_chan = clahe_op.apply(l_chan)
        work = cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)
    elif contrast != 0.0:
        alpha = 1.0 + contrast * 0.8
        work = cv2.convertScaleAbs(work, alpha=alpha, beta=0)

    if brightness != 0.0:
        beta = brightness * 60.0
        work = cv2.convertScaleAbs(work, alpha=1.0, beta=beta)

    if color_correction:
        lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB).astype(np.float32)
        l_chan, a_chan, b_chan = cv2.split(lab)
        a_mean = float(a_chan.mean()) - 128.0
        b_mean = float(b_chan.mean()) - 128.0
        a_chan = np.clip(a_chan - a_mean * 0.7, 0, 255)
        b_chan = np.clip(b_chan - b_mean * 0.7, 0, 255)
        lab = cv2.merge((l_chan, a_chan, b_chan)).astype(np.uint8)
        work = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    if saturation != 0.0 and not grayscale:
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV).astype(np.float32)
        sat = hsv[:, :, 1]
        factor = 1.0 + saturation * 0.8
        sat = np.clip(sat * factor, 0, 255)
        hsv[:, :, 1] = sat
        work = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    if sharpness > 0.02:
        work = _sharpen_strength(work, sharpness)

    if grayscale:
        work = _to_grayscale_bgr(work)

    return work
