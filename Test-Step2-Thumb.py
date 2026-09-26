# test_step2_thumb.py - Test lấy thumb thật không cần chạy server
import os, pathlib, subprocess, time, re, sys
from dotenv import load_dotenv

# tìm workspace
for p in [pathlib.Path.cwd(), pathlib.Path.home() / ".openclaw" / "workspace", pathlib.Path(__file__).parent]:
    if (p / ".env").exists():
        WORKSPACE = p
        break
else:
    WORKSPACE = pathlib.Path(__file__).parent

load_dotenv(WORKSPACE / ".env")
SHORTS_DIR = WORKSPACE / "shorts"
RAW_DIR = SHORTS_DIR / "raw"
THUMBS_DIR = SHORTS_DIR / "thumbs"
SHORTS_DIR.mkdir(exist_ok=True); RAW_DIR.mkdir(exist_ok=True); THUMBS_DIR.mkdir(exist_ok=True)

# ffmpeg detection
candidates = [str(WORKSPACE / "ffmpeg.exe"), "ffmpeg"]
FFMPEG = next((c for c in candidates if pathlib.Path(c).exists() or c=="ffmpeg"), "ffmpeg")
print(f"WORKSPACE={WORKSPACE}")
print(f"FFMPEG={FFMPEG} exists={pathlib.Path(FFMPEG).exists() if FFMPEG!='ffmpeg' else 'PATH'}")

def get_duration(p):
    r=subprocess.run([FFMPEG,"-i",str(p)],capture_output=True,text=True,timeout=15)
    m=re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",r.stderr or "")
    if m: return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
    return 16.0

# lấy 1 link test - hỏi user hoặc lấy từ supabase pending
link = input("Dán link YouTube Shorts / TikTok để test thumb (enter để lấy từ Supabase pending): ").strip()
if not link:
    try:
        from supabase import create_client
        sb=create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        res=sb.table("wow_links").select("*").eq("done",False).order("created_at",desc=True).limit(1).execute()
        if res.data:
            link=res.data[0]['link']
            print(f"Lấy từ Supabase: {link}")
        else:
            print("Không có link pending nào trong Supabase")
            sys.exit(0)
    except Exception as e:
        print(f"Supabase lỗi: {e}")
        sys.exit(1)

safe = f"test_{int(time.time())}"
raw_path = RAW_DIR / f"source_{safe}.mp4"
print(f"Đang tải {link} -> {raw_path}")
subprocess.run(["yt-dlp","--force-overwrites","--no-continue","-o",str(raw_path),link],check=False,timeout=300)
if not raw_path.exists():
    print("❌ Tải thất bại - check yt-dlp đã cài chưa: pip install yt-dlp")
    sys.exit(1)

dur=get_duration(raw_path)
print(f"Duration: {dur:.1f}s")

starts=[max(0,min(dur-5.3,dur-5.3)), max(0,min(dur*0.3,dur-5.0)), max(0,min(dur*0.6,dur-5.0)), max(0,min(dur*0.8,dur-5.0))]
labels=["Opt1 5s cuối","Opt2 30%","Opt3 60%","Opt4 80%"]
for i,(st,lb) in enumerate(zip(starts,labels),1):
    tp=THUMBS_DIR / f"thumb_{safe}_{i}.jpg"
    subprocess.run([FFMPEG,"-y","-ss",str(st),"-i",str(raw_path),"-vframes","1","-vf","scale=540:960","-q:v","2",str(tp)],capture_output=True,timeout=15)
    print(f"✅ {lb} at {st:.1f}s -> {tp} exists={tp.exists()}")

print(f"\nXong! Check folder: {THUMBS_DIR}")
print("Nếu 4 file jpg hiện ra là bước 2 OK, qua bước 3 render video.")
