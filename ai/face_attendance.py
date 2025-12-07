import argparse
import csv
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import logging
import cv2
import numpy as np

from config import Config
from ai.utils import load_yolo_model, detect_faces_yolo, get_face_encodings
from models import db, Student, Enrollment, ClassSection, Attendance


logger = logging.getLogger(__name__)


def euclidean_distance(a: List[float], b: List[float]) -> float:
    # normalize vectors before computing Euclidean distance so thresholds are
    # more stable across different embedding magnitudes
    va = np.array(a, dtype=float)
    vb = np.array(b, dtype=float)
    try:
        # avoid division by zero
        va_norm = va / (np.linalg.norm(va) + 1e-10)
        vb_norm = vb / (np.linalg.norm(vb) + 1e-10)
        return float(np.linalg.norm(va_norm - vb_norm))
    except Exception:
        return float(np.linalg.norm(va - vb))


def load_class_embeddings_and_meta(class_id: int) -> Tuple[Dict[int, List[float]], Dict[int, Tuple[str, str]]]:
    # Prefer using current_app if available to avoid creating a new Flask app
    try:
        from flask import current_app
        ctx = current_app._get_current_object()  # will raise if no context
        enrolls = Enrollment.query.filter_by(class_section_id=class_id).all()
        student_ids = [e.student_id for e in enrolls]
        embeddings: Dict[int, List[float]] = {}
        meta: Dict[int, Tuple[str, str]] = {}
        for st in Student.query.filter(Student.id.in_(student_ids)).all():
            vec = st.get_embedding()
            if vec:
                embeddings[st.id] = vec
                meta[st.id] = (st.name, st.mssv)
        return embeddings, meta
    except Exception:
        # fallback: create a temporary app context (used when running as a subprocess)
        from app import create_app
        app = create_app()
        with app.app_context():
            enrolls = Enrollment.query.filter_by(class_section_id=class_id).all()
            student_ids = [e.student_id for e in enrolls]
            embeddings = {}
            meta = {}
            for st in Student.query.filter(Student.id.in_(student_ids)).all():
                vec = st.get_embedding()
                if vec:
                    embeddings[st.id] = vec
                    meta[st.id] = (st.name, st.mssv)
            return embeddings, meta


def save_attendance(student_id: int, class_id: int, source: str = "realtime", attendance_session_id: Optional[int] = None, attendance_type: Optional[str] = None) -> None:
    # attempt to use current_app context if running inside Flask; otherwise create a short-lived app
    tried_context = False
    try:
        from flask import current_app
        ctx = current_app._get_current_object()
        tried_context = True
    except Exception:
        tried_context = False

    if tried_context:
        # we're inside the app context
        _save_att_inner(student_id, class_id, source, attendance_session_id, attendance_type)
        return
    # fallback to creating app for standalone subprocess
    from app import create_app
    app = create_app()
    with app.app_context():
        _save_att_inner(student_id, class_id, source, attendance_session_id, attendance_type)


def _save_att_inner(student_id: int, class_id: int, source: str = "realtime", attendance_session_id: Optional[int] = None, attendance_type: Optional[str] = None) -> None:
    # If caller didn't provide a session id, try best-effort to associate this
    # attendance to an existing session for the class/date. This helps when the
    # client saved attendance before the teacher "started" the session in the
    # UI or when the session id wasn't forwarded.
    if attendance_session_id is None:
        try:
            from models import AttendanceSession
            # prefer a session for today if possible and prefer matching time_bucket
            from datetime import date as _date
            today_for_query = _date.today()
            # compute current local bucket
            now_local = datetime.now()
            hour_local = now_local.hour
            if hour_local < 12:
                bucket_pref = 'morning'
            elif hour_local < 17:
                bucket_pref = 'afternoon'
            else:
                bucket_pref = 'evening'
            # prefer session matching the bucket first
            session = AttendanceSession.query.filter_by(class_section_id=class_id, session_date=today_for_query, time_bucket=bucket_pref).order_by(AttendanceSession.session_number.desc()).first()
            if not session:
                # fallback to any session today
                session = AttendanceSession.query.filter_by(class_section_id=class_id, session_date=today_for_query).order_by(AttendanceSession.session_number.desc()).first()
            if session:
                # only associate to a session that hasn't been ended
                try:
                    if getattr(session, 'ended_at', None) is not None:
                        # session already ended - do not associate
                        attendance_session_id = None
                    else:
                        attendance_session_id = int(session.id)
                except Exception:
                    attendance_session_id = int(session.id)
        except Exception:
            # ignore any failures and continue without session association
            attendance_session_id = attendance_session_id

    # avoid duplicate attendance for the same student on the same day
    # use a single timestamp for both `timestamp` and `attendance_date`
    from datetime import datetime as _dt
    # use local system time so saved timestamp matches what the user sees in the browser
    now = _dt.now()
    today = now.date()

    # If a session id was provided, prefer checking existence by that session
    # to avoid duplicate rows when multiple faces/frames are saved in parallel.
    if attendance_session_id is not None:
        # Enforce per-session uniqueness strictly and do not auto-associate
        # previous "floating" (NULL session) rows to newly created sessions.
        # Each session must start clean.
        try:
            q = Attendance.query.filter(
                Attendance.student_id == student_id,
                Attendance.class_section_id == class_id,
                Attendance.attendance_session_id == int(attendance_session_id),
            )
            exists = q.first()
        except Exception:
            exists = None

        # If the target session has ended, skip saving entirely
        try:
            from models import AttendanceSession
            _sess = AttendanceSession.query.get(int(attendance_session_id))
            if _sess is not None and getattr(_sess, 'ended_at', None) is not None:
                logger.info('Session %s already ended - skip saving attendance for student=%s class=%s', attendance_session_id, student_id, class_id)
                return
        except Exception:
            # non-fatal; continue
            pass

        if exists:
            logger.info('Attendance already exists (session scope) for student=%s class=%s session=%s', student_id, class_id, attendance_session_id)
            return
    else:
        # No session provided: keep legacy per-day uniqueness
        exists_day = Attendance.query.filter(
            Attendance.student_id == student_id,
            Attendance.class_section_id == class_id,
            getattr(Attendance, 'attendance_date') == today
        ).first()
        if exists_day:
            logger.info('Attendance already exists (date scope) for student=%s class=%s today', student_id, class_id)
            return

    # set timestamp and attendance_date explicitly so they match the save time
    kwargs = dict(student_id=student_id, class_section_id=class_id, status="present", source=source, timestamp=now)
    if hasattr(Attendance, 'attendance_date'):
        kwargs['attendance_date'] = today
    # optional session metadata
    if attendance_session_id is not None:
        try:
            kwargs['attendance_session_id'] = int(attendance_session_id)
        except Exception:
            pass
    # attendance_type no longer used; single attendance per session

    att = Attendance(**kwargs)
    db.session.add(att)
    try:
        # commit with simple retry/backoff to tolerate transient DB locks (SQLite)
        import time
        from sqlalchemy.exc import OperationalError, IntegrityError
        saved_new = False
        existed_race = False
        for attempt in range(4):
            try:
                db.session.commit()
                saved_new = True
                break
            except OperationalError as oe:
                try:
                    db.session.rollback()
                except Exception:
                    pass
                sleep = 0.05 * (2 ** attempt)
                logger.warning('DB OperationalError on commit, retrying in %.3fs (attempt %s) - %s', sleep, attempt + 1, oe)
                time.sleep(sleep)
            except IntegrityError as ie:
                # probably duplicate due to a concurrent insert; rollback and stop retrying
                try:
                    db.session.rollback()
                except Exception:
                    pass
                existed_race = True
                break
        if saved_new:
            logger.info('Saved attendance for student=%s class=%s', student_id, class_id)
            if attendance_session_id is None:
                logger.warning('Saved attendance without attendance_session_id for student=%s class=%s timestamp=%s', student_id, class_id, now.isoformat())
        elif existed_race:
            logger.info('Attendance already existed (race) for student=%s class=%s', student_id, class_id)
        else:
            raise RuntimeError('Failed to commit attendance after retries')
    except Exception as e:
        # Catch unique constraint / integrity errors caused by concurrent inserts
        try:
            from sqlalchemy.exc import IntegrityError
            if isinstance(e, IntegrityError):
                db.session.rollback()
                logger.info('Attendance insert skipped due to IntegrityError (probably duplicate) for student=%s class=%s', student_id, class_id)
                return
        except Exception:
            pass
        # If it's another error, re-raise after rollback
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception('Failed to save attendance for student=%s class=%s', student_id, class_id)
        raise


def run_realtime(class_id: int, threshold: float = 0.55, session_id: int = None, attendance_type: str = None) -> None:
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    # Load YOLO
    model = load_yolo_model(Config.YOLO_MODEL_PATH)

    # Load embeddings and meta for class
    embeddings, meta = load_class_embeddings_and_meta(class_id)
    logger.info("Loaded %d embeddings for class %d", len(embeddings), class_id)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logger.error("Cannot open camera")
        return

    seen: Dict[int, datetime] = {}
    # lightweight per-face tracker to require multiple-frame confirmation
    try:
        from ai.tracker import SimpleTracker
        tracker = SimpleTracker(iou_threshold=0.3, max_age=10, confirm_frames=3)
    except Exception:
        tracker = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        boxes = detect_faces_yolo(model, frame)
        # convert YOLO [x1,y1,x2,y2] -> [x, y, w, h] and clamp
        dets = []
        h, w = frame.shape[:2]
        for (x1, y1, x2, y2) in boxes:
            x1c = max(0, min(w - 1, int(x1)))
            x2c = max(0, min(w, int(x2)))
            y1c = max(0, min(h - 1, int(y1)))
            y2c = max(0, min(h, int(y2)))
            dets.append((x1c, y1c, max(1, x2c - x1c), max(1, y2c - y1c)))

        # get track ids for each detection (if tracker available)
        track_ids = tracker.update(dets) if tracker is not None else [None] * len(dets)

        for idx, (x, y, wbox, hbox) in enumerate(dets):
            x1c, y1c, x2c, y2c = x, y, x + wbox, y + hbox
            face = frame[y1c:y2c, x1c:x2c]
            # Extract embedding for this face using face_recognition
            try:
                enc = get_face_encodings(face)
                if not enc:
                    logger.debug('No encodings for detected face at box %s', (x1, y1, x2, y2))
                    continue
            except Exception:
                logger.exception("Failed to get face encoding")
                continue

            best_id: Optional[int] = None
            best_dist = 1e9
            for sid, vec in embeddings.items():
                d = euclidean_distance(enc, vec)
                if d < best_dist:
                    best_dist = d
                    best_id = sid

            # Log top-3 nearest distances to help tune threshold
            try:
                dists = [(sid, euclidean_distance(enc, vec)) for sid, vec in embeddings.items()]
                dists.sort(key=lambda x: x[1])
                topn = dists[:3]
                logger.debug('Top matches for box %s: %s', (x1, y1, x2, y2), ', '.join([f"{s}:{dist:.3f}" for s, dist in topn]))
            except Exception:
                logger.exception('Failed computing top distances')

            label = None
            if best_id is not None and best_dist <= threshold:
                # prefer to use tracker to require confirm_frames before saving
                should_save = False
                tid = track_ids[idx] if track_ids and idx < len(track_ids) else None
                if tracker is not None and tid is not None:
                    try:
                        should_save = tracker.note_match(tid, best_id if best_dist <= threshold else None, best_dist <= threshold)
                    except Exception:
                        should_save = False
                else:
                    # fallback: old seen-based single-save behavior
                    if best_id not in seen:
                        should_save = True

                if should_save:
                    logger.info("Matched student %d (dist=%.3f) -> saving (track=%s)", best_id, best_dist, tid)
                    try:
                        save_attendance(best_id, class_id, source="realtime", attendance_session_id=session_id, attendance_type=attendance_type)
                    except Exception:
                        logger.exception('Failed to save attendance with session metadata, falling back to save without session')
                        try:
                            save_attendance(best_id, class_id, source="realtime")
                        except Exception:
                            logger.exception('Failed to save attendance (fallback)')
                    seen[best_id] = datetime.now()
                else:
                    # update last seen time for old behavior and keep track state
                    if best_id is not None:
                        seen[best_id] = datetime.now()
                name, mssv = meta.get(best_id, (f"ID {best_id}", ""))
                label = f"{name} - {mssv} ({best_dist:.2f})"
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            else:
                # draw red box for unknown and show best distance for debugging
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                label = f"Unknown ({best_dist:.2f})" if best_id is not None else "Unknown"

            if label:
                y_label = max(0, y1 - 10)
                color = (0,255,0) if (best_id is not None and best_dist <= threshold) else (0,0,255)
                cv2.putText(frame, label, (x1, y_label), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        cv2.imshow("Attendance", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

    # Save CSV summary
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(Config.UPLOAD_FOLDER, f"attendance_{class_id}_{ts}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["student_id", "timestamp"])
        for sid, t in seen.items():
            writer.writerow([sid, t.isoformat()])
    logger.info("Saved CSV: %s", csv_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--class_id", type=int, required=True)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--session_id", type=int, required=False)
    parser.add_argument("--attendance_type", type=str, required=False, choices=['start', 'end'])
    args = parser.parse_args()
    # pass optional session metadata into run loop so save_attendance can forward it
    run_realtime(args.class_id, args.threshold, session_id=args.session_id, attendance_type=args.attendance_type)


if __name__ == "__main__":
    main()
