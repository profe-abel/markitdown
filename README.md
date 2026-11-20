# MarkItDown — interfaz local

Interfaz web mínima que corre **solo en tu equipo** (`127.0.0.1`) y convierte documentos y páginas web a Markdown usando el comando [`markitdown`](https://github.com/microsoft/markitdown), con opción de **OCR local** para PDFs escaneados e imágenes.

## Características

- Convierte PDF, Word, Excel, PowerPoint, HTML, CSV, JSON, XML, EPUB, ZIP e imágenes a Markdown.
- Acepta archivos por arrastrar y soltar o una URL (web o YouTube).
- Copiar al portapapeles, descargar `.md` o abrir el resultado en otra pestaña.
- OCR opcional con el **motor propio de Windows** (`Windows.Media.Ocr`): no envía nada a internet.
- El servidor escucha únicamente en `127.0.0.1`: no es accesible desde la red.
- Los archivos se procesan en un temporal que se borra al terminar.

## Requisitos

- **Windows 10/11** (necesario para el OCR; el resto funciona en cualquier sistema con Python).
- **Python 3.11** (el lanzador `.bat` usa `py -3.11` si está disponible).
- El comando `markitdown` instalado:
  ```powershell
  uv tool install "markitdown[all]"
  ```
- Para el OCR, los paquetes de idioma de Windows (Configuración → Hora e idioma → Idioma).

## Instalación

```powershell
git clone https://github.com/profe-abel/markitdown.git
cd markitdown

# Entorno del OCR (solo Windows)
python -m venv .ocr-venv
.ocr-venv\Scripts\pip install -r requirements.txt
```

Si `uv tool install` no está disponible, también puedes instalar el CLI con `pip install "markitdown[all]"`.

## Uso

Doble clic en **`Iniciar MarkItDown.bat`**. Se abre el navegador en `http://127.0.0.1:8765`.

Desde terminal:

```powershell
python app.py                  # abre el navegador
python app.py --no-browser     # sin abrir el navegador
python app.py --port 9000      # puerto concreto
```

Para apagarla, cierra la ventana de la terminal (o pulsa `Ctrl+C`).

## OCR para documentos escaneados

Marca la casilla **"OCR para documentos escaneados"** si el PDF es un escaneo sin texto seleccionable o si conviertes una foto/imagen.

- Solo actúa si el PDF no tiene texto útil (o si es una imagen).
- Idiomas detectados automáticamente según lo instalado en Windows: español (ES/MX), inglés (US/GB), chino simplificado, etc.
- La primera página tarda 1–2 s; un PDF largo puede tardar varios minutos.
- Se puede usar directamente desde terminal:
  ```powershell
  .ocr-venv\Scripts\python.exe ocr_windows.py archivo.pdf --lang es-ES
  .ocr-venv\Scripts\python.exe ocr_windows.py foto.jpg --list-langs
  ```

## Privacidad

Todo el procesamiento es local. El OCR usa APIs nativas de Windows, el servidor no sale de `127.0.0.1` y los archivos temporales se eliminan al finalizar. La única parte que puede acceder a internet es `markitdown` cuando le pasas una URL.

## Estructura

| Archivo | Descripción |
| --- | --- |
| `app.py` | Servidor HTTP local y interfaz web |
| `ocr_windows.py` | OCR con `winrt` + `pypdfium2` + Pillow |
| `Iniciar MarkItDown.bat` | Lanzador para Windows |
| `requirements.txt` | Dependencias del entorno OCR |
| `LEEME.txt` | Instrucciones rápidas en texto plano |

## Licencia

Uso personal. `markitdown` es un proyecto de Microsoft con su propia licencia (MIT).
