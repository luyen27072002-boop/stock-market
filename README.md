# VNDIRECT Pure Technical Alert App

Bản này đúng theo yêu cầu:

- Mua bán thuần kỹ thuật.
- Không phân tích cơ bản doanh nghiệp.
- Không dùng báo cáo tài chính, định giá, lợi nhuận, P/E.
- Logic chỉ dựa trên nến, volume, vùng đỉnh/đáy gần nhất.

## Logic hoạt động

### Điểm mua
Scan tất cả mã trong danh sách `scan_symbols`.

Báo điểm mua khi có:
- Breakout đỉnh 20 phiên + volume tăng
- Thoát nền chặt + volume
- Nến xanh mạnh + volume
- Rút chân ở hỗ trợ + volume

### Điểm bán/cắt lỗ
Chỉ báo cho các mã mày đã nhập vào mục "Đang cầm".

Báo bán khi có:
- Thủng đáy 20 phiên + volume
- Thủng đáy 10 phiên + volume
- Nến đỏ phân phối + volume
- Breakout thất bại
- Rút râu trên kèm volume

## Chạy local

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Mở:
```text
http://127.0.0.1:8000
```

## Chạy điện thoại cùng Wi-Fi

Tìm IPv4 máy tính bằng:
```bash
ipconfig
```

Điện thoại mở:
```text
http://IP_máy_tính:8000
```

## Push thật khi app đóng

Có sẵn Firebase Cloud Messaging nhưng mặc định tắt. Muốn bật:
- Deploy HTTPS
- Tạo Firebase project
- Điền config vào `config.json`
- Đặt `firebase_enabled: true`
- Tải serviceAccountKey.json đặt cùng main.py
