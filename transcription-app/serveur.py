#!/usr/bin/env python3
"""Serveur de transcription vocale - App Réunions"""
import json, os, uuid, sqlite3, threading, time, re, html as html_mod
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import whisper
import subprocess, tempfile

# ─── CONFIG ───
PORT = 8084
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DATA_DIR, "transcriptions.db")
AUDIO_DIR = os.path.join(DATA_DIR, "audio_uploads")
FRONTEND = os.path.join(DATA_DIR, "index.html")
WHISPER_MODEL = "small"  # tiny, base, small, medium, large
WHISPER_LANG = "fr"

os.makedirs(AUDIO_DIR, exist_ok=True)

# ─── DB INIT ───
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            speaker_count INTEGER DEFAULT 1,
            duration_seconds REAL,
            audio_filename TEXT,
            raw_transcription TEXT,
            formatted_text TEXT,
            summary TEXT,
            language TEXT DEFAULT 'fr',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    conn.close()

# ─── WHISPER LOAD ───
whisper_model = None
whisper_lock = threading.Lock()

def get_whisper():
    global whisper_model
    if whisper_model is None:
        print(f"[Whisper] Chargement du modèle '{WHISPER_MODEL}'...")
        whisper_model = whisper.load_model(WHISPER_MODEL)
        print("[Whisper] Modèle chargé ✓")
    return whisper_model

def transcribe_audio(audio_path):
    """Transcrit un fichier audio avec Whisper"""
    model = get_whisper()
    result = model.transcribe(audio_path, language=WHISPER_LANG, verbose=False)
    # Formater en texte horodaté
    segments = []
    for seg in result.get("segments", []):
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()
        if text:
            ts = f"{int(start//60):02d}:{int(start%60):02d}"
            segments.append({"start": start, "end": end, "text": text, "timestamp": ts})
    raw = result["text"].strip()
    formatted = build_formatted(segments)
    return raw, formatted, segments

def build_formatted(segments):
    """Construit le texte formaté avec timestamps"""
    lines = []
    for seg in segments:
        lines.append(f"[{seg['timestamp']}] {seg['text']}")
    return "\n".join(lines)

def format_duration(secs):
    if secs is None:
        return "--:--"
    m = int(secs // 60)
    s = int(secs % 60)
    return f"{m:02d}:{s:02d}"

# ─── SERVER ───
class TranscriptionHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _send_html(self, content, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _parse_post(self):
        ct = self.headers.get("Content-Type", "")
        body = self._read_body()
        if "multipart/form-data" in ct:
            return self._parse_multipart(body, ct)
        return json.loads(body.decode())

    def _parse_multipart(self, body, ct):
        """Parse multipart form data - robust version"""
        boundary_raw = ct.split("boundary=")[1].strip()
        if boundary_raw.startswith('"') and boundary_raw.endswith('"'):
            boundary_raw = boundary_raw[1:-1]
        boundary = boundary_raw.encode()
        if boundary.startswith(b'--'):
            boundary = boundary[2:]

        parts = body.split(b"--" + boundary)
        fields = {}
        file_data = None
        filename = None

        for part in parts:
            if not part or part.strip() in (b"", b"--", b"\r\n", b"--\r\n"):
                continue

            # Supprimer les \r\n de début
            while part.startswith(b"\r\n"):
                part = part[2:]

            # Headers se terminent par \r\n\r\n
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue

            headers_raw = part[:header_end].decode("utf-8", errors="replace")
            content = part[header_end + 4:]

            # Nettoyer les fins de ligne pour le fichier brut
            while content.endswith(b"\r\n"):
                content = content[:-2]

            name_match = re.search(r'name="([^"]*)"', headers_raw)
            filename_match = re.search(r'filename="([^"]*)"', headers_raw)

            if filename_match:
                filename = filename_match.group(1)
                file_data = content  # données binaires brutes, PAS de strip
            elif name_match:
                # Données texte : nettoyer
                if content.endswith(b"--"):
                    content = content[:-2]
                while content.endswith(b"\r\n"):
                    content = content[:-2]
                content = content.strip()
                try:
                    val = content.decode("utf-8", errors="replace").strip()
                    fields[name_match.group(1)] = val
                except:
                    pass

        return fields, file_data, filename

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            return self._serve_frontend()
        elif path == "/api/list":
            return self._list_transcriptions(params)
        elif path.startswith("/api/get/"):
            tid = path.split("/")[-1]
            return self._get_transcription(tid)
        elif path == "/api/search":
            return self._search_transcriptions(params)
        elif path.startswith("/audio/"):
            return self._serve_audio(path)
        else:
            self.send_error(404)

    def _serve_frontend(self):
        if os.path.exists(FRONTEND):
            with open(FRONTEND, "r", encoding="utf-8") as f:
                self._send_html(f.read())
        else:
            self.send_error(404, "Frontend not found")

    def _serve_audio(self, path):
        filepath = os.path.join(AUDIO_DIR, os.path.basename(path))
        if os.path.exists(filepath):
            self.send_response(200)
            self.send_header("Content-Type", "audio/webm")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(filepath, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404)

    def _list_transcriptions(self, params):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, date, speaker_count, duration_seconds, "
            "CASE WHEN summary IS NOT NULL AND summary != '' THEN 1 ELSE 0 END as has_summary, "
            "CASE WHEN raw_transcription IS NOT NULL AND raw_transcription != '' THEN 1 ELSE 0 END as has_transcription, "
            "created_at FROM transcriptions ORDER BY date DESC, created_at DESC"
        ).fetchall()
        conn.close()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "speaker_count": r["speaker_count"],
                "duration_str": format_duration(r["duration_seconds"]),
                "has_summary": bool(r["has_summary"]),
                "has_transcription": bool(r["has_transcription"]),
                "created_at": r["created_at"]
            })
        self._send_json({"items": items, "count": len(items)})

    def _get_transcription(self, tid):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM transcriptions WHERE id=?", (tid,)).fetchone()
        conn.close()
        if not row:
            self._send_json({"error": "not found"}, 404)
            return
        data = dict(row)
        data["duration_str"] = format_duration(data.get("duration_seconds"))
        self._send_json(data)

    def _search_transcriptions(self, params):
        q = params.get("q", [""])[0].strip()
        if not q:
            return self._list_transcriptions(params)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, date, speaker_count, duration_seconds, "
            "CASE WHEN summary IS NOT NULL AND summary != '' THEN 1 ELSE 0 END as has_summary, "
            "CASE WHEN raw_transcription IS NOT NULL AND raw_transcription != '' THEN 1 ELSE 0 END as has_transcription, "
            "created_at FROM transcriptions "
            "WHERE title LIKE ? OR raw_transcription LIKE ? OR summary LIKE ? "
            "ORDER BY date DESC, created_at DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%")
        ).fetchall()
        conn.close()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "speaker_count": r["speaker_count"],
                "duration_str": format_duration(r["duration_seconds"]),
                "has_summary": bool(r["has_summary"]),
                "has_transcription": bool(r["has_transcription"]),
                "created_at": r["created_at"]
            })
        self._send_json({"items": items, "count": len(items)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/upload":
            return self._upload_transcribe()
        elif path == "/api/save":
            return self._save_metadata()
        elif path.startswith("/api/summary/"):
            tid = path.split("/")[-1]
            return self._save_summary(tid)
        elif path == "/api/transcribe-text":
            return self._transcribe_text_only()
        else:
            self.send_error(404)

    def _upload_transcribe(self):
        fields, file_data, filename = self._parse_post()
        title = fields.get("title", "").strip()
        date = fields.get("date", datetime.now().strftime("%Y-%m-%d"))
        speakers = int(fields.get("speaker_count", "1"))

        if not title:
            title = f"Réunion du {date}"

        if not file_data:
            self._send_json({"error": "Aucun fichier audio reçu"}, 400)
            return

        # Sauvegarder l'audio
        tid = uuid.uuid4().hex[:12]
        # Déterminer l'extension
        if filename and "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
        else:
            ext = "webm"
        # Nettoyer l'extension
        ext = re.sub(r'[^a-z0-9]', '', ext) or "webm"
        audio_name = f"{tid}.{ext}"
        audio_path = os.path.join(AUDIO_DIR, audio_name)
        with open(audio_path, "wb") as f:
            f.write(file_data)

        # Lancer la transcription dans un thread
        self._send_json({
            "id": tid,
            "status": "processing",
            "message": "Transcription en cours...",
            "audio_file": audio_name
        })

        def process():
            try:
                wav_path = audio_path
                # Whisper accepte nativement webm/ogg/mp4/m4a via ffmpeg
                # Convertir en wav 16kHz mono pour être sûr
                formats_ok = {"wav", "mp3", "m4a", "aac"}
                if ext not in formats_ok:
                    wav_path = audio_path + ".wav"
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
                        capture_output=True, check=True, timeout=120
                    )

                raw, formatted, segments = transcribe_audio(wav_path)
                duration = segments[-1]["end"] if segments else 0

                if wav_path != audio_path and os.path.exists(wav_path):
                    os.remove(wav_path)

                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT OR REPLACE INTO transcriptions "
                            "(id, title, date, speaker_count, duration_seconds, audio_filename, raw_transcription, formatted_text, language) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (tid, title, date, speakers, duration, audio_name, raw, formatted, WHISPER_LANG))
                conn.commit()
                conn.close()
                print(f"[OK] Transcription terminée: {title} ({duration:.0f}s)")
            except subprocess.TimeoutExpired:
                print(f"[ERREUR] ffmpeg timeout pour {audio_name}")
                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT OR REPLACE INTO transcriptions "
                            "(id, title, date, speaker_count, audio_filename, raw_transcription, formatted_text, language) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (tid, title, date, speakers, audio_name,
                             "[Erreur: La conversion audio a expiré (fichier trop long ou corrompu)]",
                             "", WHISPER_LANG))
                conn.commit()
                conn.close()
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr.decode()[:200] if e.stderr else "Erreur inconnue"
                print(f"[ERREUR] ffmpeg: {err_msg}")
                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT OR REPLACE INTO transcriptions "
                            "(id, title, date, speaker_count, audio_filename, raw_transcription, formatted_text, language) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (tid, title, date, speakers, audio_name,
                             f"[Erreur de conversion audio: {err_msg}]",
                             "", WHISPER_LANG))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[ERREUR] Transcription: {e}")
                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT OR REPLACE INTO transcriptions "
                            "(id, title, date, speaker_count, audio_filename, raw_transcription, formatted_text, language) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (tid, title, date, speakers, audio_name,
                             f"[Erreur: {e}]",
                             "", WHISPER_LANG))
                conn.commit()
                conn.close()

        threading.Thread(target=process, daemon=True).start()
        # On retourne immédiatement, le client devra poller

    def _transcribe_text_only(self):
        """Transcrire sans upload (enregistrement navigateur déjà fait)"""
        data = self._read_body()
        body = json.loads(data.decode())
        audio_path = body.get("audio_path")
        title = body.get("title", "Réunion")
        date = body.get("date", datetime.now().strftime("%Y-%m-%d"))
        speakers = int(body.get("speaker_count", "1"))

        tid = uuid.uuid4().hex[:12]

        self._send_json({"id": tid, "status": "processing"})

        def process():
            try:
                wav_path = audio_path + ".wav"
                subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
                               capture_output=True, check=True)

                raw, formatted, segments = transcribe_audio(wav_path)
                duration = segments[-1]["end"] if segments else 0

                if os.path.exists(wav_path):
                    os.remove(wav_path)

                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT OR REPLACE INTO transcriptions "
                            "(id, title, date, speaker_count, duration_seconds, audio_filename, raw_transcription, formatted_text, language) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (tid, title, date, speakers, duration, os.path.basename(audio_path), raw, formatted, WHISPER_LANG))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[ERREUR] Transcription text: {e}")

        threading.Thread(target=process, daemon=True).start()

    def _save_metadata(self):
        data = json.loads(self._read_body().decode())
        tid = data.get("id", uuid.uuid4().hex[:12])
        title = data.get("title", "Réunion")
        date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        speakers = int(data.get("speaker_count", 1))

        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO transcriptions (id, title, date, speaker_count) VALUES (?, ?, ?, ?)",
                     (tid, title, date, speakers))
        conn.commit()
        conn.close()
        self._send_json({"id": tid, "status": "ok"})

    def _save_summary(self, tid):
        data = json.loads(self._read_body().decode())
        summary = data.get("summary", "").strip()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE transcriptions SET summary=? WHERE id=?", (summary, tid))
        conn.commit()
        conn.close()
        self._send_json({"id": tid, "status": "ok"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/delete/"):
            tid = path.split("/")[-1]
            conn = sqlite3.connect(DB_PATH)
            # Récupérer le nom du fichier audio
            row = conn.execute("SELECT audio_filename FROM transcriptions WHERE id=?", (tid,)).fetchone()
            conn.execute("DELETE FROM transcriptions WHERE id=?", (tid,))
            conn.commit()
            conn.close()
            # Supprimer le fichier audio
            if row and row[0]:
                audio_path = os.path.join(AUDIO_DIR, row[0])
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            self._send_json({"status": "deleted"})
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        print(f"[Transcription] {args[0]}" if args else "")

# ─── START ───
if __name__ == "__main__":
    init_db()
    # Précharger Whisper au démarrage
    print("[Démarrage] Chargement du modèle Whisper...")
    t = threading.Thread(target=get_whisper, daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", PORT), TranscriptionHandler)
    print(f"🎙️ Serveur de transcription sur http://0.0.0.0:{PORT}")
    print(f"   Modèle: {WHISPER_MODEL} | Langue: {WHISPER_LANG}")
    print(f"   Port: {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
        server.server_close()
