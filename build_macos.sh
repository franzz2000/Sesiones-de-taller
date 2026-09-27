#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Programador de alarmas"
BUILD_DIR="$ROOT_DIR/build"
DIST_DIR="$ROOT_DIR/dist"
ICONSET_DIR="$BUILD_DIR/AppIcon.iconset"
ICNS_FILE="$BUILD_DIR/AppIcon.icns"
DMG_STAGE_DIR="$BUILD_DIR/dmg"
DMG_FILE="$DIST_DIR/Programador-de-alarmas.dmg"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Error: este script solo puede ejecutarse en macOS." >&2
    exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Error: no se encuentra $PYTHON_BIN." >&2
    echo "Crea primero el entorno con: python3 -m venv .venv" >&2
    exit 1
fi

for command in sips iconutil hdiutil; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Error: falta la herramienta de macOS '$command'." >&2
        exit 1
    fi
done

echo "Instalando dependencias de ejecución y construcción..."
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt" -r "$ROOT_DIR/requirements-build.txt"

echo "Creando el icono de macOS..."
rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"
sips -z 16 16 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$ROOT_DIR/assets/app-icon.png" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET_DIR" -o "$ICNS_FILE"

echo "Construyendo $APP_NAME.app..."
cd "$ROOT_DIR"
"$PYTHON_BIN" -m PyInstaller \
    --clean \
    --noconfirm \
    --windowed \
    --name "$APP_NAME" \
    --icon "$ICNS_FILE" \
    --osx-bundle-identifier "com.franz.programador-alarmas" \
    --add-data "$ROOT_DIR/assets/app-icon.png:assets" \
    --add-data "$ROOT_DIR/sonidos:sonidos" \
    "$ROOT_DIR/app.py"

echo "Creando la imagen DMG..."
rm -rf "$DMG_STAGE_DIR" "$DMG_FILE"
mkdir -p "$DMG_STAGE_DIR"
ditto "$DIST_DIR/$APP_NAME.app" "$DMG_STAGE_DIR/$APP_NAME.app"
ln -s /Applications "$DMG_STAGE_DIR/Applications"
hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$DMG_STAGE_DIR" \
    -ov \
    -format UDZO \
    "$DMG_FILE" >/dev/null

echo
echo "Aplicación creada en:"
echo "$DIST_DIR/$APP_NAME.app"
echo
echo "Imagen DMG creada en:"
echo "$DMG_FILE"
