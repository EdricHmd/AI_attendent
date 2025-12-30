#!/usr/bin/env python3
"""
Script để kiểm tra trạng thái Git trước khi commit
Giúp tránh tạo empty commits
"""

import subprocess
import sys

# Constants
MAX_DISPLAYED_FILES = 10

def run_git_command(cmd_args):
    """Chạy lệnh git và trả về output
    
    Args:
        cmd_args: List of command arguments (e.g., ['git', 'status'])
    """
    try:
        result = subprocess.run(
            cmd_args, 
            capture_output=True, 
            text=True
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)

def check_git_status():
    """Kiểm tra trạng thái Git"""
    print("=" * 60)
    print("🔍 KIỂM TRA TRẠNG THÁI GIT")
    print("=" * 60)
    
    # Kiểm tra có phải Git repo không
    returncode, _, _ = run_git_command(["git", "rev-parse", "--git-dir"])
    if returncode != 0:
        print("❌ Không phải Git repository!")
        return False
    
    # Kiểm tra trạng thái
    print("\n📋 Git Status:")
    print("-" * 60)
    returncode, output, _ = run_git_command(["git", "status"])
    print(output)
    
    # Kiểm tra files đã stage
    print("\n✅ Files đã stage (sẽ được commit):")
    print("-" * 60)
    returncode, output, _ = run_git_command(["git", "diff", "--cached", "--name-status"])
    if output.strip():
        print(output)
        has_staged = True
    else:
        print("⚠️  KHÔNG có files nào được stage!")
        print("   → Commit sẽ trống (empty commit)")
        has_staged = False
    
    # Kiểm tra files chưa stage
    print("\n⏳ Files đã thay đổi nhưng chưa stage:")
    print("-" * 60)
    returncode, output, _ = run_git_command(["git", "diff", "--name-status"])
    if output.strip():
        print(output)
        print("\n💡 Để stage các files này:")
        print("   git add <file>")
        print("   hoặc: git add .")
    else:
        print("✓ Không có files chưa stage")
    
    # Kiểm tra untracked files
    print("\n📄 Files chưa được theo dõi (untracked):")
    print("-" * 60)
    returncode, output, _ = run_git_command(["git", "ls-files", "--others", "--exclude-standard"])
    if output.strip():
        files = output.strip().split('\n')
        for f in files[:MAX_DISPLAYED_FILES]:
            print(f"   {f}")
        if len(files) > MAX_DISPLAYED_FILES:
            print(f"   ... và {len(files) - MAX_DISPLAYED_FILES} files khác")
        print("\n💡 Để thêm vào Git:")
        print("   git add <file>")
    else:
        print("✓ Không có untracked files")
    
    # Xem nội dung sẽ được commit
    if has_staged:
        print("\n📝 Nội dung thay đổi sẽ được commit:")
        print("-" * 60)
        returncode, output, _ = run_git_command(["git", "diff", "--cached", "--stat"])
        print(output)
    
    print("\n" + "=" * 60)
    
    if not has_staged:
        print("⚠️  CẢNH BÁO: Commit hiện tại sẽ TRỐNG!")
        print("=" * 60)
        print("\n💡 Các bước tiếp theo:")
        print("   1. Chạy: git add <files>")
        print("   2. Chạy lại script này để kiểm tra")
        print("   3. Commit khi đã có files được stage")
        return False
    else:
        print("✅ SẴN SÀNG COMMIT!")
        print("=" * 60)
        print("\n💡 Để commit:")
        print("   git commit -m \"Mô tả thay đổi của bạn\"")
        return True

def show_last_commit():
    """Hiển thị commit cuối cùng"""
    print("\n" + "=" * 60)
    print("📜 COMMIT CUỐI CÙNG")
    print("=" * 60)
    
    # Kiểm tra có commit nào chưa
    returncode, _, _ = run_git_command(["git", "rev-parse", "HEAD"])
    if returncode != 0:
        print("⚠️  Repository chưa có commit nào!")
        return
    
    returncode, output, _ = run_git_command(["git", "log", "-1", "--stat"])
    print(output)
    
    # Kiểm tra xem commit có thay đổi file nào không
    returncode, files_output, _ = run_git_command(["git", "diff-tree", "--no-commit-id", "--name-only", "HEAD"])
    if returncode == 0 and not files_output.strip():
        print("\n⚠️  CẢNH BÁO: Commit này có vẻ TRỐNG (không có files thay đổi)!")

def main():
    """Main function"""
    if len(sys.argv) > 1 and sys.argv[1] == "--last":
        show_last_commit()
    else:
        check_git_status()
    
    print("\n" + "=" * 60)
    print("📚 Xem thêm hướng dẫn: GIT_WORKFLOW.md")
    print("=" * 60)

if __name__ == "__main__":
    main()
