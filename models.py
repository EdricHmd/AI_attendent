from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)

    student = db.relationship("Student", back_populates="user", uselist=False)
    teacher = db.relationship("Teacher", back_populates="user", uselist=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class ClassAdmin(db.Model):
    __tablename__ = "class_admins"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)

    students = db.relationship("Student", back_populates="class_admin", cascade="all, delete-orphan")


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    mssv = db.Column(db.String(32), unique=True, nullable=False)
    class_admin_id = db.Column(db.Integer, db.ForeignKey("class_admins.id"))
    gender = db.Column(db.String(10))  # 'Nam', 'Nữ', 'Khác'
    date_of_birth = db.Column(db.Date)
    embedding = db.Column(db.Text)

    user = db.relationship("User", back_populates="student")
    class_admin = db.relationship("ClassAdmin", back_populates="students")
    enrollments = db.relationship("Enrollment", back_populates="student", cascade="all, delete-orphan")
    attendances = db.relationship("Attendance", back_populates="student", cascade="all, delete-orphan")

    def set_embedding(self, vector: List[float]) -> None:
        self.embedding = json.dumps(vector)

    def get_embedding(self) -> Optional[List[float]]:
        if not self.embedding:
            return None
        try:
            data = json.loads(self.embedding)
            if isinstance(data, list):
                return [float(x) for x in data]
            return None
        except json.JSONDecodeError:
            return None


class Teacher(db.Model):
    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128))

    user = db.relationship("User", back_populates="teacher")
    class_sections = db.relationship("ClassSection", back_populates="teacher")


class Semester(db.Model):
    __tablename__ = "semesters"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)  # VD: "HK1 2024-2025"
    semester_number = db.Column(db.Integer, nullable=False)  # 1, 2, 3
    academic_year = db.Column(db.String(20), nullable=False)  # "2024-2025"
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_active = db.Column(db.Boolean, default=False)  # Học kỳ hiện tại

    class_sections = db.relationship("ClassSection", back_populates="semester_obj")

    def __repr__(self):
        return f'<Semester {self.name}>'

    @staticmethod
    def get_current_semester():
        """Lấy học kỳ hiện tại dựa trên ngày hoặc is_active flag"""
        # Ưu tiên lấy học kỳ được đánh dấu active
        semester = Semester.query.filter_by(is_active=True).first()
        if semester:
            return semester
        # Nếu không có, tìm học kỳ chứa ngày hiện tại
        from datetime import date
        today = date.today()
        semester = Semester.query.filter(
            Semester.start_date <= today,
            Semester.end_date >= today
        ).first()
        return semester


class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    # number of sessions for the course (default 6)
    number_of_sessions = db.Column(db.Integer, nullable=False, default=6)

    class_sections = db.relationship("ClassSection", back_populates="course")


class ClassSection(db.Model):
    __tablename__ = "class_sections"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey("semesters.id"))

    course = db.relationship("Course", back_populates="class_sections")
    teacher = db.relationship("Teacher", back_populates="class_sections")
    semester_obj = db.relationship("Semester", back_populates="class_sections")
    enrollments = db.relationship("Enrollment", back_populates="class_section", cascade="all, delete-orphan")
    attendances = db.relationship("Attendance", back_populates="class_section", cascade="all, delete-orphan")


class Enrollment(db.Model):
    __tablename__ = "enrollments"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    class_section_id = db.Column(db.Integer, db.ForeignKey("class_sections.id"), nullable=False)

    student = db.relationship("Student", back_populates="enrollments")
    class_section = db.relationship("ClassSection", back_populates="enrollments")


class Attendance(db.Model):
    __tablename__ = "attendances"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    class_section_id = db.Column(db.Integer, db.ForeignKey("class_sections.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    attendance_date = db.Column(db.Date)
    status = db.Column(db.String(16), nullable=False)
    source = db.Column(db.String(32), nullable=False)
    # Optional link to an AttendanceSession (new table)
    attendance_session_id = db.Column(db.Integer, db.ForeignKey('attendance_sessions.id'), nullable=True)
    # Ensure a student cannot have duplicate attendance rows for the same
    # session. Keep it optional for legacy rows where attendance_session_id may be NULL.
    __table_args__ = (
        UniqueConstraint('student_id', 'attendance_session_id', name='uq_att_student_session'),
    )

    student = db.relationship("Student", back_populates="attendances")
    class_section = db.relationship("ClassSection", back_populates="attendances")


class AttendanceSession(db.Model):
    __tablename__ = "attendance_sessions"

    id = db.Column(db.Integer, primary_key=True)
    class_section_id = db.Column(db.Integer, db.ForeignKey("class_sections.id"), nullable=False)
    session_number = db.Column(db.Integer, nullable=False, default=1)
    session_date = db.Column(db.Date, nullable=False)
    time_bucket = db.Column(db.String(16), nullable=False)  # 'morning'|'afternoon'|'evening'
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.Integer, nullable=True)  # optional teacher id
    # Note: this session represents a single attendance period. We do not
    # track separate "start"/"end" attendance flags anymore; use `ended_at`
    # to determine whether the session is still open.

    # relationships
    class_section = db.relationship("ClassSection")
    attendances = db.relationship("Attendance", back_populates="attendance_session", cascade="all, delete-orphan")

    # Note: removed UniqueConstraint to allow multiple sessions per class per day

# back-populate relationship on Attendance now that AttendanceSession exists
Attendance.attendance_session = db.relationship("AttendanceSession", back_populates="attendances")

def create_sample_data() -> None:
    # Create users if they don't exist
    def get_or_create_user(username: str, role: str, password: str) -> User:
        u = User.query.filter_by(username=username).first()
        if u:
            return u
        u = User(username=username, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.flush()
        return u

    admin_user = get_or_create_user("admin", "admin", "password123")
    teacher_user = get_or_create_user("teacher", "teacher", "password123")
    student_user = get_or_create_user("student", "student", "password123")

    # Teacher record
    teacher = Teacher.query.filter_by(user_id=teacher_user.id).first()
    if not teacher:
        teacher = Teacher(user_id=teacher_user.id, name="Teacher One", email="teacher@example.com")
        db.session.add(teacher)
        db.session.flush()

    # Class admin
    class_admin = ClassAdmin.query.filter_by(name="K67-CTTT").first()
    if not class_admin:
        class_admin = ClassAdmin(name="K67-CTTT")
        db.session.add(class_admin)
        db.session.flush()

    # Student
    student = Student.query.filter_by(mssv="SV0001").first()
    if not student:
        student = Student(user_id=student_user.id, name="Hoàng Minh Đạt", mssv="SV0001", class_admin=class_admin)
        db.session.add(student)
        db.session.flush()

    # Course
    course = Course.query.filter_by(code="CS101").first()
    if not course:
        course = Course(code="CS101", name="Introduction to CS", number_of_sessions=6)
        db.session.add(course)
        db.session.flush()

    # Class section
    class_section = ClassSection.query.filter_by(name="CS101-1").first()
    if not class_section:
        # ensure teacher and course ids are set
        class_section = ClassSection(name="CS101-1", course_id=course.id, teacher_id=teacher.id)
        db.session.add(class_section)
        db.session.flush()

    # Enrollment
    enrollment = Enrollment.query.filter_by(student_id=student.id, class_section_id=class_section.id).first()
    if not enrollment:
        enrollment = Enrollment(student_id=student.id, class_section_id=class_section.id)
        db.session.add(enrollment)

    # --- Second student (idempotent) -------------------------------------
    # different class_admin but enroll into same class_section
    second_user = get_or_create_user("student2", "student", "password123")
    second_ca = ClassAdmin.query.filter_by(name="K68-CTTT").first()
    if not second_ca:
        second_ca = ClassAdmin(name="K68-CTTT")
        db.session.add(second_ca)
        db.session.flush()

    student2 = Student.query.filter_by(mssv="SV0002").first()
    if not student2:
        student2 = Student(user_id=second_user.id, name="Huỳnh Văn Quân", mssv="SV0002", class_admin=second_ca)
        db.session.add(student2)
        db.session.flush()

    enrollment2 = Enrollment.query.filter_by(student_id=student2.id, class_section_id=class_section.id).first()
    if not enrollment2:
        enrollment2 = Enrollment(student_id=student2.id, class_section_id=class_section.id)
        db.session.add(enrollment2)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


class BackgroundProcess(db.Model):
    __tablename__ = "background_processes"

    id = db.Column(db.Integer, primary_key=True)
    pid = db.Column(db.Integer, nullable=False)
    proc_type = db.Column(db.String(32), nullable=False)  # 'attendance' or 'register'
    target_id = db.Column(db.Integer, nullable=True)  # class_section_id or student_id
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status = db.Column(db.String(32), nullable=False, default="running")

    def to_dict(self):
        return {
            "id": self.id,
            "pid": self.pid,
            "proc_type": self.proc_type,
            "target_id": self.target_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "status": self.status,
        }
