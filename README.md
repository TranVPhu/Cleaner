# Phu_Don_Rac

Công cụ dọn file rác cho Windows (kiểu CCleaner), viết bằng Python + Tkinter, không cần thư viện ngoài.

## Chạy

```
python main.py                         # giao diện
python main.py --list                  # liệt kê hạng mục
python main.py --scan                  # quét các hạng mục mặc định (không xoá)
python main.py --clean user_temp edge --yes
```

Một số mục (File tạm Windows, Windows Update...) cần quyền Administrator — bấm **Chạy với quyền Admin** trong giao diện.

## Tab "Tối ưu hệ thống"

- **hiberfil.sys**: rút gọn (giữ Fast Startup) / tắt hẳn / bật đầy đủ (`powercfg /h`).
- **pagefile.sys**: đặt cố định N GB hoặc để Windows tự quản lý (cần khởi động lại).
- **WSL**: thu gọn file `ext4.vhdx` (apt clean + fstrim + `diskpart compact vdisk`) hoặc chuyển distro sang ổ khác (`wsl --manage --move`).
- **WinSxS**: `DISM /Online /Cleanup-Image /StartComponentCleanup`.

## Tab "RAM & Khởi động"

- **RAM**: % RAM đang dùng (tự cập nhật). *Giải phóng RAM* thu gọn bộ nhớ của mọi tiến trình (`EmptyWorkingSet`, tạm thời); *Xoá bộ nhớ chờ* xoá standby list (cần Admin).
- **Ứng dụng đang chạy**: gộp theo tên, sắp theo RAM; chọn và *Đóng ứng dụng đã chọn* (buộc tắt cả tiến trình con). Tiến trình của Windows / bị bảo vệ không cho đóng.
- **Khởi động cùng Windows**: bật/tắt giống tab Startup apps của Task Manager (ghi `StartupApproved`, không xoá mục gốc). Mục "Mọi người dùng" cần Admin.

## Đóng gói thành .exe

```
pip install --upgrade pyinstaller altgraph
build.bat
```

Kết quả: `dist\Phu_Don_Rac.exe` (một file duy nhất, chạy trên máy không cài Python).

## Thêm hạng mục mới

Thêm một `Category(...)` vào `cleaner/categories.py`. Đường dẫn hỗ trợ biến môi trường (`%LOCALAPPDATA%`) và ký tự đại diện (`*`).

## An toàn

- Không đi theo / xoá symlink, junction.
- Không bao giờ xoá thư mục gốc của hạng mục; từ chối các thư mục hệ thống (`C:\`, `Windows`, hồ sơ người dùng...).
- File tạm chỉ xoá nếu cũ hơn 24 giờ; file đang được sử dụng sẽ bị bỏ qua.
- Chỉ xoá cache trình duyệt — không đụng tới cookie, lịch sử, mật khẩu.