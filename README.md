Folder này là bản Docker `full` của `activity_web_dev`.

Điểm khác của bản này:

- bundle sẵn `tools/ffdec_full/`
- Docker image có `Java + bash + fonts`
- Linux container sẽ tự chọn `ffdec.sh` thay vì nhầm sang `ffdec-cli.exe`
- cache version đã được bump để tự render lại ảnh frame thay vì giữ cache bitmap-only cũ

Run bản dev độc lập:

```bash
cd e:\decode\activity_web_dev_docker_full
python server.py --host 127.0.0.1 --port 8081
```

Docker full:

```bash
cd e:\decode\activity_web_dev_docker_full
docker compose up --build
```

Hoặc build/run tay:

```bash
cd e:\decode\activity_web_dev_docker_full
docker build -t activity-web-dev-docker-full:latest .
docker run --rm -p 8081:8080 -v ${PWD}\\cache:/app/cache -v ${PWD}\\auth.json:/app/auth.json activity-web-dev-docker-full:latest
```

Điểm tách biệt với bản đang chạy:

- frontend riêng trong `web/`
- server riêng ở `server.py`
- helper riêng: `decode_activitylist.py`, `swf_extract_images.py`
- cache/session/render riêng trong `cache/`
- Docker artifacts mới: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `requirements.txt`, `docker-entrypoint.sh`
- FFDec full bundle nằm trong `tools/ffdec_full/`

Lưu ý Docker:

- container sẽ tự tạo `auth.json` mặc định nếu file này chưa có
- mặc định mở ở cổng `8081` ngoài máy host, map vào `8080` trong container
- cache được mount ra `./cache` để giữ ảnh và session giữa các lần restart
- container chạy root mặc định để tránh lỗi quyền ghi với `./cache` và `./auth.json` khi mount từ host
- nếu source/image thiếu thư mục `web/`, container sẽ dừng ngay với lỗi rõ ràng thay vì trả `404`
- nếu thiếu `tools/ffdec_full/ffdec.sh` hoặc thiếu `java`, container sẽ fail-fast ngay
- ảnh đầy đủ kiểu frame render phụ thuộc FFDec, nên bản full này là bundle nên dùng khi bạn muốn ảnh ra giống local nhất

Kiểm tra nhanh trong container:

```bash
docker exec -it activity-web-dev-docker-full bash
java -version
ls -la /app/tools/ffdec_full
curl http://127.0.0.1:8080/
```
