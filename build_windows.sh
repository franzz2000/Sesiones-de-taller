#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKFLOW="build-windows.yml"
ARTIFACT_DIR="$ROOT_DIR/dist/windows"

if ! command -v gh >/dev/null 2>&1; then
    echo "Error: instala GitHub CLI con: brew install gh" >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "Error: inicia sesión primero con: gh auth login" >&2
    exit 1
fi

if ! git -C "$ROOT_DIR" remote get-url origin >/dev/null 2>&1; then
    echo "Error: el proyecto debe estar publicado en GitHub con un remoto 'origin'." >&2
    exit 1
fi

BRANCH="$(git -C "$ROOT_DIR" branch --show-current)"
if [[ -z "$BRANCH" ]]; then
    echo "Error: no hay una rama Git activa." >&2
    exit 1
fi

echo "Solicitando la compilación para Windows en GitHub Actions..."
gh workflow run "$WORKFLOW" --ref "$BRANCH"
sleep 3

RUN_ID="$(gh run list --workflow "$WORKFLOW" --branch "$BRANCH" --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
if [[ -z "$RUN_ID" ]]; then
    echo "Error: no se ha encontrado la ejecución del workflow." >&2
    exit 1
fi

gh run watch "$RUN_ID" --exit-status
rm -rf "$ARTIFACT_DIR"
mkdir -p "$ARTIFACT_DIR"
gh run download "$RUN_ID" --name "Programador-de-alarmas-Windows" --dir "$ARTIFACT_DIR"

echo
echo "Ejecutable descargado en:"
echo "$ARTIFACT_DIR/Programador-de-alarmas.exe"
