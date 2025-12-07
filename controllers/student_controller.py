from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import sys
import os
import subprocess
import time
import json
from datetime import datetime

from models import Student, Enrollment, ClassSection, ClassAdmin, Attendance, AttendanceSession, BackgroundProcess, Semester, db
from config import Config
import psutil
import logging

logger = logging.getLogger(__name__)

student_bp = Blueprint("student", __name__, url_prefix="/student")


# process registry persisted in DB via BackgroundProcess


@student_bp.before_request
def require_student():
    if not current_user.is_authenticated or current_user.role != "student":
        from flask import redirect, url_for
        return redirect(url_for("auth.login"))


@student_bp.route("/dashboard")
@login_required
def dashboard():
    student = Student.query.filter_by(user_id=current_user.id).first()
    class_admin = None
    sections = []
    semesters = []
    selected_semester_id = request.args.get('semester_id', type=int)
    
    if student:
        class_admin = ClassAdmin.query.get(student.class_admin_id) if student.class_admin_id else None
        enrolls = Enrollment.query.filter_by(student_id=student.id).all()
        section_ids = [e.class_section_id for e in enrolls]
        
        if section_ids:
            # Get all sections for this student
            query = ClassSection.query.filter(ClassSection.id.in_(section_ids))
            
            # Filter by semester if selected
            if selected_semester_id:
                query = query.filter(ClassSection.semester_id == selected_semester_id)
            
            sections = query.all()
            
            # Get list of semesters using JOIN - hiệu quả hơn
            semesters = db.session.query(Semester).join(
                ClassSection, Semester.id == ClassSection.semester_id
            ).filter(
                ClassSection.id.in_(section_ids)
            ).distinct().order_by(Semester.start_date.desc()).all()
    
    return render_template("student/dashboard.html", 
                         student=student, 
                         class_admin=class_admin, 
                         sections=sections,
                         semesters=semesters,
                         selected_semester_id=selected_semester_id)


@student_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    student = Student.query.filter_by(user_id=current_user.id).first()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Tên không được để trống", "danger")
            return redirect(url_for("student.profile"))
        student.name = name
        
        # Update gender
        gender = request.form.get("gender", "").strip()
        if gender:
            student.gender = gender
        
        # Update date of birth
        dob_str = request.form.get("date_of_birth", "").strip()
        if dob_str:
            try:
                student.date_of_birth = datetime.strptime(dob_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Định dạng ngày sinh không hợp lệ", "warning")
        
        from models import db
        db.session.commit()
        flash("Đã cập nhật hồ sơ", "success")
        return redirect(url_for("student.profile"))
    return render_template("student/profile.html", student=student)


@student_bp.route("/register_face")
@login_required
def register_face():
    student = Student.query.filter_by(user_id=current_user.id).first()
    running = False
    if student:
        bp = BackgroundProcess.query.filter_by(proc_type="register", target_id=student.id, status="running").order_by(BackgroundProcess.started_at.desc()).first()
        if bp:
            try:
                p = psutil.Process(bp.pid)
                running = p.is_running()
            except Exception:
                running = False
    return render_template("student/register_face.html", student=student, running=running)


@student_bp.route("/start_register", methods=["POST"])
@login_required
def start_register():
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({"ok": False, "error": "Không tìm thấy sinh viên"}), 404
    # check existing running process
    bp_existing = BackgroundProcess.query.filter_by(proc_type="register", target_id=student.id, status="running").first()
    if bp_existing:
        try:
            p = psutil.Process(bp_existing.pid)
            if p.is_running():
                return jsonify({"ok": True, "status": "already_running"})
        except Exception:
            # stale entry - mark stopped
            bp_existing.status = "stopped"
            db.session.commit()

    python_exe = sys.executable or "python"
    cmd = [python_exe, "ai/face_capture.py", "--student_id", str(student.id), "--duration", "10", "--save-db", "True"]
    try:
        # remove any previous status files so client polling won't pick up stale results
        try:
            import glob
            pattern = os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_*.json")
            for p in glob.glob(pattern):
                try:
                    os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        # prepare logfile for this register run
        try:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            log_path = os.path.join(Config.UPLOAD_FOLDER, f"register_log_{student.id}_{int(time.time())}.log")
            lf = open(log_path, "a", encoding="utf-8")
        except Exception:
            lf = None
            log_path = None

        if lf:
            proc = subprocess.Popen(cmd, cwd=os.getcwd(), creationflags=creationflags, stdout=lf, stderr=lf)
        else:
            proc = subprocess.Popen(cmd, cwd=os.getcwd(), creationflags=creationflags)
        # persist pid in DB
        bp = BackgroundProcess(pid=proc.pid, proc_type="register", target_id=student.id, status="running")
        db.session.add(bp)
        db.session.commit()
        logger.info("Started register process pid=%s for student=%s", proc.pid, student.id)
        # include expected duration for client-side countdown
        try:
            duration = int(10)
        except Exception:
            duration = 10
        return jsonify({"ok": True, "status": "started", "duration": duration})
    except Exception as e:
        logger.exception("Failed to start register process: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@student_bp.route("/stop_register", methods=["POST"])
@login_required
def stop_register():
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({"ok": False, "error": "Không tìm thấy sinh viên"}), 404
    bp = BackgroundProcess.query.filter_by(proc_type="register", target_id=student.id, status="running").order_by(BackgroundProcess.started_at.desc()).first()
    if not bp:
        return jsonify({"ok": True, "status": "not_running"})
    # Do not allow stopping while server-side register is actively running
    try:
        p = psutil.Process(bp.pid)
        if p.is_running():
            # refuse to stop to ensure recording completes
            return jsonify({"ok": False, "error": "Không thể dừng trong khi hệ thống đang quay"}), 400
        else:
            bp.status = "stopped"
            db.session.commit()
            return jsonify({"ok": True, "status": "stopped"})
    except Exception as e:
        logger.exception("Failed to stop process pid=%s: %s", getattr(bp, 'pid', '?'), e)
        return jsonify({"ok": False, "error": str(e)}), 500


@student_bp.route("/register_status")
@login_required
def register_status():
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({"ok": False, "error": "Không tìm thấy sinh viên"}), 404
    bp = BackgroundProcess.query.filter_by(proc_type="register", target_id=student.id, status="running").order_by(BackgroundProcess.started_at.desc()).first()
    running = False
    if bp:
        try:
            p = psutil.Process(bp.pid)
            running = p.is_running()
            if not running:
                bp.status = "stopped"
                db.session.commit()
        except Exception:
            running = False
    # include last status file content if exists
    status_payload = None
    try:
        import glob, json, os
        pattern = os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_*.json")
        files = sorted(glob.glob(pattern))
        if files:
            with open(files[-1], "r", encoding="utf-8") as f:
                status_payload = json.load(f)
    except Exception:
        status_payload = None
    return jsonify({"ok": True, "running": running, "status": status_payload})


@student_bp.route('/upload_register', methods=['POST'])
@login_required
def upload_register():
    """Accept multiple image files from client, extract face embeddings and save average to DB."""
    student = Student.query.filter_by(user_id=current_user.id).first()
    if not student:
        return jsonify({"ok": False, "error": "Không tìm thấy sinh viên"}), 404

    files = request.files.getlist('images') or []
    if not files:
        flash_msg = {'message': 'Không có ảnh gửi lên', 'category': 'warning'}
        return jsonify({"ok": False, "error": "Không có ảnh gửi lên", 'flash': flash_msg}), 400

    # load model(s) lazily inside function
    try:
        from ai.utils import load_yolo_model, detect_faces_yolo, get_face_encodings
        model = load_yolo_model(Config.YOLO_MODEL_PATH)
    except Exception as e:
        logger.exception('Failed to load detection model: %s', e)
        flash_msg = {'message': 'Mô-đun phát hiện không sẵn sàng', 'category': 'danger'}
        return jsonify({"ok": False, "error": "Mô-đun phát hiện không sẵn sàng", 'flash': flash_msg}), 500

    import numpy as np
    import cv2

    logger.info('upload_register received %d files for student=%s', len(files), student.id)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    embeddings = []
    for f in files:
        try:
            data = f.read()
            nparr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                logger.warning('upload_register: failed to decode image %s', getattr(f, 'filename', '<unknown>'))
                # still save raw bytes for inspection
                try:
                    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
                    dump_path = os.path.join(Config.UPLOAD_FOLDER, f"debug_frame_{student.id}_{ts}_{getattr(f, 'filename', 'unknown')}")
                    with open(dump_path, 'wb') as df:
                        df.write(data)
                    logger.info('upload_register: wrote raw failed-decode bytes to %s', dump_path)
                except Exception:
                    logger.exception('upload_register: failed to dump raw bytes')
                continue
            logger.info('upload_register: image %s shape=%s', getattr(f, 'filename', '<unknown>'), img.shape)
            # save decoded image for debugging
            try:
                os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
                dbg_name = os.path.join(Config.UPLOAD_FOLDER, f"debug_frame_{student.id}_{ts}_{getattr(f, 'filename', 'frame')}")
                cv2.imwrite(dbg_name, img)
                logger.info('upload_register: saved decoded image to %s', dbg_name)
            except Exception:
                logger.exception('upload_register: failed to write debug image')
            if img is None:
                continue
            boxes = detect_faces_yolo(model, img)
            logger.info('upload_register: detected %d boxes in image %s', len(boxes) if boxes else 0, getattr(f, 'filename', '<unknown>'))
            if not boxes:
                continue
            # crop first detected face
            x1, y1, x2, y2 = boxes[0]
            face = img[y1:y2, x1:x2]
            enc = get_face_encodings(face)
            if enc:
                embeddings.append(enc)
        except Exception:
            logger.exception('Failed processing uploaded image')
            continue

    # prepare status
    status = {"ok": False, "student_id": student.id, "num_frames": len(embeddings), "saved": False, "message": "", "error_type": None}
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    if not embeddings:
        # Check if face_recognition module is available
        try:
            import face_recognition as fr_check
            status['message'] = 'Không phát hiện khuôn mặt nào trong các ảnh đã tải lên'
            status['error_type'] = 'no_face_detected'
        except Exception:
            status['message'] = 'Lỗi hệ thống: Module nhận diện khuôn mặt không khả dụng. Vui lòng liên hệ quản trị viên.'
            status['error_type'] = 'system_error'
        try:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_{ts}.json"), 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception('Failed to write status file')
        # also include a flash payload so AJAX clients can render messages immediately
        flash_msg = {'message': 'Không phát hiện khuôn mặt trong ảnh', 'category': 'warning'}
        return jsonify({"ok": True, "status": status, 'flash': flash_msg})
    
    if len(embeddings) < 10:
        status['message'] = f'Chỉ thu thập được {len(embeddings)} khung hình, cần tối thiểu 10'
        status['error_type'] = 'insufficient_frames'
        try:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_{ts}.json"), 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception('Failed to write status file')
        flash_msg = {'message': status['message'], 'category': 'warning'}
        return jsonify({"ok": True, "status": status, 'flash': flash_msg})

    # average embedding
    try:
        arr = np.array(embeddings, dtype=float)
        avg_embedding = arr.mean(axis=0).astype(float).tolist()
    except Exception as e:
        status['message'] = f'Lỗi xử lý dữ liệu khuôn mặt: {str(e)}'
        status['error_type'] = 'processing_error'
        try:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_{ts}.json"), 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception('Failed to write status file')
        flash_msg = {'message': 'Lỗi xử lý ảnh', 'category': 'danger'}
        return jsonify({"ok": True, "status": status, 'flash': flash_msg})

    # Save to DB
    try:
        # Prevent duplicate face across different accounts (same logic as CLI)
        try:
            from ai.face_attendance import euclidean_distance
            dup_owner_id = None
            best_dist = 1e9
            all_distances = []  # Track all distances for debugging
            
            for other in Student.query.all():
                # Skip the current student being registered
                if other.id == student.id:
                    continue
                vec = other.get_embedding()
                if not vec:
                    continue
                d = euclidean_distance(avg_embedding, vec)
                all_distances.append((other.id, other.mssv, d))
                if d < best_dist:
                    best_dist = d
                    dup_owner_id = other.id
            
            # Log all distances for debugging
            if all_distances:
                logger.info("[UPLOAD] Duplicate check for student %s (MSSV: %s) - Distances to other students: %s", 
                           student.id, student.mssv, all_distances)
                logger.info("[UPLOAD] Best match: student_id=%s, distance=%.6f", dup_owner_id, best_dist)
            else:
                logger.info("[UPLOAD] No other students with embeddings to compare for student %s", student.id)
            
            # Using threshold of 0.25 - only block when faces are extremely similar
            # (normalized euclidean distance < 0.25 indicates very high similarity)
            if dup_owner_id is not None and best_dist < 0.25:
                dup_student = Student.query.get(dup_owner_id)
                dup_mssv = dup_student.mssv if dup_student else str(dup_owner_id)
                logger.warning("[UPLOAD] Duplicate face detected for student %s: matches student %s with distance %.6f", 
                              student.id, dup_owner_id, best_dist)
                status['message'] = f"Khuôn mặt này đã được đăng ký cho sinh viên khác (MSSV: {dup_mssv}, độ tương đồng: {best_dist:.4f})"
                status['error_type'] = 'duplicate_face'
                status['ok'] = False
                status['saved'] = False
                status['duplicate_student_id'] = dup_owner_id
                status['distance'] = best_dist
                # write status file for client polling
                try:
                    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
                    with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_{ts}.json"), 'w', encoding='utf-8') as f:
                        json.dump(status, f, ensure_ascii=False, indent=2)
                except Exception:
                    logger.exception('Failed to write duplicate status file')
                
                # Clean up debug frames before returning error
                try:
                    import glob
                    pattern_frame = os.path.join(Config.UPLOAD_FOLDER, f"debug_frame_{student.id}_*")
                    for p in glob.glob(pattern_frame):
                        try:
                            os.remove(p)
                            logger.info('Removed debug frame after duplicate detection %s', p)
                        except Exception:
                            logger.exception('Failed to remove debug frame %s', p)
                except Exception:
                    logger.exception('Failed during duplicate cleanup')
                
                flash_msg = {'message': status['message'], 'category': 'danger'}
                return jsonify({"ok": False, 'status': status, 'flash': flash_msg}), 400
            else:
                logger.info("[UPLOAD] No duplicate detected for student %s (best_dist=%.6f)", student.id, best_dist)
        except Exception:
            # if duplicate check fails for any reason, continue to save (best-effort)
            logger.exception('Duplicate-check failed; proceeding to save')

        student.set_embedding(avg_embedding)
        db.session.commit()
        status['saved'] = True
        status['ok'] = True
        status['message'] = f'Đăng ký thành công với {len(embeddings)} khung hình'
    except Exception as e:
        logger.exception('Failed to save embedding to DB')
        status['message'] = f'Lỗi lưu dữ liệu: {str(e)}'
        status['error_type'] = 'database_error'

    try:
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        with open(os.path.join(Config.UPLOAD_FOLDER, f"register_status_{student.id}_{ts}.json"), 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception('Failed to write status file')

    # If saved successfully, clean up debug artifacts for this student
    try:
        if status.get('saved'):
            import glob
            pattern_frame = os.path.join(Config.UPLOAD_FOLDER, f"debug_frame_{student.id}_*")
            for p in glob.glob(pattern_frame):
                try:
                    os.remove(p)
                    logger.info('Removed debug frame %s', p)
                except Exception:
                    logger.exception('Failed to remove debug frame %s', p)
            pattern_log = os.path.join(Config.UPLOAD_FOLDER, f"register_log_{student.id}_*.log")
            for p in glob.glob(pattern_log):
                try:
                    os.remove(p)
                    logger.info('Removed register log %s', p)
                except Exception:
                    logger.exception('Failed to remove register log %s', p)
    except Exception:
        logger.exception('Failed during debug cleanup')

    # If not saved, also remove debug frames to avoid leaving uploaded images on disk
    # Note: Embedding cũ trong DB vẫn được giữ lại khi upload thất bại
    try:
        if not status.get('saved'):
            import glob
            pattern_frame = os.path.join(Config.UPLOAD_FOLDER, f"debug_frame_{student.id}_*")
            for p in glob.glob(pattern_frame):
                try:
                    os.remove(p)
                    logger.info('Removed debug frame after failed upload %s', p)
                except Exception:
                    logger.exception('Failed to remove debug frame after failed upload %s', p)
    except Exception:
        logger.exception('Failed during failure cleanup')

    # return with flash payload depending on result
    if status.get('saved'):
        flash_msg = {'message': 'đăng kí khuôn mặt thành công', 'category': 'success'}
        return jsonify({"ok": True, "status": status, 'flash': flash_msg})
    else:
        # DB save failed or other failure: include danger flash with status message
        flash_msg = {'message': status.get('message', 'Đăng ký không thành công'), 'category': 'danger'}
        return jsonify({"ok": True, "status": status, 'flash': flash_msg})


@student_bp.route("/attendance_history")
@login_required
def attendance_history():
    student = Student.query.filter_by(user_id=current_user.id).first()
    # allow optional semester and class_section filter
    semester_id = request.args.get('semester_id', type=int)
    class_section_id = request.args.get('class_section_id', type=int)
    
    semesters = []
    class_sections = []
    sessions_data = []
    present_total = 0
    absent_total = 0
    selected_semester = None
    selected_section = None

    if student:
        # Get student's enrolled class sections
        enrolls = Enrollment.query.filter_by(student_id=student.id).all()
        section_ids = [e.class_section_id for e in enrolls]
        
        if section_ids:
            # Get list of semesters (học kỳ) from enrolled classes
            semesters = db.session.query(Semester).join(
                ClassSection, Semester.id == ClassSection.semester_id
            ).filter(
                ClassSection.id.in_(section_ids)
            ).distinct().order_by(Semester.start_date.desc()).all()
            
            # If semester selected, get classes in that semester
            if semester_id:
                selected_semester = Semester.query.get(semester_id)
                class_sections = ClassSection.query.filter(
                    ClassSection.id.in_(section_ids),
                    ClassSection.semester_id == semester_id
                ).all()
            # If no semester selected, show all classes
            else:
                class_sections = ClassSection.query.filter(ClassSection.id.in_(section_ids)).all()

        if class_section_id:
            # ensure student is enrolled in requested section
            if class_section_id in section_ids:
                selected_section = ClassSection.query.get(class_section_id)
                # fetch attendance sessions for this class section ordered by session_number
                sessions = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number).all()
                idx = 1
                for s in sessions:
                    # try to find attendance row for this student linked to the session
                    att = Attendance.query.filter_by(student_id=student.id, attendance_session_id=s.id).first()
                    if att:
                        # treat known status values: present vs absent
                        st = (att.status or '').lower()
                        is_present = st.startswith('p') or st in ('present', 'present_auto', 'present_manual', 'có mặt', 'co mat')
                        status_label = 'có mặt' if is_present else 'vắng'
                        time_str = att.timestamp.strftime('%H:%M:%S %d/%m/%Y') if att.timestamp else '-'
                    else:
                        # no attendance row: treat as 'vắng'
                        status_label = 'vắng'
                        time_str = '-'

                    if status_label == 'có mặt':
                        present_total += 1
                    else:
                        absent_total += 1

                    sessions_data.append({
                        'index': idx,
                        'session_number': s.session_number,
                        'session_date': s.session_date.strftime('%d/%m/%Y') if s.session_date else '',
                        'status': status_label,
                        'time': time_str
                    })
                    idx += 1

    return render_template("student/attendance_history.html",
                           semesters=semesters,
                           selected_semester=selected_semester,
                           class_sections=class_sections,
                           selected_section=selected_section,
                           sessions=sessions_data,
                           present_total=present_total,
                           absent_total=absent_total)
