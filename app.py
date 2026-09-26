
import os, pathlib, subprocess, random, json, time, re, base64, shutil
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

WORKSPACE = pathlib.Path(__file__).parent
load_dotenv(WORKSPACE / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY")
OVERLAY_PNG = WORKSPACE / "overlay" / "overlay_ninja_final_v2.png"
FONT_PATH = WORKSPACE / "overlay" / "Montserrat-Bold.ttf"
SHORTS_DIR = WORKSPACE / "shorts"
RAW_DIR = SHORTS_DIR / "raw"
THUMBS_DIR = SHORTS_DIR / "thumbs"
FFMPEG = os.getenv("FFMPEG_PATH", "ffmpeg")

if not OVERLAY_PNG.exists():
    # tạo overlay trống nếu chưa có
    OVERLAY_PNG.parent.mkdir(parents=True, exist_ok=True)

font_ok = FONT_PATH.exists()

from supabase import create_client

def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(500, "Thieu SUPABASE_URL / KEY trong .env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ---------- CAPTION LOGIC GIỮ NGUYÊN BẢN FINAL_BRACKET_v3 ----------
FS_BLACK=44
FS_RED=48
MAX_W=800

def _fonts():
    fb = ImageFont.truetype(str(FONT_PATH), FS_BLACK) if font_ok else ImageFont.load_default()
    fr = ImageFont.truetype(str(FONT_PATH), FS_RED) if font_ok else ImageFont.load_default()
    return fb, fr

def parse_caption(text):
    t = (text or "").strip()
    if not t:
        return []
    m = re.search(r'\[(.+?)\]|\{(.+?)\}|\<(.+?)\>|\*\*(.+?)\*\*', t)
    if m:
        red = next((g for g in m.groups() if g), None)
        if red:
            red=red.strip()
            before=t[:m.start()].strip(" ;|:,")
            after=t[m.end():].strip(" ;|:,")
            segs=[]
            if before: segs.append((before,"black"))
            segs.append((red,"red"))
            if after: segs.append((after,"black"))
            return segs
    placeholder="___DBL___"
    norm_tmp=t.replace(";;",placeholder).replace(";", ";;").replace(placeholder,";;")
    while ";;;;" in norm_tmp:
        norm_tmp=norm_tmp.replace(";;;;",";;")
    norm=norm_tmp
    if ";;" not in norm and "|" in norm:
        norm=norm.replace("||",";;").replace("|",";;")
    parts=[p.strip() for p in norm.split(";;")]
    parts=[p for p in parts if p]
    if not parts: return []
    if len(parts)==1: return [(parts[0],"black")]
    segs=[]
    if parts[0]: segs.append((parts[0],"black"))
    if len(parts)>=2 and parts[1]: segs.append((parts[1],"red"))
    if len(parts)>=3:
        rest=" ".join(parts[2:]).strip()
        if rest: segs.append((rest,"black"))
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
        ww=wl(w,c)
        add=ww if not cur else ww+SPACE
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
    return out_path

def get_duration(p):
    try:
        r=subprocess.run([FFMPEG,"-i",str(p)],capture_output=True,text=True,timeout=15)
        m=re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",r.stderr or "")
        if m:
            return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3))
        return 16.0
    except:
        return 16.0

def build_render(raw_video, caption_png, output_path, wow_start, wow_dur):
    overlay_arg = str(OVERLAY_PNG) if OVERLAY_PNG.exists() else None
    # nếu không có overlay thì chỉ overlay caption
    if overlay_arg:
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
        cmd=[FFMPEG,"-y","-i",str(raw_video),"-i",overlay_arg,"-i",str(caption_png),"-filter_complex",fc,"-map","[v]","-map","[a]","-c:v","libx264","-preset","fast","-crf","23","-movflags","+faststart",str(output_path)]
    else:
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
        log(f"FFMPEG fail {r.stderr[-2000:]}")
        raise RuntimeError(r.stderr[-500:])

def make_720p(src,dst):
    subprocess.run([FFMPEG,"-y","-i",str(src),"-vf","scale=720:1280","-c:v","libx264","-preset","fast","-crf","28","-c:a","copy","-movflags","+faststart",str(dst)],capture_output=True,timeout=180)

def gen_thumbs_small(raw_video, row_id):
    duration=get_duration(raw_video)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    starts=[
        max(0,min(duration-5.3,duration-5.3)),
        max(0,min(duration*0.3,duration-5.0)),
        max(0,min(duration*0.6,duration-5.0)),
        max(0,min(duration*0.8,duration-5.0)),
    ]
    labels=[f"Opt1: 5s cuối ({starts[0]:.1f}s)",f"Opt2: Giữa ({starts[1]:.1f}s)",f"Opt3: Gần cuối ({starts[2]:.1f}s)",f"Opt4: 80% ({starts[3]:.1f}s)"]
    thumbs=[]
    for i,(st,lb) in enumerate(zip(starts,labels),1):
        tp=THUMBS_DIR / f"thumb_{row_id}_{i}_{int(time.time())}.jpg"
        subprocess.run([FFMPEG,"-y","-ss",str(st),"-i",str(raw_video),"-vframes","1","-vf","scale=540:960","-q:v","2",str(tp)],capture_output=True,timeout=15)
        thumbs.append({"path":str(tp.name),"file":str(tp),"start":st,"dur":5.0,"label":lb,"url":f"/shorts/thumbs/{tp.name}"})
    return thumbs, duration

def gemini_caption_hint(prompt_text=""):
    if not OPENROUTER_API_KEY:
        return None
    try:
        import urllib.request, json
        url="https://openrouter.ai/api/v1/chat/completions"
        payload={"model":os.getenv("OPENROUTER_MODEL","google/gemini-1.5-flash-001"),
                 "messages":[{"role":"user","content":prompt_text+"\nTrả về JSON: {\"black\":\"..., [red] ...\", \"red\":\"cum do\"} chỉ JSON"}]}
        req=urllib.request.Request(url,data=json.dumps(payload).encode("utf-8"),headers={"Content-Type":"application/json","Authorization":"Bearer "+OPENROUTER_API_KEY},method="POST")
        with urllib.request.urlopen(req,timeout=18) as resp:
            d=json.loads(resp.read().decode("utf-8"))
            text=d.get("choices",[{}])[0].get("message",{}).get("content","")
            m=re.search(r'\{.*?"black".*?"red".*?\}',text,re.DOTALL)
            if m:
                j=json.loads(m.group(0))
                segs=parse_caption(j.get("black",""))
                if segs and any(c=="red" for _,c in segs):
                    return segs
                return [(j.get("black",""),"black"),(j.get("red",""),"red")]
    except Exception as e:
        log(f"Gemini fail {e}")
    return None

# ---------- FASTAPI ----------
app=FastAPI(title="Kho Content Manager")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class LinkIn(BaseModel):
    link: str
    title: Optional[str] = None

class ThumbReq(BaseModel):
    id: Optional[str] = None
    link: Optional[str] = None

class CaptionAIReq(BaseModel):
    title: Optional[str] = ""
    context: Optional[str] = ""

class RenderReq(BaseModel):
    id: str
    link: Optional[str] = None
    start: float
    dur: float = 5.0
    caption_text: str
    raw_path: Optional[str] = None

class LinkUpdate(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None
    notes: Optional[str] = None
    caption: Optional[str] = None
    done: Optional[bool] = None

class ThumbCustomReq(BaseModel):
    id: Optional[str] = None
    raw_path: Optional[str] = None
    link: Optional[str] = None
    start: float

class CaptionPreviewReq(BaseModel):
    caption_text: str
    raw_path: Optional[str] = None
    start: Optional[float] = None
    thumb_path: Optional[str] = None
    thumb_url: Optional[str] = None
    id: Optional[str] = None


@app.get("/api/links")
def list_links(status: str = "all", q: str = ""):
    sb=get_supabase()
    query=sb.table("wow_links").select("*").order("created_at",desc=True).limit(100)
    res=query.execute()
    data=res.data or []
    # filter
    if status=="pending":
        data=[d for d in data if not d.get("done")]
    elif status=="done":
        data=[d for d in data if d.get("done")]
    if q:
        ql=q.lower()
        data=[d for d in data if ql in (d.get("link","")+d.get("title","")+d.get("caption","")).lower()]
    return {"links":data}

@app.post("/api/links")
def add_link(payload: LinkIn):
    sb=get_supabase()
    row={"link":payload.link,"title":payload.title or "", "done":False}
    res=sb.table("wow_links").insert(row).execute()
    return {"ok":True,"row":res.data[0] if res.data else row}

@app.post("/api/thumbs")
def get_thumbs(payload: ThumbReq):
    sb=get_supabase()
    row_id=payload.id or "temp"
    link=payload.link
    if payload.id:
        # lay link tu DB neu chi co id
        try:
            r=sb.table("wow_links").select("*").eq("id",payload.id).single().execute()
            if r.data:
                link=r.data.get("link")
                row_id=r.data.get("id")
        except:
            pass
    if not link:
        raise HTTPException(400,"Thieu link")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    safe="".join(ch for ch in str(row_id) if ch.isalnum())[:24]
    raw_path=RAW_DIR / f"source_{safe}_{int(time.time())}.mp4"
    log(f"Downloading {link} -> {raw_path}")
    # yt-dlp
    cmd=["yt-dlp","--force-overwrites","--no-continue","-o",str(raw_path),link]
    subprocess.run(cmd,timeout=300)
    if not raw_path.exists():
        raise HTTPException(500,"Tai video that bai, check yt-dlp / link")
    thumbs,dur=gen_thumbs_small(raw_path, safe)
    raw_url = f"/shorts/raw/{raw_path.name}"
    # also ensure RAW_DIR is mounted under /shorts/raw via main mount (SHORTS_DIR)
    return {"raw_path":str(raw_path.name),"raw_file":str(raw_path),"raw_url":raw_url,"duration":dur,"thumbs":thumbs}


@app.patch("/api/links/{link_id}")
@app.put("/api/links/{link_id}")
def update_link(link_id: str, payload: LinkUpdate):
    sb=get_supabase()
    update_data={}
    if payload.title is not None:
        update_data["title"]=payload.title
    if payload.note is not None:
        update_data["note"]=payload.note
        # also try notes column fallback
        update_data["notes"]=payload.note
    if payload.notes is not None:
        update_data["notes"]=payload.notes
        update_data["note"]=payload.notes
    if payload.caption is not None:
        update_data["caption"]=payload.caption
    if payload.done is not None:
        update_data["done"]=payload.done
    if not update_data:
        raise HTTPException(400,"Không có gì để update")
    # try update, if column not exist, retry without note/notes
    try:
        res=sb.table("wow_links").update(update_data).eq("id",link_id).execute()
        return {"ok":True,"row":res.data[0] if res.data else update_data}
    except Exception as e:
        # fallback: remove note/notes if error about column
        msg=str(e)
        if "note" in msg.lower() or "notes" in msg.lower():
            update_data.pop("note",None)
            update_data.pop("notes",None)
            if update_data:
                try:
                    res=sb.table("wow_links").update(update_data).eq("id",link_id).execute()
                    return {"ok":True,"row":res.data[0] if res.data else update_data, "warning":"note column missing, saved rest"}
                except Exception as e2:
                    raise HTTPException(500,f"Update fail: {e2}")
        raise HTTPException(500,f"Update fail: {e}")

@app.post("/api/links/{link_id}/toggle")
def toggle_done(link_id: str):
    sb=get_supabase()
    try:
        r=sb.table("wow_links").select("*").eq("id",link_id).single().execute()
        if not r.data:
            raise HTTPException(404,"Not found")
        new_done = not r.data.get("done", False)
        res=sb.table("wow_links").update({"done":new_done}).eq("id",link_id).execute()
        return {"ok":True,"done":new_done,"row":res.data[0] if res.data else {"done":new_done}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500,str(e))

@app.post("/api/thumb_custom")
def thumb_custom(payload: ThumbCustomReq):
    raw_file=None
    if payload.raw_path:
        raw_file=RAW_DIR / pathlib.Path(payload.raw_path).name
        if not raw_file.exists():
            raw_file=pathlib.Path(payload.raw_path)
    elif payload.id:
        safe="".join(ch for ch in str(payload.id) if ch.isalnum())[:24]
        candidates=list(RAW_DIR.glob(f"source_{safe}_*.mp4"))
        if candidates:
            raw_file=sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[0]
    if not raw_file or not raw_file.exists():
        raise HTTPException(400,f"Không tìm thấy raw video: {payload.raw_path}")
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    stamp=int(time.time())
    safe_id="".join(ch for ch in str(payload.id or 'custom') if ch.isalnum())[:12]
    tp=THUMBS_DIR / f"thumb_{safe_id}_custom_{stamp}.jpg"
    try:
        subprocess.run([FFMPEG,"-y","-ss",str(payload.start),"-i",str(raw_file),"-vframes","1","-vf","scale=540:960","-q:v","2",str(tp)],check=True,timeout=15)
    except Exception as e:
        raise HTTPException(500,f"Gen thumb fail: {e}")
    url=f"/shorts/thumbs/{tp.name}"
    return {"ok":True,"thumb":{"path":tp.name,"file":str(tp),"start":payload.start,"dur":5.0,"label":f"Custom: {payload.start:.1f}s","url":url},"raw_url":f"/shorts/raw/{raw_file.name}"}



@app.post("/api/caption/preview")
def caption_preview(payload: CaptionPreviewReq):
    """Preview overlay + caption trước khi render - trả về ảnh jpg"""
    segs=parse_caption(payload.caption_text)
    if not segs:
        raise HTTPException(400,"Caption trống")
    # tạo caption png tạm
    stamp=int(time.time()*1000)%1000000
    cap_png=SHORTS_DIR / f"previews/caption_preview_{stamp}.png"
    preview_out=SHORTS_DIR / f"previews/preview_{stamp}.jpg"
    try:
        make_caption_png(segs, cap_png)
    except Exception as e:
        raise HTTPException(500,f"Tạo caption PNG fail: {e}")
    
    # tìm base image
    base_img=None
    # ưu tiên lấy frame từ raw video tại start
    raw_file=None
    if payload.raw_path:
        raw_file=RAW_DIR / pathlib.Path(payload.raw_path).name
        if not raw_file.exists():
            raw_file=pathlib.Path(payload.raw_path)
            if not raw_file.exists():
                # thử tìm trong RAW_DIR theo id
                if payload.id:
                    safe="".join(ch for ch in str(payload.id) if ch.isalnum())[:24]
                    candidates=list(RAW_DIR.glob(f"source_{safe}_*.mp4"))
                    if candidates:
                        raw_file=sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[0]
    elif payload.id:
        safe="".join(ch for ch in str(payload.id) if ch.isalnum())[:24]
        candidates=list(RAW_DIR.glob(f"source_{safe}_*.mp4"))
        if candidates:
            raw_file=sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[0]
    
    W,H=1080,1920
    # nếu có raw_file và start, cắt frame bằng ffmpeg
    temp_frame=None
    if raw_file and raw_file.exists() and payload.start is not None:
        temp_frame=SHORTS_DIR / f"previews/frame_{stamp}.jpg"
        try:
            subprocess.run([FFMPEG,"-y","-ss",str(payload.start),"-i",str(raw_file),"-vframes","1","-vf","scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920","-q:v","2",str(temp_frame)],check=True,timeout=15)
            base_img=Image.open(temp_frame).convert("RGBA")
        except:
            base_img=None
    
    # nếu không có base từ video, thử thumb
    if base_img is None and payload.thumb_path:
        tp=THUMBS_DIR / pathlib.Path(payload.thumb_path).name
        if not tp.exists():
            tp=pathlib.Path(payload.thumb_path)
        if tp.exists():
            try:
                base_img=Image.open(tp).convert("RGBA")
            except:
                base_img=None
    
    if base_img is None and payload.thumb_url:
        # thumb_url dạng /shorts/thumbs/xxx.jpg
        name=pathlib.Path(payload.thumb_url).name
        tp=THUMBS_DIR / name
        if tp.exists():
            try:
                base_img=Image.open(tp).convert("RGBA")
            except:
                base_img=None
    
    if base_img is None:
        # fallback black bg
        base_img=Image.new("RGBA",(W,H),(10,10,12,255))
    else:
        # resize to 1080x1920
        base_img=base_img.resize((W,H), Image.LANCZOS)
    
    # overlay ninja nếu có
    if OVERLAY_PNG.exists():
        try:
            overlay=Image.open(OVERLAY_PNG).convert("RGBA")
            if overlay.size!=(W,H):
                overlay=overlay.resize((W,H), Image.LANCZOS)
            base_img=Image.alpha_composite(base_img, overlay)
        except Exception as e:
            print(f"overlay composite fail {e}")
    
    # caption png
    try:
        cap_img=Image.open(cap_png).convert("RGBA")
        if cap_img.size!=(W,H):
            cap_img=cap_img.resize((W,H), Image.LANCZOS)
        base_img=Image.alpha_composite(base_img, cap_img)
    except Exception as e:
        print(f"caption composite fail {e}")
    
    # save jpg
    base_img.convert("RGB").save(preview_out,"JPEG",quality=85)
    # cleanup temp
    try:
        if temp_frame and temp_frame.exists():
            temp_frame.unlink()
        if cap_png.exists():
            cap_png.unlink()
    except:
        pass
    
    url=f"/shorts/previews/{preview_out.name}"
    return {"ok":True,"preview_url":url,"caption_segments":segs}

@app.post("/api/caption/ai")

def ai_caption(payload: CaptionAIReq):
    prompt = f"Video title: {payload.title}\nContext: {payload.context}\nTao caption kieu: chu den [chu do] chu den, chu do la cum gay soc, ngan gon, viral TikTok, tieng Viet, khong qua 15 tu."
    segs=gemini_caption_hint(prompt)
    if not segs:
        fallback=[
            "Tưởng chỉ là tiết mục bình thường [ai ngờ lửa bùng ngay trước mắt] cả sân khấu nín thở",
            "Đang diễn rất bình thường [tới đoạn này ai cũng đứng hình] đúng là không đoán trước được",
            "Màn trình diễn nhìn thì vui [nhưng khúc này hơi thót tim] xem lại vẫn nổi da gà",
            "Cứ tưởng thế nào, chứ [đếm tiền kiểu này] có ngày cháy túi",
            "Nhìn thì đơn giản vậy thôi [đến lúc làm mới biết khó] ai cũng phải dè chừng",
        ]
        txt=random.choice(fallback)
        segs=parse_caption(txt)
    # convert to serializable
    return {"segments":segs,"text":" ".join(t for t,_ in segs)}

@app.post("/api/render")
def render_video(payload: RenderReq):
    segs=parse_caption(payload.caption_text)
    if not segs or not any(c=="red" for _,c in segs):
        raise HTTPException(400,"Caption thieu [do] - VD: chu den [chu do] chu den")
    # tim raw_path
    raw_file=None
    if payload.raw_path:
        raw_file=RAW_DIR / pathlib.Path(payload.raw_path).name
        if not raw_file.exists():
            raw_file=pathlib.Path(payload.raw_path)
    else:
        # tim file moi nhat co id
        safe="".join(ch for ch in str(payload.id) if ch.isalnum())[:24]
        candidates=list(RAW_DIR.glob(f"source_{safe}_*.mp4"))
        if candidates:
            raw_file=sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[0]
    if not raw_file or not raw_file.exists():
        raise HTTPException(400,f"Khong tim thay raw video, hay bam Tai thumb truoc. raw_path={payload.raw_path}")
    stamp=random.randint(1000,9999)
    cap_png=SHORTS_DIR / f"caption_render_{stamp}.png"
    out_1080=SHORTS_DIR / f"short-v14-1080-{date.today()}-{stamp}.mp4"
    out_720=SHORTS_DIR / f"short-v14-720-{date.today()}-{stamp}.mp4"
    make_caption_png(segs, cap_png)
    build_render(raw_file, cap_png, out_1080, payload.start, payload.dur)
    make_720p(out_1080, out_720)
    # mark done
    try:
        sb=get_supabase()
        cap_text=" ".join(t for t,_ in segs)
        sb.table("wow_links").update({"done":True,"caption":cap_text}).eq("id",payload.id).execute()
    except Exception as e:
        log(f"Mark done fail {e}")
    return {
        "ok":True,
        "caption_segments":segs,
        "video_1080":f"/shorts/{out_1080.name}",
        "video_720":f"/shorts/{out_720.name}",
        "video_1080_name":out_1080.name,
        "video_720_name":out_720.name,
    }

# serve shorts files
SHORTS_DIR.mkdir(parents=True, exist_ok=True)
(SHORTS_DIR / "previews").mkdir(parents=True, exist_ok=True)
app.mount("/shorts", StaticFiles(directory=str(SHORTS_DIR)), name="shorts")
# serve overlay for frontend preview
if OVERLAY_PNG.parent.exists():
    app.mount("/overlay", StaticFiles(directory=str(OVERLAY_PNG.parent)), name="overlay")

# serve frontend - tìm ở nhiều chỗ cho chắc
def find_frontend():
    candidates = [
        WORKSPACE / "frontend",
        pathlib.Path(__file__).parent / "frontend",
        WORKSPACE / "kho-content-vps" / "frontend",
        pathlib.Path.home() / ".openclaw" / "workspace" / "kho-content-vps" / "frontend",
        pathlib.Path.home() / ".openclaw" / "workspace" / "frontend",
    ]
    for p in candidates:
        if p.exists() and (p / "index.html").exists():
            return p
    return None

frontend_dir = find_frontend()
if frontend_dir:
    print(f"Found frontend at {frontend_dir}")
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
else:
    print("Frontend not found, checked:", [str(p) for p in [
        WORKSPACE / "frontend",
        pathlib.Path(__file__).parent / "frontend",
    ]])
    @app.get("/")
    def root():
        return {"message":"Kho Content Manager API running. Frontend chua co. Hay copy folder frontend vao. Checked: workspace/frontend and kho-content-vps/frontend. Hay chay uvicorn tu trong thu muc kho-content-vps."}

