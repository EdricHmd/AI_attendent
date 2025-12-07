# 🔧 Khắc phục sự cố

## ⚠️ LỖI: face_recognition không khả dụng

### Triệu chứng:
- Đăng ký khuôn mặt thất bại với thông báo "Không phát hiện khuôn mặt"
- Log hiển thị: `face_recognition not available; get_face_encodings will return None`
- Lỗi: `RuntimeError: Unable to open ...shape_predictor_68_face_landmarks.dat`

### Nguyên nhân:
Thư viện `face_recognition` (dựa trên `dlib`) không hỗ trợ đường dẫn có:
- ✗ Ký tự Unicode/tiếng Việt có dấu (ví dụ: "Sao chép", "Bản sao")
- ✗ Khoảng trắng trong tên thư mục

### ✅ Giải pháp:

#### Cách 1: Di chuyển project (KHUYẾN NGHỊ)

1. **Di chuyển project sang thư mục không có ký tự đặc biệt:**
   ```powershell
   # Ví dụ: di chuyển từ
   D:\backup\face_ai_update_2 - Sao chép
   
   # Sang
   D:\backup\face_ai_update_2_copy
   # hoặc
   C:\projects\face_ai
   ```

2. **Tạo lại virtual environment:**
   ```powershell
   cd D:\backup\face_ai_update_2_copy
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Chạy lại ứng dụng:**
   ```powershell
   python app.py
   ```

#### Cách 2: Sử dụng symlink (nâng cao)

1. **Tạo symbolic link không có ký tự đặc biệt:**
   ```powershell
   # Chạy PowerShell với quyền Administrator
   New-Item -ItemType SymbolicLink -Path "D:\backup\face_ai" -Target "D:\backup\face_ai_update_2 - Sao chép"
   ```

2. **Làm việc qua symlink:**
   ```powershell
   cd D:\backup\face_ai
   .\.venv\Scripts\Activate.ps1
   python app.py
   ```

#### Cách 3: Sử dụng Docker (khuyến nghị cho production)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "app.py"]
```

### 🧪 Kiểm tra sau khi khắc phục:

```python
# Chạy test trong Python terminal
python -c "import face_recognition; print('✓ face_recognition OK')"
```

Nếu không có lỗi, bạn đã khắc phục thành công!

---

## 📋 Các lỗi đăng ký khuôn mặt khác

| Mã lỗi | Thông báo | Nguyên nhân | Giải pháp |
|---------|-----------|-------------|-----------|
| `camera_error` | Không thể mở camera | Camera bị chiếm hoặc không có quyền truy cập | Đóng ứng dụng đang dùng camera, cấp quyền camera |
| `no_face_detected` | Không phát hiện khuôn mặt | Khuôn mặt không trong khung hình hoặc quá tối | Điều chỉnh ánh sáng, nhìn thẳng camera |
| `insufficient_frames` | Không đủ khung hình (< 10) | Quét quá nhanh hoặc mất kết nối | Giữ khuôn mặt trong khung 10 giây |
| `duplicate_face` | Khuôn mặt đã đăng ký | Khuôn mặt trùng với sinh viên khác | Kiểm tra lại danh sách sinh viên |
| `database_error` | Lỗi lưu dữ liệu | Database bị lock hoặc lỗi kết nối | Khởi động lại ứng dụng |
| `system_error` | Lỗi hệ thống | Module không khả dụng | Xem hướng dẫn ở trên |

---

## 🆘 Cần trợ giúp thêm?

1. Kiểm tra log file trong `logs/`
2. Mở issue trên GitHub repository
3. Liên hệ quản trị viên hệ thống
