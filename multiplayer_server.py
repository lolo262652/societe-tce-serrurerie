#!/usr/bin/env python3
"""Serveur multijoueur pour les Échecs 3D — remplace http.server"""
import json, os, re, random, string, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

BASE = Path(__file__).parent
GAMES_FILE = BASE / "games.json"
games = {}  # room -> { white_token, black_token, moves, turn, created, started }

def load_games():
    global games
    if GAMES_FILE.exists():
        try:
            games = json.loads(GAMES_FILE.read_text())
        except:
            games = {}

def save_games():
    GAMES_FILE.write_text(json.dumps(games, indent=2, ensure_ascii=False))

def gen_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=16))

load_games()

class GameHandler(BaseHTTPRequestHandler):
    def _send(self, data, status=200, ctype='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', f'{ctype}; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if isinstance(data, str):
            self.wfile.write(data.encode())
        else:
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            return json.loads(self.rfile.read(length).decode())
        return {}

    def _serve_file(self, path):
        filepath = BASE / path.lstrip('/')
        if not filepath.exists() or not filepath.is_file():
            filepath = BASE / path.lstrip('/') / 'index.html'
        if not filepath.exists():
            self._send({"error": "Not found"}, 404)
            return
        ext = filepath.suffix.lower()
        types = {
            '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.gif': 'image/gif', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
            '.json': 'application/json', '.txt': 'text/plain',
        }
        ctype = types.get(ext, 'application/octet-stream')
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', f'{ctype}; charset=utf-8' if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.ico') else ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = parse_qs(parsed.query)

        # API routes
        if path == '/api/game/create':
            code = gen_code()
            token = gen_token()
            games[code] = {
                'white_token': token,
                'black_token': None,
                'moves': [],
                'turn': 'w',
                'created': time.time(),
                'started': False,
                'last_move_time': time.time(),
            }
            save_games()
            self._send({'room': code, 'token': token, 'color': 'w'})

        elif path == '/api/game/join':
            room = params.get('room', [None])[0]
            if not room or room not in games:
                self._send({'error': 'Salle introuvable'}, 404)
                return
            g = games[room]
            if g['started']:
                self._send({'error': 'Partie déjà commencée'}, 400)
                return
            if not g['black_token']:
                token = gen_token()
                g['black_token'] = token
                g['started'] = True
                save_games()
                self._send({'room': room, 'token': token, 'color': 'b'})
            else:
                self._send({'error': 'Salle pleine'}, 400)

        elif path == '/api/game/state':
            room = params.get('room', [None])[0]
            token = params.get('token', [None])[0]
            if not room or room not in games:
                self._send({'error': 'Salle introuvable'}, 404)
                return
            g = games[room]
            if token not in (g['white_token'], g['black_token']):
                self._send({'error': 'Non autorisé'}, 403)
                return
            color = 'w' if token == g['white_token'] else 'b'
            self._send({
                'room': room,
                'color': color,
                'turn': g['turn'],
                'moves': g['moves'],
                'started': g['started'],
                'white_connected': g['white_token'] is not None,
                'black_connected': g['black_token'] is not None,
                'last_move_time': g['last_move_time'],
            })

        elif path == '/api/game/poll':
            room = params.get('room', [None])[0]
            token = params.get('token', [None])[0]
            since = float(params.get('since', ['0'])[0])
            if not room or room not in games:
                self._send({'error': 'Salle introuvable'}, 404)
                return
            g = games[room]
            if token not in (g['white_token'], g['black_token']):
                self._send({'error': 'Non autorisé'}, 403)
                return
            new_moves = [m for m in g['moves'] if m['time'] > since]
            self._send({
                'new_moves': new_moves,
                'turn': g['turn'],
                'started': g['started'],
                'last_move_time': g['last_move_time'],
            })

        elif path.startswith('/api/'):
            self._send({'error': 'Route inconnue'}, 404)

        else:
            # Fichier statique — par défaut servir echecs.html
            if path == '' or path == '/':
                self._serve_file('/echecs.html')
            else:
                self._serve_file(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = parse_qs(parsed.query)

        if path == '/api/game/move':
            body = self._read_body()
            room = body.get('room') or params.get('room', [None])[0]
            token = body.get('token') or params.get('token', [None])[0]
            from_row = body.get('from_row')
            from_col = body.get('from_col')
            to_row = body.get('to_row')
            to_col = body.get('to_col')
            notation = body.get('notation', f'{from_row},{from_col}->{to_row},{to_col}')

            if not room or room not in games:
                self._send({'error': 'Salle introuvable'}, 404)
                return
            g = games[room]
            if token not in (g['white_token'], g['black_token']):
                self._send({'error': 'Non autorisé'}, 403)
                return
            
            player_color = 'w' if token == g['white_token'] else 'b'
            if player_color != g['turn']:
                self._send({'error': 'Ce n\'est pas votre tour'}, 400)
                return

            g['moves'].append({
                'color': player_color,
                'from_row': from_row,
                'from_col': from_col,
                'to_row': to_row,
                'to_col': to_col,
                'notation': notation,
                'time': time.time(),
            })
            g['turn'] = 'b' if g['turn'] == 'w' else 'w'
            g['last_move_time'] = time.time()
            save_games()
            self._send({'success': True})

        else:
            self._send({'error': 'Route inconnue'}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[Game] {args[0]} {args[1]} {args[2]}")

if __name__ == '__main__':
    port = 8082
    print(f'🚀 Serveur multijoueur sur http://0.0.0.0:{port}')
    server = HTTPServer(('0.0.0.0', port), GameHandler)
    server.serve_forever()
