# app.py
# TB-friendly lazy previewer (single file)
# Requirements: pip install flask requests
# Run: python app.py

import os
import sqlite3
import uuid
import urllib.parse
from flask import Flask, request, g, jsonify, render_template_string, Response, url_for
import requests

# ---------- CONFIG ----------
DATABASE = 'files.db'
DEFAULT_BYTES = 1024 * 1024  # 1 MB
MAX_UPLOAD_BYTES = 1024 * 1024 * 100000  # 100 MB
# ----------------------------

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

# -------------------- helpers --------------------
def secure_filename(name):
    """Very small sanitizer for names used on disk."""
    keep = "".join(c for c in name if c.isalnum() or c in "._- ")
    return keep[:200] or "file"

# -------------------- DB helpers --------------------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        need_init = not os.path.exists(DATABASE)
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        if need_init:
            init_db(db)
    return db

def init_db(db):
    db.execute('''
    CREATE TABLE IF NOT EXISTS files (
        id TEXT PRIMARY KEY,
        name TEXT,
        url TEXT,
        storage TEXT,
        notes TEXT
    )
    ''')
    db.commit()

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# -------------------- Utilities --------------------
def normalize_remote_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    # Dropbox: change dl=0 to dl=1
    if 'dropbox.com' in url:
        if 'dl=0' in url:
            return url.replace('dl=0', 'dl=1')
        if 'www.dropbox.com' in url and 'dl=' not in url:
            return url + '?dl=1'
        return url

    # Google Drive heuristics
    if 'drive.google.com' in url:
        parsed = urllib.parse.urlparse(url)
        if '/file/d/' in parsed.path:
            parts = parsed.path.split('/')
            try:
                fid = parts[parts.index('d') + 1]
                return f'https://drive.google.com/uc?export=download&id={fid}'
            except Exception:
                return url
        qs = urllib.parse.parse_qs(parsed.query)
        if 'id' in qs:
            return f'https://drive.google.com/uc?export=download&id={qs["id"][0]}'
        return url

    return url

def store_file_record(name, url, storage='remote', notes=None):
    db = get_db()
    file_id = str(uuid.uuid4())
    db.execute('INSERT INTO files (id,name,url,storage,notes) VALUES (?,?,?,?,?)',
               (file_id, name, url, storage, notes))
    db.commit()
    return file_id

def get_file_record(file_id):
    db = get_db()
    cur = db.execute('SELECT * FROM files WHERE id = ?', (file_id,))
    return cur.fetchone()

# -------------------- HTML Templates --------------------
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>TB-friendly Previewer</title>
  <style>
    body { font-family: Inter, system-ui, Arial; max-width: 900px; margin: 28px auto; padding: 0 16px; color: #111; }
    h1 { text-align: center; }
    .box { border: 1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 12px; background: #fff; }
    input[type=text], input[type=url] { width: 100%; padding: 8px; margin-top: 6px; margin-bottom: 8px;border-radius:6px;border:1px solid #ccc }
    button { padding: 10px 14px; border-radius:8px; cursor:pointer; border:none; background:#0ea5e9; color:white }
    .note { color:#6b7280; font-size:13px; margin-top:8px; }
    .links { margin-top:12px; }
    .links a { display:inline-block; margin-right:12px; color:#0ea5e9; text-decoration:none; }
  </style>
</head>
<body>
  <h1>TB-friendly File Previewer (single file app.py)</h1>
  <div class="box">
    <strong>Option A — Paste public file URL (recommended)</strong>
    <p>Provide a public URL that supports HTTP Range (S3, B2, R2, public Dropbox, direct GitHub raw, some Google Drive links).</p>
    <form id="urlForm">
      <label>File URL</label>
      <input type="url" id="fileUrl" placeholder="https://...">
      <label>Display name (optional)</label>
      <input type="text" id="fileName" placeholder="my_big_file.txt">
      <button type="submit">Create Preview Link</button>
    </form>
    <div id="urlRes" class="note"></div>
  </div>

  <div class="box">
    <strong>Option B — Upload a file to this server (not for TB; server storage is limited)</strong>
    <p>Only use if file is small and you control the server. For TB files, upload to S3/R2/Drive and use Option A.</p>
    <form id="uploadForm" enctype="multipart/form-data">
      <input type="file" id="fileInput" name="file">
      <button type="submit">Upload & Create Preview</button>
    </form>
    <div id="uploadRes" class="note"></div>
  </div>

  <div class="box">
    <strong>How to use with big files (TB-scale):</strong>
    <ol>
      <li>Upload file to a cloud storage you control: S3, Backblaze B2, Cloudflare R2, Wasabi, or a Dropbox/GoogleDrive account that can generate a public or direct-download link.</li>
      <li>Paste that public/direct URL above (Option A). This app will create a preview link that streams byte ranges from that URL.</li>
      <li>Share the preview link. The viewer only downloads what they scroll through.</li>
    </ol>
    <p class="note">Note: Google Drive large-file direct-download can be flaky (confirmation prompts); S3/B2/R2 are most reliable.</p>
  </div>

  <div class="box">
    <strong>Existing pre-created previews</strong>
    <div id="existing" class="note">
      Loading...
    </div>
  </div>

<script>
async function createFromUrl(e){
  e.preventDefault();
  const url = document.getElementById('fileUrl').value.trim();
  const name = document.getElementById('fileName').value.trim();
  if(!url){ alert('paste a URL'); return; }
  const res = await fetch('/create', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ url, name })
  });
  const j = await res.json();
  if(j.ok){
    document.getElementById('urlRes').innerHTML = `Preview: <a href="/view/${j.id}" target="_blank">/view/${j.id}</a>`;
    loadList();
  } else {
    document.getElementById('urlRes').innerText = 'Error: ' + (j.error || 'unknown');
  }
}

async function uploadFile(e){
  e.preventDefault();
  const fileInput = document.getElementById('fileInput');
  if(!fileInput.files || fileInput.files.length===0){ alert('pick file'); return; }
  const file = fileInput.files[0];
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/upload', { method:'POST', body: fd });
  const j = await res.json();
  if(j.ok){
    document.getElementById('uploadRes').innerHTML = `Preview: <a href="/view/${j.id}" target="_blank">/view/${j.id}</a>`;
    loadList();
  } else {
    document.getElementById('uploadRes').innerText = 'Error: ' + (j.error || 'unknown');
  }
}

async function loadList(){
  const res = await fetch('/list');
  const j = await res.json();
  if(j.ok){
    const el = document.getElementById('existing');
    if(j.items.length===0) el.innerText = 'No previews yet';
    else {
      el.innerHTML = j.items.map(it=>`<div><b>${it.name}</b> — <a href="/view/${it.id}" target="_blank">/view/${it.id}</a> <span style="color:#666">(${it.storage})</span></div>`).join('');
    }
  }
}

document.getElementById('urlForm').addEventListener('submit', createFromUrl);
document.getElementById('uploadForm').addEventListener('submit', uploadFile);
loadList();
</script>
</body>
</html>
"""

VIEW_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Preview - {{name}}</title>
  <style>
    body { font-family: Inter, system-ui, Arial; max-width: 1100px; margin: 18px auto; padding: 0 12px; color:#111 }
    .top { display:flex; justify-content:space-between; align-items:center; gap:12px }
    .viewer { margin-top:12px; border-radius:10px; border:1px solid #e5e7eb; padding:12px; height:75vh; overflow:auto; white-space:pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#fff }
    .hint { color:#6b7280; margin-top:8px; font-size:13px }
    button { padding:6px 10px; border-radius:8px; border:none; background:#0ea5e9; color:white; cursor:pointer }
  </style>
</head>
<body>
  <div class="top">
    <div>
      <h2 style="margin:0;">Preview: {{name}}</h2>
      <div style="color:#6b7280; font-size:13px;">Source: {{url}}</div>
    </div>
    <div>
      <button id="copyBtn">Copy URL</button>
      <button id="rawBtn">Open raw</button>
    </div>
  </div>

  <div id="viewer" class="viewer">Loading…</div>
  <div id="status" class="hint">Scroll to load more (chunks: {{bytes}} bytes)</div>

<script>
const ID = "{{fid}}";
const BYTES = {{bytes}};
const OVERLAP = 1024; // 1KB overlap to avoid splitting lines
let pos = 0;
let done = false;
let loading = false;
const v = document.getElementById('viewer');

async function fetchChunk(){
  if(done || loading) return;
  loading = true;
  const start = Math.max(0, pos - (pos>0 ? OVERLAP:0));
  const bytes = BYTES + (pos>0 ? OVERLAP:0);
  const url = `/stream?id=${encodeURIComponent(ID)}&start=${start}&bytes=${bytes}`;
  try {
    const res = await fetch(url);
    if(!res.ok){
      document.getElementById('status').innerText = 'Stream error: ' + res.status;
      loading = false;
      return;
    }
    const arr = new Uint8Array(await res.arrayBuffer());
    if(arr.length === 0){
      done = true;
      document.getElementById('status').innerText = 'End of file.';
      loading = false;
      return;
    }
    const text = new TextDecoder().decode(arr);
    const slice = pos>0 ? text.substring(OVERLAP) : text;
    const lastNL = slice.lastIndexOf('\\n');
    const keep = lastNL >= 0 ? slice.substring(0, lastNL+1) : slice;
    v.innerText += keep;
    pos += keep.length;
    if(arr.length < bytes) { done = true; document.getElementById('status').innerText = 'End of file.'; }
  } catch(err){
    console.error(err);
    document.getElementById('status').innerText = 'Stream network error';
  } finally { loading = false; }
}

v.addEventListener('scroll', ()=>{
  if(v.scrollTop + v.clientHeight >= v.scrollHeight - 80){
    fetchChunk();
  }
});

document.getElementById('copyBtn').addEventListener('click', ()=>{
  navigator.clipboard.writeText(location.href);
  alert('Copied URL');
});
document.getElementById('rawBtn').addEventListener('click', ()=>{
  window.open("{{rawopen}}",'_blank');
});

// initial load
fetchChunk();
</script>
</body>
</html>
"""

# -------------------- ROUTES --------------------
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/create', methods=['POST'])
def create():
    data = request.get_json(force=True)
    url = data.get('url', '').strip()
    name = data.get('name', '').strip() or None
    if not url:
        return jsonify({'ok': False, 'error': 'no url provided'}), 400
    normalized = normalize_remote_url(url)
    fid = store_file_record(name or os.path.basename(normalized) or 'file', normalized, storage='remote')
    return jsonify({'ok': True, 'id': fid})

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'no file'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'ok': False, 'error': 'empty filename'}), 400
    os.makedirs('uploads', exist_ok=True)
    fname = f'{uuid.uuid4()}_{secure_filename(f.filename)}'
    path = os.path.join('uploads', fname)
    f.save(path)
    # store local path and mark storage local
    fid = store_file_record(f.filename, path, storage='local')
    return jsonify({'ok': True, 'id': fid})

@app.route('/list')
def list_items():
    db = get_db()
    cur = db.execute('SELECT id,name,storage FROM files ORDER BY rowid DESC LIMIT 200')
    rows = cur.fetchall()
    items = [{'id': r['id'], 'name': r['name'], 'storage': r['storage']} for r in rows]
    return jsonify({'ok': True, 'items': items})

@app.route('/view/<fid>')
def view(fid):
    rec = get_file_record(fid)
    if not rec:
        return "Not found", 404
    raw = rec['url'] if rec['storage']=='remote' else url_for('stream', id=fid, _external=True)
    return render_template_string(VIEW_HTML, name=rec['name'], url=rec['url'], fid=fid, bytes=DEFAULT_BYTES, rawopen=raw)

# -------------------- STREAM PROXY --------------------
@app.route('/stream')
def stream():
    fid = request.args.get('id')
    if not fid:
        return "id required", 400
    rec = get_file_record(fid)
    if not rec:
        return "not found", 404

    try:
        start = int(request.args.get('start', '0'))
    except:
        return "invalid start", 400
    try:
        nb = int(request.args.get('bytes', str(DEFAULT_BYTES)))
    except:
        nb = DEFAULT_BYTES
    if nb <= 0:
        nb = DEFAULT_BYTES
    end = start + nb - 1

    # local
    if rec['storage'] == 'local':
        path = rec['url']
        if not os.path.exists(path):
            return "local file missing", 404
        def gen_local():
            with open(path, 'rb') as fh:
                fh.seek(start)
                remaining = nb
                while remaining > 0:
                    chunk = fh.read(min(64*1024, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)
        headers = {
            'Content-Type': 'application/octet-stream',
            'Accept-Ranges': 'bytes',
            'Content-Range': f'bytes {start}-{start+nb-1}/*'
        }
        return Response(gen_local(), headers=headers)

    remote_url = rec['url']
    if remote_url.startswith('file://') or os.path.exists(remote_url):
        return "local file not available as remote", 400

    range_header = {'Range': f'bytes={start}-{end}'}
    try:
        r = requests.get(remote_url, headers=range_header, stream=True, timeout=30)
    except requests.RequestException as e:
        return f'fetch error: {e}', 502

    if r.status_code not in (200, 206):
        return Response(r.content, status=r.status_code, headers={'Content-Type': r.headers.get('Content-Type','application/octet-stream')})

    def generate():
        try:
            for chunk in r.iter_content(chunk_size=64*1024):
                if not chunk:
                    continue
                yield chunk
        finally:
            r.close()

    headers = {
        'Content-Type': r.headers.get('Content-Type', 'application/octet-stream'),
        'Accept-Ranges': r.headers.get('Accept-Ranges', 'bytes'),
        'Content-Range': r.headers.get('Content-Range', f'bytes {start}-{end}/*')
    }
    return Response(generate(), headers=headers)

# -------------------- RUN --------------------
if __name__ == '__main__':
    with app.app_context():
        get_db()
    print("Starting TB-friendly previewer on http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
