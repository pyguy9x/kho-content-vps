# Kho Content Manager - Claw Shorts (VPS ready)

## Chạy local test
pip install -r requirements.txt
# copy .env.example -> .env và điền SUPABASE_URL, KEY, OPENROUTER_API_KEY
# copy overlay/*.png và font Montserrat-Bold.ttf vào ./overlay/
uvicorn app:app --reload --port 8000
-> http://localhost:8000

## Flow
1. Thêm link vào Supabase wow_links (done=false) hoặc bấm + Thêm link trên UI
2. Bấm "Tải thumb thật" -> backend dùng yt-dlp tải video gốc về shorts/raw, ffmpeg cắt 4 thumb ở 5s cuối, 30%, 60%, 80%
3. Nhập caption kiểu [đỏ] hoặc bấm Nghĩ hộ (gọi OpenRouter Gemini)
4. Bấm Tạo video -> backend make_caption_png + build_render (overlay + caption) -> ra 1080p + 720p, đánh dấu done=true

## Deploy VPS (Ubuntu)
apt update && apt install ffmpeg python3-pip -y
pip3 install -r requirements.txt
# yt-dlp: pip3 install yt-dlp hoặc apt install yt-dlp
mkdir -p overlay shorts/raw shorts/thumbs
# upload overlay_ninja_final_v2.png + Montserrat-Bold.ttf
# .env

# systemd
cat > /etc/systemd/system/claw-kho.service <<'UNIT'
[Unit]
Description=Kho Content Manager
After=network.target
[Service]
WorkingDirectory=/root/kho-content-vps
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
User=root
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable claw-kho && systemctl start claw-kho

# Docker
docker build -t kho-manager .
docker run -d -p 8000:8000 --env-file .env -v $(pwd)/shorts:/app/shorts -v $(pwd)/overlay:/app/overlay kho-manager
