#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Programador de alarmas"
EXECUTABLE_NAME="Programador-de-alarmas"
DIST_DIR="$ROOT_DIR/dist"
PACKAGE_DIR="$ROOT_DIR/build/linux"
PACKAGE_FILE="$DIST_DIR/$EXECUTABLE_NAME-linux.tar.gz"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Error: este script debe ejecutarse en Linux." >&2
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Error: no se encuentra $PYTHON_BIN." >&2
    echo "Crea primero el entorno con: python3 -m venv .venv" >&2
    exit 1
fi

echo "Instalando dependencias de ejecución y construcción..."
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt" -r "$ROOT_DIR/requirements-build.txt"

echo "Construyendo el ejecutable Linux..."
cd "$ROOT_DIR"
"$PYTHON_BIN" -m PyInstaller \
    --clean \
    --noconfirm \
    --onefile \
    --windowed \
    --name "$EXECUTABLE_NAME" \
    --icon "$ROOT_DIR/assets/app-icon.png" \
    --add-data "$ROOT_DIR/assets/app-icon.png:assets" \
    --add-data "$ROOT_DIR/sonidos:sonidos" \
    --add-data "$ROOT_DIR/LICENSE:." \
    "$ROOT_DIR/app.py"

echo "Creando paquete distribuible..."
rm -rf "$PACKAGE_DIR" "$PACKAGE_FILE"
mkdir -p "$PACKAGE_DIR"
cp "$DIST_DIR/$EXECUTABLE_NAME" "$PACKAGE_DIR/"
cp "$ROOT_DIR/LICENSE" "$PACKAGE_DIR/"
cp "$ROOT_DIR/assets/app-icon.png" "$PACKAGE_DIR/"
tar -czf "$PACKAGE_FILE" -C "$PACKAGE_DIR" .

echo
echo "Ejecutable creado en:"
echo "$DIST_DIR/$EXECUTABLE_NAME"
echo
echo "Paquete Linux creado en:"
echo "$PACKAGE_FILE"
