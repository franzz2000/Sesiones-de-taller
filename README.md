# Programador de alarmas

Aplicación de escritorio en Python para programar múltiples alarmas por día de la semana, hora de inicio, hora de finalización, volumen y sonido MP3.

El icono de la aplicación está disponible en `assets/app-icon.png` y se carga automáticamente al iniciar la interfaz.

## Uso

```bash
python -m pip install -r requirements.txt
python app.py
```

## Comportamiento importante

- Cada alarma puede estar activa en uno o varios días de la semana.
- El inicio y el fin se guardan como `HH:MM:SS`.
- En el inicio, los componentes omitidos se completan con `00`: `7` se convierte en `07:00:00`.
- En el fin, los componentes omitidos se completan con `59`: `7:20` se convierte en `07:20:59`.
- También admiten escritura compacta sin dos puntos. En Inicio, `0700` se convierte en `07:00:00`; en Fin, se convierte en `07:00:59`. Los segundos explícitos, como en `072015`, se conservan: `07:20:15`.
- Cada alarma tiene su propio volumen.
- El orden de la lista determina la prioridad cuando varias alarmas coinciden.
- **Detener sonido** silencia la ejecución actual hasta que termina su intervalo.
- Los MP3 se detectan automáticamente en la carpeta `sonidos` situada junto a `app.py`.
- **Añadir MP3 al catálogo** mueve el archivo seleccionado a esa carpeta.
- La melodía se reproduce en bucle.

## Datos locales

La app guarda la configuración en:

```text
~/.multi_alarm_clock/
```

Los sonidos se guardan en:

```text
./sonidos/
```

## Crear la aplicación para macOS

Para evitar incompatibilidades de Tcl/Tk en macOS, utiliza Python 3.12 o 3.13 nativo de la arquitectura del Mac para construir el bundle. En un Mac Apple Silicon, comprueba que Python sea `arm64`:

```bash
python3.13 -c "import platform; print(platform.machine())"
rm -rf .venv
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-build.txt -r requirements-macos.txt
```

El resultado debe ser `arm64` en un Mac Apple Silicon. Si aparece `x86_64`, instala Python 3.13 desde Homebrew nativo para Apple Silicon y vuelve a crear el entorno. No uses el Python de `/usr/local`, que normalmente corresponde a Homebrew Intel; el de Apple Silicon suele estar en `/opt/homebrew`.

Desde la raíz del proyecto, ejecuta:

```bash
chmod +x build_macos.sh
./build_macos.sh
```

El script instala las dependencias de construcción, convierte el icono al formato de macOS y genera:

```text
dist/macos/Programador de alarmas.app
dist/macos/Programador-de-alarmas.dmg
```

En la aplicación empaquetada, el catálogo de sonidos se copia al primer inicio en `~/.multi_alarm_clock/sonidos`, donde puede modificarse sin alterar la firma del paquete.

## Crear el ejecutable para Windows desde macOS

PyInstaller no permite compilar directamente un `.exe` desde macOS. El proyecto incluye un workflow de GitHub Actions que realiza la construcción en Windows.

Después de publicar el repositorio en GitHub y configurar el remoto `origin`, ejecuta desde el Mac:

```bash
chmod +x build_windows.sh
./build_windows.sh
```

El script inicia la compilación remota, espera a que termine y descarga:

```text
dist/windows/Programador-de-alarmas.exe
```

`build_windows.ps1` contiene el proceso real de construcción y también puede ejecutarse directamente en un equipo Windows.

## Instalar y crear el ejecutable en Ubuntu/Linux

Para ejecutar la aplicación desde el código fuente, instala Python, Tkinter y el soporte para entornos virtuales:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Para crear el ejecutable Linux y un paquete distribuible:

```bash
chmod +x build_linux.sh
./build_linux.sh
```

El script debe ejecutarse en Linux, ya que PyInstaller no genera un binario Linux nativo desde macOS. Los archivos generados son:

```text
dist/linux/Programador-de-alarmas
dist/linux/Programador-de-alarmas-linux.tar.gz
```

Para instalar el paquete en otro equipo Ubuntu/Linux:

```bash
mkdir Programador-de-alarmas
tar -xzf Programador-de-alarmas-linux.tar.gz -C Programador-de-alarmas
cd Programador-de-alarmas
chmod +x Programador-de-alarmas
./Programador-de-alarmas
```

## Licencia

Copyright © 2026 Franz Jimeno.

Esta aplicación se distribuye bajo la licencia [Creative Commons Atribución 4.0 Internacional (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). Puedes compartirla y adaptarla, incluso con fines comerciales, siempre que reconozcas adecuadamente la autoría, enlaces la licencia e indiques si has realizado cambios.

Consulta los términos legales completos en [`LICENSE`](LICENSE).
