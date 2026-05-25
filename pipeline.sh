#!/usr/bin/env bash
# pipeline.sh — cron entrypoint mar/mer/jeu
# Usage: ./pipeline.sh [--dry-run | --select-only]
#
# Deux phases distinctes (crons séparés) :
#   08h00 : ./pipeline.sh --select-only  → score RSS, sauvegarde state/pending_article.json
#   10h30 : ./pipeline.sh [--dry-run]    → lit pending_article.json, génère, publie
#
# Sécurité : aucune donnée n'est interpolée dans des chaînes Python.
# Tout passe par stdin (JSON) ou fichiers — pas de shell injection possible.

set -euo pipefail
IFS=$'\n\t'

DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${LINKEDIN_DATA_DIR:-$HOME/linkedin-posts-data}"
LOG_DIR="$DATA_DIR/logs"
OUTPUT_DIR="$DATA_DIR/output"
STATE_DIR="$DATA_DIR/state"
mkdir -p "$LOG_DIR" "$OUTPUT_DIR" "$STATE_DIR"

LOG_FILE="$LOG_DIR/pipeline.log"
METRICS_FILE="$LOG_DIR/metrics.jsonl"

PENDING_ARTICLE="$STATE_DIR/pending_article.json"

DRY_RUN="false"
SELECT_ONLY="false"
[[ "${1:-}" == "--dry-run" ]]    && DRY_RUN="true"
[[ "${1:-}" == "--select-only" ]] && SELECT_ONLY="true"

# ── Helpers ──────────────────────────────────────────────────
log() {
    local msg="$*"
    echo "[$(date --iso-8601=seconds)] $msg" | tee -a "$LOG_FILE"
}

metric() {
    # metric KEY VALUE [KEY VALUE ...]  → ligne JSONL
    python3 - "$@" >>"$METRICS_FILE" <<'PY'
import json, sys, datetime
args = sys.argv[1:]
out = {"ts": datetime.datetime.now().isoformat(), "script": "pipeline"}
for i in range(0, len(args), 2):
    k, v = args[i], args[i+1] if i+1 < len(args) else ""
    try:
        out[k] = json.loads(v)
    except Exception:
        out[k] = v
print(json.dumps(out, ensure_ascii=False))
PY
}

NEWS_FILE=""
RESULT_FILE=""

cleanup_tmp() {
    [ -n "${NEWS_FILE:-}" ] && [ -f "$NEWS_FILE" ] && rm -f "$NEWS_FILE" || true
    [ -n "${RESULT_FILE:-}" ] && [ -f "$RESULT_FILE" ] && rm -f "$RESULT_FILE" || true
}

cleanup_on_error() {
    local code=$?
    log "ERROR: pipeline failed with exit $code at line $1"
    metric event "error" exit_code "$code" line "$1"
    cleanup_tmp
    exit $code
}
trap 'cleanup_on_error $LINENO' ERR
trap cleanup_tmp EXIT

# ── Activer venv si présent ──────────────────────────────────
if [ -f "$DIR/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$DIR/.venv/bin/activate"
fi

# ── Charger nvm si présent (pour Node.js dans cron / SSH non-interactif) ──
if [ -d "$HOME/.nvm" ]; then
    export NVM_DIR="$HOME/.nvm"
    # shellcheck disable=SC1091
    [ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh" --no-use
    # Ajoute la version active de node au PATH (sans charger nvm en entier)
    NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
    [ -n "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"
fi

export LINKEDIN_DATA_DIR="$DATA_DIR"
export PIPELINE_DIR="$DIR"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"

# ── Lockfile : empêche 2 runs simultanés (race condition double-post) ──
LOCK_FILE="$DATA_DIR/.pipeline.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[$(date --iso-8601=seconds)] another pipeline run is in progress, exiting" | tee -a "$LOG_FILE"
    exit 0
fi

log "=== Pipeline start (dry_run=$DRY_RUN, select_only=$SELECT_ONLY) ==="
metric event "start" dry_run "$DRY_RUN" select_only "$SELECT_ONLY"

# ══════════════════════════════════════════════════════════════
# MODE --select-only : fetch RSS + score → sauvegarde pending_article.json
# Cron 08h00 : articles du matin capturés, Victor peut relire avant 10h30.
# ══════════════════════════════════════════════════════════════
if [ "$SELECT_ONLY" = "true" ]; then
    log "[select] Fetching + scoring RSS articles…"
    TMP_SELECT=$(mktemp "$OUTPUT_DIR/.select-XXXXXX.json")
    python3 "$DIR/rss_fetch.py" > "$TMP_SELECT" 2>>"$LOG_FILE"
    COUNT=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$TMP_SELECT" 2>/dev/null || echo "0")
    log "[select] $COUNT articles pertinents trouvés"
    if [ "$COUNT" = "0" ]; then
        log "[select] Aucun article pertinent — pending_article.json non créé"
        rm -f "$TMP_SELECT"
        metric event "select_empty"
        exit 0
    fi
    mv "$TMP_SELECT" "$PENDING_ARTICLE"
    SELECTED_TITLE=$(python3 -c "
import json, sys
arts = json.load(open(sys.argv[1]))
print(arts[0]['title'][:80] if arts else '(vide)')
" "$PENDING_ARTICLE" 2>/dev/null || echo "(inconnu)")
    log "[select] Article retenu : $SELECTED_TITLE"
    log "[select] Sauvegardé dans $PENDING_ARTICLE"
    metric event "select_done" count "$COUNT" title "$SELECTED_TITLE"
    log "=== Select done ==="
    exit 0
fi

# ── Kill-switch UI : si .publi_paused existe ET qu'on n'est pas en dry-run, skip ──
# Touch ce fichier (depuis le dashboard ou à la main) pour mettre en pause les publications
# auto sans toucher au crontab. Les dry-runs (tests) restent autorisés pour qualif contenu.
PAUSE_FLAG="$DATA_DIR/.publi_paused"
if [ "$DRY_RUN" = "false" ] && [ -f "$PAUSE_FLAG" ]; then
    PAUSE_REASON=$(head -c 200 "$PAUSE_FLAG" 2>/dev/null || echo "")
    log "PAUSED — kill-switch actif ($PAUSE_FLAG). Reason: ${PAUSE_REASON:-(no reason file)}. Skip."
    metric event "paused" reason "${PAUSE_REASON:-no_reason}"
    exit 0
fi

# ── Guard : pas plus d'1 post / jour (via history.py) ───────
if ! ALREADY=$(python3 -c "from history import posted_today; print('1' if posted_today() else '0')" 2>>"$LOG_FILE"); then
    log "ERROR: history check failed"
    metric event "error" step "history_check"
    exit 1
fi
if [ "$ALREADY" = "1" ]; then
    log "SKIP: already posted today"
    metric event "skip_already_posted_today"
    exit 0
fi

# ── 1. Fetch RSS (ou lecture pending_article.json) ──────────
NEWS_FILE=$(mktemp "$OUTPUT_DIR/.news-XXXXXX.json")
if [ -f "$PENDING_ARTICLE" ]; then
    log "[1/4] Using pre-selected article (from 08h00 select run)…"
    cp "$PENDING_ARTICLE" "$NEWS_FILE"
    rm -f "$PENDING_ARTICLE"
    metric step "rss" source "pending_article"
else
    log "[1/4] Fetching RSS (no pre-selection found)…"
    python3 "$DIR/rss_fetch.py" > "$NEWS_FILE" 2>>"$LOG_FILE"
    metric step "rss" source "live"
fi
NEWS_COUNT=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$NEWS_FILE" 2>/dev/null || echo "0")
log "RSS items: $NEWS_COUNT"
metric step "rss" items "$NEWS_COUNT"

if [ "$NEWS_COUNT" = "0" ]; then
    log "ERROR: RSS returned 0 relevant items — no post today (no silent fallback)"
    metric event "abort" reason "rss_empty"
    exit 1
fi

# ── 2. Génération (8 agents) — fallback sur article #2 si échec ──
log "[2/4] Generating post (8 agents)…"
RESULT_FILE=$(mktemp "$OUTPUT_DIR/.result-XXXXXX.json")
GEN_SUCCESS="false"
for ARTICLE_IDX in 0 1; do
    # Extraire l'article N depuis la liste (Python inline)
    SINGLE_ARTICLE_FILE=$(mktemp "$OUTPUT_DIR/.single-XXXXXX.json")
    python3 -c "
import json, sys
arts = json.load(open(sys.argv[1]))
idx = int(sys.argv[2])
print(json.dumps([arts[idx]] if idx < len(arts) else [], ensure_ascii=False))
" "$NEWS_FILE" "$ARTICLE_IDX" > "$SINGLE_ARTICLE_FILE" 2>>"$LOG_FILE"

    SINGLE_COUNT=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$SINGLE_ARTICLE_FILE" 2>/dev/null || echo "0")
    if [ "$SINGLE_COUNT" = "0" ]; then
        rm -f "$SINGLE_ARTICLE_FILE"
        break
    fi

    ARTICLE_TITLE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[0]['title'][:60])" "$SINGLE_ARTICLE_FILE" 2>/dev/null || echo "?")
    log "[2/4] Trying article #$((ARTICLE_IDX+1)): $ARTICLE_TITLE"

    if python3 "$DIR/generate_post.py" < "$SINGLE_ARTICLE_FILE" > "$RESULT_FILE" 2>>"$LOG_FILE"; then
        GEN_SUCCESS="true"
        rm -f "$SINGLE_ARTICLE_FILE"
        metric step "generate" article_idx "$ARTICLE_IDX"
        break
    else
        GEN_EXIT=$?
        log "WARNING: generate_post.py failed on article #$((ARTICLE_IDX+1)) (exit $GEN_EXIT) — trying next"
        metric event "generate_retry" article_idx "$ARTICLE_IDX" exit_code "$GEN_EXIT"
        rm -f "$SINGLE_ARTICLE_FILE"
    fi
done

if [ "$GEN_SUCCESS" = "false" ]; then
    log "ERROR: generate_post.py failed on all articles. See log for details."
    metric event "abort" reason "generate_failed_all"
    exit 1
fi

SLUG=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['slug'])" "$RESULT_FILE")
DATE_TAG=$(date +%Y-%m-%d)
POST_DIR="$OUTPUT_DIR/${DATE_TAG}-${SLUG}"
mkdir -p "$POST_DIR"
mv "$RESULT_FILE" "$POST_DIR/result.json"
mv "$NEWS_FILE" "$POST_DIR/news.json"

# Extract content to files (no shell interpolation)
python3 - "$POST_DIR/result.json" "$POST_DIR" <<'PY' 2>>"$LOG_FILE"
import json, sys, pathlib
data = json.load(open(sys.argv[1]))
out = pathlib.Path(sys.argv[2])
(out / "carousel.md").write_text(
    "\n\n".join(f"SLIDE {i+1}: {s}" for i, s in enumerate(data["slides"])),
    encoding="utf-8",
)
(out / "post.txt").write_text(data["post_text"], encoding="utf-8")
(out / "first_comment.txt").write_text(data["first_comment"], encoding="utf-8")
(out / "slides.json").write_text(
    json.dumps(data["slides"], ensure_ascii=False),
    encoding="utf-8",
)
PY
FORMAT_CHOICE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['format'])" "$POST_DIR/result.json")
log "Generated: $POST_DIR (format=$FORMAT_CHOICE)"
metric step "generate" slug "$SLUG" format "$FORMAT_CHOICE"

# ── 3. PDF carousel (uniquement si format=carousel) ─────────
EXPECTED_CAROUSEL_FORMAT=$(python3 -c "from config import FORMAT_CAROUSEL; print(FORMAT_CAROUSEL)")
if [ "$FORMAT_CHOICE" = "$EXPECTED_CAROUSEL_FORMAT" ]; then
    log "[3/5] Generating PDF…"
    node "$DIR/html_to_pdf.js" "$POST_DIR/slides.json" "$POST_DIR/carousel.pdf" 2>>"$LOG_FILE"
    PDF_SIZE=$(stat -c%s "$POST_DIR/carousel.pdf" 2>/dev/null || echo "0")
    if [ "$PDF_SIZE" -lt 10000 ]; then
        log "ERROR: PDF too small ($PDF_SIZE bytes), aborting"
        metric event "pdf_invalid" size "$PDF_SIZE"
        exit 1
    fi
    log "PDF OK: $PDF_SIZE bytes"
    metric step "pdf" size "$PDF_SIZE"
else
    log "[3/5] Skipping PDF (format=$FORMAT_CHOICE)"
fi

# ── 4. Post LinkedIn (ou dry-run) ───────────────────────────
if [ "$DRY_RUN" = "true" ]; then
    log "[4/5] DRY RUN — post not published"
    log "  preview: $(head -c 120 "$POST_DIR/post.txt")…"
    log "  format : $FORMAT_CHOICE"
    log "  comment: $(head -c 120 "$POST_DIR/first_comment.txt")…"

    # Trace le test en DB (status='test') pour qu'il apparaisse dans l'UI Dashboard.
    python3 - "$POST_DIR/result.json" <<'PY' 2>>"$LOG_FILE"
import json, os, sys
sys.path.insert(0, os.environ.get("PIPELINE_DIR", "."))
from history import record_post, record_hook_variants
data = json.load(open(sys.argv[1]))
post_pk = record_post(
    topic=data["topic"],
    slug=data["slug"],
    format=data["format"],
    keywords=data["keywords"],
    linkedin_post_id=None,
    linkedin_comment_id=None,
    status="test",
)
record_hook_variants(
    post_id=post_pk,
    variants=data["hook_variants"],
    winner_formula=data["hook_winner_formula"],
    judge_reason=data["hook_winner_reason"],
)
print(post_pk, file=sys.stderr)
PY
    metric event "dry_run_done"
else
    log "[4/5] Posting to LinkedIn ($FORMAT_CHOICE)…"
    export POST_DIR_ENV="$POST_DIR"
    POST_ID=$(python3 - <<'PY' 2>>"$LOG_FILE"
import json, os, sys, pathlib
sys.path.insert(0, os.environ.get("PIPELINE_DIR", "."))
from config import FORMAT_CAROUSEL, FORMAT_TEXT
from linkedin_post import post_document_carousel, post_text_only

post_dir = pathlib.Path(os.environ["POST_DIR_ENV"])
data = json.load(open(post_dir / "result.json"))
text = (post_dir / "post.txt").read_text(encoding="utf-8")

fmt = data["format"]
if fmt == FORMAT_CAROUSEL:
    pid = post_document_carousel(text, str(post_dir / "carousel.pdf"))
elif fmt == FORMAT_TEXT:
    pid = post_text_only(text)
else:
    # poll = retiré du roulement auto en 2026 (reach trap). post_poll() reste dispo
    # dans linkedin_post.py pour usage manuel mais n'est plus émis par format_selector.
    raise SystemExit(f"unsupported format from pipeline: {fmt!r} (carousel/text only)")
print(pid)
PY
)
    log "Posted: $POST_ID"
    metric step "post" linkedin_id "$POST_ID"

    # ── 5. 1er commentaire (engagement, délai paramétrable) ─
    COMMENT_DELAY=$(python3 -c "from config import FIRST_COMMENT_DELAY_SECONDS; print(FIRST_COMMENT_DELAY_SECONDS)")
    log "[5/5] Sleeping ${COMMENT_DELAY}s before first comment…"
    sleep "$COMMENT_DELAY"
    export POST_ID
    COMMENT_ID=$(python3 - <<'PY' 2>>"$LOG_FILE"
import os, sys, pathlib
sys.path.insert(0, os.environ.get("PIPELINE_DIR", "."))
from linkedin_post import post_first_comment
post_id = os.environ["POST_ID"]
text = pathlib.Path(os.environ["POST_DIR_ENV"], "first_comment.txt").read_text(encoding="utf-8")
print(post_first_comment(post_id, text))
PY
)
    log "Comment posted: $COMMENT_ID"
    metric step "comment" linkedin_id "$COMMENT_ID"

    # Enregistre dans history.db (avec post + comment + hook variants)
    export POST_ID COMMENT_ID
    python3 - "$POST_DIR/result.json" <<'PY' 2>>"$LOG_FILE"
import json, os, sys
sys.path.insert(0, os.environ.get("PIPELINE_DIR", "."))
from history import record_post, record_hook_variants
data = json.load(open(sys.argv[1]))
post_pk = record_post(
    topic=data["topic"],
    slug=data["slug"],
    format=data["format"],
    keywords=data["keywords"],
    linkedin_post_id=os.environ["POST_ID"],
    linkedin_comment_id=os.environ.get("COMMENT_ID") or None,
    status="published",
)
record_hook_variants(
    post_id=post_pk,
    variants=data["hook_variants"],
    winner_formula=data["hook_winner_formula"],
    judge_reason=data["hook_winner_reason"],
)
print(post_pk)
PY
    log "History updated (post + comment + hook variants)."
fi

log "=== Pipeline done ==="
metric event "end"
