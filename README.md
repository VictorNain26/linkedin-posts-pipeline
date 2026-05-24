# LinkedIn Posts Auto-Pipeline

Pipeline autonome pour le compte LinkedIn de Victor Lenain : RSS → 8 agents Claude → multi-format post → analytics → rapport hebdo.

**Objectif** : 3 posts/semaine (mar/mer/jeu 10h30, evergreen prospect PME/CTO), sans validation manuelle, avec A/B testing des hooks et tracking complet.

**Stack** : "Share on LinkedIn" (scope `w_member_social`, self-serve sans review). Publication OK. Analytics via export CSV manuel hebdo (cf. `import_analytics_csv.py`) — l'API analytics nécessite Community Management API, inaccessible en perso.

## Architecture

```
                  ┌─────────────────────────────────┐
  RSS sources ──► │  rss_fetch.py (Haiku scoring)   │
                  └────────────┬────────────────────┘
                               ▼
                  ┌─────────────────────────────────┐
                  │  generate_post.py — 8 agents :  │
                  │   1. Pain Excavator             │
                  │   2. Angle Scout                │
                  │   3. Slide Architect            │
                  │   4. Victor's Pen               │
                  │   5. Anti-AI Detector           │
                  │   6. Hook Generator (3 variants)│
                  │   7. Hook Judge                 │
                  │   8. Engagement Comment Writer  │
                  └────────────┬────────────────────┘
                               ▼
                  ┌─────────────────────────────────┐
                  │  format_selector.py             │
                  │  (carousel / text / poll)       │
                  └────────────┬────────────────────┘
                               ▼
                  ┌─────────────────────────────────┐
                  │  html_to_pdf.js (si carousel)   │
                  └────────────┬────────────────────┘
                               ▼
                  ┌─────────────────────────────────┐
                  │  linkedin_post.py               │
                  │   - post_document_carousel      │
                  │   - post_text_only              │
                  │   - post_poll                   │
                  │   - post_first_comment          │
                  └────────────┬────────────────────┘
                               ▼
                  ┌─────────────────────────────────┐
                  │  history.py SQLite              │
                  │   - posts                       │
                  │   - hook_variants (A/B winners) │
                  │   - post_analytics              │
                  │   - format_history              │
                  └─────────────────────────────────┘

       ┌──────────────────────────────────────────────────┐
       │  import_analytics_csv.py (manuel hebdo)          │
       │     ← export CSV depuis l'UI LinkedIn Analytics  │
       │  fetch_analytics.sh (cron daily, no-op si scope  │
       │     r_member_postAnalytics absent)               │
       │  weekly_report.sh (lundi)  → weekly_report.py    │
       │  healthcheck.sh (daily)                          │
       └──────────────────────────────────────────────────┘
```

## Best practices LinkedIn 2026 appliquées (sources datées mai 2026)

| Source 2026 | Application |
|---|---|
| Carousel PDF = format roi (6.6% engagement, +278% vs text-only, +14% YoY) — Dataslayer, Buffer, Oktopost 2026 | Default ; `format_selector` switch vers text-only après 3 carrousels consécutifs |
| **Polls = reach trap** : 1.78× reach MAIS 0.37× engagement, classés "low-effort bait" par 360Brew — van der Blom Algorithm InSights 2025, Dataslayer Feb 2026 | Retirés du roulement auto. `post_poll()` reste dispo dans `linkedin_post.py` pour usage manuel exceptionnel |
| Hook = cutoff mobile 140 chars (80%+ du trafic), cutoff desktop 210 — AuthoredUp 2025-2026 | Agent 6 cible 100-140 chars, hard limit 210 ; 3 formules (contrarian/data/prospect_question), Agent 7 judge |
| **Lien externe dans body = -40 à -60% reach** — van der Blom Algorithm InSights 2026 (1.3M posts), Yepads Q2 2026, LinkBoost Q2 2026 | Aucun lien dans le body du post |
| **Lien en 1er commentaire = -80% visibilité du commentaire + désormais pénalité sur le post parent** ("bridge behavior" détecté Q2 2026) — Voketa, ConnectSafely | Aucun lien dans le 1er commentaire. CTA `DM ouvert` uniquement |
| Format 1080×1350 portrait | `html_to_pdf.js` (60% espace vertical mobile en plus) |
| **360Brew (mars 2026) : save > comment > like** (save = 5× reach vs like) — LinkedIn VP Product, Upgrowth | Carrousels designés "saveable" (frameworks, checklists, post-mortems) |
| Dwell time = signal #1 ("Depth Score") — LinkBoost Q2 2026 | 5-10 slides bien rythmées + slide CTA finale |
| Hashtags 3-5 ciblés (pyramid : 1 broad + 2-3 nichés) — Sprout Social 2026 | 5 hashtags fixes (`#IntégrationIA #PME #IA #Productivité #Freelance`) |
| Cadence sweet spot 3-5/sem (au-delà : -18 à -32% engagement par post) — Buffer 2M+ posts 2026 | cron mar/mer/jeu 10h30 (3/sem) |
| Détection sémantique hooks copy-paste (templates anglais saturés) par 360Brew | Agent 5 (Anti-AI Detector) + liste `ANTI_AI_PATTERNS` enrichie 2026 |
| Analytics : memberCreatorPostAnalytics requiert Community Management API (entité légale) | Pour "Share on LinkedIn" : `import_analytics_csv.py` (export UI hebdo). Le module `linkedin_analytics.py` reste dispo si Community Management décroché un jour. |
| LinkedIn-Version `202605` (release 2026-05-11) | `LINKEDIN_API_VERSION = "202605"` dans `config.py` |

## Patterns CCA-F Anthropic appliqués

- **D1 §3** : hub-and-spoke + structured handoff
- **D1 §6b** : retry+backoff exponentiel (429/5xx)
- **D4 §3** : `tool_use` + JSON Schema → zéro parsing libre sur 8 agents
- **D4 §4** : retry-with-feedback (Anti-AI Detector)
- **D5 §1b** : prompt caching `cache_control: ephemeral` sur 2 blocs system
- **D5 §3** : dédup par overlap keywords + structured error propagation

## Layout

```
linkedin-posts/                      # code (versionné)
├── config.py                        # models, system blocks cacheables, formats, anti-AI patterns
├── anthropic_client.py              # wrapper SDK : retry + tool_use forcé
├── rss_fetch.py                     # veille RSS + scoring Haiku
├── generate_post.py                 # pipeline 8 agents Sonnet+Haiku
├── format_selector.py               # carousel / text (polls retirés 2026)
├── history.py                       # SQLite (4 tables)
├── linkedin_post.py                 # API LinkedIn /rest/posts v202605 (carousel + text + comment ; poll legacy)
├── linkedin_analytics.py            # /rest/memberCreatorPostAnalytics — gated Community Management
├── import_analytics_csv.py          # IMPORT manuel CSV depuis UI LinkedIn (alternative API analytics)
├── weekly_report.py                 # synthèse hebdo + Gmail SMTP
├── oauth_setup.py                   # OAuth OpenID Connect (scope w_member_social uniquement)
├── token_refresh.py                 # refresh prophylactique
├── html_to_pdf.js                   # Puppeteer 1080×1350
├── pipeline.sh                      # cron mar/mer/jeu 10h30 (publie post + commentaire ; --dry-run pour tests)
├── fetch_analytics.sh               # cron daily 21h (no-op si scope absent)
├── weekly_report.sh                 # cron lundi 7h
├── healthcheck.sh                   # cron daily 8h
├── dashboard.py                     # UI Streamlit (Historique / Analytics / Tests + toggle publi auto)
├── ui.sh                            # launcher Streamlit sur localhost:8501
├── templates/carousel.html
└── requirements.txt

~/linkedin-posts-data/               # data (non versionné)
├── history.db                       # 4 tables
├── output/YYYY-MM-DD-slug/
│   ├── result.json                  # output complet generate_post.py
│   ├── news.json                    # snapshot RSS
│   ├── carousel.md                  # version texte des slides
│   ├── carousel.pdf                 # PDF carrousel (si format=carousel)
│   ├── post.txt                     # texte du post
│   ├── first_comment.txt            # 1er commentaire
│   └── slides.json
├── reports/linkedin-week-2026-NN.md
└── logs/
    ├── pipeline.log, analytics.log, weekly_report.log
    ├── healthcheck.log, alerts.log
    └── metrics.jsonl
```

## Setup sur victorserv

```bash
# 1. Code
cd ~/linkedin-posts
git pull   # ou rsync depuis Windows

# 2. Python venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. .env (mode 600)
cp .env.example .env
chmod 600 .env
# Remplir : ANTHROPIC_API_KEY, LI_CLIENT_ID/SECRET, GMAIL_SENDER, GMAIL_APP_PASSWORD

# 4. OAuth one-shot via SSH tunnel (scope r_member_postAnalytics inclus)
# Depuis Windows : ssh -L 8080:localhost:8080 victormoi@victorserv
# Sur victorserv :
python oauth_setup.py
# Ouvrir l'URL dans le navigateur Windows → callback rempli automatiquement

# 5. Tests
PIPELINE_MODE=veille ./pipeline.sh --dry-run

# 6. Permissions exécutables
chmod +x pipeline.sh healthcheck.sh fetch_analytics.sh weekly_report.sh

# 7. Activer les 4 crons (cf. CRON_DISABLED.txt)
crontab -e
```

## Cron (4 jobs)

```cron
PATH=/home/victormoi/.nvm/versions/node/v22.17.0/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Mar/Mer/Jeu 10h30 — cadence 3/sem (sweet spot LinkedIn 2026)
30 10 * * 2,3,4 cd ~/linkedin-posts && ./pipeline.sh >> ~/linkedin-posts-data/logs/cron.log 2>&1
# Daily 21h — analytics (no-op si scope absent, sinon fetch via API)
0 21 * * * cd ~/linkedin-posts && ./fetch_analytics.sh >> ~/linkedin-posts-data/logs/cron.log 2>&1
# Daily 8h — healthcheck
0 8 * * * cd ~/linkedin-posts && ./healthcheck.sh >> ~/linkedin-posts-data/logs/cron.log 2>&1
# Lundi 7h — weekly report
0 7 * * 1 cd ~/linkedin-posts && ./weekly_report.sh >> ~/linkedin-posts-data/logs/cron.log 2>&1
```

**Workflow analytics manuel hebdo** (à faire le lundi avant le `weekly_report`) :
```bash
# 1. LinkedIn UI : Profil → Analytics → "Show all analytics" → Export CSV
# 2. Copier le fichier sur victorserv (rsync ou scp)
python3 ~/linkedin-posts/import_analytics_csv.py /chemin/vers/export.csv
```

## Dashboard UI (Streamlit)

Interface web pour visualiser l'historique, les analytics, et les tests de génération.

### Mode Docker (recommandé, accessible LAN, restart auto)

```bash
# Build (rebuild nécessaire après update de requirements.txt qui a ajouté streamlit/pandas/pypdfium2)
docker compose build linkedin-dashboard

# Démarrage en background
docker compose up -d linkedin-dashboard

# Accès : depuis n'importe quelle machine du réseau local
#   http://<IP-du-host>:8501
# (ex: http://192.168.1.10:8501)

# Logs
docker compose logs -f linkedin-dashboard

# Stop
docker compose stop linkedin-dashboard
```

**Variable d'env `LINKEDIN_DATA_DIR`** :
Par défaut le compose mount `../linkedin-posts-data` (= `~/linkedin-posts-data` si le repo est dans `~/linkedin-posts/`). Pour pointer ailleurs :
```bash
# Soit en CLI ponctuel
LINKEDIN_DATA_DIR=/chemin/absolu/data docker compose up -d linkedin-dashboard
# Soit dans .env (chargé auto par docker compose)
echo "LINKEDIN_DATA_DIR=/chemin/absolu/data" >> .env
```
**Important** : le path doit appartenir à UID 1000 (sinon permission denied côté container). Sur le host courant, `victormoi` = UID 1000 — donc tout dossier owned par toi marche directement.

⚠️ **Pas d'authentification** dans cette config. Le dashboard expose les drafts et le toggle publi auto à tout le LAN. Adapté à un home network solo. Si tu invites du monde, ajoute un sidecar Caddy/nginx + Basic Auth.

### Mode CLI local (dev / debug rapide)

```bash
./ui.sh          # port 8501 par défaut
./ui.sh 8502     # port custom (si 8501 pris par un tunnel SSH)
```

Accès via tunnel SSH si tourne sur serveur distant :
```bash
ssh -L 8501:localhost:8501 victormoi@victorserv
# → http://localhost:8501 dans le navigateur local
```

3 pages :
- **📜 Historique** : posts publiés (`status='published'`), preview PDF + texte + 3 hook variants + métriques
- **📊 Analytics** : KPIs (impressions / reactions / comments / **saves** = signal #1 360Brew), table métriques par post, formula win rate, format distribution
- **🧪 Tests** : posts générés en dry-run (`status='test'`), même vue que l'historique — utile pour valider la qualité du contenu sans publier

**Toggle publi auto** (sidebar) :
- Switch ON/OFF qui touch/rm `~/linkedin-posts-data/.publi_paused`
- En pause, le `pipeline.sh` exit 0 immédiatement (le cron continue à tourner, no-op)
- Réversible en 1 clic, ne touche pas au crontab
- Equivalent CLI : `touch ~/linkedin-posts-data/.publi_paused` / `rm`

**Générer un test depuis CLI** :
```bash
./pipeline.sh --dry-run
# → enregistre en DB avec status='test', visible dans la page "🧪 Tests"
```

## Monitoring

```bash
# Dernière run
tail -50 ~/linkedin-posts-data/logs/pipeline.log

# Posts publiés
sqlite3 ~/linkedin-posts-data/history.db "SELECT published_at, mode, format, slug FROM posts ORDER BY published_at DESC LIMIT 10"

# Hook formules gagnantes (90j)
sqlite3 ~/linkedin-posts-data/history.db "SELECT formula, COUNT(*) FROM hook_variants WHERE is_winner=1 GROUP BY formula"

# Dernières métriques d'un post
sqlite3 ~/linkedin-posts-data/history.db "SELECT metric, count FROM post_analytics WHERE post_id=1 ORDER BY fetched_at DESC LIMIT 10"

# Dernier rapport hebdo
ls -t ~/linkedin-posts-data/reports/ | head -1
```

## Coût estimé

- Pipeline : ~8.7 posts/mois × ~$0.20/post = **~$1.80/mois** (Sonnet + Haiku, avec prompt caching)
- Analytics : ~$0/mois (LinkedIn API gratuite avec scope)
- Rapport hebdo : ~$0/mois (uniquement formatage SQLite + envoi SMTP, pas de LLM)

## Sécurité

- `.env` mode 600, exclu du git
- Aucun secret loggé (resp.text tronqué dans token_refresh)
- `pipeline.sh` sans shell injection (passage par fichiers + env vars)
- Lockfile `flock` anti race condition (1 seul run à la fois)
- Timeouts sur toutes les requêtes HTTP
- Retry+backoff exponentiel sur 429/5xx
- Aucun fallback silencieux : RSS vide ou échec → `exit ≠ 0`
