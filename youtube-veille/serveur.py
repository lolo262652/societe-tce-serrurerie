#!/usr/bin/env python3
"""
Serveur de veille YouTube — Ajoute des vidéos, récupère la transcription,
stocke le tout et permet la recherche plein texte.

Routes API :
  GET  /              → sert le frontend (index.html)
  POST /api/add       → ajoute une vidéo (body: {"url": "..."})
  GET  /api/list      → liste toutes les vidéos sauvegardées
  GET  /api/search?q=... → recherche dans les transcripts
  GET  /api/video/:id → détails + transcript complet
  DELETE /api/delete/:id → supprime une vidéo
"""

import json, os, re, sys, hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

DATA_DIR = Path(__file__).parent / "youtube-data"
DATA_DIR.mkdir(exist_ok=True)
INDEX_FILE = DATA_DIR / "index.json"
TAGS_FILE = DATA_DIR / "tags.json"
FRONTEND = Path(__file__).parent.parent / "youtube-veille.html"

# === GESTION DES DONNÉES ===

def load_index():
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text())
    return {}

def save_index(index):
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2))

def load_tags():
    if TAGS_FILE.exists():
        return json.loads(TAGS_FILE.read_text())
    return []

def save_tags(tags):
    TAGS_FILE.write_text(json.dumps(tags, ensure_ascii=False, indent=2))

def strip_tag_from_all_videos(tag_name):
    """Supprime un tag de toutes les vidéos qui l'ont."""
    index = load_index()
    for vid, info in index.items():
        tags = info.get("tags", [])
        if tag_name in tags:
            tags.remove(tag_name)
            info["tags"] = tags
            # Also update the video's full data file
            vp = get_video_path(vid)
            if vp.exists():
                data = json.loads(vp.read_text())
                data["tags"] = tags
                vp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    save_index(index)

def get_video_path(video_id):
    return DATA_DIR / f"{video_id}.json"

def extract_video_id(url):
    patterns = [
        r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',
    ]
    for pat in patterns:
        m = re.search(pat, url.strip())
        if m: return m.group(1)
    return None

def fetch_video_info_light(video_id):
    """Récupère le titre (oEmbed) + description (curl HTML) — rapide et fiable."""
    title = "Titre inconnu"
    description = ""
    import urllib.request, json, subprocess, re

    # Titre via oEmbed API (fiable, < 2s)
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            if data.get('title'):
                title = data['title']
    except Exception as e:
        print(f"[WARN] Title failed for {video_id}: {e}")

    # Description via curl + extraction de ytInitialData (rapide, < 3s, pas de bot detection)
    try:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "8",
             f"https://www.youtube.com/watch?v={video_id}",
             "-H", f"User-Agent: {ua}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
            # Extraire ytInitialData JSON
            m = re.search(r'ytInitialData\s*=\s*({.*?});', text, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                # Naviguer vers la description
                tabs = data.get('contents', {}).get('twoColumnWatchNextResults', {}).get('results', {}).get('results', {}).get('contents', [])
                for item in tabs:
                    vid_sec = item.get('videoSecondaryInfoRenderer', {})
                    if vid_sec:
                        desc_text = vid_sec.get('attributedDescription', {}).get('content', '')
                        if desc_text:
                            # Nettoyer les retours à la ligne échappés
                            description = desc_text.replace('\\n', '\n')
                            break
                        # Fallback simpleText
                        desc_text = vid_sec.get('description', {}).get('simpleText', '')
                        if desc_text:
                            description = desc_text.replace('\\n', '\n')
                            break
    except Exception as e:
        print(f"[WARN] Description via curl/HTML failed for {video_id}: {e}")

    # Fallback: yt-dlp si curl a échoué
    if not description:
        try:
            result = subprocess.run(
                ["yt-dlp", "--skip-download", "--print", "description",
                 f"https://www.youtube.com/watch?v={video_id}"],
                timeout=10, capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                description = result.stdout.strip()
        except:
            pass

    return {
        "video_id": video_id,
        "title": title,
        "description": description,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "added_at": None,
        "duration_sec": 0,
        "segment_count": 0,
        "full_text": "",
        "segments": [],
        "tags": [],
        "needs_manual_transcript": True,
        "transcript_pending": True,
    }

def fetch_transcript_background(video_id):
    """Récupère la transcription en arrière-plan, met à jour le fichier vidéo."""
    import subprocess, tempfile, os, re, time, json
    from pathlib import Path

    DATA_DIR = Path(__file__).parent / "youtube-data"

    time.sleep(0.5)
    segments = []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            outtmpl = os.path.join(tmpdir, "sub")
            cmd = [
                "yt-dlp",
                "--skip-download",
                "--write-auto-sub",
                "--sub-lang", "en",
                "--convert-subs", "srt",
                "--output", outtmpl,
                "--quiet", "--no-warnings",
                f"https://www.youtube.com/watch?v={video_id}",
            ]
            subprocess.run(cmd, timeout=30, capture_output=True)

            for f in os.listdir(tmpdir):
                if f.endswith('.srt'):
                    srt_path = os.path.join(tmpdir, f)
                    break
            else:
                srt_path = None

            if srt_path:
                with open(srt_path, 'r', encoding='utf-8', errors='replace') as fh:
                    content = fh.read()

                blocks = re.split(r'\n\s*\n', content.strip())
                for block in blocks:
                    lines = block.strip().split('\n')
                    if len(lines) >= 3:
                        time_match = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)', lines[1])
                        if time_match:
                            start_sec = int(time_match.group(1)) * 3600 + int(time_match.group(2)) * 60 + int(time_match.group(3))
                            end_sec = int(time_match.group(5)) * 3600 + int(time_match.group(6)) * 60 + int(time_match.group(7))
                            text = ' '.join(l for l in lines[2:] if l.strip() and not l.startswith('['))
                            text = re.sub(r'<[^>]+>', '', text).strip()
                            if text and text.lower() not in ['music', '♪', '[music]', '♫']:
                                segments.append({
                                    "text": text,
                                    "start": start_sec,
                                    "duration": end_sec - start_sec,
                                })
    except Exception as e:
        print(f"[WARN] Transcript bg failed for {video_id}: {e}")

    full_text = " ".join(s["text"] for s in segments)
    duration = segments[-1]["start"] + segments[-1]["duration"] if segments else 0

    # Mettre à jour le fichier vidéo
    vp = DATA_DIR / f"{video_id}.json"
    if vp.exists():
        data = json.loads(vp.read_text())
        data["segments"] = segments
        data["full_text"] = full_text
        data["segment_count"] = len(segments)
        data["duration_sec"] = duration
        data["needs_manual_transcript"] = len(segments) == 0
        data["transcript_pending"] = False
        vp.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # Mettre à jour l'index
    idx_file = DATA_DIR / "index.json"
    if idx_file.exists():
        index = json.loads(idx_file.read_text())
        if video_id in index:
            index[video_id]["segment_count"] = len(segments)
            index[video_id]["duration_sec"] = duration
            index[video_id]["transcript_pending"] = False
            idx_file.write_text(json.dumps(index, ensure_ascii=False, indent=2))

    if segments:
        print(f"[OK] Transcript fetched for {video_id}: {len(segments)} segments")
    else:
        print(f"[INFO] No transcript available for {video_id}")

def parse_transcript_text(text):
    """Parse un texte brut ou formaté avec timestamps en segments."""
    lines = text.strip().split('\n')
    segments = []

    # Détecter si contient des timestamps [mm:ss] ou mm:ss ->
    ts_pattern = re.compile(r'^(?:\[)?(\d+):(\d{2})(?:\])?(?:\s*[-–>]+\s*|\s+)(.+)$')
    has_timestamps = any(ts_pattern.match(l.strip()) for l in lines if l.strip())

    if has_timestamps:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = ts_pattern.match(line)
            if m:
                mins, secs, txt = int(m.group(1)), int(m.group(2)), m.group(3).strip()
                txt = re.sub(r'<[^>]+>', '', txt).strip()
                if txt:
                    segments.append({
                        "text": txt,
                        "start": mins * 60 + secs,
                        "duration": 10,
                    })
        # Calculer les durées réelles
        for i in range(len(segments) - 1):
            segments[i]["duration"] = max(segments[i+1]["start"] - segments[i]["start"], 1)
        if segments:
            segments[-1]["duration"] = max(segments[-1]["duration"], 5)
    else:
        # Texte brut → un seul segment
        text_clean = re.sub(r'<[^>]+>', '', text.strip())
        if text_clean:
            segments.append({
                "text": text_clean,
                "start": 0,
                "duration": 0,
            })

    full_text = " ".join(s["text"] for s in segments)
    return segments, full_text

# === SERVEUR HTTP ===

class YouTubeAPIHandler(BaseHTTPRequestHandler):
    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _html(self, content):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(content.encode())

    def _serve_frontend(self):
        if FRONTEND.exists():
            self._html(FRONTEND.read_text())
        else:
            self._json({"error": "Frontend non trouvé"}, 404)

    def _serve_static(self, filename):
        """Sert un fichier statique (manifest.json, icon.svg, etc.)."""
        from pathlib import Path
        static_path = Path(__file__).parent / filename
        if not static_path.exists():
            self._json({"error": "Fichier non trouvé"}, 404)
            return
        content_types = {
            '.json': 'application/json',
            '.svg': 'image/svg+xml',
            '.png': 'image/png',
        }
        ext = static_path.suffix
        ctype = content_types.get(ext, 'application/octet-stream')
        data = static_path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', f'{ctype}; charset=utf-8' if ext != '.png' else ctype)
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            body = self.rfile.read(length).decode()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {}
        return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = parse_qs(parsed.query)

        if path == '/' or path == '' or path == '/index.html':
            self._serve_frontend()
        elif path == '/api/tags':
            self._json({"tags": load_tags()})
        elif path == '/api/list':
            index = load_index()
            videos = []
            tag_filter = params.get('tag', [None])[0]
            for vid, info in index.items():
                tags = info.get("tags", [])
                if tag_filter and tag_filter not in tags:
                    continue
                videos.append({
                    "video_id": vid,
                    "title": info.get("title", ""),
                    "url": info.get("url", ""),
                    "added_at": info.get("added_at", ""),
                    "segment_count": info.get("segment_count", 0),
                    "duration_sec": info.get("duration_sec", 0),
                    "tags": tags,
                    "has_description": info.get("has_description", False),
                    "transcript_pending": info.get("transcript_pending", False),
                })
            self._json({"videos": sorted(videos, key=lambda v: v.get("added_at", ""), reverse=True)})
        elif path.startswith('/api/video/'):
            video_id = path.split('/')[-1]
            vp = get_video_path(video_id)
            if vp.exists():
                self._json(json.loads(vp.read_text()))
            else:
                self._json({"error": "Vidéo non trouvée"}, 404)
        elif path == '/api/search':
            q = params.get('q', [''])[0].strip().lower()
            if not q:
                self._json({"results": []})
                return
            index = load_index()
            results = []
            for vid, info in index.items():
                vp = get_video_path(vid)
                if not vp.exists():
                    continue
                data = json.loads(vp.read_text())
                full_text = data.get("full_text", "").lower()
                # Trouver les extraits pertinents
                excerpts = []
                segments = data.get("segments", [])
                for seg in segments:
                    if q in seg.get("text", "").lower():
                        excerpts.append({
                            "text": seg["text"],
                            "start": seg["start"],
                        })
                        if len(excerpts) >= 5:
                            break
                if excerpts:
                    results.append({
                        "video_id": vid,
                        "title": info.get("title", ""),
                        "url": info.get("url", ""),
                        "added_at": info.get("added_at", ""),
                        "match_count": len(excerpts),
                        "excerpts": excerpts,
                    })
            self._json({"results": results, "query": q})
        elif path == '/api/export':
            index = load_index()
            export = []
            for vid in index:
                vp = get_video_path(vid)
                if vp.exists():
                    export.append(json.loads(vp.read_text()))
            self._json({"export": export})
        elif path in ('/manifest.json', '/icon.svg', '/icon-192.png'):
            self._serve_static(path.lstrip('/'))
        else:
            self._json({"error": "Route inconnue"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/api/tags':
            body = self._read_body()
            name = body.get("name", "").strip().lower().replace(" ", "-")
            if not name:
                self._json({"error": "Nom de tag invalide"}, 400)
                return
            tags = load_tags()
            if name in tags:
                self._json({"error": "Ce tag existe déjà"}, 409)
                return
            tags.append(name)
            save_tags(tags)
            self._json({"success": True, "tag": name})
        elif path == '/api/add':
            body = self._read_body()
            url = body.get("url", "").strip()
            if not url:
                self._json({"error": "URL manquante"}, 400)
                return
            video_id = extract_video_id(url)
            if not video_id:
                self._json({"error": "URL YouTube invalide"}, 400)
                return

            index = load_index()
            if video_id in index:
                self._json({"error": "Déjà dans la bibliothèque", "video_id": video_id}, 409)
                return

            # Récupérer les infos (rapide : titre + description uniquement)
            import threading
            info = fetch_video_info_light(video_id)
            info["added_at"] = __import__("datetime").datetime.now().isoformat()

            # Sauvegarder immédiatement
            get_video_path(video_id).write_text(json.dumps(info, ensure_ascii=False, indent=2))
            index[video_id] = {
                "title": info["title"],
                "url": info["url"],
                "added_at": info["added_at"],
                "segment_count": info["segment_count"],
                "duration_sec": info["duration_sec"],
                "has_description": bool(info.get("description", "")),
                "transcript_pending": True,
            }
            save_index(index)

            # Lancer la récupération du transcript en arrière-plan
            t = threading.Thread(target=fetch_transcript_background, args=(video_id,), daemon=True)
            t.start()

            self._json({
                "success": True,
                "video_id": video_id,
                "title": info["title"],
                "segment_count": info["segment_count"],
            })
        elif re.match(r'^/api/video/[a-zA-Z0-9_-]+/tags$', path):
            video_id = path.split('/')[-2]
            body = self._read_body()
            new_tags = body.get("tags", [])
            # Valider que les tags existent
            valid_tags = load_tags()
            new_tags = [t for t in new_tags if t in valid_tags]
            # Mettre à jour l'index
            index = load_index()
            if video_id in index:
                index[video_id]["tags"] = new_tags
                save_index(index)
            # Mettre à jour le fichier vidéo complet
            vp = get_video_path(video_id)
            if vp.exists():
                data = json.loads(vp.read_text())
                data["tags"] = new_tags
                vp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            self._json({"success": True, "video_id": video_id, "tags": new_tags})
        elif re.match(r'^/api/video/[a-zA-Z0-9_-]+/transcript$', path):
            video_id = path.split('/')[-2]
            body = self._read_body()
            raw_text = body.get("text", "").strip()
            if not raw_text:
                self._json({"error": "Texte du transcript manquant"}, 400)
                return
            segments, full_text = parse_transcript_text(raw_text)
            duration = segments[-1]["start"] + segments[-1]["duration"] if segments else 0
            vp = get_video_path(video_id)
            if not vp.exists():
                self._json({"error": "Vidéo non trouvée"}, 404)
                return
            data = json.loads(vp.read_text())
            data["segments"] = segments
            data["full_text"] = full_text
            data["segment_count"] = len(segments)
            data["duration_sec"] = duration
            data["manual_transcript"] = True
            vp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            # Mettre à jour l'index
            index = load_index()
            if video_id in index:
                index[video_id]["segment_count"] = len(segments)
                index[video_id]["duration_sec"] = duration
                save_index(index)
            word_count = len(full_text.split())
            self._json({"success": True, "segment_count": len(segments), "word_count": word_count})
        elif re.match(r'^/api/video/[a-zA-Z0-9_-]+/refresh-description$', path):
            video_id = path.split('/')[-2]
            import subprocess
            result = subprocess.run(
                ["yt-dlp", "--skip-download", "--js-runtimes", "node", "--dump-json",
                 f"https://www.youtube.com/watch?v={video_id}"],
                timeout=15, capture_output=True, text=True,
            )
            if not result.stdout.strip():
                self._json({"error": "Impossible de récupérer la description"}, 400)
                return
            info = json.loads(result.stdout)
            description = info.get("description", "")
            # Mettre à jour le fichier vidéo
            vp = get_video_path(video_id)
            if vp.exists():
                data = json.loads(vp.read_text())
                data["description"] = description
                vp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            # Mettre à jour l'index
            index = load_index()
            if video_id in index:
                index[video_id]["has_description"] = bool(description)
                save_index(index)
            word_count = len(description.split())
            self._json({"success": True, "has_description": bool(description), "word_count": word_count})
        else:
            self._json({"error": "Route inconnue"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        if path == '/api/tags':
            # /api/tags?name=tagName
            params = parse_qs(parsed.query)
            name = params.get('name', [None])[0]
            if not name:
                self._json({"error": "Nom de tag requis (?name=...)"}, 400)
                return
            tags = load_tags()
            if name not in tags:
                self._json({"error": "Tag non trouvé"}, 404)
                return
            tags.remove(name)
            save_tags(tags)
            # Supprimer le tag de toutes les vidéos
            strip_tag_from_all_videos(name)
            self._json({"success": True, "tag": name})
        elif path.startswith('/api/delete/'):
            video_id = path.split('/')[-1]
            index = load_index()
            if video_id in index:
                del index[video_id]
                save_index(index)
                vp = get_video_path(video_id)
                if vp.exists():
                    vp.unlink()
                self._json({"success": True, "video_id": video_id})
            else:
                self._json({"error": "Vidéo non trouvée"}, 404)
        else:
            self._json({"error": "Route inconnue"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[YouTube] {args[0]} {args[1]} {args[2]}")


if __name__ == "__main__":
    port = 8083
    print(f"🚀 Serveur YouTube Veille — http://0.0.0.0:{port}")
    print(f"📁 Données sauvegardées dans : {DATA_DIR}")
    server = HTTPServer(("0.0.0.0", port), YouTubeAPIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Arrêt du serveur")
        server.server_close()
