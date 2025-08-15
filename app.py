# app.py - Permanent File Previewer (single file)
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
UPLOAD_FOLDER = 'permanent_uploads'  # All files stored here permanently
DEFAULT_BYTES = 1024 * 1024  # 1 MB chunk size for preview
MAX_UPLOAD_BYTES = 1024 * 1024 * 100000  # 100 MB max upload size
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # Ensure upload folder exists
# ----------------------------

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

# -------------------- helpers --------------------
def secure_filename(name):
    """Sanitize filenames for safe storage."""
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
        path TEXT,  # Changed from 'url' to 'path' since we only store local now
        storage TEXT DEFAULT 'local',
        notes TEXT,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    db.commit()

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def store_file_record(name, path, notes=None):
    """Store file record - always marked as local storage now."""
    db = get_db()
    file_id = str(uuid.uuid4())
    db.execute('INSERT INTO files (id, name, path, storage, notes) VALUES (?,?,?,?,?)',
               (file_id, name, path, 'local', notes))
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
  <title>Permanent File Previewer</title>
  <style>
    body { font-family: Inter, system-ui, Arial; max-width: 900px; margin: 28px auto; padding: 0 16px; color: #111; }
    h1 { text-align: center; }
    .box { border: 1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 12px; background: #fff; }
    input[type=text], input[type=file] { width: 100%; padding: 8px; margin-top: 6px; margin-bottom: 8px;border-radius:6px;border:1px solid #ccc }
    button { padding: 10px 14px; border-radius:8px; cursor:pointer; border:none; background:#0ea5e9; color:white }
    .note { color:#6b7280; font-size:13px; margin-top:8px; }
    .links { margin-top:12px; }
    .links a { display:inline-block; margin-right:12px; color:#0ea5e9; text-decoration:none; }
    .file-list { margin-top: 16px; }
    .file-item { padding: 8px 0; border-bottom: 1px solid #eee; }
  </style>
</head>
<body>
  <h1>Permanent File Previewer</h1>
  <div class="box">
    <strong>Upload a file for permanent preview</strong>
    <p>All files are stored permanently on the server with no expiration.</p>
    <form id="uploadForm" enctype="multipart/form-data">
      <input type="file" id="fileInput" name="file" required>
      <label>Display name (optional)</label>
      <input type="text" id="displayName" placeholder="Custom name for preview">
      <button type="submit">Upload & Create Permanent Preview</button>
    </form>
    <div id="uploadRes" class="note"></div>
  </div>

  <div class="box">
    <strong>Existing Permanent Previews</strong>
    <div id="existing" class="file-list">
      Loading...
    </div>
  </div>

<script>
async function uploadFile(e){
  e.preventDefault();
  const fileInput = document.getElementById('fileInput');
  if(!fileInput.files || fileInput.files.length===0){ alert('Please select a file'); return; }
  
  const fd = new FormData();
  fd.append('file', fileInput.files[0]);
  const displayName = document.getElementById('displayName').value.trim();
  if(displayName) fd.append('name', displayName);
  
  try {
    const res = await fetch('/upload', { method:'POST', body: fd });
    const j = await res.json();
    if(j.ok){
      document.getElementById('uploadRes').innerHTML = `
        <div style="color:green;">
          Preview created: <a href="/view/${j.id}" target="_blank">/view/${j.id}</a>
        </div>
      `;
      loadList();
      fileInput.value = ''; // Clear file input
    } else {
      document.getElementById('uploadRes').innerHTML = `
        <div style="color:red;">Error: ${j.error || 'Unknown error'}</div>
      `;
    }
  } catch(err) {
    document.getElementById('uploadRes').innerHTML = `
      <div style="color:red;">Network error: ${err.message}</div>
    `;
  }
}

async function loadList(){
  try {
    const res = await fetch('/list');
    const j = await res.json();
    const el = document.getElementById('existing');
    
    if(!j.ok || !j.items || j.items.length === 0) {
      el.innerHTML = '<div class="note">No previews created yet</div>';
      return;
    }
    
    el.innerHTML = j.items.map(it => `
      <div class="file-item">
        <div><strong>${it.name}</strong></div>
        <div>
          <a href="/view/${it.id}" target="_blank">Preview</a> | 
          <a href="/download/${it.id}" target="_blank">Download</a> | 
          <small>${new Date(it.created).toLocaleString()}</small>
        </div>
      </div>
    `).join('');
  } catch(err) {
    document.getElementById('existing').innerHTML = `
      <div style="color:red;">Error loading list: ${err.message}</div>
    `;
  }
}

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
    .top { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom: 16px; }
    .viewer { margin-top:12px; border-radius:10px; border:1px solid #e5e7eb; padding:12px; height:75vh; overflow:auto; white-space:pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background:#fff }
    .hint { color:#6b7280; margin-top:8px; font-size:13px }
    button { padding:6px 10px; border-radius:8px; border:none; background:#0ea5e9; color:white; cursor:pointer; margin-left: 8px; }
    .file-info { color: #666; font-size: 14px; margin-top: 4px; }
  </style>
</head>
<body>
  <div class="top">
    <div>
      <h2 style="margin:0;">{{name}}</h2>
      <div class="file-info">
        Uploaded: {{created}} | Size: {{size}}
      </div>
    </div>
    <div>
      <button id="downloadBtn">Download</button>
      <button id="copyBtn">Copy Link</button>
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
  alert('Preview link copied to clipboard');
});

document.getElementById('downloadBtn').addEventListener('click', ()=>{
  window.location.href = `/download/${ID}`;
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

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'ok': False, 'error': 'Empty filename'}), 400
    
    # Get display name or use original filename
    display_name = request.form.get('name', '').strip() or secure_filename(file.filename)
    
    # Generate permanent storage path
    unique_id = str(uuid.uuid4())
    fname = f'{unique_id}_{secure_filename(file.filename)}'
    path = os.path.join(UPLOAD_FOLDER, fname)
    
    try:
        # Save file permanently
        file.save(path)
        
        # Store record
        fid = store_file_record(display_name, path)
        
        return jsonify({
            'ok': True,
            'id': fid,
            'name': display_name,
            'path': path
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Upload failed: {str(e)}'}), 500

@app.route('/list')
def list_items():
    db = get_db()
    cur = db.execute('''
        SELECT id, name, path, storage, 
               datetime(created, 'localtime') as created,
               (SELECT COUNT(*) FROM files) as total_count
        FROM files 
        ORDER BY created DESC 
        LIMIT 200
    ''')
    rows = cur.fetchall()
    
    items = []
    for r in rows:
        try:
            size = os.path.getsize(r['path']) if os.path.exists(r['path']) else 0
        except:
            size = 0
            
        items.append({
            'id': r['id'],
            'name': r['name'],
            'storage': r['storage'],
            'created': r['created'],
            'size': sizeof_fmt(size)
        })
    
    return jsonify({
        'ok': True,
        'items': items,
        'total_count': rows[0]['total_count'] if rows else 0
    })

def sizeof_fmt(num, suffix='B'):
    """Convert file size to human-readable format"""
    for unit in ['','K','M','G','T','P','E','Z']:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

@app.route('/view/<fid>')
def view(fid):
    rec = get_file_record(fid)
    if not rec:
        return "Preview not found", 404
    
    # Get file info
    try:
        size = os.path.getsize(rec['path']) if os.path.exists(rec['path']) else 0
        created = rec['created'] if 'created' in rec else 'unknown'
    except:
        size = 0
        created = 'unknown'
    
    return render_template_string(
        VIEW_HTML,
        name=rec['name'],
        fid=fid,
        bytes=DEFAULT_BYTES,
        created=created,
        size=sizeof_fmt(size)
    )

@app.route('/download/<fid>')
def download(fid):
    rec = get_file_record(fid)
    if not rec or not os.path.exists(rec['path']):
        return "File not found", 404
    
    return Response(
        open(rec['path'], 'rb'),
        mimetype='application/octet-stream',
        headers={
            'Content-Disposition': f'attachment; filename="{secure_filename(rec["name"])}"'
        }
    )

@app.route('/stream')
def stream():
    fid = request.args.get('id')
    if not fid:
        return "Preview ID required", 400
    
    rec = get_file_record(fid)
    if not rec or not os.path.exists(rec['path']):
        return "File not found", 404

    try:
        start = int(request.args.get('start', '0'))
    except:
        return "Invalid start position", 400
    
    try:
        nb = int(request.args.get('bytes', str(DEFAULT_BYTES)))
    except:
        nb = DEFAULT_BYTES
    
    if nb <= 0:
        nb = DEFAULT_BYTES

    def generate():
        with open(rec['path'], 'rb') as f:
            f.seek(start)
            remaining = nb
            while remaining > 0:
                chunk = f.read(min(64*1024, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)

    headers = {
        'Content-Type': 'application/octet-stream',
        'Accept-Ranges': 'bytes',
        'Content-Range': f'bytes {start}-{start+nb-1}/*'
    }
    
    return Response(generate(), headers=headers)

# -------------------- RUN --------------------
if __name__ == '__main__':
    with app.app_context():
        get_db()  # Initialize database
    
    print(f"Starting Permanent File Previewer on http://127.0.0.1:5000")
    print(f"Uploads stored in: {os.path.abspath(UPLOAD_FOLDER)}")
    app.run(host='0.0.0.0', port=5000, debug=True)
