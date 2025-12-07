"""Teacher controller: start/stop attendance subprocess and polling endpoints.

This file is intentionally compact and single-versioned to avoid the previous duplication/corruption.
"""

from __future__ import annotations

import logging
import os
import sys
import subprocess
from datetime import datetime, date

from flask import Blueprint, render_template, jsonify, redirect, url_for, request, flash, send_file
from flask_login import login_required, current_user
from io import BytesIO

import psutil
import threading
import openpyxl
from openpyxl.styles import Font, Alignment

from models import (
    ClassSection,
    Teacher,
    Attendance,
    AttendanceSession,
    Student,
    Enrollment,
    Semester,
    db,
)
# Create blueprint early so route decorators below can reference it
teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")


@teacher_bp.route('/api/model_status')
@login_required
def model_status():
    """Simple model health check used by client to decide if Start should be enabled.
    Currently implemented as a quick filesystem check for the YOLO model file.
    """
    try:
        from config import Config
        path = getattr(Config, 'YOLO_MODEL_PATH', None)
        ready = bool(path and os.path.exists(path))
        return jsonify({'ok': True, 'ready': ready, 'path': path})
    except Exception:
        return jsonify({'ok': True, 'ready': False, 'path': None})



logger = logging.getLogger(__name__)


def _bucket_index(bucket: str) -> int:
    """Return an index for bucket ordering: morning < afternoon < evening."""
    order = {'morning': 0, 'afternoon': 1, 'evening': 2}
    return order.get(bucket, 3)


def _auto_close_expired_sessions(class_section_id: int | None = None) -> None:
    """Lazily close any open sessions whose time_bucket/date has passed.

    This is called from request entry points so sessions are closed on demand
    rather than by a background scheduler.
    """
    try:
        now = datetime.now()
        today = now.date()
        hour = now.hour
        if hour < 12:
            current_bucket = 'morning'
        elif hour < 17:
            current_bucket = 'afternoon'
        else:
            current_bucket = 'evening'

        q = AttendanceSession.query.filter(AttendanceSession.ended_at.is_(None))
        if class_section_id:
            q = q.filter(AttendanceSession.class_section_id == class_section_id)

        sessions = q.all()
        changed = False
        for s in sessions:
            # if session is from previous day or same day but older bucket -> close it
            try:
                if getattr(s, 'session_date', None) is None:
                    continue
                if s.session_date < today:
                    s.ended_at = now
                    s.paused = False
                    db.session.add(s)
                    changed = True
                elif s.session_date == today and _bucket_index(s.time_bucket) < _bucket_index(current_bucket):
                    s.ended_at = now
                    s.paused = False
                    db.session.add(s)
                    changed = True
            except Exception:
                continue
        if changed:
            try:
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _auto_delete_unused_sessions(class_section_id: int | None = None) -> None:
    """Delete sessions that expired without being started (no attendance records).
    
    A session is considered unused if:
    - It has no attendance records
    - Its time bucket + date has passed
    """
    try:
        now = datetime.now()
        today = now.date()
        hour = now.hour
        if hour < 12:
            current_bucket = 'morning'
        elif hour < 17:
            current_bucket = 'afternoon'
        else:
            current_bucket = 'evening'

        # Find sessions for the specified class
        q = AttendanceSession.query
        if class_section_id:
            q = q.filter(AttendanceSession.class_section_id == class_section_id)

        sessions = q.all()
        deleted_count = 0
        
        for s in sessions:
            try:
                # Skip if session has no date
                if not getattr(s, 'session_date', None):
                    continue
                
                # Check if session time has passed
                is_expired = False
                if s.session_date < today:
                    is_expired = True
                elif s.session_date == today and _bucket_index(s.time_bucket) < _bucket_index(current_bucket):
                    is_expired = True
                
                if not is_expired:
                    continue
                
                # Delete if session expired but was never closed (ended_at = NULL)
                # This means teacher either:
                # 1. Created session but never started attendance
                # 2. Started but forgot to click "Kết thúc"
                if s.ended_at is None:
                    # Also check if has no attendance records to be safe
                    att_count = Attendance.query.filter_by(
                        attendance_session_id=s.id
                    ).count()
                    
                    if att_count == 0:
                        # Session expired, not closed, and no attendance - delete it
                        logger.info(f"Auto-deleting unused session: id={s.id}, "
                                   f"section={s.class_section_id}, date={s.session_date}, "
                                   f"bucket={s.time_bucket}, ended_at={s.ended_at}")
                        db.session.delete(s)
                        deleted_count += 1
            except Exception as e:
                logger.debug(f"Error checking session {s.id}: {e}")
                continue
        
        if deleted_count > 0:
            try:
                db.session.commit()
                logger.info(f"Auto-deleted {deleted_count} unused sessions")
            except Exception as e:
                logger.error(f"Error committing deleted sessions: {e}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error in _auto_delete_unused_sessions: {e}")
        pass


def _teacher_owns_section(user_id: int, section_id: int) -> bool:
    teacher = Teacher.query.filter_by(user_id=user_id).first()
    if not teacher:
        return False
    section = ClassSection.query.get(section_id)
    return bool(section and section.teacher_id == teacher.id)


@teacher_bp.before_request
def require_teacher():
    if not current_user.is_authenticated or getattr(current_user, "role", None) != "teacher":
        return redirect(url_for("auth.login"))


@teacher_bp.route("/dashboard")
@login_required
def dashboard():
    from models import Semester
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    
    # Get semester filter from query params
    semester_id = request.args.get('semester_id', type=int)
    
    # Get current semester
    current_semester = Semester.get_current_semester()
    
    # Get all semesters for dropdown
    semesters = Semester.query.order_by(Semester.start_date.desc()).all()
    
    # Filter sections by semester if selected
    if teacher:
        if semester_id:
            sections = ClassSection.query.filter_by(
                teacher_id=teacher.id,
                semester_id=semester_id
            ).all()
            selected_semester = Semester.query.get(semester_id)
        else:
            sections = ClassSection.query.filter_by(teacher_id=teacher.id).all()
            selected_semester = None
    else:
        sections = []
        selected_semester = None
    
    return render_template("teacher/dashboard.html", 
                         sections=sections,
                         current_semester=current_semester,
                         semesters=semesters,
                         selected_semester=selected_semester)


@teacher_bp.route("/take_attendance")
@login_required
def attendance_index():
    """List classes for teacher with buttons to open take_attendance per class."""
    from models import Semester
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    
    # Chỉ hiển thị các lớp của học kỳ hiện tại
    current_semester = Semester.get_current_semester()
    
    if teacher and current_semester:
        sections = ClassSection.query.filter_by(
            teacher_id=teacher.id,
            semester_id=current_semester.id
        ).all()
    else:
        sections = []
    
    return render_template("teacher/attendance_index.html", 
                         sections=sections,
                         current_semester=current_semester)


@teacher_bp.route('/sessions/<int:class_section_id>')
@login_required
def sessions_list(class_section_id: int):
    # list sessions for a class, newest first
    if not _teacher_owns_section(current_user.id, class_section_id):
        return redirect(url_for('teacher.attendance_index'))
    
    # Auto-delete unused expired sessions before showing the list
    try:
        _auto_delete_unused_sessions(class_section_id)
    except Exception:
        pass
    
    class_section = ClassSection.query.get_or_404(class_section_id)
    sessions = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.desc()).all()
    # detect if there is an open (not-ended) session for this class so the UI can
    # disable creating a new session while one is active.
    open_sess = AttendanceSession.query.filter_by(class_section_id=class_section_id, ended_at=None).order_by(AttendanceSession.started_at.desc()).first()
    # compute how many sessions exist and the course's allowed number
    try:
        course_total = getattr(class_section.course, 'number_of_sessions', 6) if class_section.course else 6
    except Exception:
        course_total = 6
    try:
        sessions_count = AttendanceSession.query.filter_by(class_section_id=class_section_id).count()
    except Exception:
        sessions_count = len(sessions)

    return render_template('teacher/sessions_list.html', 
                           sessions=sessions, 
                           open_session=open_sess,
                           class_section=class_section, 
                           sessions_count=sessions_count, 
                           course_total=course_total)


@teacher_bp.route("/history")
@login_required
def history_index():
    """List classes for teacher with buttons to view history per class."""
    from models import Semester
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    
    # Lấy semester_id từ query params
    semester_id = request.args.get('semester_id', type=int)
    
    # Lấy danh sách học kỳ của giảng viên
    semesters = []
    sections = []
    selected_semester = None
    
    if teacher:
        # Lấy tất cả lớp của giảng viên
        all_sections = ClassSection.query.filter_by(teacher_id=teacher.id).all()
        section_ids = [s.id for s in all_sections]
        
        if section_ids:
            # Lấy danh sách học kỳ
            semesters = db.session.query(Semester).join(
                ClassSection, Semester.id == ClassSection.semester_id
            ).filter(
                ClassSection.id.in_(section_ids)
            ).distinct().order_by(Semester.start_date.desc()).all()
            
            # Nếu có chọn học kỳ
            if semester_id:
                selected_semester = Semester.query.get(semester_id)
                sections = ClassSection.query.filter_by(
                    teacher_id=teacher.id,
                    semester_id=semester_id
                ).all()
            else:
                sections = all_sections
    
    return render_template("teacher/history_index.html", 
                         sections=sections,
                         semesters=semesters,
                         selected_semester=selected_semester)


@teacher_bp.route('/reports')
@login_required
def report_index():
    from models import Semester
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    
    # Lấy semester_id từ query params
    semester_id = request.args.get('semester_id', type=int)
    
    # Lấy danh sách học kỳ của giảng viên
    semesters = []
    sections = []
    selected_semester = None
    
    if teacher:
        # Lấy tất cả lớp của giảng viên
        all_sections = ClassSection.query.filter_by(teacher_id=teacher.id).all()
        section_ids = [s.id for s in all_sections]
        
        if section_ids:
            # Lấy danh sách học kỳ
            semesters = db.session.query(Semester).join(
                ClassSection, Semester.id == ClassSection.semester_id
            ).filter(
                ClassSection.id.in_(section_ids)
            ).distinct().order_by(Semester.start_date.desc()).all()
            
            # Nếu có chọn học kỳ
            if semester_id:
                selected_semester = Semester.query.get(semester_id)
                sections = ClassSection.query.filter_by(
                    teacher_id=teacher.id,
                    semester_id=semester_id
                ).all()
            else:
                sections = all_sections
    
    return render_template('teacher/report_index.html', 
                         sections=sections,
                         semesters=semesters,
                         selected_semester=selected_semester)


@teacher_bp.route('/report/<int:class_section_id>')
@login_required
def report_view(class_section_id: int):
    if not _teacher_owns_section(current_user.id, class_section_id):
        return redirect(url_for('teacher.attendance_index'))
    
    class_section = ClassSection.query.get_or_404(class_section_id)
    # gather sessions ordered by session_number (ascending)
    sessions = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.asc()).all()
    # determine course total sessions
    try:
        course_total = getattr(class_section.course, 'number_of_sessions', 6) if class_section.course else 6
    except Exception:
        course_total = 6
    # enrollments -> students
    enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
    student_ids = [e.student_id for e in enrolls]
    students = {s.id: s for s in Student.query.filter(Student.id.in_(student_ids)).all()} if student_ids else {}

    # build attendance lookup per (student_id, session_id)
    sess_ids = [s.id for s in sessions]
    att_rows = Attendance.query.filter(Attendance.class_section_id == class_section_id, Attendance.attendance_session_id.in_(sess_ids)).all() if sess_ids else []
    att_map = set((a.student_id, a.attendance_session_id) for a in att_rows)

    rows = []
    for sid in student_ids:
        st = students.get(sid)
        present_list = []
        absent_count = 0
        for s in sessions:
            present = (sid, s.id) in att_map
            present_list.append(present)
            if not present:
                absent_count += 1
        present_count = sum(1 for p in present_list if p)
        rows.append({
            'student_id': sid, 
            'mssv': st.mssv if st else '',
            'name': st.name if st else f'ID {sid}', 
            'present_list': present_list, 
            'present_count': present_count, 
            'absent_count': absent_count
        })

    return render_template('teacher/report.html', class_section=class_section, sessions=sessions, rows=rows, course_total=course_total)


@teacher_bp.route('/report/<int:class_section_id>/export')
@login_required
def report_export(class_section_id: int):
    """Export attendance report to Excel"""
    if not _teacher_owns_section(current_user.id, class_section_id):
        flash("Bạn không có quyền truy cập lớp này", "danger")
        return redirect(url_for('teacher.dashboard'))
    
    class_section = ClassSection.query.get_or_404(class_section_id)
    sessions = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.asc()).all()
    
    try:
        course_total = getattr(class_section.course, 'number_of_sessions', 6) if class_section.course else 6
    except Exception:
        course_total = 6
    
    enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
    student_ids = [e.student_id for e in enrolls]
    students = {s.id: s for s in Student.query.filter(Student.id.in_(student_ids)).all()} if student_ids else {}
    
    sess_ids = [s.id for s in sessions]
    att_rows = Attendance.query.filter(Attendance.class_section_id == class_section_id, Attendance.attendance_session_id.in_(sess_ids)).all() if sess_ids else []
    att_map = set((a.student_id, a.attendance_session_id) for a in att_rows)
    
    # Create Excel file
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Báo cáo điểm danh"
    
    # Header row 1: Buổi X
    ws['A1'] = 'STT'
    ws['B1'] = 'MSSV'
    ws['C1'] = 'Họ tên'
    col_idx = 4
    for i in range(1, course_total + 1):
        if i <= len(sessions):
            s = sessions[i-1]
            ws.cell(row=1, column=col_idx, value=f'Buổi {s.session_number}')
        else:
            ws.cell(row=1, column=col_idx, value=f'Buổi {i}')
        col_idx += 1
    ws.cell(row=1, column=col_idx, value='Đã điểm danh')
    ws.cell(row=1, column=col_idx+1, value='Tổng Vắng')
    
    # Header row 2: Time info
    ws['A2'] = ''
    ws['B2'] = ''
    ws['C2'] = ''
    col_idx = 4
    bucket_names = {'morning': 'Sáng', 'afternoon': 'Chiều', 'evening': 'Tối'}
    for i in range(1, course_total + 1):
        if i <= len(sessions):
            s = sessions[i-1]
            bucket_name = bucket_names.get(s.time_bucket, s.time_bucket)
            date_str = s.session_date.strftime('%d/%m/%Y') if s.session_date else ''
            ws.cell(row=2, column=col_idx, value=f'{bucket_name} - {date_str}')
        else:
            ws.cell(row=2, column=col_idx, value='')
        col_idx += 1
    ws.cell(row=2, column=col_idx, value='')
    ws.cell(row=2, column=col_idx+1, value='')
    
    # Merge cells for STT, MSSV, Họ tên
    ws.merge_cells('A1:A2')
    ws.merge_cells('B1:B2')
    ws.merge_cells('C1:C2')
    ws.merge_cells(start_row=1, start_column=col_idx, end_row=2, end_column=col_idx)
    ws.merge_cells(start_row=1, start_column=col_idx+1, end_row=2, end_column=col_idx+1)
    
    # Style headers
    from openpyxl.styles import Font, Alignment
    for row in [1, 2]:
        for col in range(1, col_idx + 2):
            cell = ws.cell(row=row, column=col)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    
    # Data rows
    row_idx = 3
    for sid in student_ids:
        st = students.get(sid)
        ws.cell(row=row_idx, column=1, value=row_idx - 2)
        ws.cell(row=row_idx, column=2, value=st.mssv if st else '')
        ws.cell(row=row_idx, column=3, value=st.name if st else f'ID {sid}')
        
        col_idx = 4
        absent_count = 0
        present_count = 0
        for i in range(course_total):
            if i < len(sessions):
                s = sessions[i]
                present = (sid, s.id) in att_map
                ws.cell(row=row_idx, column=col_idx, value='có mặt' if present else 'vắng')
                if present:
                    present_count += 1
                else:
                    absent_count += 1
            else:
                ws.cell(row=row_idx, column=col_idx, value='')
            col_idx += 1
        
        ws.cell(row=row_idx, column=col_idx, value=present_count)
        ws.cell(row=row_idx, column=col_idx+1, value=absent_count)
        row_idx += 1
    
    # Set column widths
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 15
    ws.column_dimensions['C'].width = 25
    for i in range(4, col_idx + 2):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 15
    
    # Save to bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    section_name = class_section.name.replace(' ', '_')
    filename = f"bao_cao_diem_danh_{section_name}.xlsx"
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


@teacher_bp.route('/class_students/<int:class_section_id>')
@login_required
def class_students(class_section_id: int):
    # show list of students enrolled in the class
    if not _teacher_owns_section(current_user.id, class_section_id):
        return redirect(url_for('teacher.attendance_index'))
    
    class_section = ClassSection.query.get_or_404(class_section_id)
    enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
    student_ids = [e.student_id for e in enrolls]
    students = Student.query.filter(Student.id.in_(student_ids)).order_by(Student.mssv).all() if student_ids else []
    
    return render_template('teacher/class_students.html', 
                         class_section=class_section, 
                         students=students)


@teacher_bp.route('/class_students/<int:class_section_id>/export')
@login_required
def export_class_students(class_section_id: int):
    """Export class students list to Excel"""
    if not _teacher_owns_section(current_user.id, class_section_id):
        flash("Bạn không có quyền truy cập lớp này", "danger")
        return redirect(url_for('teacher.dashboard'))
    
    class_section = ClassSection.query.get_or_404(class_section_id)
    enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
    student_ids = [e.student_id for e in enrolls]
    students = Student.query.filter(Student.id.in_(student_ids)).order_by(Student.mssv).all() if student_ids else []
    
    # Create Excel file
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Danh sách sinh viên"
    
    # Header
    ws['A1'] = 'STT'
    ws['B1'] = 'MSSV'
    ws['C1'] = 'Họ tên'
    ws['D1'] = 'Giới tính'
    ws['E1'] = 'Ngày sinh'
    ws['F1'] = 'Lớp hành chính'
    ws['G1'] = 'Đăng ký khuôn mặt'
    
    # Style header
    for cell in ['A1', 'B1', 'C1', 'D1', 'E1', 'F1', 'G1']:
        ws[cell].font = Font(bold=True)
        ws[cell].alignment = Alignment(horizontal='center')
    
    # Data rows
    for idx, student in enumerate(students, start=2):
        ws[f'A{idx}'] = idx - 1
        ws[f'B{idx}'] = student.mssv
        ws[f'C{idx}'] = student.name
        ws[f'D{idx}'] = student.gender or ''
        ws[f'E{idx}'] = student.date_of_birth.strftime('%d/%m/%Y') if student.date_of_birth else ''
        ws[f'F{idx}'] = student.class_admin.name if student.class_admin else ''
        ws[f'G{idx}'] = 'Đã đăng ký' if student.get_embedding() else 'Chưa đăng ký'
    
    # Set column widths
    ws.column_dimensions['A'].width = 10
    ws.column_dimensions['B'].width = 15
    ws.column_dimensions['C'].width = 25
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 20
    ws.column_dimensions['G'].width = 18
    
    # Save to bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    filename = f"danh_sach_sinh_vien_{class_section.name.replace(' ', '_')}.xlsx"
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


@teacher_bp.route('/report_csv/<int:class_section_id>')
@login_required
def report_csv(class_section_id: int):
    if not _teacher_owns_section(current_user.id, class_section_id):
        return redirect(url_for('teacher.attendance_index'))
    # similar to report_view: build rows
    sessions = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.asc()).all()
    try:
        section = ClassSection.query.get(class_section_id)
    except Exception:
        section = None
    enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
    student_ids = [e.student_id for e in enrolls]
    students = {s.id: s for s in Student.query.filter(Student.id.in_(student_ids)).all()} if student_ids else {}
    sess_ids = [s.id for s in sessions]
    att_rows = Attendance.query.filter(Attendance.class_section_id == class_section_id, Attendance.attendance_session_id.in_(sess_ids)).all() if sess_ids else []
    att_map = set((a.student_id, a.attendance_session_id) for a in att_rows)

    import io
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Helper function to format time_bucket
    def format_bucket(bucket):
        bucket_map = {'morning': 'Sáng', 'afternoon': 'Chiều', 'evening': 'Tối'}
        return bucket_map.get(bucket, bucket)
    
    # header - chỉ bao gồm các buổi đã điểm danh
    header = ['STT', 'MSSV', 'Họ tên']
    for s in sessions:
        session_date_str = s.session_date.strftime('%d/%m/%Y') if s.session_date else ''
        header.append(f'Buổi {s.session_number} - {format_bucket(s.time_bucket)} - {session_date_str}')
    header += ['Đã điểm danh', 'Tổng Vắng']
    writer.writerow(header)
    
    # rows
    for idx, sid in enumerate(student_ids, start=1):
        st = students.get(sid)
        row = [idx, st.mssv if st else '', st.name if st else f'ID {sid}']
        absent = 0
        present_count = 0
        # chỉ xuất các buổi đã tồn tại
        for s in sessions:
            present = (sid, s.id) in att_map
            row.append('có mặt' if present else 'vắng')
            if not present:
                absent += 1
            else:
                present_count += 1
        # append present_count then absent
        row.append(present_count)
        row.append(absent)
        writer.writerow(row)

    # prepare response with UTF-8 BOM so Excel shows Vietnamese correctly
    csv_bytes = output.getvalue().encode('utf-8-sig')
    from flask import Response
    resp = Response(csv_bytes, mimetype='text/csv; charset=utf-8')
    section_name = section.name.replace(' ', '_') if section else str(class_section_id)
    filename = f"bao_cao_diem_danh_{section_name}.csv"
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@teacher_bp.route("/take_attendance/<int:class_section_id>")
@login_required
def take_attendance(class_section_id: int):
    # Browser-only flow: we don't track a background subprocess here.
    running = False
    
    # Kiểm tra giờ hợp lệ ngay từ đầu
    now = datetime.now()
    hour = now.hour
    
    # Khung giờ không hợp lệ (0h-2h59 là nửa đêm, không được điểm danh)
    if hour < 3:
        flash("Chưa đến giờ điểm danh. Buổi sáng bắt đầu từ 3h", "warning")
        return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
    # Sau 11h sáng và trước 13h chiều
    elif 11 <= hour < 13:
        flash("Đã hết giờ điểm danh buổi sáng (11h). Buổi chiều bắt đầu từ 13h", "warning")
        return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
    # Sau 17h chiều và trước 18h tối
    elif 17 < hour < 18:
        flash("Đã hết giờ điểm danh buổi chiều (17h). Buổi tối bắt đầu từ 18h", "warning")
        return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
    # Sau 23h45 tối
    elif (hour == 23 and now.minute > 45) or hour == 0:
        flash("Đã hết giờ điểm danh. Buổi tối kết thúc lúc 23h45", "warning")
        return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))

    # Provide teacher's sections so template can show class list on the attendance page
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    sections = ClassSection.query.filter_by(teacher_id=teacher.id).all() if teacher else []
    # If asked to create a new session (from sessions_list "Tạo buổi mới" link), create it here
    try:
        # lazily close expired sessions before potentially creating/resuming
        try:
            _auto_close_expired_sessions(class_section_id)
        except Exception:
            pass
        # delete unused expired sessions to clean up
        try:
            _auto_delete_unused_sessions(class_section_id)
        except Exception:
            pass
        create_flag = request.args.get('create_session')
        if create_flag in ('1', 'true', 'yes'):
            # ensure teacher owns this section
            if not _teacher_owns_section(current_user.id, class_section_id):
                return redirect(url_for('teacher.attendance_index'))
            # Do not allow creating a new session while the latest session is still open
            open_sess = AttendanceSession.query.filter_by(class_section_id=class_section_id, ended_at=None).order_by(AttendanceSession.started_at.desc()).first()
            if open_sess:
                # Redirect to existing open session instead of creating a new one
                return redirect(url_for('teacher.take_attendance', class_section_id=class_section_id, session_id=open_sess.id))
            # enforce maximum sessions per course
            try:
                section = ClassSection.query.get(class_section_id)
                max_sessions = getattr(section.course, 'number_of_sessions', 6) if section and section.course else 6
            except Exception:
                max_sessions = 6
            last = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.desc()).first()
            next_num = (last.session_number + 1) if last else 1
            if next_num > max_sessions:
                # do not create; redirect back to sessions list with a flash message
                flash(f"Không thể tạo buổi mới: đã đạt tối đa {max_sessions} buổi của học phần", "warning")
                return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
            now = datetime.now()
            today = now.date()
            hour = now.hour
            
            # Log để debug
            logging.info(f"Creating session at hour: {hour}")
            
            # Kiểm tra khung giờ hợp lệ và xác định bucket
            if hour < 3:
                logging.info(f"Blocked: hour {hour} < 3")
                flash("Chưa đến giờ điểm danh. Buổi sáng bắt đầu từ 3h", "warning")
                return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
            elif 3 <= hour < 11:
                bucket = 'morning'
                bucket_name = 'sáng'
            elif 11 <= hour < 13:
                flash("Đã hết giờ điểm danh buổi sáng (11h). Buổi chiều bắt đầu từ 13h", "warning")
                return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
            elif 13 <= hour < 17:
                bucket = 'afternoon'
                bucket_name = 'chiều'
            elif 17 < hour < 18:
                flash("Đã hết giờ điểm danh buổi chiều (17h). Buổi tối bắt đầu từ 18h", "warning")
                return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
            elif 18 <= hour < 23 or (hour == 23 and now.minute <= 45):
                bucket = 'evening'
                bucket_name = 'tối'
            else:
                flash("Đã hết giờ điểm danh. Buổi tối kết thúc lúc 23h45", "warning")
                return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
            
            # Kiểm tra đã có buổi điểm danh trong khung giờ này hôm nay chưa
            existing_session = AttendanceSession.query.filter_by(
                class_section_id=class_section_id,
                session_date=today,
                time_bucket=bucket
            ).first()
            
            if existing_session:
                flash(f"Đã điểm danh cho buổi {bucket_name} hôm nay", "warning")
                return redirect(url_for('teacher.sessions_list', class_section_id=class_section_id))
            
            # compute next session number (already computed or recompute)
            last = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.desc()).first()
            next_num = (last.session_number + 1) if last else 1
            session = AttendanceSession(class_section_id=class_section_id, session_number=next_num, session_date=today, time_bucket=bucket, created_by=getattr(current_user, 'id', None))
            try:
                db.session.add(session)
                db.session.commit()
                # Do NOT backfill old attendance rows into the newly created session.
                # New sessions should start with fresh attendance data.
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
            # redirect to same view with session_id so UI preloads the created session
            return redirect(url_for('teacher.take_attendance', class_section_id=class_section_id, session_id=session.id))
    except Exception:
        # on any failure, continue and render the page without creating
        pass
    # If session_id provided in query string, load that session, else find today's session for this class (current bucket) to preload
    try:
        session_id = request.args.get('session_id')
        if session_id:
            current_session = AttendanceSession.query.get(int(session_id))
            # If session is already ended, redirect to history
            if current_session and current_session.ended_at:
                flash("Buổi điểm danh này đã kết thúc. Chỉ có thể xem lịch sử.", "warning")
                return redirect(url_for('teacher.attendance_history', class_section_id=class_section_id, session_id=current_session.id))
        else:
            now = datetime.now()
            today = now.date()
            hour = now.hour
            if hour < 12:
                bucket = 'morning'
            elif hour < 17:
                bucket = 'afternoon'
            else:
                bucket = 'evening'
            # Prefer an open session for the current bucket (ended_at is NULL).
            current_session = AttendanceSession.query.filter_by(
                class_section_id=class_section_id,
                session_date=today,
                time_bucket=bucket,
                ended_at=None,
            ).order_by(AttendanceSession.started_at.desc()).first()
    except Exception:
        current_session = None
    # compute class size and attended count for UI
    try:
        class_size = Enrollment.query.filter_by(class_section_id=class_section_id).count()
    except Exception:
        class_size = 0
    try:
        if current_session:
            attended_count = Attendance.query.filter_by(class_section_id=class_section_id, attendance_session_id=current_session.id).count()
        else:
            attended_count = 0
    except Exception:
        attended_count = 0

    # compute not-yet-marked count for UI
    try:
        not_marked = max(0, int(class_size) - int(attended_count))
    except Exception:
        not_marked = 0

    # Get class_section for displaying semester info
    class_section = ClassSection.query.get_or_404(class_section_id)
    
    return render_template(
        "teacher/take_attendance.html",
        class_section_id=class_section_id,
        class_section=class_section,
        running=running,
        sections=sections,
        current_session=current_session,
        class_size=class_size,
        attended_count=attended_count,
        not_marked=not_marked,
    )


@teacher_bp.route("/start_attendance/<int:class_section_id>", methods=["POST"])
@login_required
def start_attendance(class_section_id: int):
    logger.info("start_attendance called user=%s class_section_id=%s", getattr(current_user, 'id', None), class_section_id)
    if not _teacher_owns_section(current_user.id, class_section_id):
        logger.warning("Unauthorized start_attendance attempt user=%s class=%s", getattr(current_user, 'id', None), class_section_id)
        return jsonify({"ok": False, "error": "Không có quyền"}), 403

    # Browser-only mode: no BackgroundProcess duplication checks required.

    # lazily auto-close expired sessions for this class before starting/resuming
    try:
        _auto_close_expired_sessions(class_section_id)
    except Exception:
        pass

    # determine session bucket (for informational grouping) but DO NOT restrict sessions per bucket
    now = datetime.now()
    today = now.date()
    hour = now.hour
    if hour < 12:
        bucket = 'morning'
    elif hour < 17:
        bucket = 'afternoon'
    else:
        bucket = 'evening'

    # Note: we no longer support separate 'start'/'end' attendance types.
    # All attendance records belong to a single session.

    # Find existing open session (not yet ended) for this class. If exists, reuse it.
    session = AttendanceSession.query.filter_by(class_section_id=class_section_id, ended_at=None).order_by(AttendanceSession.started_at.desc()).first()

    # If an open session exists, reuse it
    if session:
        try:
            sess_date = session.session_date.isoformat() if getattr(session, 'session_date', None) else None
            logger.info("Reusing open session for class=%s session=%s", class_section_id, session.id)
            return jsonify({
                "ok": True,
                "status": "already_open",
                "session_id": session.id,
                "time_bucket": session.time_bucket,
                "session_number": session.session_number,
                "session_date": sess_date,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "session_open": session.ended_at is None,
            })
        except Exception as e:
            logger.exception("Failed to return existing session metadata: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # No open session exists: create one according to attendance_type
    # enforce maximum sessions per course
    try:
        section = ClassSection.query.get(class_section_id)
        max_sessions = getattr(section.course, 'number_of_sessions', 6) if section and section.course else 6
    except Exception:
        max_sessions = 6
    last = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.desc()).first()
    next_num = (last.session_number + 1) if last else 1
    if next_num > max_sessions:
        return jsonify({"ok": False, "error": "max_sessions_reached", "max_sessions": max_sessions}), 400
    session = AttendanceSession(class_section_id=class_section_id, session_number=next_num, session_date=today, time_bucket=bucket, created_by=getattr(current_user, 'id', None))
    # Create session (no start/end flags). ended_at remains None for an active session.
    try:
        db.session.add(session)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    # Browser-only mode: do not spawn a subprocess. Return session metadata so
    # the client can begin sending frames to the `/api/realtime/recognize` endpoint.
    try:
        logger.info("Starting browser-only attendance session for class=%s session=%s", class_section_id, session.id)
        return jsonify({
            "ok": True,
            "status": "started",
            "session_id": session.id,
            "time_bucket": session.time_bucket,
            "session_number": session.session_number,
            "session_date": session.session_date.isoformat() if session.session_date else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "session_open": session.ended_at is None,
        })
    except Exception as e:
        logger.exception("Failed to return session metadata for browser-only start: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@teacher_bp.route("/stop_attendance/<int:class_section_id>", methods=["POST"])
@login_required
def stop_attendance(class_section_id: int):
    if not _teacher_owns_section(current_user.id, class_section_id):
        return jsonify({"ok": False, "error": "Không có quyền"}), 403
    # End the current open session
    try:
        now = datetime.now()
        # find currently open session and end it
        session = AttendanceSession.query.filter_by(class_section_id=class_section_id, ended_at=None).order_by(AttendanceSession.started_at.desc()).first()
        if not session:
            return jsonify({"ok": True, "status": "not_running"})

        # End the session
        session.ended_at = now
        try:
            db.session.add(session)
            db.session.commit()
            logger.info('Ended session %s for class=%s', getattr(session, 'id', None), class_section_id)
            return jsonify({"ok": True, "status": "stopped"})
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return jsonify({"ok": False, "error": "db_commit_failed"}), 500
    except Exception as e:
        logger.exception("Failed to end session for class=%s: %s", class_section_id, e)
        return jsonify({"ok": False, "error": str(e)}), 500


@teacher_bp.route("/delete_unused_session/<int:class_section_id>", methods=["POST"])
@login_required
def delete_unused_session(class_section_id: int):
    """Delete a session that was created but never started (no attendance records)"""
    if not _teacher_owns_section(current_user.id, class_section_id):
        return jsonify({"ok": False, "error": "Không có quyền"}), 403
    
    try:
        # Find the latest open session (ended_at = NULL)
        session = AttendanceSession.query.filter_by(
            class_section_id=class_section_id, 
            ended_at=None
        ).order_by(AttendanceSession.started_at.desc()).first()
        
        if not session:
            return jsonify({"ok": True, "message": "No open session found"})
        
        # Check if session has any attendance records
        att_count = Attendance.query.filter_by(
            attendance_session_id=session.id
        ).count()
        
        if att_count > 0:
            # Has attendance records, cannot delete
            return jsonify({"ok": False, "error": "Session has attendance records"}), 400
        
        # Delete the session
        session_id = session.id
        db.session.delete(session)
        db.session.commit()
        
        logger.info(f"Deleted unused session {session_id} for class {class_section_id}")
        return jsonify({"ok": True, "message": "Session deleted", "session_id": session_id})
        
    except Exception as e:
        logger.exception(f"Failed to delete unused session for class={class_section_id}: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500


@teacher_bp.route("/history/<int:class_section_id>")
@login_required
def attendance_history(class_section_id: int):
    # Support session-based history view.
    class_section = ClassSection.query.get_or_404(class_section_id)
    session_id = request.args.get('session_id')
    # If session_id not provided, show sessions for the class with counts
    if not session_id:
        sessions = AttendanceSession.query.filter_by(class_section_id=class_section_id).order_by(AttendanceSession.session_number.asc()).all()
        # compute class size once
        try:
            class_size = Enrollment.query.filter_by(class_section_id=class_section_id).count()
        except Exception:
            class_size = 0
        # for each session compute total attendance count and absent (= class_size - count)
        sess_view = []
        for s in sessions:
            total_count = Attendance.query.filter_by(class_section_id=class_section_id, attendance_session_id=s.id).count()
            absent = max(0, int(class_size) - int(total_count))
            sess_view.append({
                'id': s.id,
                'session_number': s.session_number,
                'session_date': s.session_date,
                'time_bucket': s.time_bucket,
                'ended': bool(s.ended_at),
                'count': total_count,
                'class_size': class_size,
                'absent': absent,
            })
        return render_template('teacher/attendance_history.html', 
                             class_section=class_section, 
                             sessions=sess_view, 
                             class_size=class_size)

    # session_id provided: show per-enrolled-student rows with start/end cells
    try:
        sid = int(session_id)
    except Exception:
        return redirect(url_for('teacher.attendance_history', class_section_id=class_section_id))

    # find enrollments and for each student, find the attendance row for this session (single)
    enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
    rows = []
    for e in enrolls:
        st = Student.query.get(e.student_id)
        att_row = Attendance.query.filter_by(class_section_id=class_section_id, attendance_session_id=sid, student_id=e.student_id).order_by(Attendance.timestamp.asc()).first()
        rows.append({
            'student_id': e.student_id,
            'student_name': st.name if st else f'ID {e.student_id}',
            'student_mssv': st.mssv if st else '',
            'start_ts': att_row.timestamp if att_row else None,
            'start_source': att_row.source if att_row else None,
            'end_ts': None,
            'end_source': None,
        })
    # Also get session meta for header
    session = AttendanceSession.query.get(sid)
    # compute class size and absent count for this session view
    try:
        class_size = len(enrolls)
    except Exception:
        class_size = 0
    try:
        absent_count = sum(1 for r in rows if not r.get('start_ts'))
    except Exception:
        absent_count = 0
    return render_template('teacher/attendance_history.html', 
                         class_section=class_section, 
                         session=session, 
                         rows=rows, 
                         class_size=class_size, 
                         absent_count=absent_count)


@teacher_bp.route('/attendance_poll/<int:class_section_id>')
@login_required
def attendance_poll(class_section_id: int):
    # Support optional session filtering so the UI can poll only for the current session
    # lazily close expired sessions before polling so UI sees updated session state
    try:
        _auto_close_expired_sessions(class_section_id)
    except Exception:
        pass

    session_id = request.args.get('session_id')
    q = Attendance.query.filter_by(class_section_id=class_section_id)
    if session_id:
        try:
            sid = int(session_id)
            q = q.filter(Attendance.attendance_session_id == sid)
        except Exception:
            pass

    records = q.order_by(Attendance.timestamp.desc()).limit(100).all()
    out = []
    for r in records:
        st = Student.query.get(r.student_id)
        out.append({
            'id': r.id,
            'student_id': r.student_id,
            'student_name': st.name if st else f'ID {r.student_id}',
            'student_mssv': st.mssv if st else '',
            'timestamp': r.timestamp.isoformat(),
            'status': r.status,
            'source': r.source,
        })
    return jsonify({'ok': True, 'records': out})


@teacher_bp.route('/finalize_attendance/<int:class_section_id>', methods=['POST'])
@login_required
def finalize_attendance(class_section_id: int):
    """Create 'absent' Attendance rows for enrolled students who don't have an
    attendance record for today. Returns JSON with number of records created.
    """
    if not _teacher_owns_section(current_user.id, class_section_id):
        return jsonify({"ok": False, "error": "Không có quyền"}), 403

    try:
        # Accept optional session_id and attendance_type from POST form
        session_id = request.form.get('session_id') or request.json.get('session_id') if request.is_json else None

        if session_id:
            # finalize for the specific session (single attendance per student)
            try:
                session_id = int(session_id)
            except Exception:
                return jsonify({'ok': False, 'error': 'invalid session_id'}), 400

            # find enrolled students
            enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
            student_ids = [e.student_id for e in enrolls]
            created = 0

            # fetch the session to get session_date when available
            session_obj = AttendanceSession.query.get(session_id)
            sess_date = session_obj.session_date if session_obj and getattr(session_obj, 'session_date', None) else date.today()

            for sid in student_ids:
                exists = Attendance.query.filter(
                    Attendance.student_id == sid,
                    Attendance.class_section_id == class_section_id,
                    Attendance.attendance_session_id == session_id,
                ).first()
                if not exists:
                    a = Attendance(
                        student_id=sid,
                        class_section_id=class_section_id,
                        status='absent',
                        source='system',
                        attendance_date=sess_date,
                        attendance_session_id=session_id,
                    )
                    db.session.add(a)
                    created += 1
            try:
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
            logger.info('Finalize session attendance created=%s absent records for class=%s session=%s', created, class_section_id, session_id)
            return jsonify({'ok': True, 'created': created, 'session_id': session_id})

        # Backwards-compatible: finalize by date if no session_id provided
        today = date.today()
        enrolls = Enrollment.query.filter_by(class_section_id=class_section_id).all()
        student_ids = [e.student_id for e in enrolls]
        logger.info('Finalize attendance for class=%s found %d enrollments (date-based)', class_section_id, len(student_ids))
        created = 0
        for sid in student_ids:
            exists = Attendance.query.filter(
                Attendance.student_id == sid,
                Attendance.class_section_id == class_section_id,
                getattr(Attendance, 'attendance_date') == today
            ).first()
            if not exists:
                a = Attendance(student_id=sid, class_section_id=class_section_id, status='absent', source='system', attendance_date=today)
                db.session.add(a)
                created += 1
        if created:
            try:
                db.session.commit()
            except Exception as e:
                try:
                    from sqlalchemy.exc import IntegrityError
                    if isinstance(e, IntegrityError):
                        db.session.rollback()
                        logger.info('Finalize: IntegrityError while committing absent records, likely duplicates. created=%s', created)
                    else:
                        db.session.rollback()
                        raise
                except Exception:
                    db.session.rollback()
                    raise
        logger.info('Finalize attendance created=%s absent records for class=%s', created, class_section_id)
        return jsonify({'ok': True, 'created': created})
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception('Failed to finalize attendance for class=%s: %s', class_section_id, e)
        return jsonify({'ok': False, 'error': str(e)}), 500
"""Teacher controller: start/stop attendance subprocess and polling endpoints.

Compact single-file implementation to avoid merge corruption.
"""
