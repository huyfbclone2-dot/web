Folder này là bản Docker riêng của `activity_web_dev`.

Run bản dev độc lập:

```bash
cd e:\decode\activity_web_dev_docker
python server.py --host 127.0.0.1 --port 8081
```

Docker mới:

```bash
cd e:\decode\activity_web_dev_docker
docker compose up --build
```

Hoặc build/run tay:

```bash
cd e:\decode\activity_web_dev_docker
docker build -t activity-web-dev-docker:latest .
docker run --rm -p 8081:8080 -v ${PWD}\\cache:/app/cache -v ${PWD}\\auth.json:/app/auth.json activity-web-dev-docker:latest
```

Điểm tách biệt với bản đang chạy:

- frontend riêng trong `web/`
- server riêng ở `server.py`
- helper riêng: `decode_activitylist.py`, `swf_extract_images.py`
- cache/session/render riêng trong `cache/`
- Docker artifacts mới: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `requirements.txt`, `docker-entrypoint.sh`

Lưu ý Docker:

- container sẽ tự tạo `auth.json` mặc định nếu file này chưa có
- mặc định mở ở cổng `8081` ngoài máy host, map vào `8080` trong container
- cache được mount ra `./cache` để giữ ảnh và session giữa các lần restart
- container chạy root mặc định để tránh lỗi quyền ghi với `./cache` và `./auth.json` khi mount từ host
- nếu source/image thiếu thư mục `web/`, container sẽ dừng ngay với lỗi rõ ràng thay vì trả `404`

`FFDec` là tùy chọn. Nếu không có, web vẫn decode asset và extract bitmap bình thường; chỉ thiếu phần render frame đẹp hơn.

`FFDec` sẽ tự tìm theo thứ tự:

1. biến môi trường `FFDEC_BIN`
2. `activity_web_dev/tools/ffdec_full/ffdec-cli.exe`
3. `activity_web_dev/tools/ffdec_full/ffdec.sh`
4. `e:\decode\tools\ffdec_full\ffdec-cli.exe`
5. `/opt/ffdec/ffdec.sh`

Nếu muốn cấp `FFDec` cho container, bạn có thể mount vào `/opt/ffdec` và set `FFDEC_BIN`, ví dụ:

```bash
docker run --rm -p 8081:8080 ^
  -e FFDEC_BIN=/opt/ffdec/ffdec.sh ^
  -v ${PWD}\\cache:/app/cache ^
  -v ${PWD}\\auth.json:/app/auth.json ^
  -v E:\\decode\\tools\\ffdec_full:/opt/ffdec ^
  activity-web-dev-docker:latest
```
