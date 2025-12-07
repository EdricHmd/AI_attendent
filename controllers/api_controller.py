from flask import Blueprint, request, jsonify, current_app
import base64
import logging
from pathlib import Path

bp = Blueprint("api", __name__, url_prefix="/api")

import threading

# lazy-load detector with thread-safety
_detector = None
_detector_lock = threading.Lock()
def get_detector():
    global _detector
    if _detector is not None:
        return _detector
    with _detector_lock:
        if _detector is not None:
            return _detector
        try:
            from ultralytics import YOLO
            model_path = Path(current_app.root_path) / "models" / "yolov8n-face.pt"
            if model_path.exists():
                _detector = YOLO(str(model_path))
                logging.getLogger(__name__).info("YOLO model loaded for preview API")
            else:
                logging.getLogger(__name__).warning("YOLO model not found for preview API: %s", model_path)
        except Exception:
            logging.getLogger(__name__).exception("Error loading YOLO (preview); will use Haar fallback")
    return _detector

# simple preview tracker (module-level so it persists between requests in the
# same worker). This reduces immediate duplicates when the client sends
# frequent frames.
try:
    from ai.tracker import SimpleTracker
    _preview_tracker = SimpleTracker(iou_threshold=0.3, max_age=8, confirm_frames=3)
    _preview_tracker_session_id = None  # track current session id to reset tracker when session changes
except Exception:
    _preview_tracker = None
    _preview_tracker_session_id = None

def detect_face_in_frame_bytes(image_bytes):
    import numpy as np
    import cv2

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"detected": False}

    yolo = get_detector()
    if yolo:
        try:
            # run detector with a conservative confidence; YOLO may still return small detections
            # slightly smaller network input & a bit higher confidence to speed up and cut false positives
            res = yolo(img, imgsz=512, conf=0.5)
            if len(res) and len(res[0].boxes) > 0:
                box = res[0].boxes.xyxy[0].cpu().numpy().tolist()
                x1, y1, x2, y2 = map(int, box[:4])
                w = x2 - x1
                h = y2 - y1
                # require the bbox to be a reasonable fraction of the frame to avoid tiny/false detections
                h_img, w_img = img.shape[:2]
                area_ratio = (w * h) / float(max(1, w_img * h_img))
                # be slightly more permissive to avoid missing small but valid faces
                if area_ratio < 0.04:
                    # treat as not detected (too small)
                    pass
                else:
                    # Accept detection based on YOLO + area. If face_recognition landmarks are
                    # available we'll log presence but do not require them (landmark checking
                    # can be unreliable on low-quality frames and causes false negatives).
                    try:
                        import face_recognition
                        face_rgb = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
                        lms = face_recognition.face_landmarks(face_rgb)
                        if lms and isinstance(lms, list) and lms[0].get('left_eye') and lms[0].get('right_eye'):
                            logging.getLogger(__name__).debug('preview: landmarks found for detection')
                        else:
                            logging.getLogger(__name__).debug('preview: landmarks NOT found but accepting detection based on area/YOLO')
                    except Exception:
                        # face_recognition not available or failed -> continue and accept based on area
                        logging.getLogger(__name__).debug('preview: face_recognition unavailable, accepting detection based on area/YOLO')
                    return {"detected": True, "bbox": [x1, y1, w, h]}
        except Exception:
            logging.getLogger(__name__).exception("YOLO preview detection failed; falling back to Haar")

    # Haar fallback
    try:
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # allow smaller minSize to work with lower-res webcams
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60,60))
        if len(faces) > 0:
            x, y, w, h = faces[0]
            # require a minimum area fraction as well
            h_img, w_img = img.shape[:2]
            if (w * h) / float(max(1, w_img * h_img)) < 0.04:
                return {"detected": False}
            logging.getLogger(__name__).debug('preview: Haar detection accepted (area_ratio=%.4f)', (w * h) / float(max(1, w_img * h_img)))
            return {"detected": True, "bbox": [int(x), int(y), int(w), int(h)]}
    except Exception:
        logging.getLogger(__name__).exception("Haar fallback failed for preview")

    return {"detected": False}


@bp.route("/preview/detect", methods=["POST"])
def preview_detect():
    data = request.form.get("image")
    if data and data.startswith("data:"):
        header, encoded = data.split(",", 1)
        try:
            image_bytes = base64.b64decode(encoded)
        except Exception:
            return jsonify({"error": "invalid image data"}), 400
    else:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no image provided"}), 400
        image_bytes = f.read()

    result = detect_face_in_frame_bytes(image_bytes)
    return jsonify(result)


@bp.route('/realtime/recognize', methods=['POST'])
def realtime_recognize():
    """Accepts a base64 image (form=image) and class_id (form/class_id).
    Returns a list of detected faces with bbox and optional match info.
    Keeps backward-compatible fields for single-face clients.
    """
    # allow assigning to module-level tracker vars in this function
    global _preview_tracker, _preview_tracker_session_id
    image_b64 = request.form.get('image')
    class_id = request.form.get('class_id')
    write_db = request.form.get('write', '1') != '0'

    if not image_b64 or not class_id:
        return jsonify({'ok': False, 'error': 'image and class_id required'}), 400

    # decode image
    if image_b64.startswith('data:'):
        header, encoded = image_b64.split(',', 1)
        try:
            image_bytes = base64.b64decode(encoded)
        except Exception:
            return jsonify({'ok': False, 'error': 'invalid image data'}), 400
    else:
        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            return jsonify({'ok': False, 'error': 'invalid image data'}), 400

    # prepare image
    import cv2
    import numpy as np
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return jsonify({'ok': False, 'error': 'failed to decode image'}), 400

    # Try to get multiple detections from YOLO if available, otherwise Haar fallback
    faces = []
    yolo = get_detector()
    if yolo:
        try:
            # smaller input size to reduce inference time
            res = yolo(img, imgsz=512, conf=0.5)
            if len(res) and len(res[0].boxes) > 0:
                for b in res[0].boxes.xyxy:
                    coords = b.cpu().numpy().tolist()
                    x1, y1, x2, y2 = map(int, coords[:4])
                    w = x2 - x1
                    h = y2 - y1
                    faces.append([x1, y1, w, h])
        except Exception:
            logging.getLogger(__name__).exception("YOLO preview detection failed; falling back to Haar")

    if not faces:
        try:
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            detected = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60,60))
            for (x, y, w, h) in detected:
                faces.append([int(x), int(y), int(w), int(h)])
        except Exception:
            logging.getLogger(__name__).exception('Haar fallback failed for preview')

    if not faces:
        return jsonify({'ok': True, 'detected': False, 'faces': []})

    # load class embeddings & meta once
    try:
        from ai.face_attendance import load_class_embeddings_and_meta, euclidean_distance
        embeddings, meta = load_class_embeddings_and_meta(int(class_id))
    except Exception:
        logging.getLogger(__name__).exception('failed loading class embeddings')
        return jsonify({'ok': False, 'error': 'failed loading class embeddings'}), 500

    # NOTE: session-ended check moved below after reading attendance_session_id from the form

    from ai.utils import get_face_encodings

    THRESH = 0.55
    attendance_session_id = request.form.get('attendance_session_id')
    attendance_type = request.form.get('attendance_type')
    # If tracker exists and session changed, reset tracking state so matches in
    # the new session can be saved again (avoid "already saved in this track" freeze).
    if _preview_tracker is not None and attendance_session_id:
        try:
            global _preview_tracker_session_id
            if str(_preview_tracker_session_id) != str(attendance_session_id):
                try:
                    _preview_tracker.reset()
                except Exception:
                    # if reset not available, recreate tracker instance
                    try:
                        _preview_tracker = SimpleTracker(iou_threshold=0.3, max_age=8, confirm_frames=3)  # type: ignore
                    except Exception:
                        pass
                _preview_tracker_session_id = str(attendance_session_id)
        except Exception:
            pass
    # If attendance_session_id provided, verify session not ended
    if attendance_session_id:
        try:
            from models import AttendanceSession
            sess = AttendanceSession.query.get(int(attendance_session_id))
            if sess and (getattr(sess, 'has_end_attendance', False) or getattr(sess, 'ended_at', None) is not None):
                current_app.logger.info('realtime_recognize: session %s already ended - ignoring frame', attendance_session_id)
                # return OK but no detections so client will not mark any attendance
                return jsonify({'ok': True, 'detected': False, 'faces': []})
        except Exception:
            # if lookup fails, continue as before
            pass
    try:
        # log incoming form keys and important fields (do NOT log image content)
        img_present = 'image' in request.form or 'image' in request.files
        current_app.logger.debug(
            "realtime_recognize request: class_id=%s attendance_session_id=%r attendance_type=%r write=%s image_present=%s form_keys=%s",
            class_id, attendance_session_id, attendance_type, write_db, img_present, list(request.form.keys())
        )
    except Exception:
        current_app.logger.exception('failed to log realtime_recongize debug info')

    # If tracker available, map detections -> track ids so we can require
    # confirm_frames before calling save_attendance.
    track_ids = _preview_tracker.update(faces) if _preview_tracker is not None else [None] * len(faces)

    out_faces = []
    for idx, (x, y, w, h) in enumerate(faces):
        # associated track id for this detection (may be None)
        tid = track_ids[idx] if track_ids and idx < len(track_ids) else None
        x2 = x + w
        y2 = y + h
        h_img, w_img = img.shape[:2]
        x0 = max(0, min(w_img-1, int(x)))
        x2 = max(0, min(w_img, int(x2)))
        y0 = max(0, min(h_img-1, int(y)))
        y2 = max(0, min(h_img, int(y2)))
        face = img[y0:y2, x0:x2]

        try:
            enc = get_face_encodings(face)
        except Exception:
            logging.getLogger(__name__).exception('embedding failed for one face')
            enc = None

        face_result = {'bbox': [x0, y0, x2 - x0, y2 - y0], 'match': None, 'track_id': tid}

        if enc is not None and len(embeddings) > 0:
            best_id = None
            best_dist = float('inf')
            for sid, vec in embeddings.items():
                try:
                    d = euclidean_distance(enc, vec)
                except Exception:
                    continue
                if d < best_dist:
                    best_dist = d
                    best_id = sid
            if best_id is not None and best_dist <= THRESH:
                # Ensure the matched student is actually enrolled in this class.
                # This is an extra safety check: embeddings are normally loaded
                # only for enrolled students, but double-check here so a student
                # who has an embedding in the DB but is not enrolled will not be
                # recognized/saved for this class.
                try:
                    from models import Enrollment
                    enrolled = Enrollment.query.filter_by(student_id=best_id, class_section_id=int(class_id)).first()
                except Exception:
                    enrolled = None

                if not enrolled:
                    # treat as no-match for this class
                    current_app.logger.info('realtime_recognize: matched student %s not enrolled in class %s - suppressing match', best_id, class_id)
                    best_id = None
                    best_dist = float('inf')

                name, mssv = meta.get(best_id, (f'ID {best_id}', ''))
                face_result['match'] = {'student_id': best_id, 'name': name, 'mssv': mssv, 'dist': float(best_dist)}
                # decide whether to persist this match based on tracker confirmation
                should_save = False
                # tid already computed above
                confirmed = False
                if _preview_tracker is not None and tid is not None:
                    try:
                        # note_match returns True when the track is newly confirmed
                        just_confirmed = _preview_tracker.note_match(tid, best_id, True)
                        confirmed = _preview_tracker.is_confirmed(tid, best_id)
                        if just_confirmed:
                            should_save = True
                    except Exception:
                        just_confirmed = False
                        confirmed = False
                else:
                    # fallback: immediate save on first match
                    should_save = True
                    confirmed = True

                # attach confirmed flag so clients can show 'pending' vs 'confirmed'
                face_result['confirmed'] = bool(confirmed)

                if write_db and should_save:
                    # Strict: only save when a valid attendance_session_id is provided.
                    # This prevents creating floating (no-session) rows that won't show up in session history.
                    if not attendance_session_id:
                        logging.getLogger(__name__).info('Skip saving attendance: missing attendance_session_id (class=%s student=%s)', class_id, best_id)
                        face_result['db_saved'] = False
                    else:
                        try:
                            from ai.face_attendance import save_attendance
                            save_attendance(best_id, int(class_id), source='web-realtime', attendance_session_id=attendance_session_id, attendance_type=attendance_type)
                            face_result['db_saved'] = True
                        except Exception:
                            logging.getLogger(__name__).exception('failed to save attendance for matched face')
                            face_result['db_saved'] = False
                else:
                    # ensure db_saved present and false when not saved
                    face_result['db_saved'] = face_result.get('db_saved', False)

        out_faces.append(face_result)

    # keep backward-compatible single-face fields when only one face was returned
    result = {'ok': True, 'detected': True, 'faces': out_faces}
    if len(out_faces) == 1:
        result['bbox'] = out_faces[0]['bbox']
        result['match'] = out_faces[0]['match']

    return jsonify(result)


@bp.route('/preview/tracker_state')
def preview_tracker_state():
    """Debug endpoint: return tracker state for inspection.

    Only allowed when app is in debug mode to avoid leaking runtime state.
    """
    from flask import current_app
    if not getattr(current_app, 'debug', False):
        return jsonify({'ok': False, 'error': 'tracker_state only available in debug mode'}), 403

    if _preview_tracker is None:
        return jsonify({'ok': True, 'tracks': {}})
    try:
        state = _preview_tracker.dump_state()
        return jsonify({'ok': True, 'tracks': state})
    except Exception:
        logging.getLogger(__name__).exception('failed to dump tracker state')
        return jsonify({'ok': False, 'error': 'failed to dump tracker state'}), 500