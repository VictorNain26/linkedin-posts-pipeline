# LinkedIn Posts Auto-Pipeline

> Mon pipeline autonome qui poste 2 carrousels LinkedIn par semaine, sans que j'aie à intervenir.
> Construit pour mon compte de freelance, partagé en open source.

[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13.13-blue.svg)](#)
[![Anthropic](https://img.shields.io/badge/Claude-Sonnet_4.6_%2B_Haiku_4.5-orange.svg)](#)
[![LinkedIn API](https://img.shields.io/badge/LinkedIn_API-v202604-0077B5.svg)](#)

## Ce que ça fait

1. **Veille RSS** chaque mardi et jeudi à 10h30 (5 sources : Anthropic, OpenAI, HuggingFace, TLDR AI, Le Monde Informatique)
2. **8 agents Claude** transforment l'actu en contenu :
   1. **Pain Excavator** (Sonnet) — identifie 3 douleurs prospect
   2. **Angle Scout** (Sonnet) — angle contre-intuitif + hook visuel slide 1
   3. **Slide Architect** (Sonnet) — structure 7 slides
   4. **Victor's Pen** (Sonnet) — réécrit dans ma voix authentique (anti-IA)
   5. **Anti-AI Detector** (Sonnet) — supprime les patterns LLM (em-dash, "concrètement", buzzwords) avec retry-with-feedback
   6. **Hook Generator** (Sonnet) — produit 3 hooks (contrarian / data / narrative)
   7. **Hook Judge** (Haiku) — sélectionne le meilleur
   8. **Engagement Comment Writer** (Haiku) — rédige le 1er commentaire (question + lien profil)
3. **Format selector** déterministe : carrousel par défaut, switch text/poll après 3 carrousels consécutifs (best practice 2026)
4. **PDF carrousel 1080×1350** (portrait optimal mobile feed 2026) via Puppeteer
5. **Publication LinkedIn** via Community Management API `/rest/posts` (v202604)
6. **1er commentaire** posté 30s après le post (valeur ajoutée, pas teaser)
7. **Analytics** quotidiennes (10 métriques : impressions, reach, reactions, saves, link clicks, profile views, followers gained...)
8. **Rapport hebdomadaire** envoyé par mail chaque lundi : best post, formule de hook gagnante, stats détaillées

## Pourquoi je l'ai construit

J'en avais marre de :
- Écrire un post LinkedIn par semaine en me forçant
- Laisser passer l'actu IA qui tombe pendant que je code
- Avoir l'impression de produire du contenu qui sonne "généré par IA"
- Ne pas savoir quelle formule de hook marche pour mon audience

Je l'ai construit pour moi en mai 2026. Je rends le code public pour deux raisons :
1. Montrer concrètement ce que je sais faire (intégration IA, pipelines multi-agents, conformité API)
2. Permettre à quelqu'un d'autre de l'adapter à son cas

**Je suis dev freelance — intégration IA + fullstack à Paris. Si tu veux une version adaptée à ton entreprise ou un projet IA similaire, contacte-moi sur [victorlenain.fr](https://victorlenain.fr).**

## Stack & best practices appliquées

### LinkedIn algorithm 2026
| Pratique | Source |
|---|---|
| Carrousel format core (24% engagement, 3.5× reach) | [Oktopost 2026](https://www.oktopost.com/blog/linkedin-carousel-pdf-best-practices/) |
| Mix carrousel/text/poll 1×/2sem | [CarouselMaker 2026](https://carouselmaker.co/en/blog/linkedin-carousels-vs-text-posts-vs-videos) |
| Hook 150-200 chars, 3 formules (contrarian/data/narrative) | [finallayer 2026](https://finallayer.com/blog/linkedin-hook-frameworks) |
| Format 1080×1350 portrait | [Wavegen 2026](https://wavegen.ai/linkedin-carousel-size) |
| 1er commentaire = valeur ajoutée (lien-spam pénalisé en 2026) | [HypergrowthAI 2026](https://medium.com/@HypergrowthAI/5-steps-to-10x-your-linkedin-reach-in-2026-comments-now-count-2x-more-than-likes-aa6d5bedf60d) |
| Sweet spot publication B2B FR : 10h-12h mar-jeu | [Buffer 2026](https://buffer.com/resources/best-time-to-post-on-linkedin/) |

### Architecture multi-agent (Anthropic Claude CCA-F)
- **`tool_use` + JSON Schema** forcé sur tous les agents → zéro parsing libre
- **Retry-with-feedback** sur l'Anti-AI Detector (max 2 tentatives)
- **Retry exponentiel** sur erreurs API transitoires (429, 5xx)
- **Hub-and-spoke** : un orchestrateur, 8 agents spécialisés
- **Modèle adapté à la tâche** : Sonnet pour la créativité, Haiku pour sélection/structuration → ~30% économie tokens sans perte qualité

## Architecture

```
RSS sources ──► rss_fetch (Haiku scoring tool_use)
                    │
                    ▼
            generate_post (8 agents séquentiels)
                    │
                    ▼
            format_selector (carousel | text | poll)
                    │
                    ▼
            html_to_pdf.js (si carousel — Puppeteer 1080×1350)
                    │
                    ▼
            linkedin_post (Community Management API /rest/posts)
                    │
                    ▼
            sleep 30s → post_first_comment
                    │
                    ▼
            history.py (SQLite 4 tables)

Crons indépendants :
- fetch_analytics.sh (daily 21h)  → linkedin_analytics.py
- weekly_report.sh (lundi 7h)     → weekly_report.py + Gmail SMTP
- healthcheck.sh (daily 8h)
```

## Démarrage rapide

### Option 1 — Docker (recommandé)

```bash
git clone https://github.com/VictorNain26/linkedin-posts-pipeline.git
cd linkedin-posts-pipeline
cp .env.example .env  # remplir secrets
docker compose up -d
```

### Option 2 — Setup local (Ubuntu)

```bash
git clone https://github.com/VictorNain26/linkedin-posts-pipeline.git
cd linkedin-posts-pipeline

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install   # puppeteer

cp .env.example .env
chmod 600 .env
# Remplir : ANTHROPIC_API_KEY, LI_CLIENT_ID/SECRET, GMAIL_SENDER, GMAIL_APP_PASSWORD

# OAuth one-shot (via SSH tunnel si headless)
python oauth_setup.py

# Test sans publier
PIPELINE_MODE=veille ./pipeline.sh --dry-run

# Activer les crons depuis CRON_DISABLED.txt
crontab -e
```

## Configuration

Tout est dans `config.py` et `.env`. Les principaux paramètres :

```python
# config.py
RSS_SOURCES = [...]          # mes flux RSS
TOKEN_BUDGETS = {...}        # max tokens par agent
HASHTAGS_BY_MODE = {         # hashtags par audience
    "evergreen": "#PME #IA ...",
    "veille": "#Claude #LLM ...",
}
SLIDE_COUNT = 7              # 7-10 est le sweet spot 2026
LINKEDIN_API_VERSION = "202604"
```

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
LI_CLIENT_ID=...
LI_CLIENT_SECRET=...
LI_ACCESS_TOKEN=...          # auto-rempli par oauth_setup.py
LI_REFRESH_TOKEN=...
LI_PERSON_URN=...
GMAIL_SENDER=you@gmail.com
GMAIL_APP_PASSWORD=...       # app password Google
```

## Adapter à ton cas

| Cas d'usage | À adapter |
|---|---|
| Autre persona / voix | `config.VOICE_RULES` + `ANTI_AI_PATTERNS` + `AUDIENCE_DESC` |
| Autre secteur (pas IA) | `config.RSS_SOURCES` + `HASHTAGS_BY_MODE` |
| Autre fréquence | crons + `current_mode()` weekday mapping |
| Marque entreprise | `templates/carousel.html` (CSS) |
| Langue autre que FR | tous les prompts dans `generate_post.py` |

## Coût d'exploitation

Pour ~8 posts/mois (2/semaine) :
- **Anthropic API** : ~$1.30/mois (mix Sonnet/Haiku optimisé)
- **LinkedIn API** : gratuit (Community Management API)
- **Gmail SMTP** : gratuit
- **Serveur** : $5-10/mois VPS de base (1 vCPU, 1GB RAM)
- **Total** : **~$10/mois tout compris**

## Versions des dépendances (mai 2026)

| Composant | Version | Notes |
|---|---|---|
| Python | 3.13.13 | LTS, pinné précis pour reproductibilité |
| Node.js | 22 LTS | Pour Puppeteer |
| Puppeteer | 24.43.1 | Chrome for Testing inclus |
| Anthropic SDK | ≥0.101.0 | |
| feedparser | ≥6.0.12 | |
| requests | ≥2.34.0 | |
| python-dotenv | ≥1.2.2 | |
| Chromium | apt-get latest | Debian Bookworm |
| supercronic | v0.2.45 | SHA1 verified |
| LinkedIn API | v202604 | Community Management API |

## Sécurité & conformité

- `.env` mode 600, exclu du git
- Pas de stockage de données tierces (respect LinkedIn ToS)
- Aucun secret loggé (`resp.text` tronqué dans les logs)
- Lockfile `flock` anti race condition
- Timeouts sur toutes les requêtes HTTP
- Retry exponentiel sur 429/5xx
- **Aucun fallback silencieux** : RSS vide ou échec → `exit ≠ 0`
- Compatible RGPD (aucune data utilisateur tierce stockée)

## Limites & "pas-faits"

Volontairement pas dans le scope :
- **Pas de scraping LinkedIn** (interdit par ToS)
- **Pas de connexions automatiques** ni de messages auto (LinkedIn ban si abus)
- **Pas de multi-tenant SaaS** : c'est un outil personnel
- **Pas de UI web** : tout est CLI/cron — pour moi c'est suffisant

Si tu cherches une version SaaS : voir [Taplio](https://taplio.com), [Supergrow](https://supergrow.ai), [Postiv AI](https://postiv.ai).

## Contribuer

Issues et PR bienvenues. Pour adapter à un autre cas d'usage, fork et adapte les fichiers cités plus haut.

## Contact

[Victor Lenain](https://victorlenain.fr) — Dev freelance, intégration IA + fullstack, Paris.

## License

[MIT](LICENSE) — utilise, modifie, redistribue.
