#!/usr/bin/env python3
"""
MarkItDown - interfaz web local minima.

Arranca un servidor que solo escucha en 127.0.0.1 y convierte documentos a
Markdown usando el comando `markitdown` ya instalado en el sistema.

Uso:
    python app.py                 # abre el navegador automaticamente
    python app.py --no-browser    # sin abrir el navegador
    python app.py --port 9000     # puerto concreto
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

HOST = "127.0.0.1"
DEFAULT_PORTS = [8765, 8766, 8767, 8768, 8769]
MAX_BYTES = 100 * 1024 * 1024  # 100 MB
TIMEOUT = 180  # segundos
CHUNK = 1024 * 1024

APP_NAME = "MarkItDown"


def find_markitdown() -> str | None:
    """Localiza el ejecutable de markitdown."""
    exe = shutil.which("markitdown")
    if exe:
        return exe
    candidates = [
        os.path.expanduser(r"~\.local\bin\markitdown.exe"),
        os.path.expanduser("~/.local/bin/markitdown"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


MARKITDOWN = find_markitdown()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_SCRIPT = os.path.join(BASE_DIR, "ocr_windows.py")
OCR_PYTHON = os.path.join(BASE_DIR, ".ocr-venv", "Scripts", "python.exe")
OCR_TIMEOUT = 600  # el OCR pagina a pagina puede tardar
OCR_MIN_TEXT = 200  # por debajo de esto se considera "sin texto util"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}


def ocr_available() -> bool:
    return os.path.isfile(OCR_SCRIPT) and os.path.isfile(OCR_PYTHON)


_OCR_LANGS: list[str] | None = None


def ocr_languages() -> list[str]:
    """Idiomas OCR que ofrece Windows (se consulta una sola vez)."""
    global _OCR_LANGS
    if _OCR_LANGS is None:
        _OCR_LANGS = []
        if ocr_available():
            try:
                proc = subprocess.run(
                    [OCR_PYTHON, OCR_SCRIPT, "--list-langs"],
                    capture_output=True,
                    timeout=30,
                    env=build_env(),
                )
                if proc.returncode == 0:
                    _OCR_LANGS = [
                        line.strip()
                        for line in proc.stdout.decode("utf-8", errors="replace").splitlines()
                        if line.strip()
                    ]
            except (OSError, subprocess.TimeoutExpired):
                pass
    return _OCR_LANGS


def build_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_markitdown(source: str) -> tuple[bool, str]:
    """Ejecuta markitdown sobre un archivo o URL. Devuelve (ok, texto)."""
    if not MARKITDOWN:
        return False, (
            "No se encontro el comando 'markitdown'. "
            "Instalalo con: uv tool install \"markitdown[all]\""
        )
    try:
        proc = subprocess.run(
            [MARKITDOWN, source],
            capture_output=True,
            timeout=TIMEOUT,
            env=build_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"La conversion tardo mas de {TIMEOUT} segundos y se cancelo."
    except OSError as exc:
        return False, f"No se pudo ejecutar markitdown: {exc}"

    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        detail = err or out.strip() or f"codigo de salida {proc.returncode}"
        return False, "No se pudo convertir el archivo.\n\n" + detail[-2000:]
    if not out.strip():
        return False, "La conversion no produjo texto (el archivo puede estar vacio o no ser compatible)."
    return True, out


def run_ocr(path: str, lang: str | None = None) -> tuple[bool, str]:
    """Ejecuta el OCR local de Windows sobre un PDF o imagen."""
    if not ocr_available():
        return False, "El OCR local no esta instalado (falta .ocr-venv u ocr_windows.py)."
    cmd = [OCR_PYTHON, OCR_SCRIPT, path]
    if lang:
        cmd += ["--lang", lang]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=OCR_TIMEOUT,
            env=build_env(),
        )
    except subprocess.TimeoutExpired:
        return False, f"El OCR tardo mas de {OCR_TIMEOUT // 60} minutos y se cancelo."
    except OSError as exc:
        return False, f"No se pudo ejecutar el OCR: {exc}"

    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode == 0 and out:
        return True, out
    if proc.returncode == 2:
        return False, "Windows no tiene ningun idioma OCR instalado."
    return False, err or "El OCR no detecto texto en el documento."


def should_try_ocr(path: str, text: str | None) -> bool:
    """Decide si conviene aplicar OCR al archivo."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return True  # markitdown solo da metadatos de imagenes
    if ext != ".pdf":
        return False
    useful = sum(1 for ch in (text or "") if not ch.isspace())
    return useful < OCR_MIN_TEXT


class Handler(BaseHTTPRequestHandler):
    server_version = "MarkItDownUI/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- utilidades ----------
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionError, OSError):
            self.close_connection = True

    def _send_json(self, code: int, payload: dict) -> None:
        import json

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._send_json(code, {"ok": False, "error": message})

    def log_message(self, fmt: str, *args) -> None:  # silencio en consola
        pass

    # ---------- rutas ----------
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/health":
            self._send_json(200, {
                "ok": True,
                "markitdown": MARKITDOWN is not None,
                "ocr": ocr_available(),
                "ocr_langs": ocr_languages(),
            })
        elif self.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if self.path == "/convert":
            self._handle_file()
        elif self.path == "/convert-url":
            self._handle_url()
        else:
            self._error(404, "Ruta no encontrada.")

    # ---------- conversion ----------
    def _handle_url(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 8192:
            self._error(400, "URL no valida.")
            return
        url = self.rfile.read(length).decode("utf-8", errors="replace").strip()
        if not url.lower().startswith(("http://", "https://")):
            self._error(400, "La direccion debe empezar por http:// o https://")
            return
        ok, text = run_markitdown(url)
        if ok:
            self._send_json(200, {"ok": True, "markdown": text, "filename": "enlace.md"})
        else:
            self._error(500, text)

    def _handle_file(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._error(400, "No se recibio ningun archivo.")
            return
        if length > MAX_BYTES:
            self._error(413, f"El archivo supera el limite de {MAX_BYTES // (1024 * 1024)} MB.")
            return

        raw_name = unquote(self.headers.get("X-Filename") or "documento")
        safe_name = os.path.basename(raw_name.replace("\\", "/")) or "documento"
        stem, ext = os.path.splitext(safe_name)
        stem = stem[:80] or "documento"
        ext = ext[:16]

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="mdi_", suffix=ext)
            remaining = length
            with os.fdopen(fd, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            if remaining > 0:
                self._error(400, "La subida se interrumpio.")
                return

            ocr_requested = (self.headers.get("X-Ocr") or "") == "1"
            ocr_lang = (self.headers.get("X-Ocr-Lang") or "").strip() or None
            ok, text = run_markitdown(tmp_path)
            used_ocr = False
            warning = None

            if ocr_requested and should_try_ocr(tmp_path, text if ok else None):
                ocr_ok, ocr_text = run_ocr(tmp_path, ocr_lang)
                if ocr_ok:
                    ok, text, used_ocr = True, ocr_text, True
                elif ok:
                    warning = ocr_text  # se conserva el resultado de markitdown
                else:
                    ok, text = False, ocr_text

            if ok:
                payload = {"ok": True, "markdown": text, "filename": stem + ".md", "ocr": used_ocr}
                if warning:
                    payload["warning"] = warning
                self._send_json(200, payload)
            else:
                self._error(500, text)
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"Error inesperado: {exc}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MarkItDown</title>
<style>
  :root{
    --bg:#0f1216; --panel:#171c22; --panel2:#1e252d; --line:#2b3540;
    --text:#e6edf3; --muted:#8b98a5; --accent:#3b82f6; --accent2:#2563eb;
    --ok:#22c55e; --err:#ef4444;
  }
  @media (prefers-color-scheme: light){
    :root{ --bg:#f4f6f9; --panel:#ffffff; --panel2:#f0f3f7; --line:#d8dfe8;
           --text:#101720; --muted:#5b6b7c; }
  }
  *{box-sizing:border-box}
  body{
    margin:0; min-height:100vh; background:var(--bg); color:var(--text);
    font:15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif;
    display:flex; justify-content:center; padding:32px 16px 56px;
  }
  .wrap{width:100%; max-width:860px}
  header{display:flex; align-items:center; justify-content:space-between; margin-bottom:22px}
  h1{font-size:22px; margin:0; letter-spacing:-.3px}
  h1 span{color:var(--accent)}
  .status{display:flex; align-items:center; gap:7px; font-size:12.5px; color:var(--muted)}
  .dot{width:8px; height:8px; border-radius:50%; background:var(--ok)}
  .dot.bad{background:var(--err)}
  .card{background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:20px}
  #drop{
    border:2px dashed var(--line); border-radius:12px; padding:38px 20px;
    text-align:center; cursor:pointer; transition:.15s; background:var(--panel2);
  }
  #drop:hover, #drop.over{border-color:var(--accent); background:color-mix(in srgb, var(--accent) 8%, var(--panel2))}
  #drop .big{font-size:16.5px; font-weight:600; margin-bottom:6px}
  #drop .hint{color:var(--muted); font-size:13px}
  #drop .formats{color:var(--muted); font-size:12px; margin-top:12px; opacity:.8}
  .fileinfo{display:none; margin-top:14px; align-items:center; gap:10px; font-size:13.5px;
    background:var(--panel2); border:1px solid var(--line); border-radius:9px; padding:9px 12px}
  .fileinfo.show{display:flex}
  .fileinfo b{font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .fileinfo .size{color:var(--muted); margin-left:auto; white-space:nowrap}
  .urlrow{display:flex; gap:10px; margin-top:14px; align-items:center}
  .urlrow label{color:var(--muted); font-size:13px; white-space:nowrap}
  input[type=text]{
    flex:1; background:var(--panel2); border:1px solid var(--line); color:var(--text);
    border-radius:9px; padding:10px 12px; font-size:14px; outline:none; min-width:0;
  }
  input[type=text]:focus{border-color:var(--accent)}
  .opt{display:flex; align-items:center; gap:9px; font-size:13.5px;
    color:var(--text); cursor:pointer; user-select:none}
  .opt input{width:16px; height:16px; accent-color:var(--accent); cursor:pointer; margin:0}
  .opt em{color:var(--muted); font-style:normal; font-size:12px}
  .opt.disabled{opacity:.5; cursor:not-allowed}
  .ocrrow{display:flex; align-items:center; gap:12px; margin-top:14px; flex-wrap:wrap}
  .ocrrow em{color:var(--muted); font-style:normal; font-size:12px}
  select{
    background:var(--panel2); border:1px solid var(--line); color:var(--text);
    border-radius:8px; padding:6px 9px; font:13px/1.2 inherit; outline:none; cursor:pointer;
  }
  select:focus{border-color:var(--accent)}
  select:disabled{opacity:.45; cursor:not-allowed}
  .actions{display:flex; gap:10px; margin-top:18px}
  button{
    font:inherit; font-weight:600; border-radius:9px; padding:11px 20px; cursor:pointer;
    border:1px solid transparent; transition:.15s; display:inline-flex; align-items:center; gap:8px;
  }
  button:disabled{opacity:.45; cursor:not-allowed}
  .primary{background:var(--accent); color:#fff; flex:1; justify-content:center}
  .primary:not(:disabled):hover{background:var(--accent2)}
  .ghost{background:transparent; border-color:var(--line); color:var(--text)}
  .ghost:not(:disabled):hover{border-color:var(--accent); color:var(--accent)}
  #msg{display:none; margin-top:14px; font-size:13.5px; border-radius:9px; padding:11px 13px;
    white-space:pre-wrap; word-break:break-word}
  #msg.show{display:block}
  #msg.err{background:color-mix(in srgb, var(--err) 12%, transparent); border:1px solid color-mix(in srgb, var(--err) 40%, transparent); color:var(--err)}
  #msg.info{background:var(--panel2); border:1px solid var(--line); color:var(--muted)}
  #result{display:none; margin-top:22px}
  #result.show{display:block}
  .rhead{display:flex; align-items:baseline; justify-content:space-between; margin-bottom:10px; gap:12px}
  .rhead h2{font-size:15px; margin:0}
  .meta{color:var(--muted); font-size:12.5px}
  textarea{
    width:100%; min-height:340px; resize:vertical; background:var(--panel2);
    border:1px solid var(--line); border-radius:11px; color:var(--text); padding:14px;
    font:13.5px/1.6 Consolas, "Cascadia Mono", monospace; outline:none; white-space:pre;
    overflow:auto;
  }
  textarea:focus{border-color:var(--accent)}
  .spinner{width:15px; height:15px; border:2px solid rgba(255,255,255,.35); border-top-color:#fff;
    border-radius:50%; animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .toast{position:fixed; left:50%; bottom:26px; transform:translate(-50%, 20px); background:var(--ok);
    color:#04140a; font-weight:600; font-size:13.5px; padding:10px 18px; border-radius:999px;
    opacity:0; pointer-events:none; transition:.22s}
  .toast.show{opacity:1; transform:translate(-50%, 0)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>MarkIt<span>Down</span></h1>
    <div class="status"><span class="dot" id="dot"></span><span id="statusText">comprobando…</span></div>
  </header>

  <div class="card">
    <div id="drop" tabindex="0" role="button" aria-label="Elegir archivo">
      <div class="big">Arrastra tu archivo aqui</div>
      <div class="hint">o haz clic para elegirlo</div>
      <div class="formats">PDF · Word · Excel · PowerPoint · HTML · CSV · JSON · XML · EPUB · ZIP · imagenes</div>
      <input type="file" id="file" hidden>
    </div>

    <div class="fileinfo" id="fileinfo">
      <b id="fname"></b><span class="size" id="fsize"></span>
      <button class="ghost" id="clear" style="padding:4px 10px; font-size:12px">Quitar</button>
    </div>

    <div class="urlrow">
      <label for="url">o pega una URL</label>
      <input type="text" id="url" placeholder="https://... (web o YouTube)" spellcheck="false">
    </div>

    <div class="ocrrow">
      <label class="opt" for="ocr">
        <input type="checkbox" id="ocr">
        <span>OCR para documentos escaneados</span>
      </label>
      <select id="ocrlang" title="Idioma del documento" disabled>
        <option value="">Idioma…</option>
      </select>
      <em id="ocrhint"></em>
    </div>

    <div class="actions">
      <button class="primary" id="go" disabled>Convertir</button>
    </div>

    <div id="msg"></div>
  </div>

  <div id="result">
    <div class="rhead">
      <h2>Resultado Markdown</h2>
      <span class="meta" id="meta"></span>
    </div>
    <textarea id="out" spellcheck="false"></textarea>
    <div class="actions">
      <button class="ghost" id="copy">Copiar</button>
      <button class="ghost" id="download">Descargar .md</button>
      <button class="ghost" id="open">Abrir en pestana</button>
    </div>
  </div>
</div>

<div class="toast" id="toast">Copiado al portapapeles</div>

<script>
const $ = (id) => document.getElementById(id);
const drop = $("drop"), fileInput = $("file"), go = $("go"), msg = $("msg");
const fileinfo = $("fileinfo"), fname = $("fname"), fsize = $("fsize");
const out = $("out"), result = $("result"), meta = $("meta"), url = $("url");
const ocr = $("ocr"), ocrhint = $("ocrhint"), ocrlang = $("ocrlang");
let currentFile = null, outName = "documento.md", busy = false;

const fmtSize = (n) => n < 1024 ? n + " B"
  : n < 1048576 ? (n/1024).toFixed(1) + " KB"
  : (n/1048576).toFixed(1) + " MB";

function showMsg(text, kind){
  msg.textContent = text;
  msg.className = "show " + (kind || "info");
}
function hideMsg(){ msg.className = ""; msg.textContent = ""; }

function refresh(){
  go.disabled = busy || (!currentFile && !url.value.trim());
}

function setFile(f){
  currentFile = f || null;
  if (f){
    fname.textContent = f.name;
    fsize.textContent = fmtSize(f.size);
    fileinfo.classList.add("show");
    url.value = "";
  } else {
    fileinfo.classList.remove("show");
  }
  hideMsg();
  refresh();
}

drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
$("clear").addEventListener("click", (e) => { e.stopPropagation(); fileInput.value = ""; setFile(null); });

["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => {
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) setFile(f);
});
window.addEventListener("dragover", e => e.preventDefault());
window.addEventListener("drop", e => e.preventDefault());

url.addEventListener("input", () => { if (url.value.trim()) setFile(null); refresh(); });
url.addEventListener("keydown", e => { if (e.key === "Enter" && !go.disabled) go.click(); });

async function convert(){
  if (busy) return;
  busy = true;
  go.disabled = true;
  go.innerHTML = '<span class="spinner"></span> Convirtiendo…';
  hideMsg();
  result.classList.remove("show");
  const started = Date.now();

  try {
    let res;
    if (currentFile){
      res = await fetch("/convert", {
        method: "POST",
        headers: {
          "X-Filename": encodeURIComponent(currentFile.name),
          "X-Ocr": ocr.checked ? "1" : "0",
          "X-Ocr-Lang": ocr.checked ? ocrlang.value : ""
        },
        body: currentFile
      });
    } else {
      res = await fetch("/convert-url", {
        method: "POST",
        headers: { "Content-Type": "text/plain; charset=utf-8" },
        body: url.value.trim()
      });
    }
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Error desconocido");

    out.value = data.markdown;
    outName = data.filename || "documento.md";
    meta.textContent = data.markdown.length.toLocaleString("es-ES") + " caracteres · " +
      ((Date.now() - started) / 1000).toFixed(1) + " s" + (data.ocr ? " · OCR" : "");
    result.classList.add("show");
    if (data.warning) showMsg("Aviso: " + data.warning, "info");
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    showMsg(err.message, "err");
  } finally {
    busy = false;
    go.innerHTML = "Convertir";
    refresh();
  }
}
go.addEventListener("click", convert);
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !go.disabled) go.click();
});

$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(out.value);
  } catch {
    out.select();
    document.execCommand("copy");
  }
  const t = $("toast");
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1600);
});

$("download").addEventListener("click", () => {
  const blob = new Blob([out.value], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = outName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
});

$("open").addEventListener("click", () => {
  const blob = new Blob([out.value], { type: "text/markdown;charset=utf-8" });
  window.open(URL.createObjectURL(blob), "_blank");
});

(async () => {
  try {
    const r = await fetch("/health");
    const j = await r.json();
    $("dot").classList.toggle("bad", !j.markitdown);
    $("statusText").textContent = j.markitdown ? "servidor listo" : "markitdown no encontrado";
    if (!j.ocr){
      ocr.disabled = true;
      ocr.checked = false;
      ocr.parentElement.classList.add("disabled");
      ocrlang.disabled = true;
      ocrhint.textContent = "(no instalado)";
    } else {
      const names = { "es-ES": "Español (España)", "es-MX": "Español (México)",
                      "en-US": "English (US)", "en-GB": "English (UK)",
                      "zh-Hans-CN": "中文 (简体)" };
      (j.ocr_langs || []).forEach(tag => {
        const o = document.createElement("option");
        o.value = tag;
        o.textContent = names[tag] || tag;
        ocrlang.appendChild(o);
      });
      if (j.ocr_langs && j.ocr_langs.length)
        ocrlang.value = j.ocr_langs.includes("es-ES") ? "es-ES" : j.ocr_langs[0];
      ocr.addEventListener("change", () => { ocrlang.disabled = !ocr.checked; });
      ocrhint.textContent = "(solo si el PDF no tiene texto)";
    }
  } catch {
    $("dot").classList.add("bad");
    $("statusText").textContent = "servidor no disponible";
  }
})();
</script>
</body>
</html>
"""


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return  # el cliente corto la conexion: no ensuciar la consola
        super().handle_error(request, client_address)


def serve(port: int | None, open_browser: bool) -> None:
    if not MARKITDOWN:
        print("AVISO: no se encontro el comando 'markitdown' en el PATH.")
        print('       Instalalo con:  uv tool install "markitdown[all]"')
        print()

    ports = [port] if port else DEFAULT_PORTS
    httpd = None
    chosen = None
    for candidate in ports:
        try:
            httpd = Server((HOST, candidate), Handler)
            chosen = candidate
            break
        except OSError:
            continue

    if httpd is None:
        print("No se pudo abrir ningun puerto libre (probados: %s)." % ", ".join(map(str, ports)))
        sys.exit(1)

    url = f"http://{HOST}:{chosen}/"
    print("=" * 58)
    print(f"  {APP_NAME}  ->  {url}")
    print("  Deja esta ventana abierta. Cierrala para apagar.")
    print("=" * 58)
    if not MARKITDOWN:
        print("  (sin markitdown: la conversion fallara)")

    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        httpd.server_close()


def main() -> None:
    args = sys.argv[1:]
    port = None
    open_browser = True
    if "--no-browser" in args:
        open_browser = False
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            print("Uso: python app.py [--port N] [--no-browser]")
            sys.exit(2)
    serve(port, open_browser)


if __name__ == "__main__":
    main()
