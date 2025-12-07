# Hệ thống điểm danh sinh viên bằng nhận diện khuôn mặt (Flask + YOLO + face_recognition)

## Yêu cầu
- Python 3.10+
- Windows (đã cài Visual C++ Build Tools nếu cần cho face_recognition)

## Cài đặt
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Tạo thư mục uploads:
```bash
mkdir static\uploads
```

Tùy chọn `.env`:
```
SECRET_KEY=secret-key
DATABASE_URL=sqlite:///app.db
UPLOAD_FOLDER=static/uploads
YOLO_MODEL_PATH=models/yolov8n-face.pt
```

Tải trọng số YOLOv8 face (đặt vào `models/yolov8n-face.pt`).

## Chạy ứng dụng
```bash
python app.py
```
Đăng nhập:
- admin/password123
- teacher/password123
- student/password123

## Đăng ký khuôn mặt (Sinh viên)
```bash
python ai/face_capture.py --student_id 1 --duration 10 --save-db True
```

## Điểm danh realtime (Giảng viên)
```bash
python ai/face_attendance.py --class_id 1
```
Kết quả sẽ lưu vào CSDL và CSV trong `static/uploads/`.

## Phân biệt lớp
- Lớp hành chính: quản lý bởi Admin, Student thuộc 1 lớp hành chính.
- Lớp học phần: thuộc 1 học phần, 1 giảng viên phụ trách, có Enrollment và Attendance.

## Ghi chú
- Khi import CSV sinh viên, tài khoản/mật khẩu = MSSV.
- Ngưỡng khớp embedding mặc định 0.6.
