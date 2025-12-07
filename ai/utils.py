import json
import logging
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None
    logger.warning("ultralytics not available; load_yolo_model will fail until installed")

try:
    import face_recognition
except Exception as e:
    face_recognition = None
    logger.error(f"face_recognition import failed: {str(e)}")
    logger.error("CRITICAL: face_recognition không khả dụng. Vui lòng di chuyển project sang thư mục không có ký tự đặc biệt (không dấu, không khoảng trắng).")


def load_yolo_model(path: str):
    if YOLO is None:
        raise RuntimeError("ultralytics not available")
    return YOLO(path)


def detect_faces_yolo(model, frame) -> List[Tuple[int, int, int, int]]:
    results = model(frame)
    boxes: List[Tuple[int, int, int, int]] = []
    for r in results:
        if not hasattr(r, "boxes") or r.boxes is None:
            continue
        for b in r.boxes.xyxy.cpu().numpy().astype(int):
            x1, y1, x2, y2 = b[:4]
            boxes.append((x1, y1, x2, y2))
    return boxes


def get_face_encodings(face_image) -> Optional[List[float]]:
    if face_recognition is None:
        logger.error("face_recognition module is not available - cannot extract face encodings")
        return None
    try:
        # face_image may come from OpenCV (BGR). Convert to RGB for face_recognition.
        import cv2
        if face_image is None:
            return None
        # If the image has 3 channels, assume BGR and convert
        if hasattr(face_image, 'shape') and len(face_image.shape) == 3 and face_image.shape[2] == 3:
            rgb = cv2.cvtColor(face_image, cv2.COLOR_BGR2RGB)
        else:
            rgb = face_image
        # Ensure the face image isn't too small for the encoder; upscale if necessary
        try:
            h, w = rgb.shape[:2]
            min_side = min(h, w)
            if min_side < 80:
                scale = 160 / float(min_side)
                new_w = max(160, int(w * scale))
                new_h = max(160, int(h * scale))
                rgb = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        except Exception:
            pass
    except Exception:
        # if cv2 not available or conversion fails, fall back
        rgb = face_image

    encs = face_recognition.face_encodings(rgb)
    if not encs:
        return None
    return encs[0].astype(float).tolist()


def embedding_to_json(vector: List[float]) -> str:
    return json.dumps([float(v) for v in vector])


def json_to_embedding(json_str: str) -> Optional[List[float]]:
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return [float(x) for x in data]
        return None
    except json.JSONDecodeError:
        return None
