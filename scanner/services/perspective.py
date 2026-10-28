import cv2
import numpy as np

MIN_OUTPUT_SIDE = 48
MAX_OUTPUT_SIDE = 6000


def destination_size(corners: np.ndarray) -> tuple:
    rect = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    (tl, tr, br, bl) = rect

    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    height_left = np.linalg.norm(bl - tl)
    height_right = np.linalg.norm(br - tr)

    width = int(round(max(width_top, width_bottom)))
    height = int(round(max(height_left, height_right)))
    return width, height


def warp_document(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    rect = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    rect[:, 0] = np.clip(rect[:, 0], 0, width - 1)
    rect[:, 1] = np.clip(rect[:, 1], 0, height - 1)
    rect = rect.reshape(4, 2)

    dst_width, dst_height = destination_size(rect)
    dst_width = max(MIN_OUTPUT_SIDE, min(MAX_OUTPUT_SIDE, dst_width))
    dst_height = max(MIN_OUTPUT_SIDE, min(MAX_OUTPUT_SIDE, dst_height))

    source = rect.astype(np.float32)
    destination = np.array(
        [
            [0, 0],
            [dst_width - 1, 0],
            [dst_width - 1, dst_height - 1],
            [0, dst_height - 1],
        ],
        dtype=np.float32,
    )

    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(
        image,
        transform,
        (dst_width, dst_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return warped
