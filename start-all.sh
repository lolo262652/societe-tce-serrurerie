#!/bin/bash
# Script de démarrage des serveurs LH TCE & Serrurerie
cd /root/societe-tce-serrurerie

# Ports: 8080 site, 8081 devis, 8082 jeux, 8083 youtube-veille, 8084 transcription

cleanup() {
    echo "Arrêt des serveurs..."
    pkill -f "python3 serveur.py" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "🚀 Démarrage des serveurs LH TCE..."

# 8080 - Site vitrine (fichiers statiques) 
cd /root/societe-tce-serrurerie
python3 -m http.server 8080 --bind 0.0.0.0 &
echo "   ✅ Site vitrine → :8080"

# 8081 - Devis Premium
cd /root/societe-tce-serrurerie/devis-app
python3 serveur.py &
echo "   ✅ Devis Premium → :8081"

# 8082 - Jeux (échecs, multi)
cd /root/societe-tce-serrurerie
python3 -m http.server 8082 --bind 0.0.0.0 &
python3 multiplayer_server.py &
echo "   ✅ Jeux → :8082"

# 8083 - Veille YouTube
cd /root/societe-tce-serrurerie/youtube-veille
python3 serveur.py &
echo "   ✅ Veille YouTube → :8083"

# 8084 - Transcription réunions
cd /root/societe-tce-serrurerie/transcription-app
python3 serveur.py &
echo "   ✅ Transcription → :8084"

echo ""
echo "📡 Tous les serveurs sont opérationnels !"
wait
