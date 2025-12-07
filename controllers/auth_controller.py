from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from models import User
from models import db


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Sai tài khoản hoặc mật khẩu", "danger")
            return render_template("auth/login.html")
        login_user(user)
        flash("Đăng nhập thành công", "success")
        if user.role == "admin":
            return redirect(url_for("admin.dashboard"))
        if user.role == "teacher":
            return redirect(url_for("teacher.dashboard"))
        return redirect(url_for("student.dashboard"))
    if current_user.is_authenticated:
        if current_user.role == "admin":
            return redirect(url_for("admin.dashboard"))
        if current_user.role == "teacher":
            return redirect(url_for("teacher.dashboard"))
        return redirect(url_for("student.dashboard"))
    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Đã đăng xuất", "info")
    return redirect(url_for("auth.login"))
