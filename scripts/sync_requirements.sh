#!/usr/bin/env bash
# Régénère requirements.txt depuis pyproject.toml (source unique de vérité).
#
# Utilise pip-compile (pip-tools) qui résout proprement les versions et
# extrait UNIQUEMENT les deps de [project].dependencies (pas les dev deps).
#
# Usage :
#   bash scripts/sync_requirements.sh
#
# Pré-requis : pip install pip-tools  (déjà dans pyproject [dev])
#
# Note : on ne pin pas les sous-deps transitives ici — le Dockerfile résout
# les versions à build-time. Si tu veux du pinning strict pour reproductibilité,
# ajoute --generate-hashes au pip-compile (ralentit le build).

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

if ! command -v pip-compile >/dev/null 2>&1; then
    echo "❌ pip-compile not found. Install : pip install pip-tools" >&2
    exit 1
fi

OUT="$DIR/requirements.txt"
HEADER="$DIR/requirements.txt.header"

# Header explicite (préserve les commentaires "ne pas éditer")
cat > "$HEADER" <<'EOF'
# ════════════════════════════════════════════════════════════════════
# GÉNÉRÉ DEPUIS pyproject.toml — NE PAS ÉDITER MANUELLEMENT
# ════════════════════════════════════════════════════════════════════
# Pour mettre à jour, modifie [project].dependencies dans pyproject.toml
# puis lance : bash scripts/sync_requirements.sh
#
# Ce fichier sert uniquement au build Docker. En dev local :
#   pip install -e ".[dev]"
# ════════════════════════════════════════════════════════════════════

EOF

# pip-compile génère le fichier final
pip-compile --quiet --no-header --output-file=/tmp/req-compiled.txt pyproject.toml

# Concat header + compiled
cat "$HEADER" /tmp/req-compiled.txt > "$OUT"
rm -f "$HEADER" /tmp/req-compiled.txt

echo "✅ requirements.txt regenerated from pyproject.toml"
echo "   → review the diff before commit : git diff requirements.txt"
