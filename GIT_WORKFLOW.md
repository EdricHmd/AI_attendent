# 📚 Hướng dẫn sử dụng Git

## ❓ Tại sao commit của tôi không có thay đổi?

Khi bạn thấy một commit không có thay đổi (empty commit), điều này xảy ra vì:

1. **Không có file nào được stage** trước khi commit
2. **Không có thay đổi nào** trong working directory khi commit
3. **Các file bị ignore** bởi `.gitignore`

## ✅ Quy trình commit đúng cách

### Bước 1: Kiểm tra trạng thái
```bash
git status
```
Lệnh này sẽ hiển thị:
- ✓ Files đã được stage (màu xanh) - sẽ được commit
- ⚠️ Files chưa được stage (màu đỏ) - sẽ KHÔNG được commit
- ℹ️ Files không bị theo dõi

### Bước 2: Xem thay đổi
```bash
# Xem tất cả thay đổi chưa stage
git diff

# Xem thay đổi đã stage (sẽ được commit)
git diff --cached
```

### Bước 3: Stage files
```bash
# Stage một file cụ thể
git add app.py

# Stage nhiều files
git add app.py models.py controllers/

# Stage tất cả files đã thay đổi
git add .
```

### Bước 4: Kiểm tra lại
```bash
# Kiểm tra files đã stage
git status

# Xem nội dung thay đổi sẽ được commit
git diff --cached
```

### Bước 5: Commit
```bash
git commit -m "Mô tả thay đổi của bạn"
```

### Bước 6: Xác nhận commit
```bash
# Xem commit vừa tạo
git show HEAD

# Hoặc xem lịch sử
git log --oneline -5
```

## 🔍 Kiểm tra commit hiện tại

### Xem nội dung của một commit
```bash
# Xem commit hiện tại
git show HEAD

# Xem commit trước đó
git show HEAD~1

# Xem commit cụ thể
git show e6914b8

# Xem danh sách files trong commit
git show --stat HEAD
```

### Xem lịch sử commits
```bash
# Xem lịch sử ngắn gọn
git log --oneline -10

# Xem lịch sử với files thay đổi
git log --stat -5

# Xem lịch sử đầy đủ
git log -5
```

## 🚨 Các lỗi thường gặp

### Lỗi 1: Quên stage files
```bash
# ❌ SAI: Commit mà không stage
git commit -m "Update code"
# Kết quả: Empty commit!

# ✅ ĐÚNG: Stage trước khi commit
git add .
git status  # Kiểm tra
git commit -m "Update code"
```

### Lỗi 2: Files bị ignore
Kiểm tra file `.gitignore`:
```bash
# Xem nội dung .gitignore
cat .gitignore

# Kiểm tra file có bị ignore không
git check-ignore -v app.db
```

Các file thường bị ignore:
- `*.pyc`, `__pycache__/` - Python compiled files
- `.venv/`, `venv/` - Virtual environment
- `*.db`, `*.sqlite` - Database files
- `node_modules/` - Node packages
- `.env` - Environment variables

### Lỗi 3: Không có thay đổi thực sự
```bash
# Kiểm tra có thay đổi không
git status

# Nếu hiển thị "nothing to commit, working tree clean"
# → Bạn chưa thay đổi gì hoặc đã commit rồi
```

## 💡 Tips hữu ích

### 1. Luôn kiểm tra trước khi commit
```bash
git status && git diff --cached
```

### 2. Sử dụng git add interactive
```bash
# Chọn từng phần để stage
git add -p
```

### 3. Unstage files nếu stage nhầm
```bash
# Unstage một file
git reset HEAD app.py

# Unstage tất cả
git reset HEAD
```

### 4. Xem thay đổi giữa commits
```bash
# So sánh 2 commits
git diff commit1 commit2

# So sánh với commit trước
git diff HEAD~1 HEAD
```

### 5. Tạo alias cho lệnh thường dùng
```bash
git config --global alias.st status
git config --global alias.co commit
git config --global alias.cm "commit -m"
git config --global alias.df "diff --cached"
```

Sau đó có thể dùng:
```bash
git st      # thay vì git status
git df      # thay vì git diff --cached
git cm "msg" # thay vì git commit -m "msg"
```

## 📋 Checklist trước mỗi commit

- [ ] Chạy `git status` để xem files thay đổi
- [ ] Chạy `git diff` để xem nội dung thay đổi
- [ ] Chạy `git add` để stage files cần commit
- [ ] Chạy `git status` lần nữa để xác nhận
- [ ] Chạy `git diff --cached` để xem thay đổi sẽ được commit
- [ ] Chạy tests nếu có: `python -m pytest` hoặc test thủ công
- [ ] Chạy `git commit -m "mô tả rõ ràng"`
- [ ] Chạy `git show HEAD` để xác nhận commit

## 🔧 Sửa commit vừa tạo

### Thêm files vào commit cuối
```bash
git add forgotten_file.py
git commit --amend --no-edit
```

### Sửa commit message
```bash
git commit --amend -m "Commit message mới"
```

**⚠️ Cảnh báo**: Chỉ amend commit chưa push. Nếu đã push, cần `git push --force` (nguy hiểm nếu làm việc nhóm).

## 🌐 Làm việc với remote

### Push commits
```bash
# Push lên remote
git push origin branch-name

# Xem remote URL
git remote -v
```

### Kiểm tra trạng thái sync với remote
```bash
# Fetch thông tin từ remote
git fetch

# So sánh local và remote
git log origin/main..HEAD  # Commits local chưa push
git log HEAD..origin/main  # Commits remote chưa pull
```

## 📞 Cần trợ giúp?

1. Đọc thêm: `git --help` hoặc `git command --help`
2. Xem log chi tiết trong `logs/` folder
3. Tạo issue trên GitHub repository
4. Liên hệ team lead hoặc quản trị viên
