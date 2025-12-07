import csv

# Create CSV template with UTF-8 BOM encoding for Excel compatibility
with open('static/templates/enroll_template.csv', 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    
    # Header row
    writer.writerow(['STT', 'MSSV', 'Họ tên', 'Giới tính', 'Ngày sinh'])
    
    # Sample data with Vietnamese characters
    writer.writerow([1, 'SV001', 'Nguyễn Văn A', 'Nam', '15/01/2005'])
    writer.writerow([2, 'SV002', 'Trần Thị B', 'Nữ', '20/03/2005'])
    writer.writerow([3, 'SV003', 'Lê Văn C', 'Nam', '10/12/2004'])
    writer.writerow([4, 'SV004', 'Phạm Thị D', 'Nữ', '25/05/2005'])
    writer.writerow([5, 'SV005', 'Hoàng Văn E', 'Nam', '08/07/2005'])

print('Created enroll_template.csv successfully with format: STT, MSSV, Họ tên, Giới tính, Ngày sinh')
