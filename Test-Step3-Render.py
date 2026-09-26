# test_step3_render.py - Test render 1 video với caption [đỏ]
import os, pathlib, subprocess, random, time, re, sys
from datetime import date
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# tìm workspace
for p in [pathlib.Path.cwd(), pathlib.Path.home() / ".openclaw" / "workspace", pathlib.Path(__file__).parent]:
    if (p / ".env").exists():
        WORKSPACE = p
        break
else:
    WORKSPACE = pathlib.Path.cwd()

load_dotenv(WORKSPACE / ".env")
SHORTS_DIR = WORKSPACE / "shorts"
RAW_DIR = SHORTS_DIR / "raw"
SHORTS_DIR.mkdir(exist_ok=True); RAW_DIR.mkdir(exist_ok=True)

# ffmpeg detection
candidates = [str(WORKSPACE / "ffmpeg.exe"), pathlib.Path.home() / ".openclaw" / "workspace" / "ffmpeg.exe", "ffmpeg"]
FFMPEG = "ffmpeg"
for c in candidates:
    cp = pathlib.Path(c)
    if cp.exists() or c=="ffmpeg":
        if cp.exists() or c=="ffmpeg":
            FFMPEG = str(c) if cp.exists() else "ffmpeg"
            if cp.exists():
                break

OVERLAY_PNG = WORKSPACE / "overlay" / "overlay_ninja_final_v2.png"
FONT_PATH = WORKSPACE / "overlay" / "Montserrat-Bold.ttf"
font_ok = FONT_PATH.exists()
overlay_ok = OVERLAY_PNG.exists()
print(f"WORKSPACE={WORKSPACE}")
print(f"FFMPEG={FFMPEG} overlay={overlay_ok} font={font_ok}")

# --- copy logic caption từ bản FINAL ---
FS_BLACK=44; FS_RED=48; MAX_W=800
def _fonts():
    fb = ImageFont.truetype(str(FONT_PATH), FS_BLACK) if font_ok else ImageFont.load_default()
    fr = ImageFont.truetype(str(FONT_PATH), FS_RED) if font_ok else ImageFont.load_default()
    return fb, fr

def parse_caption(text):
    import re
    t=(text or "").strip()
    if not t: return []
    m=re.search(r'\[(.+?)\]|\{(.+?)\}|\<(.+?)\>|\*\*(.+?)\*\*', t)
    if m:
        red=next((g for g in m.groups() if g),None)
        if red:
            red=red.strip()
            before=t[:m.start()].strip(" ;|:,")
            after=t[m.end():].strip(" ;|:,")
            segs=[]
            if before: segs.append((before,"black"))
            segs.append((red,"red"))
            if after: segs.append((after,"black"))
            return segs
    parts=[p.strip() for p in t.split(";;")]
    parts=[p for p in parts if p]
    if len(parts)<=1: return [(t,"black")]
    segs=[]
    if parts[0]: segs.append((parts[0],"black"))
    if len(parts)>=2: segs.append((parts[1],"red"))
    if len(parts)>=3: segs.append((" ".join(parts[2:]),"black"))
    return segs

def _words_from_segments(segments):
    return [(word,color) for text,color in segments for word in text.split()]

def wrap_lines(words):
    fb,fr=_fonts()
    dummy=ImageDraw.Draw(Image.new("RGBA",(10,10)))
    def wl(t,c): return dummy.textlength(t, font=(fr if c=="red" else fb))
    SPACE=wl("x x","black")-wl("xx","black")
    red_idx=[i for i,(w,c) in enumerate(words) if c=="red"]
    red_start,red_end=(min(red_idx),max(red_idx)) if red_idx else (None,None)
    red_total=0
    if red_start is not None:
        red_total=sum(wl(w,"red") for w,c in words[red_start:red_end+1])+SPACE*(red_end-red_start)
    lines,cur,cur_w=[],[],0
    for i,(w,c) in enumerate(words):
        ww=wl(w,c); add=ww if not cur else ww+SPACE
        if red_start is not None and i==red_start and cur and cur_w+add+red_total>MAX_W:
            lines.append(cur);cur,cur_w=[],0;add=ww
        elif cur and cur_w+add>MAX_W:
            lines.append(cur);cur,cur_w=[],0;add=ww
        cur.append((w,c));cur_w+=add
    if cur: lines.append(cur)
    while len(lines)>5:
        last=lines.pop();lines[-1]=lines[-1]+[(" ","black")]+last
    return lines

Y_MAP={1:[205],2:[285,205],3:[365,285,205],4:[445,365,285,205],5:[505,425,345,265,185]}
CAPTION_X_OFFSET=140

def make_caption_png(segments, out_path, lift=35):
    W,H=1080,1920
    img=Image.new("RGBA",(W,H),(0,0,0,0))
    draw=ImageDraw.Draw(img)
    fb,fr=_fonts()
    def wl(t,c): return draw.textlength(t, font=(fr if c=="red" else fb))
    SPACE=wl("x x","black")-wl("xx","black")
    lines=wrap_lines(_words_from_segments(segments))
    print(f"Caption wrap {len(lines)} dòng")
    ys=Y_MAP.get(len(lines),Y_MAP[5])
    for li,line_words in enumerate(lines):
        y=H-ys[li]-lift
        widths=[wl(w,c) for w,c in line_words]
        total=sum(widths)+SPACE*(len(widths)-1)
        x=(W-total)//2+CAPTION_X_OFFSET
        x=max(20,min(x,W-total-20))
        for (w,c),ww in zip(line_words,widths):
            f=fr if c=="red" else fb
            color=(255,0,40,255) if c=="red" else (10,10,10,255)
            draw.text((x,y),w,font=f,fill=color,stroke_width=6,stroke_fill=(255,255,255,255))
            x+=ww+SPACE
    img.save(out_path,"PNG")
    print(f"Saved caption PNG {out_path}")
    return out_path

def get_duration(p):
    r=subprocess.run([FFMPEG,"-i",str(p)],capture_output=True,text=True,timeout=15)
    m=re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",r.stderr or "")
    if m: return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
    return 16.0

def build_render(raw_video, caption_png, output_path, wow_start, wow_dur):
    if OVERLAY_PNG.exists():
        fc=";".join([
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[base]",
            f"[base][1:v]overlay=0:0[ov1]",
            f"[ov1][2:v]overlay=0:0[ov2]",
            f"[ov2]split=2[wsrc][ffull]",
            f"[wsrc]trim=start={wow_start}:duration={wow_dur},setpts=PTS-STARTPTS[wsec]",
            f"anullsrc=r=44100:cl=stereo:d={wow_dur}[awow]",
            f"[0:a]aformat=channel_layouts=stereo,aresample=44100[afull]",
            f"[wsec][awow][ffull][afull]concat=n=2:v=1:a=1[v][a]",
        ])
        cmd=[FFMPEG,"-y","-i",str(raw_video),"-i",str(OVERLAY_PNG),"-i",str(caption_png),"-filter_complex",fc,"-map","[v]","-map","[a]","-c:v","libx264","-preset","fast","-crf","23","-movflags","+faststart",str(output_path)]
    else:
        print("⚠️ Không có overlay PNG, render chỉ có caption")
        fc=";".join([
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[base]",
            f"[base][1:v]overlay=0:0[ov]",
            f"[ov]split=2[wsrc][ffull]",
            f"[wsrc]trim=start={wow_start}:duration={wow_dur},setpts=PTS-STARTPTS[wsec]",
            f"anullsrc=r=44100:cl=stereo:d={wow_dur}[awow]",
            f"[0:a]aformat=channel_layouts=stereo,aresample=44100[afull]",
            f"[wsec][awow][ffull][afull]concat=n=2:v=1:a=1[v][a]",
        ])
        cmd=[FFMPEG,"-y","-i",str(raw_video),"-i",str(caption_png),"-filter_complex",fc,"-map","[v]","-map","[a]","-c:v","libx264","-preset","fast","-crf","23","-movflags","+faststart",str(output_path)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
    if r.returncode!=0:
        print(r.stderr[-2000:])
        raise RuntimeError("ffmpeg fail")

def make_720p(src,dst):
    subprocess.run([FFMPEG,"-y","-i",str(src),"-vf","scale=720:1280","-c:v","libx264","-preset","fast","-crf","28","-c:a","copy","-movflags","+faststart",str(dst)],capture_output=True,timeout=180)

# ---- MAIN ----
link = input("Dán link test (enter để dùng video raw mới nhất trong shorts/raw): ").strip()
if link:
    safe=f"test3_{int(time.time())}"
    raw_path=RAW_DIR / f"source_{safe}.mp4"
    print(f"Tải {link}")
    subprocess.run(["yt-dlp","--force-overwrites","--no-continue","-o",str(raw_path),link],check=False,timeout=300)
else:
    # lấy file raw mới nhất
    raws=sorted(RAW_DIR.glob("source_*.mp4"),key=lambda p:p.stat().st_mtime,reverse=True)
    if not raws:
        print("Không có raw nào, hãy dán link")
        sys.exit(1)
    raw_path=raws[0]
    print(f"Dùng raw có sẵn: {raw_path}")

dur=get_duration(raw_path)
print(f"Duration {dur:.1f}s")
wow_start=max(0,min(dur-5.3,dur-5.3))
wow_dur=5.0

cap_text=input("Nhập caption kiểu [đỏ] VD: Cứ tưởng thế nào, chứ [đếm tiền kiểu này] có ngày cháy túi\n> ").strip()
if not cap_text:
    cap_text="Cứ tưởng thế nào, chứ [đếm tiền kiểu này] có ngày cháy túi"

segs=parse_caption(cap_text)
print(f"Parsed segs: {segs}")
if not any(c=="red" for _,c in segs):
    print("⚠️ Caption không có [đỏ], sẽ không nổi bật")
    
stamp=random.randint(1000,9999)
cap_png=SHORTS_DIR / f"caption_test3_{stamp}.png"
out_1080=SHORTS_DIR / f"short-test3-1080-{date.today()}-{stamp}.mp4"
out_720=SHORTS_DIR / f"short-test3-720-{date.today()}-{stamp}.mp4"

make_caption_png(segs, cap_png)
print(f"Render {wow_start:.1f}s dur {wow_dur}s")
build_render(raw_path, cap_png, out_1080, wow_start, wow_dur)
make_720p(out_1080, out_720)

print(f"\n✅ DONE!")
print(f"1080p: {out_1080}")
print(f"720p: {out_720}")
print(f"Mở file 720p xem thử caption [đỏ] có bị cắt chữ không")
