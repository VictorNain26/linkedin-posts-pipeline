#!/usr/bin/env bash
# pipeline.sh — cron entrypoint mar/mer/jeu
# Usage: ./pipeline.sh [--dry-run | --select-only | --draft]
#
# Trois phases distinctes (crons séparés) :
#   08h00 : ./pipeline.sh --select-only  → score RSS, sauvegarde state/pending_article.json
#   09h00 : ./pipeline.sh --draft        → lit pending_article.json, 8 agents + PDF,
#                                           sauvegarde state/pending_draft.json, envoie email
#   10h30 : ./pipeline.sh [--dry-run]    → lit pending_draft.json, publie si state/approved
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
PENDING_DRAFT="$STATE_DIR/pending_draft.json"
APPROVED_FLAG="$STATE_DIR/approved"

DRY_RUN="false"
SELECT_ONLY="false"
DRAFT_MODE="false"
[[ "${1:-}" == "--dry-run" ]]    && DRY_RUN="true"
[[ "${1:-}" == "--select-only" ]] && SELECT_ONLY="true"
[[ "${1:-}" == "--draft" ]] && DRAFT_MODE="true"

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

log "=== Pipeline start (dry_run=$DRY_RUN, select_only=$SELECT_ONLY, draft_mode=$DRAFT_MODE) ==="
metric event "start" dry_run "$DRY_RUN" select_only "$SELECT_ONLY" draft_mode "$DRAFT_MODE"

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

# ══════════════════════════════════════════════════════════════
# MODE --draft : lit pending_article.json, 8 agents + PDF,
# sauvegarde pending_draft.json, envoie email de rappel
# Cron 09h00 : génération du draft avant validation dashboard.
# ══════════════════════════════════════════════════════════════
if [ "$DRAFT_MODE" = "true" ]; then
    log "=== Draft generation start ==="
    metric event "draft_start"

    if [ ! -f "$PENDING_ARTICLE" ]; then
        log "[draft] No pending_article.json — run --select-only first (or wait for 08h00 cron)"
        metric event "draft_skip" reason "no_pending_article"
        exit 0
    fi

    # Read article + generate (with fallback to article #2)
    NEWS_FILE=$(mktemp "$OUTPUT_DIR/.news-XXXXXX.json")
    cp "$PENDING_ARTICLE" "$NEWS_FILE"
    rm -f "$PENDING_ARTICLE"

    RESULT_FILE=$(mktemp "$OUTPUT_DIR/.result-XXXXXX.json")
    GEN_SUCCESS="false"
    for ARTICLE_IDX in 0 1; do
        SINGLE_ARTICLE_FILE=$(mktemp "$OUTPUT_DIR/.single-XXXXXX.json")
        python3 -c "
import json, sys
arts = json.load(open(sys.argv[1]))
idx = int(sys.argv[2])
print(json.dumps([arts[idx]] if idx < len(arts) else [], ensure_ascii=False))
" "$NEWS_FILE" "$ARTICLE_IDX" > "$SINGLE_ARTICLE_FILE" 2>>"$LOG_FILE"
        SINGLE_COUNT=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$SINGLE_ARTICLE_FILE" 2>/dev/null || echo "0")
        if [ "$SINGLE_COUNT" = "0" ]; then rm -f "$SINGLE_ARTICLE_FILE"; break; fi
        ARTICLE_TITLE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[0]['title'][:60])" "$SINGLE_ARTICLE_FILE" 2>/dev/null || echo "?")
        log "[draft] Trying article #$((ARTICLE_IDX+1)): $ARTICLE_TITLE"
        if python3 "$DIR/generate_post.py" < "$SINGLE_ARTICLE_FILE" > "$RESULT_FILE" 2>>"$LOG_FILE"; then
            GEN_SUCCESS="true"
            rm -f "$SINGLE_ARTICLE_FILE"
            metric step "draft_generate" article_idx "$ARTICLE_IDX"
            break
        else
            GEN_EXIT=$?
            log "WARNING: generate failed on article #$((ARTICLE_IDX+1)) (exit $GEN_EXIT) — trying next"
            rm -f "$SINGLE_ARTICLE_FILE"
        fi
    done

    if [ "$GEN_SUCCESS" = "false" ]; then
        log "ERROR: draft generation failed on all articles"
        metric event "draft_abort" reason "generate_failed"
        rm -f "$RESULT_FILE" "$NEWS_FILE"
        exit 1
    fi

    # Save files to output dir
    SLUG=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['slug'])" "$RESULT_FILE")
    DATE_TAG=$(date +%Y-%m-%d)
    POST_DIR="$OUTPUT_DIR/${DATE_TAG}-${SLUG}"
    mkdir -p "$POST_DIR"
    cp "$RESULT_FILE" "$POST_DIR/result.json"
    cp "$NEWS_FILE" "$POST_DIR/news.json"

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

    # PDF generation if carousel
    FORMAT_CHOICE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['format'])" "$POST_DIR/result.json")
    EXPECTED_CAROUSEL_FORMAT=$(python3 -c "from config import FORMAT_CAROUSEL; print(FORMAT_CAROUSEL)")
    if [ "$FORMAT_CHOICE" = "$EXPECTED_CAROUSEL_FORMAT" ]; then
        log "[draft] Generating PDF…"
        node "$DIR/html_to_pdf.js" "$POST_DIR/slides.json" "$POST_DIR/carousel.pdf" 2>>"$LOG_FILE"
        PDF_SIZE=$(stat -c%s "$POST_DIR/carousel.pdf" 2>/dev/null || echo "0")
        if [ "$PDF_SIZE" -lt 10000 ]; then
            log "ERROR: PDF too small ($PDF_SIZE bytes)"
            metric event "draft_abort" reason "pdf_invalid"
            exit 1
        fi
        log "[draft] PDF OK: $PDF_SIZE bytes"
    fi

    # Write pending_draft.json (state file for dashboard + publish phase)
    rm -f "$APPROVED_FLAG"  # reset any stale approval
    python3 - "$POST_DIR/result.json" "$POST_DIR" "$PENDING_DRAFT" <<'PY' 2>>"$LOG_FILE"
import json, sys, datetime
data = json.load(open(sys.argv[1]))
post_dir = sys.argv[2]
draft = {
    "generated_at": datetime.datetime.now().isoformat(),
    "post_dir": post_dir,
    "article_title": data.get("article_title", ""),
    "article_url": data.get("article_url", ""),
    "format": data["format"],
    "topic": data["topic"],
    "slug": data["slug"],
    "post_text": data["post_text"],
    "first_comment": data["first_comment"],
    "slides_structured": data.get("slides_structured", []),
    "hook_winner_formula": data["hook_winner_formula"],
    "hook_winner_reason": data["hook_winner_reason"],
    "hook_variants": data.get("hook_variants", []),
}
open(sys.argv[3], "w", encoding="utf-8").write(json.dumps(draft, ensure_ascii=False, indent=2))
print(f"[draft] pending_draft.json saved: {draft['article_title'][:60]}")
PY
    DRAFT_TITLE=$(python3 -c "import json; d=json.load(open('$PENDING_DRAFT')); print(d.get('article_title','?')[:60])" 2>/dev/null || echo "?")
    log "[draft] Draft ready: $DRAFT_TITLE"
    log "[draft] ACTION REQUIRED : approuver dans le dashboard avant 10h30"

    # Email de rappel (si Gmail configuré)
    python3 - "$PENDING_DRAFT" <<'PY' 2>>"$LOG_FILE"
import json, os, sys, smtplib
from email.mime.text import MIMEText
sender = os.environ.get("GMAIL_SENDER", "").strip()
password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
recipient = os.environ.get("WEEKLY_REPORT_RECIPIENT", sender)
if not sender or not password:
    print("[draft] Gmail not configured — skip email reminder", file=sys.stderr)
    sys.exit(0)
data = json.load(open(sys.argv[1]))
title = data.get("article_title", "?")
fmt = data.get("format", "?")
subject = f"[LinkedIn Pipeline] Draft à valider avant 10h30 — {title[:50]}"
body = f"""Un draft est prêt pour validation.

Article source : {title}
Format : {fmt}
Généré à : {data.get('generated_at', '?')}

Hook :
{data.get('post_text', '')[:300]}…

→ Ouvrir le dashboard pour approuver ou rejeter :
  http://victorserv:8501

Le post sera publié automatiquement à 10h30 si approuvé.
Si aucune action → skip silencieux, aucune publication.
"""
msg = MIMEText(body, "plain", "utf-8")
msg["Subject"] = subject
msg["From"] = sender
msg["To"] = recipient
try:
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(sender, password)
        s.sendmail(sender, [recipient], msg.as_string())
    print(f"[draft] Reminder email sent to {recipient}")
except Exception as e:
    print(f"[draft] Email failed (non-blocking): {e}", file=sys.stderr)
PY

    metric event "draft_done" title "$DRAFT_TITLE"
    rm -f "$NEWS_FILE" "$RESULT_FILE"
    log "=== Draft done ==="
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

# ── Vérification draft + approbation ────────────────────────
if [ "$DRY_RUN" = "false" ]; then
    if [ ! -f "$PENDING_DRAFT" ]; then
        log "SKIP: aucun draft en attente (pipeline.sh --draft non exécuté ou déjà publié)"
        metric event "skip" reason "no_draft"
        exit 0
    fi
    if [ ! -f "$APPROVED_FLAG" ]; then
        log "SKIP: draft non approuvé via dashboard — aucune publication aujourd'hui"
        metric event "skip" reason "not_approved"
        exit 0
    fi
    log "Draft approuvé — lecture du post pré-généré…"
fi

# ── Lecture du draft pré-généré (ou génération live en dry-run) ─
if [ "$DRY_RUN" = "true" ]; then
    # En dry-run : génération live (pas de draft requis)
    NEWS_FILE=$(mktemp "$OUTPUT_DIR/.news-XXXXXX.json")
    if [ -f "$PENDING_ARTICLE" ]; then
        log "[dry-run] Using pre-selected article (from 08h00 select run)…"
        cp "$PENDING_ARTICLE" "$NEWS_FILE"
        rm -f "$PENDING_ARTICLE"
        metric step "rss" source "pending_article"
    else
        log "[dry-run] Fetching RSS (no pre-selection found)…"
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

    RESULT_FILE=$(mktemp "$OUTPUT_DIR/.result-XXXXXX.json")
    GEN_SUCCESS="false"
    for ARTICLE_IDX in 0 1; do
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
        log "[dry-run] Trying article #$((ARTICLE_IDX+1)): $ARTICLE_TITLE"

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

    # PDF carousel (uniquement si format=carousel)
    EXPECTED_CAROUSEL_FORMAT=$(python3 -c "from config import FORMAT_CAROUSEL; print(FORMAT_CAROUSEL)")
    if [ "$FORMAT_CHOICE" = "$EXPECTED_CAROUSEL_FORMAT" ]; then
        log "[dry-run] Generating PDF…"
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
        log "[dry-run] Skipping PDF (format=$FORMAT_CHOICE)"
    fi
else
    # Publish mode : lit le draft pré-généré
    POST_DIR=$(python3 -c "import json; print(json.load(open('$PENDING_DRAFT'))['post_dir'])")
    FORMAT_CHOICE=$(python3 -c "import json; print(json.load(open('$PENDING_DRAFT'))['format'])")
    SLUG=$(python3 -c "import json; print(json.load(open('$PENDING_DRAFT'))['slug'])")
    log "Post dir: $POST_DIR (format=$FORMAT_CHOICE)"
    metric step "publish" slug "$SLUG" format "$FORMAT_CHOICE"

    # Nettoyer les flags d'état (avant publication pour éviter double-post si ctrl+c après)
    rm -f "$PENDING_DRAFT" "$APPROVED_FLAG"
fi

# ── Post LinkedIn (ou dry-run) ───────────────────────────────
if [ "$DRY_RUN" = "true" ]; then
    log "DRY RUN — post not published"
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
    cost_usd=data.get("cost_usd"),
    tokens_in=data.get("tokens_in"),
    tokens_out=data.get("tokens_out"),
    tokens_cache_write=data.get("tokens_cache_write"),
    tokens_cache_read=data.get("tokens_cache_read"),
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
    log "Posting to LinkedIn ($FORMAT_CHOICE)…"
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

    # ── 1er commentaire (engagement, délai paramétrable) ─────
    COMMENT_DELAY=$(python3 -c "from config import FIRST_COMMENT_DELAY_SECONDS; print(FIRST_COMMENT_DELAY_SECONDS)")
    log "Sleeping ${COMMENT_DELAY}s before first comment…"
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
    cost_usd=data.get("cost_usd"),
    tokens_in=data.get("tokens_in"),
    tokens_out=data.get("tokens_out"),
    tokens_cache_write=data.get("tokens_cache_write"),
    tokens_cache_read=data.get("tokens_cache_read"),
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
