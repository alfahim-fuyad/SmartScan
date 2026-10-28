from typing import Optional

import cv2
import numpy as np

from .detection import corners_valid
from .enhancement import EnhancementParams, apply_enhancement, apply_filter
from .perspective import warp_document


class PipelineError(ValueError):
    pass


def _scaled_corners(corners: np.ndarray, scale: float) -> Optional[np.ndarray]:
    return (np.asarray(corners, dtype=np.float32).reshape(4, 2) * scale)


def run_pipeline(
    source_bgr: np.ndarray,
    corners: Optional[np.ndarray] = None,
    params: Optional[EnhancementParams] = None,
    filter_name: str = "original",
    perspective: bool = True,
    max_dim: Optional[int] = None,
) -> np.ndarray:
    image = source_bgr
    working_corners = corners

    if max_dim is not None:
        height, width = image.shape[:2]
        longest = max(height, width)
        if longest > max_dim:
            scale = max_dim / float(longest)
            image = cv2.resize(
                image,
                (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )
            working_corners = _scaled_corners(corners, scale) if corners is not None else None

    height, width = image.shape[:2]
    use_perspective = perspective and working_corners is not None
    if use_perspective:
        valid, reason = corners_valid(working_corners, width, height)
        if not valid:
            raise PipelineError(reason or "Invalid document corners.")
        image = warp_document(image, working_corners)

    params = params or EnhancementParams()
    image = apply_enhancement(image, params)
    image = apply_filter(image, filter_name)

    if min(image.shape[:2]) < 24:
        raise PipelineError("The processed result is too small.")
    return image
