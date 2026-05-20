# 🎮 Prompt global — Jeu d'échecs complet

> **Stack** : HTML/CSS/JS pur (1 seul fichier) + Three.js (CDN)
> **Hébergé sur** : `http://173.249.10.24:8082`
> **Port 8082** : jeux divers (serveur Python statique)

---

Crée un jeu d'échecs complet en un seul fichier HTML (CSS + JS inline) avec les fonctionnalités suivantes :

## Plateau & Règles
- Échiquier 8×8 standard avec pièces unicode (♔♕♖♗♘♙ / ♚♛♜♝♞♟)
- Clic pour sélectionner une pièce, surbrillance des coups possibles (rond bleu pour déplacement, cercle rouge pour capture)
- Rocs (petit et grand), prise en passant
- Promotion automatique en dame
- Détection des échecs, échec et mat, pat
- Dernier coup surligné en jaune

## IA
- IA adverse (joueur = Blancs, IA = Noirs) avec minimax + élagage alpha-bêta
- Évaluation basée sur les valeurs des pièces + tables de position
- Option "Auto-jouer" (IA contre IA)

## Mode Aventure 🎲
- Cases Explosion 💥 : la pièce qui atterrit dessus disparaît
- Cases Mutation 🔀 : la pièce se transforme au choix du joueur (overlay de sélection : Dame, Tour, Fou, Cavalier)
- Cases générées aléatoirement à chaque nouvelle partie
- Toggle ON/OFF dans la sidebar

## Vue 3D (deux modes)
- **Perspective 3D** : plateau en perspective via CSS transforms avec animation flottante, cases qui se soulèvent au survol
- **Vue 3D Libre (Three.js)** :
  - Pièces modélisées en volume (cylindres, cônes, sphères, parallélépipèdes) avec matériaux blancs/noirs
  - Contrôle caméra orbital (drag pour tourner, molette pour zoomer, tactile supporté)
  - Éclairage directionnel avec ombres (PCF Soft)
  - Clic sur une case ou une pièce 3D pour jouer
  - Anneau de sélection et indicateurs de coups valides en 3D

## Multijoueur distant
- Créer une salle avec un code
- Rejoindre une partie avec un code
- Synchronisation des coups en temps réel

## Interface
- Design sombre élégant (#1a1a2e / #16213e / #0f3460)
- Sidebar avec : toggle mode aventure, toggle vues 3D, contrôles multijoueur, historique des coups (défilant), pièces capturées
- Boutons : Nouvelle partie, Annuler, Auto-jouer
- Responsive mobile
- Statut du tour avec indicateur couleur
