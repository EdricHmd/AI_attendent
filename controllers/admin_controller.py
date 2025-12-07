from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from io import StringIO, BytesIO
import csv
from datetime import datetime
import openpyxl
from openpyxl import Workbook

from models import db, User, Student, ClassAdmin, Teacher, Course, ClassSection, Enrollment, Attendance, AttendanceSession, Semester
from werkzeug.security import generate_password_hash

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def require_admin():
    if not current_user.is_authenticated or current_user.role != "admin":
        from flask import redirect, url_for
        return redirect(url_for("auth.login"))


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    from datetime import datetime, timedelta
    
    # Get current semester for filtering data
    current_semester = Semester.get_current_semester()
    
    # Aggregate stats for quick overview
    total_students = Student.query.count()
    total_teachers = Teacher.query.count()
    total_courses = Course.query.count()
    total_class_admins = ClassAdmin.query.count()
    
    # Total sections in current semester only
    if current_semester:
        total_sections = ClassSection.query.filter_by(semester_id=current_semester.id).count()
    else:
        total_sections = ClassSection.query.count()

    # Recent entities
    recent_students = Student.query.order_by(Student.id.desc()).limit(5).all()
    
    # Recent sections from current semester
    if current_semester:
        recent_sections = ClassSection.query.filter_by(semester_id=current_semester.id).order_by(ClassSection.id.desc()).limit(5).all()
    else:
        recent_sections = ClassSection.query.order_by(ClassSection.id.desc()).limit(5).all()

    # Attendance for last 7 days (for chart) - only from current semester
    seven_days_ago = datetime.now() - timedelta(days=7)
    
    if current_semester:
        # Get class sections in current semester
        current_semester_section_ids = [s.id for s in ClassSection.query.filter_by(semester_id=current_semester.id).all()]
        recent_attendance = Attendance.query.filter(
            Attendance.timestamp >= seven_days_ago,
            Attendance.class_section_id.in_(current_semester_section_ids)
        ).order_by(Attendance.timestamp.desc()).all() if current_semester_section_ids else []
    else:
        recent_attendance = Attendance.query.filter(
            Attendance.timestamp >= seven_days_ago
        ).order_by(Attendance.timestamp.desc()).all()
    
    # Calculate attendance rate by class (for top/low charts)
    # Only get class sections from current semester
    if current_semester:
        class_sections = ClassSection.query.filter_by(semester_id=current_semester.id).all()
    else:
        class_sections = ClassSection.query.all()
    
    class_stats = []
    
    for cls in class_sections:
        # Get student count (sĩ số) - count enrollments for this class section
        student_count = Enrollment.query.filter_by(class_section_id=cls.id).count()
        if student_count == 0:
            continue  # Skip classes with no students
        
        # Get attendance sessions for this class in last 7 days
        sessions = AttendanceSession.query.filter(
            AttendanceSession.class_section_id == cls.id,
            AttendanceSession.started_at >= seven_days_ago
        ).all()
        
        session_count = len(sessions)
        if session_count == 0:
            continue  # Skip classes with no sessions
        
        # Get attendance records for this class in last 7 days
        attendances = Attendance.query.filter(
            Attendance.class_section_id == cls.id,
            Attendance.timestamp >= seven_days_ago
        ).all()
        
        # Count present/absent
        present_count = sum(1 for a in attendances if a.status and (
            a.status.lower().startswith('p') or 
            'present' in a.status.lower() or 
            'có mặt' in a.status.lower()
        ))
        absent_count = len(attendances) - present_count
        
        # Calculate expected attendance (sĩ số × số buổi)
        expected_attendance = student_count * session_count
        
        # Calculate rates
        present_rate = (present_count / expected_attendance * 100) if expected_attendance > 0 else 0
        absent_rate = (absent_count / expected_attendance * 100) if expected_attendance > 0 else 0
        
        class_stats.append({
            'name': cls.name,
            'present_rate': round(present_rate, 1),
            'absent_rate': round(absent_rate, 1),
            'present_count': present_count,
            'absent_count': absent_count,
            'student_count': student_count,
            'session_count': session_count,
            'expected': expected_attendance
        })

    return render_template(
        "admin/dashboard.html",
        total_students=total_students,
        total_teachers=total_teachers,
        total_courses=total_courses,
        total_class_admins=total_class_admins,
        total_sections=total_sections,
        recent_students=recent_students,
        recent_sections=recent_sections,
        recent_attendance=recent_attendance,
        class_stats=class_stats,
        current_semester=current_semester,
    )


# ---- Students management ----
@admin_bp.route("/students")
@login_required
def students_list():
    """List all students with search and filter"""
    search = request.args.get('search', '').strip()
    class_filter = request.args.get('class_admin_id', '')
    
    query = Student.query
    
    if search:
        query = query.filter(
            (Student.name.ilike(f'%{search}%')) | 
            (Student.mssv.ilike(f'%{search}%'))
        )
    
    if class_filter:
        query = query.filter_by(class_admin_id=int(class_filter))
    
    students = query.order_by(Student.id.asc()).all()
    class_admins = ClassAdmin.query.order_by(ClassAdmin.name).all()
    
    return render_template(
        "admin/students_list.html",
        students=students,
        class_admins=class_admins,
        search=search,
        class_filter=class_filter
    )


@admin_bp.route("/students/<int:student_id>/edit", methods=["GET", "POST"])
@login_required
def students_edit(student_id: int):
    st = Student.query.get_or_404(student_id)
    if request.method == "POST":
        st.name = request.form.get("name", "").strip()
        mssv_new = request.form.get("mssv", "").strip()
        class_admin_id = request.form.get("class_admin_id") or None
        gender = request.form.get("gender", "").strip() or None
        date_of_birth_str = request.form.get("date_of_birth", "").strip()
        
        if not st.name or not mssv_new:
            flash("Tên và MSSV là bắt buộc", "danger")
            return redirect(url_for("admin.students_edit", student_id=student_id))
        if mssv_new != st.mssv and Student.query.filter_by(mssv=mssv_new).first():
            flash("MSSV đã tồn tại", "danger")
            return redirect(url_for("admin.students_edit", student_id=student_id))
        
        # Update linked user username if MSSV changed
        if mssv_new != st.mssv:
            st.user.username = mssv_new
        st.mssv = mssv_new
        st.class_admin_id = int(class_admin_id) if class_admin_id else None
        st.gender = gender
        
        # Parse date_of_birth
        if date_of_birth_str:
            try:
                from datetime import datetime
                st.date_of_birth = datetime.strptime(date_of_birth_str, "%Y-%m-%d").date()
            except ValueError:
                flash("Định dạng ngày sinh không hợp lệ", "warning")
        else:
            st.date_of_birth = None
        
        db.session.commit()
        flash("Đã cập nhật sinh viên", "success")
        return redirect(url_for("admin.students_list"))
    class_admins = ClassAdmin.query.all()
    return render_template("admin/student_form.html", class_admins=class_admins, student=st)


@admin_bp.route("/students/<int:student_id>/delete", methods=["POST"])
@login_required
def students_delete(student_id: int):
    """Delete student and cascade delete all related data"""
    st = Student.query.get_or_404(student_id)
    student_name = st.name
    
    # SQLAlchemy will cascade delete:
    # - enrollments (due to cascade="all, delete-orphan" in Student.enrollments)
    # - attendances (due to cascade="all, delete-orphan" in Student.attendances)
    # - user will remain but can be cleaned up separately if needed
    
    # Delete associated user account
    user = st.user
    
    db.session.delete(st)
    db.session.delete(user)
    db.session.commit()
    
    flash(f"Đã xóa sinh viên '{student_name}' và toàn bộ dữ liệu liên quan (lớp hành chính, lớp học phần, điểm danh)", "success")
    return redirect(url_for("admin.students_list"))


# ---- Teachers CRUD ----
@admin_bp.route("/teachers")
@login_required
def teachers_list():
    teachers = Teacher.query.all()
    return render_template("admin/teachers_list.html", teachers=teachers)


@admin_bp.route("/teachers/create", methods=["GET", "POST"])
@login_required
def teachers_create():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip() or "password123"
        if not name or not username:
            flash("Tên và tài khoản là bắt buộc", "danger")
            return redirect(url_for("admin.teachers_create"))
        if User.query.filter_by(username=username).first():
            flash("Tài khoản đã tồn tại", "danger")
            return redirect(url_for("admin.teachers_create"))
        user = User(username=username, role="teacher", password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()
        t = Teacher(user_id=user.id, name=name, email=email or None)
        db.session.add(t)
        db.session.commit()
        flash("Đã tạo giảng viên", "success")
        return redirect(url_for("admin.teachers_list"))
    return render_template("admin/teacher_form.html", teacher=None)


@admin_bp.route("/teachers/<int:teacher_id>/edit", methods=["GET", "POST"])
@login_required
def teachers_edit(teacher_id: int):
    t = Teacher.query.get_or_404(teacher_id)
    if request.method == "POST":
        t.name = request.form.get("name", "").strip()
        t.email = request.form.get("email", "").strip() or None
        username = request.form.get("username", "").strip()
        if not t.name or not username:
            flash("Tên và tài khoản là bắt buộc", "danger")
            return redirect(url_for("admin.teachers_edit", teacher_id=teacher_id))
        if username != t.user.username and User.query.filter_by(username=username).first():
            flash("Tài khoản đã tồn tại", "danger")
            return redirect(url_for("admin.teachers_edit", teacher_id=teacher_id))
        t.user.username = username
        db.session.commit()
        flash("Đã cập nhật giảng viên", "success")
        return redirect(url_for("admin.teachers_list"))
    return render_template("admin/teacher_form.html", teacher=t)


@admin_bp.route("/teachers/<int:teacher_id>/delete", methods=["POST"]) 
@login_required
def teachers_delete(teacher_id: int):
    t = Teacher.query.get_or_404(teacher_id)
    user = t.user
    db.session.delete(t)
    if user and user.role == "teacher":
        db.session.delete(user)
    db.session.commit()
    flash("Đã xóa giảng viên", "success")
    return redirect(url_for("admin.teachers_list"))


# ---- Courses CRUD ----
@admin_bp.route("/courses")
@login_required
def courses_list():
    courses = Course.query.all()
    return render_template("admin/courses_list.html", courses=courses)


@admin_bp.route("/courses/create", methods=["GET", "POST"]) 
@login_required
def courses_create():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name = request.form.get("name", "").strip()
        try:
            number_of_sessions = int(request.form.get("number_of_sessions") or 6)
        except Exception:
            number_of_sessions = 6
        if not code or not name:
            flash("Mã và tên học phần bắt buộc", "danger")
            return redirect(url_for("admin.courses_create"))
        if Course.query.filter_by(code=code).first():
            flash("Mã học phần đã tồn tại", "danger")
            return redirect(url_for("admin.courses_create"))
        db.session.add(Course(code=code, name=name, number_of_sessions=number_of_sessions))
        db.session.commit()
        flash("Đã tạo học phần", "success")
        return redirect(url_for("admin.courses_list"))
    return render_template("admin/course_form.html", course=None)


@admin_bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"]) 
@login_required
def courses_edit(course_id: int):
    c = Course.query.get_or_404(course_id)
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name = request.form.get("name", "").strip()
        try:
            number_of_sessions = int(request.form.get("number_of_sessions") or c.number_of_sessions or 6)
        except Exception:
            number_of_sessions = c.number_of_sessions or 6
        if not code or not name:
            flash("Mã và tên học phần bắt buộc", "danger")
            return redirect(url_for("admin.courses_edit", course_id=course_id))
        if code != c.code and Course.query.filter_by(code=code).first():
            flash("Mã học phần đã tồn tại", "danger")
            return redirect(url_for("admin.courses_edit", course_id=course_id))
        c.code = code
        c.name = name
        c.number_of_sessions = number_of_sessions
        db.session.commit()
        flash("Đã cập nhật học phần", "success")
        return redirect(url_for("admin.courses_list"))
    return render_template("admin/course_form.html", course=c)


@admin_bp.route("/courses/<int:course_id>/delete", methods=["POST"]) 
@login_required
def courses_delete(course_id: int):
    c = Course.query.get_or_404(course_id)
    db.session.delete(c)
    db.session.commit()
    flash("Đã xóa học phần", "success")
    return redirect(url_for("admin.courses_list"))


# ---- Class Sections + Enrollment ----
@admin_bp.route("/class_sections")
@login_required
def class_sections_list():
    # Get semester filter from query params
    semester_id = request.args.get('semester_id', type=int)
    
    # Get all semesters for dropdown
    semesters = Semester.query.order_by(Semester.start_date.desc()).all()
    
    # Filter sections by semester if selected
    if semester_id:
        sections = ClassSection.query.filter_by(semester_id=semester_id).all()
        selected_semester = Semester.query.get(semester_id)
    else:
        sections = ClassSection.query.all()
        selected_semester = None
    
    return render_template("admin/class_sections_list.html", 
                         sections=sections,
                         semesters=semesters,
                         selected_semester=selected_semester)


@admin_bp.route("/class_sections/create", methods=["GET", "POST"]) 
@login_required
def class_sections_create():
    teachers = Teacher.query.all()
    courses = Course.query.all()
    semesters = Semester.query.order_by(Semester.start_date.desc()).all()
    current_semester = Semester.get_current_semester()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        course_id = request.form.get("course_id")
        teacher_id = request.form.get("teacher_id")
        semester_id = request.form.get("semester_id") or None
        
        # Nếu không chọn học kỳ, dùng học kỳ hiện tại
        if not semester_id and current_semester:
            semester_id = current_semester.id
        
        if not name or not course_id or not teacher_id:
            flash("Tên lớp, học phần và giảng viên bắt buộc", "danger")
            return redirect(url_for("admin.class_sections_create"))
        
        db.session.add(ClassSection(
            name=name, 
            course_id=int(course_id), 
            teacher_id=int(teacher_id), 
            semester_id=int(semester_id) if semester_id else None
        ))
        db.session.commit()
        flash("Đã tạo lớp học phần", "success")
        return redirect(url_for("admin.class_sections_list"))
    
    return render_template("admin/class_section_form.html", 
                         section=None, 
                         teachers=teachers, 
                         courses=courses,
                         semesters=semesters,
                         current_semester=current_semester)


@admin_bp.route("/class_sections/<int:section_id>/edit", methods=["GET", "POST"]) 
@login_required
def class_sections_edit(section_id: int):
    section = ClassSection.query.get_or_404(section_id)
    teachers = Teacher.query.all()
    courses = Course.query.all()
    semesters = Semester.query.order_by(Semester.start_date.desc()).all()
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        course_id = request.form.get("course_id")
        teacher_id = request.form.get("teacher_id")
        semester_id = request.form.get("semester_id") or None
        
        if not name or not course_id or not teacher_id:
            flash("Tên lớp, học phần và giảng viên bắt buộc", "danger")
            return redirect(url_for("admin.class_sections_edit", section_id=section_id))
        
        section.name = name
        section.course_id = int(course_id)
        section.teacher_id = int(teacher_id)
        section.semester_id = int(semester_id) if semester_id else None
        db.session.commit()
        flash("Đã cập nhật lớp học phần", "success")
        return redirect(url_for("admin.class_sections_list"))
    
    return render_template("admin/class_section_form.html", 
                         section=section, 
                         teachers=teachers, 
                         courses=courses,
                         semesters=semesters)


@admin_bp.route("/class_sections/<int:section_id>/delete", methods=["POST"]) 
@login_required
def class_sections_delete(section_id: int):
    section = ClassSection.query.get_or_404(section_id)
    db.session.delete(section)
    db.session.commit()
    flash("Đã xóa lớp học phần", "success")
    return redirect(url_for("admin.class_sections_list"))


@admin_bp.route("/class_sections/<int:section_id>/enroll", methods=["GET", "POST"]) 
@login_required
def class_sections_enroll(section_id: int):
    section = ClassSection.query.get_or_404(section_id)
    
    # Get filter parameter for class_admin
    filter_class_admin = request.args.get('class_admin_id', type=int)
    
    # Query students with optional filter
    students_query = Student.query
    if filter_class_admin:
        students_query = students_query.filter_by(class_admin_id=filter_class_admin)
    students = students_query.order_by(Student.name).all()
    
    # Get all class admins for filter dropdown
    class_admins = ClassAdmin.query.order_by(ClassAdmin.name).all()
    
    current_ids = {e.student_id for e in Enrollment.query.filter_by(class_section_id=section_id).all()}
    
    # Get enrolled students separately
    enrolled_students = Student.query.filter(Student.id.in_(current_ids)).order_by(Student.mssv).all() if current_ids else []
    
    if request.method == "POST":
        selected = request.form.getlist("student_ids")
        selected_ids = {int(x) for x in selected}
        # add new students only
        added_count = 0
        for sid in selected_ids - current_ids:
            db.session.add(Enrollment(student_id=sid, class_section_id=section_id))
            added_count += 1
        db.session.commit()
        if added_count > 0:
            flash(f"Đã thêm {added_count} sinh viên vào lớp", "success")
        else:
            flash("Không có sinh viên mới được thêm", "info")
        return redirect(url_for("admin.class_sections_enroll", section_id=section_id))
    
    return render_template("admin/enrollment_form.html", 
                         section=section, 
                         students=students, 
                         enrolled_students=enrolled_students,
                         current_ids=current_ids,
                         class_admins=class_admins,
                         filter_class_admin=filter_class_admin)


@admin_bp.route("/class_sections/<int:section_id>/enroll/import", methods=["POST"]) 
@login_required
def class_sections_enroll_import(section_id: int):
    """Import students from CSV or XLSX to enroll in class section"""
    section = ClassSection.query.get_or_404(section_id)
    file = request.files.get("file")
    
    if not file:
        flash("Chưa chọn tệp", "danger")
        return redirect(url_for("admin.class_sections_enroll", section_id=section_id))
    
    filename = file.filename.lower()
    if not (filename.endswith('.csv') or filename.endswith('.xlsx')):
        flash("Chỉ chấp nhận file CSV hoặc XLSX", "danger")
        return redirect(url_for("admin.class_sections_enroll", section_id=section_id))
    
    try:
        count_added = 0
        count_skipped = 0
        errors = []
        rows = []
        
        # Read file based on extension
        if filename.endswith('.csv'):
            # Try multiple encodings with error handling
            raw_content = file.stream.read()
            content = None
            encodings = [
                ('utf-8-sig', 'strict'),
                ('utf-8', 'strict'),
                ('utf-8', 'ignore'),
                ('utf-8', 'replace'),
                ('latin-1', 'strict'),
                ('cp1252', 'strict'),
                ('iso-8859-1', 'strict'),
            ]
            
            for encoding, errors in encodings:
                try:
                    content = raw_content.decode(encoding, errors=errors)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            
            if content is None:
                # Last resort: use utf-8 with replace to force decode
                content = raw_content.decode('utf-8', errors='replace')
            
            reader = csv.DictReader(content.splitlines())
            rows = list(reader)
        else:  # .xlsx
            wb = openpyxl.load_workbook(file.stream)
            ws = wb.active
            headers = [cell.value for cell in ws[1]]
            for row in ws.iter_rows(min_row=2, values_only=True):
                row_dict = {}
                for i, value in enumerate(row):
                    if i < len(headers) and headers[i]:
                        row_dict[headers[i]] = value
                rows.append(row_dict)
        
        # Process rows
        for row_num, row in enumerate(rows, start=2):  # start=2 because row 1 is header
            # Support multiple column formats including Vietnamese
            mssv = str(row.get("MSSV") or row.get("mssv") or "").strip()
            # Optional: can also read name for validation (not used for enrollment)
            name = str(row.get("Họ tên") or row.get("name") or row.get("Tên") or "").strip()
            
            if not mssv:
                errors.append(f"Dòng {row_num}: Thiếu MSSV")
                count_skipped += 1
                continue
            
            # Find student by MSSV
            student = Student.query.filter_by(mssv=mssv).first()
            
            if not student:
                errors.append(f"Dòng {row_num}: Không tìm thấy sinh viên {mssv}")
                count_skipped += 1
                continue
            
            # Check if student has class_admin
            if not student.class_admin_id:
                errors.append(f"Dòng {row_num}: Sinh viên {mssv} chưa thuộc lớp hành chính")
                count_skipped += 1
                continue
            
            # Check if already enrolled
            existing = Enrollment.query.filter_by(
                student_id=student.id, 
                class_section_id=section_id
            ).first()
            
            if existing:
                count_skipped += 1
                continue
            
            # Add enrollment
            enrollment = Enrollment(student_id=student.id, class_section_id=section_id)
            db.session.add(enrollment)
            count_added += 1
        
        db.session.commit()
        
        # Prepare flash message
        if count_added > 0:
            flash(f"Đã thêm {count_added} sinh viên vào lớp", "success")
        if count_skipped > 0:
            flash(f"Bỏ qua {count_skipped} sinh viên (đã tồn tại hoặc lỗi)", "warning")
        if errors:
            # Show first 5 errors
            error_msg = "Lỗi: " + "; ".join(errors[:5])
            if len(errors) > 5:
                error_msg += f" (và {len(errors) - 5} lỗi khác)"
            flash(error_msg, "danger")
            
    except Exception as e:
        db.session.rollback()
        flash(f"Lỗi xử lý file: {str(e)}", "danger")
    
    return redirect(url_for("admin.class_sections_enroll", section_id=section_id))


@admin_bp.route("/class_sections/<int:section_id>/remove_student/<int:student_id>", methods=["POST"])
@login_required
def class_sections_remove_student(section_id: int, student_id: int):
    """Remove a student from class section"""
    enrollment = Enrollment.query.filter_by(
        class_section_id=section_id, 
        student_id=student_id
    ).first_or_404()
    
    db.session.delete(enrollment)
    db.session.commit()
    flash("Đã xóa sinh viên khỏi lớp", "success")
    return redirect(url_for("admin.class_sections_enroll", section_id=section_id))


@admin_bp.route("/class_sections/<int:section_id>/export_students")
@login_required
def class_sections_export_students(section_id: int):
    """Export enrolled students to Excel"""
    from openpyxl.styles import Font, Alignment
    
    section = ClassSection.query.get_or_404(section_id)
    
    # Get enrolled students
    enrollments = Enrollment.query.filter_by(class_section_id=section_id).all()
    student_ids = [e.student_id for e in enrollments]
    students = Student.query.filter(Student.id.in_(student_ids)).order_by(Student.mssv).all()
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
    # Style header
    for cell in ['A1', 'B1', 'C1', 'D1', 'E1', 'F1']:
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
    # Set column widths
    ws.column_dimensions['A'].width = 10
    ws.column_dimensions['B'].width = 15
    ws.column_dimensions['C'].width = 25
    ws.column_dimensions['D'].width = 12
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 20
    
    # Save to bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    filename = f"danh_sach_sinh_vien_{section.name.replace(' ', '_')}.xlsx"
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )


# ---- ClassAdmin full CRUD with student management ----
@admin_bp.route("/class_admins")
@login_required
def class_admins_list():
    class_admins = ClassAdmin.query.all()
    # Add student count for each class
    class_admin_data = []
    for ca in class_admins:
        student_count = Student.query.filter_by(class_admin_id=ca.id).count()
        class_admin_data.append({'class_admin': ca, 'student_count': student_count})
    return render_template("admin/classes_admin_list.html", class_admin_data=class_admin_data)


@admin_bp.route("/class_admins/create", methods=["POST"]) 
@login_required
def class_admins_create():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Tên lớp hành chính bắt buộc", "danger")
        return redirect(url_for("admin.class_admins_list"))
    
    # Check if name already exists
    existing = ClassAdmin.query.filter_by(name=name).first()
    if existing:
        flash("Tên lớp đã tồn tại", "warning")
        return redirect(url_for("admin.class_admins_list"))
    
    # Create new class admin
    new_class_admin = ClassAdmin(name=name)
    db.session.add(new_class_admin)
    db.session.commit()
    flash(f"Đã tạo lớp hành chính {name}", "success")
    
    # Redirect to detail page
    return redirect(url_for("admin.class_admins_detail", class_admin_id=new_class_admin.id))


@admin_bp.route("/class_admins/<int:class_admin_id>")
@login_required
def class_admins_detail(class_admin_id: int):
    """View class admin details with list of students"""
    class_admin = ClassAdmin.query.get_or_404(class_admin_id)
    students = Student.query.filter_by(class_admin_id=class_admin_id).order_by(Student.name).all()
    return render_template("admin/class_admin_detail.html", class_admin=class_admin, students=students)


@admin_bp.route("/class_admins/<int:class_admin_id>/edit", methods=["GET", "POST"])
@login_required
def class_admins_edit(class_admin_id: int):
    """Edit class admin name"""
    ca = ClassAdmin.query.get_or_404(class_admin_id)
    if request.method == "POST":
        new_name = request.form.get("name", "").strip()
        if not new_name:
            flash("Tên lớp hành chính bắt buộc", "danger")
            return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
        
        # Check if name already exists (excluding current class)
        existing = ClassAdmin.query.filter(ClassAdmin.name == new_name, ClassAdmin.id != class_admin_id).first()
        if existing:
            flash("Tên lớp đã tồn tại", "danger")
            return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
        
        ca.name = new_name
        db.session.commit()
        flash("Đã cập nhật tên lớp hành chính", "success")
        return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
    
    return render_template("admin/class_admin_edit.html", class_admin=ca)


@admin_bp.route("/class_admins/<int:class_admin_id>/delete", methods=["POST"])
@login_required
def class_admins_delete(class_admin_id: int):
    """Delete class admin only if no students are enrolled"""
    ca = ClassAdmin.query.get_or_404(class_admin_id)
    
    # Check if there are any students in this class
    student_count = Student.query.filter_by(class_admin_id=class_admin_id).count()
    if student_count > 0:
        flash(f"Không thể xóa lớp hành chính vì còn {student_count} sinh viên", "danger")
        return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
    
    db.session.delete(ca)
    db.session.commit()
    flash("Đã xóa lớp hành chính", "success")
    return redirect(url_for("admin.class_admins_list"))


@admin_bp.route("/class_admins/<int:class_admin_id>/add_student", methods=["GET", "POST"])
@login_required
def class_admins_add_student(class_admin_id: int):
    """Add a single student to class admin"""
    class_admin = ClassAdmin.query.get_or_404(class_admin_id)
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        mssv = request.form.get("mssv", "").strip()
        gender = request.form.get("gender", "").strip()
        date_of_birth_str = request.form.get("date_of_birth", "").strip()
        
        if not name or not mssv:
            flash("Tên và MSSV là bắt buộc", "danger")
            return redirect(url_for("admin.class_admins_add_student", class_admin_id=class_admin_id))
        
        # Check if MSSV already exists
        if Student.query.filter_by(mssv=mssv).first():
            flash("MSSV đã tồn tại", "danger")
            return redirect(url_for("admin.class_admins_add_student", class_admin_id=class_admin_id))
        
        # Parse date of birth
        date_of_birth = None
        if date_of_birth_str:
            try:
                date_of_birth = datetime.strptime(date_of_birth_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        
        # Create user with username/password = MSSV
        user = User(username=mssv, role="student", password_hash=generate_password_hash(mssv))
        db.session.add(user)
        db.session.flush()
        
        # Create student linked to this class admin
        student = Student(
            user_id=user.id, 
            name=name, 
            mssv=mssv, 
            class_admin_id=class_admin_id,
            gender=gender if gender else None,
            date_of_birth=date_of_birth
        )
        db.session.add(student)
        db.session.commit()
        
        flash(f"Đã thêm sinh viên {name} vào lớp {class_admin.name}", "success")
        return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
    
    return render_template("admin/class_admin_add_student.html", class_admin=class_admin)


@admin_bp.route("/class_admins/<int:class_admin_id>/import_students", methods=["POST"])
@login_required
def class_admins_import_students(class_admin_id: int):
    """Import students from CSV or XLSX into class admin"""
    class_admin = ClassAdmin.query.get_or_404(class_admin_id)
    file = request.files.get("file")
    
    if not file:
        flash("Chưa chọn tệp", "danger")
        return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
    
    filename = file.filename.lower()
    if not (filename.endswith('.csv') or filename.endswith('.xlsx')):
        flash("Chỉ chấp nhận file CSV hoặc XLSX", "danger")
        return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
    
    try:
        count_added = 0
        count_skipped = 0
        errors = []
        rows = []
        
        # Read file based on extension
        if filename.endswith('.csv'):
            raw_content = file.stream.read()
            content = None
            encodings = [
                ('utf-8-sig', 'strict'),
                ('utf-8', 'strict'),
                ('utf-8', 'ignore'),
                ('utf-8', 'replace'),
                ('latin-1', 'strict'),
                ('cp1252', 'strict'),
            ]
            
            for encoding, error_mode in encodings:
                try:
                    content = raw_content.decode(encoding, errors=error_mode)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            
            if content is None:
                content = raw_content.decode('utf-8', errors='replace')
            
            reader = csv.DictReader(content.splitlines())
            rows = list(reader)
        else:  # .xlsx
            wb = openpyxl.load_workbook(file.stream)
            ws = wb.active
            headers = [cell.value for cell in ws[1]]
            for row in ws.iter_rows(min_row=2, values_only=True):
                row_dict = {}
                for i, value in enumerate(row):
                    if i < len(headers) and headers[i]:
                        row_dict[headers[i]] = value
                rows.append(row_dict)
        
        # Process rows
        for row_num, row in enumerate(rows, start=2):
            # Support multiple column name formats
            name = str(row.get("Họ tên") or row.get("name") or row.get("ten") or row.get("Name") or row.get("Tên") or "").strip()
            mssv = str(row.get("MSSV") or row.get("mssv") or "").strip()
            gender = str(row.get("Giới tính") or row.get("gender") or row.get("Gioi tinh") or "").strip()
            date_of_birth_str = str(row.get("Ngày sinh") or row.get("date_of_birth") or row.get("Ngay sinh") or "").strip()
            
            if not name or not mssv:
                errors.append(f"Dòng {row_num}: Thiếu tên hoặc MSSV")
                count_skipped += 1
                continue
            
            # Check if student already exists
            existing = Student.query.filter_by(mssv=mssv).first()
            if existing:
                # If student exists but in different class, optionally update
                if existing.class_admin_id != class_admin_id:
                    errors.append(f"Dòng {row_num}: Sinh viên {mssv} đã thuộc lớp khác")
                count_skipped += 1
                continue
            
            # Parse date of birth
            date_of_birth = None
            if date_of_birth_str and date_of_birth_str != 'None':
                try:
                    # Try multiple date formats (prioritize dd/mm/yyyy)
                    for fmt in ['%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d']:
                        try:
                            date_of_birth = datetime.strptime(date_of_birth_str, fmt).date()
                            break
                        except ValueError:
                            continue
                except:
                    pass
            
            # Create user
            user = User(username=mssv, role="student", password_hash=generate_password_hash(mssv))
            db.session.add(user)
            db.session.flush()
            
            # Create student
            student = Student(
                user_id=user.id, 
                name=name, 
                mssv=mssv, 
                class_admin_id=class_admin_id,
                gender=gender if gender and gender != 'None' else None,
                date_of_birth=date_of_birth
            )
            db.session.add(student)
            count_added += 1
        
        db.session.commit()
        
        # Flash messages
        if count_added > 0:
            flash(f"Đã thêm {count_added} sinh viên vào lớp {class_admin.name}", "success")
        if count_skipped > 0:
            flash(f"Bỏ qua {count_skipped} sinh viên (đã tồn tại hoặc lỗi)", "warning")
        if errors:
            error_msg = "Lỗi: " + "; ".join(errors[:5])
            if len(errors) > 5:
                error_msg += f" (và {len(errors) - 5} lỗi khác)"
            flash(error_msg, "danger")
    
    except Exception as e:
        db.session.rollback()
        flash(f"Lỗi xử lý file: {str(e)}", "danger")
    
    return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))


@admin_bp.route("/class_admins/<int:class_admin_id>/remove_student/<int:student_id>", methods=["POST"])
@login_required
def class_admins_remove_student(class_admin_id: int, student_id: int):
    """Remove a student from class admin (set class_admin_id to NULL)"""
    class_admin = ClassAdmin.query.get_or_404(class_admin_id)
    student = Student.query.get_or_404(student_id)
    
    if student.class_admin_id != class_admin_id:
        flash("Sinh viên không thuộc lớp này", "danger")
        return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))
    
    student.class_admin_id = None
    db.session.commit()
    flash(f"Đã xóa sinh viên {student.name} khỏi lớp {class_admin.name}", "success")
    return redirect(url_for("admin.class_admins_detail", class_admin_id=class_admin_id))


@admin_bp.route("/class_admins/<int:class_admin_id>/export_students")
@login_required
def class_admins_export_students(class_admin_id: int):
    """Export students of a class admin to CSV"""
    class_admin = ClassAdmin.query.get_or_404(class_admin_id)
    students = Student.query.filter_by(class_admin_id=class_admin_id).order_by(Student.mssv).all()
    
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["STT", "MSSV", "Họ tên", "Giới tính", "Ngày sinh"])
    
    for idx, student in enumerate(students, start=1):
        dob_str = student.date_of_birth.strftime('%d/%m/%Y') if student.date_of_birth else ''
        writer.writerow([idx, student.mssv, student.name, student.gender or '', dob_str])
    
    output = si.getvalue().encode("utf-8-sig")
    from flask import Response
    filename = f"danh_sach_{class_admin.name}.csv"
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ---- Reports ----
@admin_bp.route("/reports/attendance", methods=["GET", "POST"]) 
@login_required
def report_attendance():
    # Get semester filter
    selected_semester_id = request.values.get("semester_id")
    semesters = Semester.query.order_by(Semester.start_date.desc()).all()
    
    # Filter sections by semester
    if selected_semester_id:
        sections = ClassSection.query.filter_by(semester_id=int(selected_semester_id)).all()
    else:
        sections = ClassSection.query.all()
    
    selected_section_id = request.values.get("class_section_id")
    # Prepare report grid data (students × sessions) if a section selected
    report_rows = []
    session_totals = []
    course_total = 0
    selected_section = None
    if selected_section_id:
        selected_section = ClassSection.query.get(int(selected_section_id))
        if selected_section:
            course_total = (selected_section.course.number_of_sessions if selected_section.course and getattr(selected_section.course, 'number_of_sessions', None) else 0)
            # map existing AttendanceSession by session_number
            sessions = AttendanceSession.query.filter_by(class_section_id=selected_section.id).all()
            sess_map = {s.session_number: s for s in sessions} if sessions else {}
            # initialize per-session totals with full session info
            session_totals = []
            for i in range(1, course_total+1):
                if i in sess_map:
                    s = sess_map[i]
                    bucket_name = {'morning': 'Sáng', 'afternoon': 'Chiều', 'evening': 'Tối'}.get(s.time_bucket, s.time_bucket)
                    date_str = s.session_date.strftime('%d/%m/%Y') if s.session_date else ''
                    session_totals.append({
                        'present': 0, 
                        'absent': 0, 
                        'created': True, 
                        'session_number': s.session_number,
                        'time_bucket': bucket_name,
                        'date': date_str
                    })
                else:
                    session_totals.append({'present': 0, 'absent': 0, 'created': False, 'session_number': i, 'time_bucket': '', 'date': ''})
            # get enrolled students
            enrolls = Enrollment.query.filter_by(class_section_id=selected_section.id).all()
            student_ids = [e.student_id for e in enrolls]
            students = Student.query.filter(Student.id.in_(student_ids)).order_by(Student.name).all() if student_ids else []
            for idx, st in enumerate(students, start=1):
                row = {'stt': idx, 'student_id': st.id, 'name': st.name, 'mssv': st.mssv, 'cells': [], 'present_count': 0, 'absent_count': 0}
                for sn in range(1, course_total+1):
                    if sn in sess_map:
                        s = sess_map[sn]
                        # look up attendance record for this student + session
                        att = Attendance.query.filter_by(student_id=st.id, attendance_session_id=s.id).first()
                        if att:
                            st_status = (att.status or '').lower()
                            is_present = st_status.startswith('p') or st_status in ('present', 'present_auto', 'present_manual', 'có mặt', 'co mat')
                            cell = 'có mặt' if is_present else 'vắng'
                            # tally
                            if cell == 'có mặt':
                                row['present_count'] += 1
                                session_totals[sn-1]['present'] += 1
                            else:
                                row['absent_count'] += 1
                                session_totals[sn-1]['absent'] += 1
                        else:
                            # created session but no attendance row -> check if session is finalized
                            # If session is ended/finalized, treat as absent; otherwise leave blank
                            if s.ended_at:
                                cell = 'vắng'
                                row['absent_count'] += 1
                                session_totals[sn-1]['absent'] += 1
                            else:
                                # Session still ongoing or not finalized
                                cell = '-'
                        row['cells'].append(cell)
                    else:
                        # session not created yet -> blank
                        row['cells'].append('')
                report_rows.append(row)

    return render_template("admin/report_attendance.html", 
                         sections=sections, 
                         selected_section_id=selected_section_id, 
                         report_rows=report_rows, 
                         session_totals=session_totals, 
                         course_total=course_total, 
                         selected_section=selected_section,
                         semesters=semesters,
                         selected_semester_id=selected_semester_id)


@admin_bp.route("/reports/attendance/export") 
@login_required
def report_attendance_export():
    """Export attendance report to Excel with formatted table"""
    section_id = request.args.get("class_section_id")
    
    if not section_id:
        flash("Vui lòng chọn lớp học phần", "warning")
        return redirect(url_for('admin.report_attendance'))
    
    selected_section = ClassSection.query.get_or_404(int(section_id))
    course_total = (selected_section.course.number_of_sessions if selected_section.course and getattr(selected_section.course, 'number_of_sessions', None) else 0)
    
    # Get sessions
    sessions = AttendanceSession.query.filter_by(class_section_id=selected_section.id).order_by(AttendanceSession.session_number.asc()).all()
    sess_map = {s.session_number: s for s in sessions} if sessions else {}
    
    # Get students
    enrolls = Enrollment.query.filter_by(class_section_id=selected_section.id).all()
    student_ids = [e.student_id for e in enrolls]
    students = Student.query.filter(Student.id.in_(student_ids)).order_by(Student.name).all() if student_ids else []
    
    # Build data
    report_rows = []
    session_totals = []
    
    for i in range(1, course_total+1):
        if i in sess_map:
            s = sess_map[i]
            bucket_name = {'morning': 'Sáng', 'afternoon': 'Chiều', 'evening': 'Tối'}.get(s.time_bucket, s.time_bucket)
            date_str = s.session_date.strftime('%d/%m/%Y') if s.session_date else ''
            session_totals.append({
                'present': 0, 'absent': 0, 'created': True,
                'session_number': s.session_number,
                'time_bucket': bucket_name,
                'date': date_str
            })
        else:
            session_totals.append({'present': 0, 'absent': 0, 'created': False, 'session_number': i, 'time_bucket': '', 'date': ''})
    
    for idx, st in enumerate(students, start=1):
        row = {'stt': idx, 'mssv': st.mssv, 'name': st.name, 'cells': [], 'present_count': 0, 'absent_count': 0}
        for sn in range(1, course_total+1):
            if sn in sess_map:
                s = sess_map[sn]
                att = Attendance.query.filter_by(student_id=st.id, attendance_session_id=s.id).first()
                if att:
                    st_status = (att.status or '').lower()
                    is_present = st_status.startswith('p') or st_status in ('present', 'present_auto', 'present_manual', 'có mặt', 'co mat')
                    cell = 'có mặt' if is_present else 'vắng'
                else:
                    cell = 'vắng'
                
                if cell == 'có mặt':
                    row['present_count'] += 1
                    session_totals[sn-1]['present'] += 1
                else:
                    row['absent_count'] += 1
                    session_totals[sn-1]['absent'] += 1
                row['cells'].append(cell)
            else:
                row['cells'].append('')
        report_rows.append(row)
    
    # Create Excel
    import openpyxl
    from openpyxl.styles import Font, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Báo cáo điểm danh"
    
    # Header row 1: Buổi X
    ws['A1'] = 'STT'
    ws['B1'] = 'MSSV'
    ws['C1'] = 'Họ tên'
    col_idx = 4
    for s in session_totals:
        ws.cell(row=1, column=col_idx, value=f"Buổi {s['session_number']}")
        col_idx += 1
    ws.cell(row=1, column=col_idx, value='Đã điểm danh')
    ws.cell(row=1, column=col_idx+1, value='Tổng Vắng')
    
    # Header row 2: Time info
    ws['A2'] = ''
    ws['B2'] = ''
    ws['C2'] = ''
    col_idx = 4
    for s in session_totals:
        if s['created']:
            ws.cell(row=2, column=col_idx, value=f"{s['time_bucket']} - {s['date']}")
        else:
            ws.cell(row=2, column=col_idx, value='')
        col_idx += 1
    ws.cell(row=2, column=col_idx, value='')
    ws.cell(row=2, column=col_idx+1, value='')
    
    # Merge cells
    ws.merge_cells('A1:A2')
    ws.merge_cells('B1:B2')
    ws.merge_cells('C1:C2')
    ws.merge_cells(start_row=1, start_column=col_idx, end_row=2, end_column=col_idx)
    ws.merge_cells(start_row=1, start_column=col_idx+1, end_row=2, end_column=col_idx+1)
    
    # Style headers
    for row in [1, 2]:
        for col in range(1, col_idx + 2):
            cell = ws.cell(row=row, column=col)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    
    # Data rows
    row_idx = 3
    for r in report_rows:
        ws.cell(row=row_idx, column=1, value=r['stt'])
        ws.cell(row=row_idx, column=2, value=r['mssv'])
        ws.cell(row=row_idx, column=3, value=r['name'])
        
        col_idx = 4
        for cell_value in r['cells']:
            ws.cell(row=row_idx, column=col_idx, value=cell_value)
            col_idx += 1
        
        ws.cell(row=row_idx, column=col_idx, value=r['present_count'])
        ws.cell(row=row_idx, column=col_idx+1, value=r['absent_count'])
        row_idx += 1
    
    # Statistics rows
    if any(s['created'] for s in session_totals):
        # Row: Thống kê - vắng
        ws.cell(row=row_idx, column=1, value='Thống kê')
        ws.cell(row=row_idx, column=2, value='')
        ws.cell(row=row_idx, column=3, value='vắng')
        col_idx = 4
        for s in session_totals:
            ws.cell(row=row_idx, column=col_idx, value=s['absent'] if s['created'] else '')
            col_idx += 1
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx+1, end_column=1)
        ws.merge_cells(start_row=row_idx, start_column=2, end_row=row_idx+1, end_column=2)
        row_idx += 1
        
        # Row: có mặt
        ws.cell(row=row_idx, column=3, value='có mặt')
        col_idx = 4
        for s in session_totals:
            ws.cell(row=row_idx, column=col_idx, value=s['present'] if s['created'] else '')
            col_idx += 1
    
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
    
    section_name = selected_section.name.replace(' ', '_')
    filename = f"bao_cao_diem_danh_{section_name}.xlsx"
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )
# ---- Semester Management ----
@admin_bp.route("/semesters")
@login_required
def semesters_list():
    """Danh sách học kỳ"""
    semesters = Semester.query.order_by(Semester.start_date.desc()).all()
    return render_template("admin/semesters_list.html", semesters=semesters)


@admin_bp.route("/semesters/create", methods=["GET", "POST"])
@login_required
def semester_create():
    """Tạo học kỳ mới"""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        semester_number = request.form.get("semester_number")
        academic_year = request.form.get("academic_year", "").strip()
        start_date_str = request.form.get("start_date")
        end_date_str = request.form.get("end_date")
        
        if not all([name, semester_number, academic_year, start_date_str, end_date_str]):
            flash("Vui lòng điền đầy đủ thông tin", "danger")
            return redirect(url_for("admin.semester_create"))
        
        try:
            from datetime import datetime
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            
            if start_date >= end_date:
                flash("Ngày kết thúc phải sau ngày bắt đầu", "danger")
                return redirect(url_for("admin.semester_create"))
            
            # Kiểm tra trùng tên
            existing = Semester.query.filter_by(name=name).first()
            if existing:
                flash(f"Học kỳ '{name}' đã tồn tại", "danger")
                return redirect(url_for("admin.semester_create"))
            
            semester = Semester(
                name=name,
                semester_number=int(semester_number),
                academic_year=academic_year,
                start_date=start_date,
                end_date=end_date,
                is_active=False
            )
            db.session.add(semester)
            db.session.commit()
            flash("Đã tạo học kỳ mới", "success")
            return redirect(url_for("admin.semesters_list"))
        except ValueError as e:
            flash(f"Lỗi định dạng ngày: {e}", "danger")
            return redirect(url_for("admin.semester_create"))
    
    return render_template("admin/semester_form.html", semester=None)


@admin_bp.route("/semesters/<int:semester_id>/edit", methods=["GET", "POST"])
@login_required
def semester_edit(semester_id: int):
    """Sửa học kỳ"""
    semester = Semester.query.get_or_404(semester_id)
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        semester_number = request.form.get("semester_number")
        academic_year = request.form.get("academic_year", "").strip()
        start_date_str = request.form.get("start_date")
        end_date_str = request.form.get("end_date")
        
        if not all([name, semester_number, academic_year, start_date_str, end_date_str]):
            flash("Vui lòng điền đầy đủ thông tin", "danger")
            return redirect(url_for("admin.semester_edit", semester_id=semester_id))
        
        try:
            from datetime import datetime
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            
            if start_date >= end_date:
                flash("Ngày kết thúc phải sau ngày bắt đầu", "danger")
                return redirect(url_for("admin.semester_edit", semester_id=semester_id))
            
            # Kiểm tra trùng tên (trừ chính nó)
            existing = Semester.query.filter(Semester.name == name, Semester.id != semester_id).first()
            if existing:
                flash(f"Học kỳ '{name}' đã tồn tại", "danger")
                return redirect(url_for("admin.semester_edit", semester_id=semester_id))
            
            semester.name = name
            semester.semester_number = int(semester_number)
            semester.academic_year = academic_year
            semester.start_date = start_date
            semester.end_date = end_date
            
            db.session.commit()
            flash("Đã cập nhật học kỳ", "success")
            return redirect(url_for("admin.semesters_list"))
        except ValueError as e:
            flash(f"Lỗi định dạng ngày: {e}", "danger")
            return redirect(url_for("admin.semester_edit", semester_id=semester_id))
    
    return render_template("admin/semester_form.html", semester=semester)


@admin_bp.route("/semesters/<int:semester_id>/delete", methods=["POST"])
@login_required
def semester_delete(semester_id: int):
    """Xóa học kỳ"""
    semester = Semester.query.get_or_404(semester_id)
    
    # Kiểm tra xem có lớp học phần nào đang dùng học kỳ này không
    class_count = ClassSection.query.filter_by(semester_id=semester_id).count()
    if class_count > 0:
        flash(f"Không thể xóa học kỳ này vì có {class_count} lớp học phần đang sử dụng", "danger")
        return redirect(url_for("admin.semesters_list"))
    
    db.session.delete(semester)
    db.session.commit()
    flash("Đã xóa học kỳ", "success")
    return redirect(url_for("admin.semesters_list"))


@admin_bp.route("/semesters/<int:semester_id>/set_active", methods=["POST"])
@login_required
def set_active_semester(semester_id: int):
    """Đặt học kỳ hiện tại"""
    # Bỏ active tất cả các học kỳ
    Semester.query.update({Semester.is_active: False})
    
    # Set active cho học kỳ được chọn
    semester = Semester.query.get_or_404(semester_id)
    semester.is_active = True
    db.session.commit()
    
    flash(f"Đã đặt '{semester.name}' làm học kỳ hiện tại", "success")
    return redirect(url_for("admin.semesters_list"))
