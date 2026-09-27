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

Desde la raíz del proyecto, ejecuta:

```bash
chmod +x build_macos.sh
./build_macos.sh
```

El script instala las dependencias de construcción, convierte el icono al formato de macOS y genera:

```text
dist/Programador de alarmas.app
dist/Programador-de-alarmas.dmg
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
