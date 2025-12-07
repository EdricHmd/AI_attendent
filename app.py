import logging
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_migrate import Migrate

from config import Config
from models import db, User, create_sample_data
from logging_config import setup_logging


login_manager = LoginManager()
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def create_app() -> Flask:
    # configure logging before app creation
    setup_logging()
    # Avoid logging 'Starting Flask app' multiple times if create_app() is called
    # repeatedly in the same process (other modules sometimes call create_app()).
    global _app_start_logged
    try:
        _app_start_logged
    except NameError:
        _app_start_logged = False
    if not _app_start_logged:
        logging.getLogger(__name__).info("Starting Flask app")
        _app_start_logged = True

    app = Flask(__name__)
    app.config.from_object(Config)

    # Increase SQLite busy timeout to reduce 'database is locked' errors when multiple
    # processes access the DB concurrently. Also enable pool_pre_ping for robustness.
    app.config.setdefault('SQLALCHEMY_ENGINE_OPTIONS', {})
    engine_opts = app.config['SQLALCHEMY_ENGINE_OPTIONS']
    engine_opts.setdefault('connect_args', {})
    # timeout in seconds for sqlite busy handler
    engine_opts['connect_args'].setdefault('timeout', 30)
    engine_opts.setdefault('pool_pre_ping', True)

    db.init_app(app)
    login_manager.init_app(app)
    Migrate(app, db)

    # Register blueprints
    from controllers.auth_controller import auth_bp
    app.register_blueprint(auth_bp)

    try:
        from controllers.admin_controller import admin_bp
        app.register_blueprint(admin_bp)
    except Exception:
        logging.getLogger(__name__).exception("Failed to register admin blueprint")

    try:
        from controllers.teacher_controller import teacher_bp
        app.register_blueprint(teacher_bp)
    except Exception:
        logging.getLogger(__name__).exception("Failed to register teacher blueprint")

    try:
        from controllers.student_controller import student_bp
        app.register_blueprint(student_bp)
    except Exception:
        logging.getLogger(__name__).exception("Failed to register student blueprint")

    # register preview API blueprint
    try:
        # controllers/api_controller defines `bp` - import and alias to api_bp
        from controllers.api_controller import bp as api_bp
        app.register_blueprint(api_bp)
    except Exception:
        logging.getLogger(__name__).exception("Failed to register api blueprint")

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    with app.app_context():
        db.create_all()
        create_sample_data()

        # --- Startup housekeeping -------------------------------------------------
        # 1) Cleanup stale BackgroundProcess entries (mark as stopped if PID not running)
        try:
            import psutil
            from models import BackgroundProcess
            for bp in BackgroundProcess.query.filter_by(status='running').all():
                try:
                    p = psutil.Process(bp.pid)
                    if not p.is_running():
                        BackgroundProcess.query.filter_by(id=bp.id).update({'status': 'stopped'})
                except psutil.NoSuchProcess:
                    BackgroundProcess.query.filter_by(id=bp.id).update({'status': 'stopped'})
                except Exception:
                    logging.getLogger(__name__).exception('Error checking BackgroundProcess pid=%s', getattr(bp, 'pid', None))
            db.session.commit()
        except Exception:
            logging.getLogger(__name__).exception('Failed to cleanup BackgroundProcess entries on startup')

        # 2) Ensure attendance_date column exists; remove old per-day UNIQUE index
        try:
            from sqlalchemy import text
            # check columns
            res = db.session.execute(text("PRAGMA table_info('attendances')")).fetchall()
            cols = [r[1] for r in res]
            if 'attendance_date' not in cols:
                # add column (SQLite supports ADD COLUMN)
                db.session.execute(text("ALTER TABLE attendances ADD COLUMN attendance_date DATE"))
                db.session.commit()
            # IMPORTANT: We no longer enforce per-day uniqueness across sessions.
            # Drop legacy unique index if it exists to allow multiple sessions per day.
            db.session.execute(text("DROP INDEX IF EXISTS uix_attendance_student_class_date"))
            db.session.commit()
            # Optionally keep a NON-UNIQUE index to speed up history/queries.
            try:
                db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_attendance_student_class_date ON attendances (student_id, class_section_id, attendance_date)"))
                db.session.commit()
            except Exception:
                db.session.rollback()
        except Exception:
            logging.getLogger(__name__).exception('Failed to ensure attendance_date column / unique index')

    return app


if __name__ == "__main__":
    app = create_app()
    # Disable the auto-reloader so we run a single stable process while
    # performing interactive realtime tests. The debugger can still be
    # active, but the separate reloader process often causes temporary
    # connection resets (ERR_CONNECTION_RESET) during rapid restarts.
    app.run(host='127.0.0.1', port=5500, debug=True, use_reloader=False)
