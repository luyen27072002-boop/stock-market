# Firebase Push Setup

Dùng khi muốn điện thoại đóng app vẫn nhận thông báo.

## Cần có

- App deploy HTTPS
- Firebase Project
- Firebase Web app config
- Firebase Web Push certificate key
- serviceAccountKey.json

## Cách làm nhanh

1. Firebase Console → Create project
2. Project settings → Add Web app → copy firebaseConfig
3. Project settings → Cloud Messaging → Web Push certificates → Generate key pair
4. Project settings → Service accounts → Generate new private key
5. Đổi file tải về thành `serviceAccountKey.json`, đặt cùng `main.py`
6. Mở `config.json`, sửa:
```json
"firebase_enabled": true,
"firebase_vapid_key": "...",
"firebase_web_config": {
  "apiKey": "...",
  "authDomain": "...firebaseapp.com",
  "projectId": "...",
  "storageBucket": "...appspot.com",
  "messagingSenderId": "...",
  "appId": "..."
}
```
7. Mở app bằng HTTPS, vào Cài đặt, bấm "Bật Firebase Push thật".
