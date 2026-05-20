#!/usr/bin/env python3
"""Serveur devis - SQLite, API, statique, signature client"""
import json, os, smtplib, ssl, uuid, base64, re, sqlite3, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

PORT = 8081
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "devis_premium.db")
DEVIS_DIR = os.path.join(BASE_DIR, "devis_sauvegardes")
PUBLIC_URL = "http://173.249.10.24:8081"

# ─── Base de données SQLite ──────────────────────────────

def init_db():
    """Crée les tables si elles n'existent pas et migre les données JSON"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS devis (
            id TEXT PRIMARY KEY,
            numero TEXT DEFAULT '',
            date TEXT DEFAULT '',
            type TEXT DEFAULT 'tce',
            tva REAL DEFAULT 20,
            remise REAL DEFAULT 0,
            acompte INTEGER DEFAULT 30,
            validite INTEGER DEFAULT 30,
            delai TEXT DEFAULT '',
            client_nom TEXT DEFAULT '',
            client_email TEXT DEFAULT '',
            client_adresse TEXT DEFAULT '',
            client_tel TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'brouillon',
            signature TEXT DEFAULT '',
            signature_text TEXT DEFAULT '',
            signed_at TEXT DEFAULT '',
            pieces TEXT DEFAULT '[]',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            accepted_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            pass TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS clients (
            email TEXT,
            nom TEXT DEFAULT '',
            telephone TEXT DEFAULT '',
            type_client TEXT DEFAULT 'particulier',
            siret TEXT DEFAULT '',
            adresse TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            PRIMARY KEY (email)
        );
        CREATE TABLE IF NOT EXISTS presets (
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            unite TEXT DEFAULT '',
            prix REAL DEFAULT 0,
            heures REAL DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            PRIMARY KEY (category, description)
        );
        CREATE TABLE IF NOT EXISTS company (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
    """)
    conn.commit()

    # Migration : ajouter colonne accepted_at si absente
    try:
        c.execute("ALTER TABLE devis ADD COLUMN accepted_at TEXT DEFAULT ''")
    except:
        pass

    # Migration : importer les JSON existants
    if os.path.exists(DEVIS_DIR):
        imported = 0
        for fname in sorted(os.listdir(DEVIS_DIR)):
            if fname.endswith(".json"):
                fpath = os.path.join(DEVIS_DIR, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        d = json.load(f)
                    did = d.get("id", fname.replace("devis_","").replace(".json",""))
                    existing = c.execute("SELECT id FROM devis WHERE id=?", (did,)).fetchone()
                    if not existing:
                        c.execute("""INSERT OR REPLACE INTO devis
                            (id, numero, date, type, tva, remise, acompte, validite, delai,
                             client_nom, client_email, client_adresse, client_tel, notes,
                             status, signature, signature_text, signed_at, pieces, created_at, accepted_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                            did,
                            d.get("numero",""),
                            d.get("date",""),
                            d.get("type","tce"),
                            d.get("tva",20),
                            d.get("remise",0),
                            d.get("acompte",30),
                            d.get("validite",30),
                            d.get("delai",""),
                            d.get("clientNom",""),
                            d.get("clientEmail",""),
                            d.get("clientAdresse",""),
                            d.get("clientTel",""),
                            d.get("notes",""),
                            d.get("status","brouillon"),
                            d.get("signature",""),
                            d.get("signatureText",""),
                            d.get("signedAt",""),
                            json.dumps(d.get("pieces",[]), ensure_ascii=False),
                            d.get("createdAt",""),
                            ""
                        ))
                        imported += 1
                except Exception as e:
                    print(f"[Migration] Erreur {fname}: {e}")
        if imported:
            conn.commit()
            print(f"[Migration] {imported} devis importés depuis JSON")

    # Migration : importer utilisateurs JSON
    users_file = os.path.join(BASE_DIR, "utilisateurs.json")
    if os.path.exists(users_file):
        try:
            with open(users_file, encoding="utf-8") as f:
                users = json.load(f)
            for u in users:
                c.execute("""INSERT OR IGNORE INTO users (email, name, pass, created_at)
                    VALUES (?,?,?,?)""", (
                    u.get("email",""),
                    u.get("name",""),
                    u.get("pass",""),
                    u.get("createdAt","")
                ))
            conn.commit()
            print(f"[Migration] {len(users)} utilisateurs importés")
        except Exception as e:
            print(f"[Migration] Erreur utilisateurs: {e}")

    conn.close()

init_db()

# ─── Fonctions base de données ──────────────────────────

DB = None  # lazy connection per thread

def get_db():
    global DB
    if DB is None:
        DB = sqlite3.connect(DB_PATH)
        DB.row_factory = sqlite3.Row
    return DB

def devis_to_dict(row):
    """Convertit une ligne SQLite en dict compatible avec le frontend"""
    if not row: return None
    d = dict(row)
    d["pieces"] = json.loads(d.get("pieces") or "[]")
    d["clientNom"] = d.pop("client_nom", "")
    d["clientEmail"] = d.pop("client_email", "")
    d["clientAdresse"] = d.pop("client_adresse", "")
    d["clientTel"] = d.pop("client_tel", "")
    d["signatureText"] = d.pop("signature_text", "")
    d["signedAt"] = d.pop("signed_at", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    d["acceptedAt"] = d.pop("accepted_at", "")
    d["type_client"] = None  # not needed for devis
    return d

def dict_to_devis(d):
    """Convertit un dict frontend en tuple pour SQLite"""
    return (
        d.get("id",""),
        d.get("numero",""),
        d.get("date",""),
        d.get("type","tce"),
        d.get("tva",20),
        d.get("remise",0),
        d.get("acompte",30),
        d.get("validite",30),
        d.get("delai",""),
        d.get("clientNom",""),
        d.get("clientEmail",""),
        d.get("clientAdresse",""),
        d.get("clientTel",""),
        d.get("notes",""),
        d.get("status","brouillon"),
        d.get("signature",""),
        d.get("signatureText",""),
        d.get("signedAt",""),
        json.dumps(d.get("pieces",[]), ensure_ascii=False),
        d.get("createdAt", datetime.datetime.now().isoformat()),
        datetime.datetime.now().isoformat(),
        d.get("acceptedAt","")
    )

# ─── Page publique de signature ─────────────────────────

SIGN_PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signature devis N°{NUM}</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:Arial,Helvetica,sans-serif;background:#f5f5f4;padding:20px;color:#1c1917}}
  .container{{max-width:750px;margin:0 auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
  h1{{font-size:1.4rem;margin-bottom:24px;color:#d97706}}
  h2{{font-size:1rem;margin:20px 0 12px;color:#57534e}}
  .piece-box{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;margin-bottom:16px}}
  .piece-box h3{{font-size:.95rem;margin-bottom:8px;color:#1c1917}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem}}
  th{{text-align:left;padding:6px 8px;border-bottom:2px solid #e7e5e4;color:#57534e;font-size:.8rem}}
  td{{padding:5px 8px;border-bottom:1px solid #e7e5e4}}
  .totaux{{background:#fffbeb;padding:16px 20px;border-radius:8px;margin:20px 0;text-align:right;font-size:.95rem}}
  .totaux div{{margin-bottom:4px;display:flex;justify-content:flex-end;gap:16px}}
  .totaux .total{{font-size:1.3rem;font-weight:800;color:#d97706;border-top:2px solid #d97706;padding-top:8px}}
  .sig-area{{border:2px dashed #d97706;border-radius:8px;padding:16px;margin:20px 0;text-align:center}}
  .sig-area canvas{{width:100%;max-width:500px;height:150px;border:1px solid #e7e5e4;border-radius:6px;cursor:crosshair;touch-action:none;display:block;margin:8px auto;background:#fff}}
  .btn{{display:inline-block;padding:12px 28px;border-radius:8px;font-weight:700;font-size:1rem;cursor:pointer;border:none;transition:all .2s;margin:4px}}
  .btn-primary{{background:#d97706;color:#fff}}
  .btn-primary:hover{{background:#b45309}}
  .btn-outline{{background:transparent;border:2px solid #d6d3d1;color:#57534e}}
  .btn-outline:hover{{border-color:#d97706;color:#d97706}}
  .btn-success{{background:#059669;color:#fff}}
  .btn-success:hover{{background:#047857}}
  .success-msg{{text-align:center;padding:40px 20px;display:none}}
  .success-msg .icon{{font-size:3rem;margin-bottom:16px}}
  .success-msg h2{{font-size:1.4rem;color:#059669}}
  .info{{text-align:center;font-size:.85rem;color:#57534e;margin:8px 0}}
  .conditions{{font-size:.85rem;color:#57534e;white-space:pre-wrap;line-height:1.5;margin:16px 0}}
  @media(max-width:600px){{.container{{padding:16px}} h1{{font-size:1.1rem}} body{{padding:8px}} }}
</style></head><body>
<div class="container" id="app">
  <h1>📄 Devis Premium N°{NUM}</h1>
  <p style="color:#57534e;margin-bottom:20px">Bonjour <strong>{CLIENT}</strong>, veuillez vérifier le devis ci-dessous et le signer.</p>
  <div id="devisContent">{CONTENT}</div>
  <div class="sig-area" id="sigSection">
    <strong>✍️ Signez ici</strong>
    <p style="font-size:.85rem;color:#57534e;margin:8px 0">Dessinez votre signature avec la souris ou le doigt</p>
    <canvas id="sigCanvas" width="500" height="150"></canvas>
    <button class="btn btn-outline" onclick="clearSig()">🗑️ Effacer</button>
    <button class="btn btn-primary" onclick="signer()" id="signBtn">✅ Signer le devis</button>
  </div>
  <div class="success-msg" id="successMsg">
    <div class="icon">✅</div>
    <h2>Devis signé !</h2>
    <p style="color:#57534e;margin-top:8px">Votre signature a été enregistrée. Un email de confirmation a été envoyé à l'artisan.</p>
  </div>
</div>
<script>
const DEVIS_ID = "{ID}";
let isDrawing = false;
let lastX = 0, lastY = 0;
function initCanvas() {{
  const c = document.getElementById("sigCanvas");
  if(!c) return;
  const ctx = c.getContext("2d");
  ctx.strokeStyle = "#1c1917";
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  function getPos(e) {{
    const rect = c.getBoundingClientRect();
    return {{
      x: ((e.clientX||(e.touches&&e.touches[0].clientX)) - rect.left) * (c.width/rect.width),
      y: ((e.clientY||(e.touches&&e.touches[0].clientY)) - rect.top) * (c.height/rect.height)
    }};
  }}
  function start(e) {{ e.preventDefault(); isDrawing = true; const p = getPos(e); lastX = p.x; lastY = p.y; }}
  function move(e) {{ e.preventDefault(); if(!isDrawing) return; const p = getPos(e); ctx.beginPath(); ctx.moveTo(lastX,lastY); ctx.lineTo(p.x,p.y); ctx.stroke(); lastX = p.x; lastY = p.y; }}
  function stop(e) {{ isDrawing = false; }}
  c.addEventListener("mousedown", start);
  c.addEventListener("mousemove", move);
  c.addEventListener("mouseup", stop);
  c.addEventListener("mouseleave", stop);
  c.addEventListener("touchstart", start, {{passive:false}});
  c.addEventListener("touchmove", move, {{passive:false}});
  c.addEventListener("touchend", stop, {{passive:false}});
}}
function clearSig() {{
  const c = document.getElementById("sigCanvas");
  if(!c) return;
  c.getContext("2d").clearRect(0,0,c.width,c.height);
}}
function signer() {{
  const c = document.getElementById("sigCanvas");
  const dataUrl = c.toDataURL("image/png");
  const pixels = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
  let filled = false;
  for(let i=3; i<pixels.length; i+=4) {{ if(pixels[i] > 0) {{ filled = true; break; }} }}
  if(!filled) {{ alert("Veuillez dessiner votre signature avant de signer."); return; }}
  document.getElementById("signBtn").disabled = true;
  document.getElementById("signBtn").textContent = "⏳ Envoi en cours...";
  fetch("/api/devis/"+DEVIS_ID+"/sign", {{
    method:"POST", headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{signature: dataUrl}})
  }})
  .then(r=>r.json())
  .then(res => {{
    if(res.ok) {{
      document.getElementById("sigSection").style.display = "none";
      document.getElementById("successMsg").style.display = "block";
      const img = document.createElement("img");
      img.src = dataUrl;
      img.style.maxHeight = "60px";
      img.style.display = "block";
      img.style.margin = "20px auto 0";
      img.alt = "Signature";
      document.getElementById("successMsg").after(img);
    }} else {{
      alert("Erreur : "+res.message);
      document.getElementById("signBtn").disabled = false;
      document.getElementById("signBtn").textContent = "✅ Signer le devis";
    }}
  }})
  .catch(() => {{
    alert("Erreur de connexion. Veuillez réessayer.");
    document.getElementById("signBtn").disabled = false;
    document.getElementById("signBtn").textContent = "✅ Signer le devis";
  }});
}}
initCanvas();
</script></body></html>"""

ACCEPT_PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acceptation devis N°{NUM}</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:Arial,Helvetica,sans-serif;background:#f5f5f4;padding:20px;color:#1c1917;display:flex;min-height:100vh;align-items:center;justify-content:center}}
  .card{{max-width:520px;width:100%;background:#fff;border-radius:16px;padding:48px 36px;box-shadow:0 4px 30px rgba(0,0,0,.1);text-align:center}}
  .icon{{font-size:4rem;margin-bottom:16px}}
  h1{{font-size:1.6rem;margin-bottom:8px}}
  .sub{{color:#57534e;margin-bottom:24px;font-size:.95rem}}
  .btn{{display:inline-block;padding:16px 36px;border-radius:10px;font-weight:700;font-size:1.1rem;cursor:pointer;border:none;text-decoration:none;transition:all .2s;margin:6px}}
  .btn-success{{background:#059669;color:#fff;width:100%}}
  .btn-success:hover{{background:#047857}}
  .btn-outline{{background:transparent;border:2px solid #d6d3d1;color:#57534e;width:100%}}
  .btn-outline:hover{{border-color:#059669;color:#059669}}
  .success-box{{display:none;padding:32px 0}}
  .success-box h2{{color:#059669;font-size:1.3rem;margin-bottom:8px}}
  .footer{{font-size:.8rem;color:#a8a29e;margin-top:32px}}
</style></head><body>
<div class="card">
  <div class="icon">📄</div>
  <h1>Devis N°{NUM}</h1>
  <p class="sub">Bonjour <strong>{CLIENT}</strong>, souhaitez-vous accepter ce devis ?</p>
  <div id="actionBox">
    <p style="margin-bottom:16px;font-size:.9rem;color:#57534e">
      En cliquant sur « Accepter », vous confirmez votre accord sur les travaux décrits dans le devis.<br><br>
      <span style="font-size:.85rem;background:#f0fdf4;display:block;padding:12px;border-radius:8px;border:1px solid #bbf7d0">
        ✅ Un email de confirmation sera envoyé à l'artisan
      </span>
    </p>
    <button class="btn btn-success" onclick="accepter()" id="acceptBtn">✅ Accepter le devis</button>
  </div>
  <div class="success-box" id="successBox">
    <div style="font-size:3rem;margin-bottom:12px">🎉</div>
    <h2>Devis accepté !</h2>
    <p style="color:#57534e;margin-top:8px">Merci {CLIENT}, votre acceptation a bien été enregistrée. Un email de confirmation a été envoyé à l'artisan.</p>
    <p style="color:#a8a29e;font-size:.85rem;margin-top:16px">Vous recevrez prochainement le devis signé par email.</p>
  </div>
</div>
<script>
function accepter() {{
  document.getElementById("acceptBtn").disabled = true;
  document.getElementById("acceptBtn").textContent = "⏳ Enregistrement...";
  fetch("/api/devis/{ID}/accept", {{
    method:"POST", headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{}})
  }})
  .then(r=>r.json())
  .then(res => {{
    if(res.ok) {{
      document.getElementById("actionBox").style.display = "none";
      document.getElementById("successBox").style.display = "block";
    }} else {{
      alert("Erreur : "+res.message);
      document.getElementById("acceptBtn").disabled = false;
      document.getElementById("acceptBtn").textContent = "✅ Accepter le devis";
    }}
  }})
  .catch(() => {{
    alert("Erreur de connexion. Veuillez réessayer.");
    document.getElementById("acceptBtn").disabled = false;
    document.getElementById("acceptBtn").textContent = "✅ Accepter le devis";
  }});
}}
</script>
</body></html>"""

class DevisHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        m = re.match(r"^/accepter/([a-zA-Z0-9_-]+)$", path)
        if m:
            self._page_accepter(m.group(1))
            return
        m = re.match(r"^/devis/([a-zA-Z0-9_-]+)$", path)
        if m:
            self._page_signature(m.group(1))
            return
        m = re.match(r"^/api/devis/([a-zA-Z0-9_-]+)$", path)
        if m:
            self._get_devis(m.group(1))
            return
        if path == "/api/devis":
            self._liste_devis()
            return
        if path == "/api/users":
            self._get_users()
            return
        if path == "/api/clients":
            self._get_clients()
            return
        if path == "/api/presets":
            self._get_presets()
            return
        if path == "/api/company":
            self._get_company()
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        if path == "/api/envoyer":
            self._envoyer_devis(body)
        elif path == "/api/sauvegarder":
            self._sauvegarder_devis(body)
        elif path == "/api/devis":
            self._liste_devis()
        elif path == "/api/users":
            self._save_users(body)
        elif path == "/api/clients":
            self._save_clients(body)
        elif path == "/api/presets":
            self._save_presets(body)
        elif path == "/api/company":
            self._save_company(body)
        elif path == "/api/sync":
            self._sync_all(body)
        else:
            m = re.match(r"^/api/devis/([a-zA-Z0-9_-]+)/sign$", path)
            if m:
                self._signer_devis(m.group(1), body)
            else:
                m = re.match(r"^/api/devis/([a-zA-Z0-9_-]+)/accept$", path)
                if m:
                    self._accepter_devis(m.group(1), body)
                else:
                    self.send_error(404)

    # ── Pages publiques ──────────────────────────────

    def _page_accepter(self, devis_id):
        db = get_db()
        row = db.execute("SELECT * FROM devis WHERE id=?", (devis_id,)).fetchone()
        if not row:
            self.send_error(404, "Devis introuvable")
            return
        d = devis_to_dict(row)
        now = datetime.datetime.now()
        db.execute("UPDATE devis SET status=?, accepted_at=?, updated_at=? WHERE id=?",
            ("accepte", now.isoformat(), now.isoformat(), devis_id))
        db.commit()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(ACCEPT_PAGE.format(
            NUM=d.get("numero", devis_id),
            CLIENT=d.get("clientNom", "Client"),
            ID=devis_id
        ).encode("utf-8"))
        # Notification email
        try:
            msg = MIMEText(
                f"✅ Le client {d.get('clientNom','Client')} a ACCEPTÉ le devis N°{d.get('numero','XXX')} en ligne.\n\n"
                f"Consultez-le sur votre tableau de bord : {PUBLIC_URL}/index.html\n\n"
                f"Lien direct : {PUBLIC_URL}/devis/{devis_id}",
                "plain", "utf-8"
            )
            msg["From"] = "Laurent Habib <laurent.habib@gmail.com>"
            msg["To"] = "laurent.habib@gmail.com"
            msg["Subject"] = f"✅ Devis Premium N°{d.get('numero','XXX')} accepté par {d.get('clientNom','Client')}"
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
                s.login("laurent.habib@gmail.com", "pahn pksy fhfi bpvt")
                s.sendmail("laurent.habib@gmail.com", ["laurent.habib@gmail.com"], msg.as_string())
        except:
            pass

    def _page_signature(self, devis_id):
        db = get_db()
        row = db.execute("SELECT * FROM devis WHERE id=?", (devis_id,)).fetchone()
        if not row:
            self.send_error(404, "Devis introuvable")
            return
        d = devis_to_dict(row)
        content = self._devis_html(d)
        page = SIGN_PAGE.format(
            NUM=d.get("numero", devis_id),
            CLIENT=d.get("clientNom", "Client"),
            CONTENT=content,
            ID=devis_id
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def _devis_html(self, d):
        parts = []
        pieces = d.get("pieces", [])
        tva = d.get("tva", 20)
        totalHT = 0
        for p in pieces:
            pHT = 0
            pH = 0
            rows = ""
            for l in p.get("lignes", []):
                if l.get("active") is False: continue
                t = l["quantite"] * l["prixUnitaire"]
                h = (l.get("heures", 0) or 0) * l["quantite"]
                pHT += t
                pH += h
                totalHT += t
                hStr = f"{h:.1f}h".replace('.',',') if h > 0 else "—"
                rows += f"<tr><td>{self._h(l['description'])}</td><td style='text-align:center'>{l['quantite']}</td><td style='text-align:center'>{self._h(l['unite'])}</td><td style='text-align:right'>{self._f(l['prixUnitaire'])}</td><td style='text-align:center'>{hStr}</td><td style='text-align:right;font-weight:700'>{self._f(t)}</td></tr>"
            hTotalStr = f" | ⏱ {pH:.1f}h".replace('.',',') if pH > 0 else ""
            parts.append(f"""<div class="piece-box"><h3>🏠 {self._h(p['nom'])} <span style="font-weight:400;color:#57534e">— {self._f(pHT)} HT{hTotalStr}</span></h3><table><thead><tr><th>Description</th><th>Qté</th><th>Unité</th><th>Prix unit.</th><th>Temps</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table></div>""")
        mtTva = totalHT * tva / 100
        ttc = totalHT + mtTva
        remisePct = d.get("remise", 0)
        remiseMt = totalHT * remisePct / 100
        netHT = totalHT - remiseMt
        netTtc = netHT + (netHT * tva / 100)
        totalH = sum((l.get("heures", 0) or 0) * l["quantite"] for p in pieces for l in p.get("lignes", []) if l.get("active") is not False)
        tempsRow = f"<div style='margin-top:6px;font-size:.9rem;color:#d97706;border-top:1px solid #fde68a;padding-top:6px'><span>⏱ Temps total estimé</span><span>{(totalH):.1f}h</span></div>" if totalH > 0 else ""
        remiseRow = f"<div style='display:flex;justify-content:space-between;font-size:.9rem'><span>Remise {remisePct:.0f}%</span><span style='color:#dc2626'>-{self._f(remiseMt)}</span></div><div style='display:flex;justify-content:space-between;font-size:.9rem'><span>Net HT</span><span>{self._f(netHT)}</span></div>" if remiseMt > 0 else ""
        acompte = netTtc * (d.get("acompte", 30)) / 100 if remiseMt > 0 else ttc * (d.get("acompte", 30)) / 100
        solde = netTtc - acompte if remiseMt > 0 else ttc - acompte
        totals = f"""<div class="totaux">
          <div><span>Total HT</span><span>{self._f(totalHT)}</span></div>
          {remiseRow}
          <div><span>TVA {tva}%</span><span>{self._f(mtTva)}</span></div>
          <div class="total"><span>Total TTC</span><span>{self._f(remiseMt and netTtc or ttc)}</span></div>
          {tempsRow}
          <div style="font-size:.85rem;margin-top:8px;border-top:1px solid #e7e5e4;padding-top:8px"><span>Acompte {d.get('acompte',30)}%</span><span>{self._f(acompte)}</span></div>
          <div style="font-size:.85rem"><span>Solde à la livraison</span><span>{self._f(solde)}</span></div>
        </div>"""
        notes = f"""<div class="conditions">{self._h(d.get('notes', ''))}</div>""" if d.get("notes") else ""
        return "".join(parts) + totals + notes

    # ── API Devis ────────────────────────────────────

    def _sauvegarder_devis(self, body):
        try:
            data = json.loads(body)
            devis_id = data.get("id") or str(uuid.uuid4())[:8]
            data["id"] = devis_id
            db = get_db()
            # Conserver la signature existante si pas envoyée
            if not data.get("signature"):
                existing = db.execute("SELECT signature, signature_text, signed_at FROM devis WHERE id=?", (devis_id,)).fetchone()
                if existing:
                    data.setdefault("signature", existing["signature"] or "")
                    data.setdefault("signatureText", existing["signature_text"] or "")
                    data.setdefault("signedAt", existing["signed_at"] or "")
            vals = dict_to_devis(data)
            db.execute("""INSERT OR REPLACE INTO devis
                (id, numero, date, type, tva, remise, acompte, validite, delai,
                 client_nom, client_email, client_adresse, client_tel, notes,
                 status, signature, signature_text, signed_at, pieces, created_at, updated_at, accepted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
            db.commit()
            self._json({"ok": True, "id": devis_id})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    def _get_devis(self, devis_id):
        db = get_db()
        row = db.execute("SELECT * FROM devis WHERE id=?", (devis_id,)).fetchone()
        if not row:
            self.send_error(404, "Devis introuvable")
            return
        self._json(devis_to_dict(row))

    def _liste_devis(self):
        db = get_db()
        rows = db.execute("SELECT * FROM devis ORDER BY created_at DESC").fetchall()
        self._json([devis_to_dict(r) for r in rows])

    def _signer_devis(self, devis_id, body):
        try:
            data = json.loads(body)
            signature = data.get("signature", "")
            db = get_db()
            row = db.execute("SELECT * FROM devis WHERE id=?", (devis_id,)).fetchone()
            if not row:
                self._json({"ok": False, "message": "Devis introuvable"})
                return
            d = devis_to_dict(row)
            now = datetime.datetime.now().isoformat()
            db.execute("""UPDATE devis SET signature=?, signature_text=?, status=?, signed_at=?, updated_at=?
                WHERE id=?""", (signature, d.get("clientNom",""), "signe", now, now, devis_id))
            db.commit()
            # Notification email
            try:
                msg = MIMEText(
                    f"✅ Le client {d.get('clientNom','Client')} a signé le devis N°{d.get('numero','XXX')} en ligne.\n\n"
                    f"Consultez-le sur votre tableau de bord : {PUBLIC_URL}/index.html\n\n"
                    f"Lien direct : {PUBLIC_URL}/devis/{devis_id}",
                    "plain", "utf-8"
                )
                msg["From"] = "Laurent Habib <laurent.habib@gmail.com>"
                msg["To"] = "laurent.habib@gmail.com"
                msg["Subject"] = f"✅ Devis Premium N°{d.get('numero','XXX')} signé par {d.get('clientNom','Client')}"
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
                    s.login("laurent.habib@gmail.com", "pahn pksy fhfi bpvt")
                    s.sendmail("laurent.habib@gmail.com", ["laurent.habib@gmail.com"], msg.as_string())
            except:
                pass
            self._json({"ok": True, "message": "Devis signé !"})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    def _accepter_devis(self, devis_id, body):
        try:
            db = get_db()
            row = db.execute("SELECT * FROM devis WHERE id=?", (devis_id,)).fetchone()
            if not row:
                self._json({"ok": False, "message": "Devis introuvable"})
                return
            d = devis_to_dict(row)
            now = datetime.datetime.now().isoformat()
            db.execute("UPDATE devis SET status=?, accepted_at=?, updated_at=? WHERE id=?",
                ("accepte", now, now, devis_id))
            db.commit()
            # Notification email
            try:
                msg = MIMEText(
                    f"✅ Le client {d.get('clientNom','Client')} a ACCEPTÉ le devis N°{d.get('numero','XXX')} en ligne.\n\n"
                    f"Consultez-le sur votre tableau de bord : {PUBLIC_URL}/index.html\n\n"
                    f"Lien direct : {PUBLIC_URL}/devis/{devis_id}",
                    "plain", "utf-8"
                )
                msg["From"] = "Laurent Habib <laurent.habib@gmail.com>"
                msg["To"] = "laurent.habib@gmail.com"
                msg["Subject"] = f"✅ Devis Premium N°{d.get('numero','XXX')} accepté par {d.get('clientNom','Client')}"
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
                    s.login("laurent.habib@gmail.com", "pahn pksy fhfi bpvt")
                    s.sendmail("laurent.habib@gmail.com", ["laurent.habib@gmail.com"], msg.as_string())
            except:
                pass
            self._json({"ok": True, "message": "Devis accepté !"})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    def _envoyer_devis(self, body):
        try:
            data = json.loads(body)
            client_email = data["client_email"]
            devis_id = data.get("devis_id", "")
            sujet = data.get("sujet", f"Devis N°{data.get('numero', 'XXX')}")
            html_content = data["html"]
            if devis_id:
                sign_link = f"{PUBLIC_URL}/devis/{devis_id}"
                accept_link = f"{PUBLIC_URL}/accepter/{devis_id}"
                html_content = html_content.replace(
                    "</body>",
                    f"""<p style="margin:24px 0 0;padding:20px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;text-align:center;font-size:.95rem">
                    <strong>📋 Que souhaitez-vous faire ?</strong><br><br>
                    <table style="width:100%;border-collapse:collapse" role="presentation">
                    <tr>
                      <td style="padding:8px;text-align:center;width:50%">
                        <a href="{accept_link}" style="display:inline-block;padding:14px 24px;background:#059669;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:.95rem">
                        ✅ Accepter le devis<br><span style="font-weight:400;font-size:.8rem">1 clic, sans signature</span></a>
                      </td>
                      <td style="padding:8px;text-align:center;width:50%">
                        <a href="{sign_link}" style="display:inline-block;padding:14px 24px;background:#d97706;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;font-size:.95rem">
                        ✍️ Signer le devis<br><span style="font-weight:400;font-size:.8rem">Avec signature dessinée</span></a>
                      </td>
                    </tr>
                    </table>
                    <span style="font-size:.85rem;color:#57534e;display:block;margin-top:12px">
                    🔗 <strong>Accepter</strong> : {accept_link}<br>
                    🔗 <strong>Signer</strong> : {sign_link}
                    </span>
                    </p></body>""",
                    1
                )
            msg = MIMEMultipart("alternative")
            msg["From"] = "Laurent Habib <laurent.habib@gmail.com>"
            msg["To"] = client_email
            msg["Subject"] = sujet
            msg.attach(MIMEText(html_content, "html", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
                s.login("laurent.habib@gmail.com", "pahn pksy fhfi bpvt")
                s.sendmail("laurent.habib@gmail.com", [client_email], msg.as_string())
            self._json({"ok": True, "message": f"✅ Devis envoyé à {client_email} (avec lien de signature)"})
        except Exception as e:
            self._json({"ok": False, "message": f"❌ Erreur : {str(e)}"})

    # ── API Utilisateurs ─────────────────────────────

    def _get_users(self):
        db = get_db()
        rows = db.execute("SELECT * FROM users").fetchall()
        self._json([dict(r) for r in rows])

    def _save_users(self, body):
        try:
            users = json.loads(body)
            db = get_db()
            db.execute("DELETE FROM users")
            for u in users:
                db.execute("INSERT OR REPLACE INTO users (email, name, pass, created_at) VALUES (?,?,?,?)",
                    (u.get("email",""), u.get("name",""), u.get("pass",""), u.get("createdAt","")))
            db.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    # ── API Clients ──────────────────────────────────

    def _get_clients(self):
        db = get_db()
        rows = db.execute("SELECT * FROM clients ORDER BY nom").fetchall()
        clients = []
        for r in rows:
            c = dict(r)
            c["type"] = c.pop("type_client", "particulier")
            clients.append(c)
        self._json(clients)

    def _save_clients(self, body):
        try:
            clients = json.loads(body)
            db = get_db()
            db.execute("DELETE FROM clients")
            for c in clients:
                db.execute("""INSERT OR REPLACE INTO clients
                    (email, nom, telephone, type_client, siret, adresse, notes, created_at)
                    VALUES (?,?,?,?,?,?,?,?)""", (
                    c.get("email",""),
                    c.get("nom",""),
                    c.get("telephone",""),
                    c.get("type","particulier"),
                    c.get("siret",""),
                    c.get("adresse",""),
                    c.get("notes",""),
                    c.get("createdAt","")
                ))
            db.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    # ── API Présélections ───────────────────────────

    def _get_presets(self):
        db = get_db()
        rows = db.execute("SELECT * FROM presets ORDER BY category, sort_order").fetchall()
        result = {}
        for r in rows:
            cat = r["category"]
            if cat not in result:
                result[cat] = []
            result[cat].append([r["description"], r["unite"], r["prix"], r["heures"]])
        self._json(result)

    def _save_presets(self, body):
        try:
            presets = json.loads(body)  # dict category → [[desc, unite, prix, heures], ...]
            db = get_db()
            db.execute("DELETE FROM presets")
            order = 0
            for cat, items in presets.items():
                for item in items:
                    db.execute("""INSERT INTO presets (category, description, unite, prix, heures, sort_order)
                        VALUES (?,?,?,?,?,?)""", (cat, item[0], item[1], item[2], item[3] if len(item) > 3 else 0, order))
                    order += 1
            db.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    # ── API Entreprise ───────────────────────────────

    def _get_company(self):
        db = get_db()
        rows = db.execute("SELECT * FROM company").fetchall()
        result = {r["key"]: r["value"] for r in rows}
        self._json(result)

    def _save_company(self, body):
        try:
            data = json.loads(body)
            db = get_db()
            for key, value in data.items():
                db.execute("INSERT OR REPLACE INTO company (key, value) VALUES (?,?)", (key, str(value)))
            db.commit()
            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    # ── Sync tout-en-un ──────────────────────────────

    def _sync_all(self, body):
        """Réception de TOUTES les données du navigateur en une requête"""
        try:
            data = json.loads(body)
            db = get_db()
            # Sync users
            if "users" in data:
                db.execute("DELETE FROM users")
                for u in data["users"]:
                    db.execute("INSERT OR REPLACE INTO users (email, name, pass, created_at) VALUES (?,?,?,?)",
                        (u.get("email",""), u.get("name",""), u.get("pass",""), u.get("createdAt","")))
            # Sync clients
            if "clients" in data:
                db.execute("DELETE FROM clients")
                for c in data["clients"]:
                    db.execute("""INSERT OR REPLACE INTO clients
                        (email, nom, telephone, type_client, siret, adresse, notes, created_at)
                        VALUES (?,?,?,?,?,?,?,?)""", (
                        c.get("email",""), c.get("nom",""), c.get("telephone",""),
                        c.get("type","particulier"), c.get("siret",""), c.get("adresse",""),
                        c.get("notes",""), c.get("createdAt","")))
            # Sync presets
            if "presets" in data:
                db.execute("DELETE FROM presets")
                order = 0
                for cat, items in data["presets"].items():
                    for item in items:
                        db.execute("""INSERT INTO presets (category, description, unite, prix, heures, sort_order)
                            VALUES (?,?,?,?,?,?)""", (cat, item[0], item[1], item[2], item[3] if len(item) > 3 else 0, order))
                        order += 1
            # Sync company
            if "company" in data:
                for key, value in data["company"].items():
                    db.execute("INSERT OR REPLACE INTO company (key, value) VALUES (?,?)", (key, str(value)))
            db.commit()
            self._json({"ok": True, "message": "Synchronisation réussie"})
        except Exception as e:
            self._json({"ok": False, "message": str(e)})

    # ── Utilitaires ──────────────────────────────────

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    @staticmethod
    def _h(s): return str(s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    @staticmethod
    def _f(n): return f"{(n or 0):.2f}".replace(".",",") + " €"

    def log_message(self, fmt, *args):
        print(f"[Devis] {args[0]} {args[1]} {args[2]}")

if __name__ == "__main__":
    os.chdir(BASE_DIR)
    srv = HTTPServer(("0.0.0.0", PORT), DevisHandler)
    print(f"✅ Serveur devis (SQLite) lancé sur http://173.249.10.24:{PORT}")
    print(f"   📝 Signature client : http://173.249.10.24:{PORT}/devis/<ID>")
    print(f"   🗄️  Base : {DB_PATH}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
