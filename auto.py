from flask import Flask, request, render_template_string, redirect, url_for, send_from_directory
from threading import Thread
import requests, time, os
from pathlib import Path
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)
app.secret_key = "change_this_secret_in_prod"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

TOKENS_PATH = os.path.join(UPLOAD_FOLDER, "tokens.txt")
TEXTS_PATH = os.path.join(UPLOAD_FOLDER, "text.txt")
PHOTO_LIST_PATH = os.path.join(UPLOAD_FOLDER, "photo.txt")
VIDEO_LIST_PATH = os.path.join(UPLOAD_FOLDER, "video.txt")
CAPTION_PATH = os.path.join(UPLOAD_FOLDER, "caption.txt")
TAGS_PATH = os.path.join(UPLOAD_FOLDER, "tags.txt")  # unlimited tags/mentions

valid_tokens = []
token_index = 0
is_running = False
posting_thread = None
current_status = "Stopped"
recent_logs = []

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    print(entry)
    global recent_logs
    recent_logs = ([entry] + recent_logs)[:500]  # bigger log buffer

def save_text_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        if content: f.write(content.strip() + ("\n" if not content.endswith("\n") else ""))

def append_list_file(path, items):
    existing = []
    if os.path.exists(path):
        existing = [l.strip() for l in open(path,"r",encoding="utf-8") if l.strip()]
    new = existing + items
    with open(path,"w",encoding="utf-8") as f:
        for i in new: f.write(i+"\n")

def load_lines(path):
    if not os.path.exists(path): return []
    with open(path,"r",encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]

def validate_tokens_file(path):
    tokens = load_lines(path)
    good = []
    log(f"Validating {len(tokens)} tokens...")
    for i,t in enumerate(tokens,start=1):
        try:
            r = requests.get(f"https://graph.facebook.com/me?access_token={t}", timeout=10).json()
            if 'id' in r: good.append(t); log(f"[{i}] VALID: {r.get('name')}")
            else: log(f"[{i}] INVALID: {r.get('error',{}).get('message','Unknown')}")
        except Exception as e: log(f"[{i}] INVALID: {e}")
    return good

def next_token():
    global token_index
    token = valid_tokens[token_index % len(valid_tokens)]
    token_index +=1
    return token

def get_tags():
    if os.path.exists(TAGS_PATH):
        lines = [l.strip() for l in open(TAGS_PATH,"r",encoding="utf-8") if l.strip()]
        return ",".join(lines)
    return ""

def image_to_ascii(path,width=80):
    chars = "@%#*+=-:. "
    try:
        img = Image.open(path).convert('L')
        wpercent = (width/float(img.size[0]))
        hsize = int((float(img.size[1])*float(wpercent)))
        img = img.resize((width, hsize))
        pixels = img.getdata()
        ascii_str = "".join([chars[pixel//25] for pixel in pixels])
        ascii_lines = [ascii_str[i:i+width] for i in range(0,len(ascii_str),width)]
        return "\n".join(ascii_lines)
    except Exception as e:
        log(f"ASCII conversion failed: {e}")
        return "[Image ASCII Conversion Failed]"

def post_text_fb(token,message):
    tags = get_tags()  # unlimited mentions/tags
    url='https://graph.facebook.com/me/feed'
    payload={'message':message,'privacy':'{"value":"EVERYONE"}','access_token':token}
    if tags: payload['tags']=tags
    return requests.post(url,data=payload,timeout=30)

def upload_video_fb(token,file_path,caption):
    tags = get_tags()
    url='https://graph.facebook.com/me/videos'
    with open(file_path,'rb') as fd:
        files={'file':fd}
        payload={'access_token':token,'description':caption}
        if tags: payload['tags']=tags
        return requests.post(url,data=payload,files=files,timeout=180)

def posting_worker(post_type,delay_seconds):
    global is_running,current_status
    log(f"Worker started: type={post_type} delay={delay_seconds}s")
    current_status="Running"
    try:
        if post_type=="text":
            posts=load_lines(TEXTS_PATH)
            if not posts: log("No text posts found."); return
            while is_running:
                for text in posts:
                    if not is_running: break
                    token=next_token()
                    try:
                        res=post_text_fb(token,text)
                        jr=res.json()
                        if 'id' in jr: log(f"TEXT POSTED: {text[:60]}... id={jr.get('id')}")
                        else: log(f"TEXT ERROR: {jr}")
                    except Exception as e: log(f"EXC TEXT: {e}")
                    time.sleep(delay_seconds)
        elif post_type=="photo":
            media_entries=load_lines(PHOTO_LIST_PATH)
            captions=load_lines(CAPTION_PATH)
            pairs=[]
            for i,name in enumerate(media_entries):
                full=os.path.join(UPLOAD_FOLDER,name)
                if os.path.exists(full):
                    caption=captions[i] if i<len(captions) else ""
                    ascii_text = image_to_ascii(full)
                    pairs.append({'text':ascii_text,'caption':caption})
                else: log(f"Missing media file: {name}")
            if not pairs: log("No valid media found. Stopping worker."); return
            while is_running:
                for item in pairs:
                    if not is_running: break
                    token=next_token()
                    msg=f"{item['caption']}\n\n{item['text']}"
                    try:
                        res=post_text_fb(token,msg)
                        jr=res.json()
                        if 'id' in jr: log(f"PHOTO AS TEXT POSTED id={jr.get('id')}")
                        else: log(f"PHOTO AS TEXT ERROR: {jr}")
                    except Exception as e: log(f"EXC PHOTO AS TEXT: {e}")
                    time.sleep(delay_seconds)
        elif post_type=="video":
            media_entries=load_lines(VIDEO_LIST_PATH)
            captions=load_lines(CAPTION_PATH)
            pairs=[]
            for i,name in enumerate(media_entries):
                full=os.path.join(UPLOAD_FOLDER,name)
                if os.path.exists(full):
                    caption=captions[i] if i<len(captions) else ""
                    pairs.append({'path':full,'caption':caption})
                else: log(f"Missing video file: {name}")
            if not pairs: log("No valid videos found. Stopping worker."); return
            while is_running:
                for item in pairs:
                    if not is_running: break
                    token=next_token()
                    try:
                        r=upload_video_fb(token,item['path'],item['caption'])
                        jr=r.json()
                        if 'id' in jr: log(f"VIDEO POSTED: {Path(item['path']).name} id={jr.get('id')}")
                        else: log(f"VIDEO ERROR: {jr}")
                    except Exception as e: log(f"EXC VIDEO: {e}")
                    time.sleep(delay_seconds)
    finally:
        is_running=False
        current_status="Stopped"
        log("Worker stopped.")

# Premium dashboard with tabs and unlimited mentions/tags
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multi Poster ASCII Premium Unlimited</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body{background:#121212;color:#0f0;font-family:monospace;}
textarea,input,select,button{background:#1a1a1a;color:#0f0;border:1px solid #0f0;}
pre{background:#111;color:#0f0;padding:10px;overflow:auto;max-height:400px;}
a{color:#0f0;}
.card{background:#1b1b1b;border:1px solid #0f0;}
.btn{color:#0f0;border-color:#0f0;}
.tab-content{margin-top:15px;}
</style>
</head>
<body>
<div class="container my-3">
<h2>Multi Poster ASCII — Premium Unlimited</h2>
<p>Status: <strong>{{status}}</strong></p>

<ul class="nav nav-tabs" id="mainTab" role="tablist">
  <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#tokens">Tokens</a></li>
  <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#text">Text Posts</a></li>
  <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#media">Media</a></li>
  <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#captions">Captions</a></li>
  <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tags">Tags/Mentions</a></li>
  <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#logs">Logs</a></li>
</ul>

<div class="tab-content">
  <div class="tab-pane fade show active" id="tokens">
    <div class="card p-2 my-2">
      <form method="POST" action="/upload_tokens" enctype="multipart/form-data">
      <textarea name="tokens" rows="4" class="form-control mb-2" placeholder="One token per line"></textarea>
      <input type="file" name="tokens_file" class="form-control mb-2">
      <button type="submit" class="btn btn-outline-success w-100">Save Tokens</button>
      </form>
    </div>
  </div>

  <div class="tab-pane fade" id="text">
    <div class="card p-2 my-2">
      <form method="POST" action="/upload_text" enctype="multipart/form-data">
      <textarea name="texts" rows="4" class="form-control mb-2" placeholder="One text per line"></textarea>
      <input type="file" name="text_file" class="form-control mb-2">
      <button type="submit" class="btn btn-outline-success w-100">Save Texts</button>
      </form>
    </div>
  </div>

  <div class="tab-pane fade" id="media">
    <div class="card p-2 my-2">
      <form method="POST" action="/upload_media" enctype="multipart/form-data">
      <input type="file" name="media_files" multiple class="form-control mb-2">
      <button type="submit" class="btn btn-outline-success w-100">Upload Media</button>
      </form>
      <h6>Uploaded Files:</h6>
      <ul>{% for f in files %}<li><a href="/uploads/{{f}}" target="_blank">{{f}}</a></li>{% endfor %}</ul>
    </div>
  </div>

  <div class="tab-pane fade" id="captions">
    <div class="card p-2 my-2">
      <form method="POST" action="/upload_captions" enctype="multipart/form-data">
      <textarea name="captions" rows="4" class="form-control mb-2"></textarea>
      <input type="file" name="caption_file" class="form-control mb-2">
      <button type="submit" class="btn btn-outline-success w-100">Save Captions</button>
      </form>
    </div>
  </div>

  <div class="tab-pane fade" id="tags">
    <div class="card p-2 my-2">
      <form method="POST" action="/upload_tags">
      <textarea name="tags" rows="4" class="form-control mb-2" placeholder="Comma-separated IDs for unlimited mentions"></textarea>
      <button type="submit" class="btn btn-outline-success w-100">Save Tags/Mentions</button>
      </form>
    </div>
  </div>

  <div class="tab-pane fade" id="logs">
    <div class="card p-2 my-2">
      <pre>{% for l in logs %}{{l}}\n{% endfor %}</pre>
    </div>
  </div>
</div>

<div class="card p-2 my-3">
<h5>Controls</h5>
<form method="POST" action="/start" class="mb-2">
<select name="post_type" class="form-select mb-2">
<option value="text">Text</option>
<option value="photo">Photo (ASCII)</option>
<option value="video">Video</option>
</select>
<input type="number" name="delay" value="30" min="1" class="form-control mb-2" placeholder="Delay seconds">
<button type="submit" class="btn btn-outline-primary w-100">Start Posting</button>
</form>
<form method="POST" action="/stop">
<button type="submit" class="btn btn-outline-danger w-100">Stop Posting</button>
</form>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

@app.route("/")
def index():
    files=sorted(os.listdir(UPLOAD_FOLDER),reverse=True)
    return render_template_string(INDEX_HTML, files=files, status=current_status, logs=recent_logs)

@app.route("/uploads/<path:filename>")
def uploaded(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=False)

@app.route("/upload_tokens", methods=["POST"])
def upload_tokens():
    txt=request.form.get("tokens","").strip()
    file=request.files.get("tokens_file")
    if file and file.filename:
        file.save(os.path.join(UPLOAD_FOLDER,"tokens.txt")); log("tokens.txt uploaded")
    elif txt: save_text_file(TOKENS_PATH,txt); log("tokens.txt saved from textarea")
    return redirect(url_for("index"))

@app.route("/upload_text", methods=["POST"])
def upload_text():
    txt=request.form.get("texts","").strip()
    file=request.files.get("text_file")
    if file and file.filename:
        file.save(os.path.join(UPLOAD_FOLDER,"text.txt")); log("text.txt uploaded")
    elif txt: save_text_file(TEXTS_PATH,txt); log("text.txt saved from textarea")
    return redirect(url_for("index"))

@app.route("/upload_media", methods=["POST"])
def upload_media():
    files=request.files.getlist("media_files")
    saved_names=[]
    for f in files:
        if f and f.filename:
            name=secure_filename(f.filename)
            f.save(os.path.join(UPLOAD_FOLDER,name))
            saved_names.append(name)
            log(f"Saved media file: {name}")
    if saved_names:
        append_list_file(PHOTO_LIST_PATH,saved_names)
        log(f"Appended {len(saved_names)} files to {PHOTO_LIST_PATH}")
    return redirect(url_for("index"))

@app.route("/upload_captions", methods=["POST"])
def upload_captions():
    txt=request.form.get("captions","").strip()
    file=request.files.get("caption_file")
    if file and file.filename:
        file.save(os.path.join(UPLOAD_FOLDER,"caption.txt")); log("caption.txt uploaded")
    elif txt: save_text_file(CAPTION_PATH,txt); log("caption.txt saved from textarea")
    return redirect(url_for("index"))

@app.route("/upload_tags", methods=["POST"])
def upload_tags():
    txt=request.form.get("tags","").strip()
    if txt: save_text_file(TAGS_PATH,txt); log("tags.txt saved")
    return redirect(url_for("index"))

@app.route("/start", methods=["POST"])
def start():
    global valid_tokens, posting_thread, is_running, token_index
    post_type=request.form.get("post_type","text")
    delay=int(request.form.get("delay",30))
    if not os.path.exists(TOKENS_PATH): log("No tokens.txt found."); return redirect(url_for("index"))
    valid_tokens=validate_tokens_file(TOKENS_PATH)
    if not valid_tokens: log("No valid tokens after validation."); return redirect(url_for("index"))
    token_index=0
    if is_running: log("Worker already running."); return redirect(url_for("index"))
    is_running=True
    posting_thread=Thread(target=posting_worker,args=(post_type,delay),daemon=True)
    posting_thread.start()
    log("Posting started.")
    return redirect(url_for("index"))

@app.route("/stop", methods=["POST"])
def stop():
    global is_running
    if is_running: is_running=False; log("Stop requested.")
    else: log("Worker not running.")
    return redirect(url_for("index"))

if __name__=="__main__":
    app.run(host="0.0.0.0", port=21378)
