"""
Transcritor de vídeo -> texto (local).

Stack: Flask + yt-dlp + faster-whisper + ffmpeg.
- Cole a URL de um vídeo (YouTube, Instagram, TikTok, etc.) OU suba um arquivo.
- O áudio é extraído e transcrito 100% localmente (nenhum dado sai da máquina).
- Baixe o resultado em .txt ou .srt (com timestamps).

Rodar:  ./run.sh   (ou: python3 app.py)
"""

import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

# ------------------------------------------------------------------ config ---
BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "downloads"
WORK_DIR.mkdir(exist_ok=True)

# ffmpeg/ffprobe: no Mac (Apple Silicon), um app iniciado fora de um shell de login não
# herda /opt/homebrew/bin no PATH — então o yt-dlp/faster-whisper acusam "ffmpeg not found"
# mesmo com o ffmpeg instalado. Aqui a gente acha a pasta do ffmpeg e a garante no PATH.
def _find_ffmpeg_dir() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return os.path.dirname(exe)
    for cand in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        if os.path.exists(os.path.join(cand, "ffmpeg")):
            return cand
    return None


FFMPEG_DIR = _find_ffmpeg_dir()
if FFMPEG_DIR and FFMPEG_DIR not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")

# Modelo padrão do faster-whisper. Trade-off qualidade x velocidade:
#   tiny < base < small < medium < large-v3
# "small" costuma ser o melhor equilíbrio em CPU. Sobrescreva com WHISPER_MODEL.
DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "small")
ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large-v3"}

# device/compute: CPU + int8 funciona em qualquer Mac. Em Apple Silicon o
# ctranslate2 usa as libs otimizadas automaticamente.
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "int8")


def _perf_cores(default: int = 4) -> int:
    """
    Nº de cores de PERFORMANCE (Apple Silicon tem P-cores + E-cores).

    Medido neste M4 (4P + 6E), mesmo áudio de 3 min: 4 threads = 31s, 6 = 43s,
    8 = 68s. Jogar trabalho nos E-cores não acelera — o join espera o core lento.
    Por isso a gente trava nos P-cores em vez de deixar o ctranslate2 escolher.
    """
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout.strip()
        return int(out) or default
    except Exception:  # noqa: BLE001 - não-Apple/erro: cai no default
        return default


CPU_THREADS = int(os.environ.get("WHISPER_THREADS", "0")) or _perf_cores()

# beam_size=5 (padrão do whisper) custa ~2x o tempo de beam_size=1 e, em áudio
# de fala normal, o texto sai praticamente igual. Default rápido; suba pra 5 em
# áudio difícil (ruído, sotaque forte) com WHISPER_BEAM=5.
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM", "1"))

# Não realimentar o texto anterior no prompt: além de mais rápido, evita o loop
# de repetição clássico do whisper quando pega um trecho ruim.
CONDITION_ON_PREVIOUS = os.environ.get("WHISPER_CONDITION", "0") == "1"

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))  # 1 GB

# Quantas vezes tentar baixar antes de desistir (cada tentativa com sessão nova).
DOWNLOAD_ATTEMPTS = int(os.environ.get("DOWNLOAD_ATTEMPTS", "3"))
# Opcional: "chrome", "safari", "firefox"… usa os cookies do navegador quando o
# YouTube pede verificação de robô / idade.
COOKIES_FROM_BROWSER = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# jobs[job_id] = {status, stage, progress, text, segments, srt, error, filename}
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

# Cache de modelos carregados (carregar é caro; reutiliza entre jobs).
_models: dict[str, object] = {}
_models_lock = threading.Lock()


def get_model(name: str):
    from faster_whisper import WhisperModel

    with _models_lock:
        if name not in _models:
            _models[name] = WhisperModel(
                name,
                device=DEVICE,
                compute_type=COMPUTE_TYPE,
                cpu_threads=CPU_THREADS,
            )
        return _models[name]


_HF_HUB = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"


def model_ja_baixado(name: str) -> bool:
    """
    O modelo já está no cache local?

    Se não estiver, a 1ª transcrição baixa 0,5–3 GB no meio do job — e a tela fica
    parada em "Carregando modelo" parecendo travada. Aqui a gente detecta pra avisar.
    """
    repo = _HF_HUB / f"models--Systran--faster-whisper-{name}"
    return any(repo.glob("snapshots/*/model.bin"))


def preload_model() -> None:
    """Carrega o modelo padrão no boot, pra 1ª transcrição não pagar a espera."""
    try:
        get_model(DEFAULT_MODEL)
        print(f"  modelo '{DEFAULT_MODEL}' pré-carregado ({CPU_THREADS} threads, {COMPUTE_TYPE})")
    except Exception as exc:  # noqa: BLE001 - sem rede/modelo: o job carrega depois
        print(f"  (pré-carga do modelo falhou, segue sob demanda: {exc})")


# ------------------------------------------------------------------ utils ----
def set_job(job_id: str, **kw):
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(kw)


def _fmt_ts(seconds: float) -> str:
    """Segundos -> HH:MM:SS,mmm (formato SRT)."""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    out = []
    for i, seg in enumerate(segments, 1):
        out.append(str(i))
        out.append(f"{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}")
        out.append(seg["text"].strip())
        out.append("")
    return "\n".join(out)


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "audio"


class DownloadFailed(RuntimeError):
    """Erro de download já traduzido pro usuário (a UI mostra a mensagem crua)."""


# Erros que NÃO adianta tentar de novo: o vídeo é que não dá.
_PERMANENT_MARKERS = (
    "video unavailable",
    "private video",
    "members-only",
    "this video is drm protected",
    "is not available",
    "removed by the uploader",
    "unsupported url",
    "does not pass filter",
)


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return not any(m in msg for m in _PERMANENT_MARKERS)


def _reset_ytdlp_session_state(ydl=None) -> None:
    """
    Zera o estado que 'azeda' num processo longo (o serviço fica dias no ar).

    O yt-dlp guarda o PO token do YouTube num cache de MEMÓRIA global do processo
    (yt_dlp.extractor.youtube.pot._registry). Quando a sessão vira do outro lado,
    os metadados continuam extraindo normal mas a URL do áudio volta 403 — e o
    processo continua reusando o mesmo token até morrer. Um processo novo funciona;
    é por isso que reiniciar o serviço 'resolvia'. Aqui a gente faz isso sem reiniciar.
    """
    try:
        from yt_dlp.extractor.youtube.pot._registry import _pot_memory_cache

        cache = _pot_memory_cache.value.get("cache")
        if isinstance(cache, dict):
            with _pot_memory_cache.value.get("lock") or threading.Lock():
                cache.clear()
    except Exception:  # noqa: BLE001 - API interna do yt-dlp; se mudar, seguimos sem
        pass
    if ydl is not None:
        try:
            ydl.cache.remove()  # cache em disco (player/nsig)
        except Exception:  # noqa: BLE001
            pass


def _friendly_download_error(exc: Exception, attempts: int) -> str:
    msg = str(exc).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "403" in low or "forbidden" in low:
        return (
            f"O YouTube recusou o download do áudio (403) em {attempts} tentativas, "
            "mesmo com sessão nova. Costuma ser bloqueio temporário: espere alguns "
            "minutos e tente de novo, ou baixe o vídeo e use a aba “Arquivo”."
        )
    if "not a bot" in low or "sign in to confirm" in low or ("age" in low and "restrict" in low):
        return (
            "O YouTube pediu verificação (robô/idade). Reinicie o serviço com os cookies "
            "do navegador: YTDLP_COOKIES_FROM_BROWSER=chrome ./run.sh"
        )
    if "drm" in low:
        return "Vídeo protegido por DRM — não dá para extrair o áudio."
    if "429" in low or "too many requests" in low:
        return "O YouTube limitou a taxa de download (429). Espere alguns minutos e tente de novo."
    if any(m in low for m in ("video unavailable", "private video", "members-only", "is not available")):
        return "Vídeo indisponível, privado ou restrito a membros."
    return msg or "Falha ao baixar o áudio."


def download_audio(url: str, job_id: str) -> Path:
    """Baixa o áudio de uma URL com yt-dlp e devolve o caminho do .mp3."""
    import yt_dlp

    out_tmpl = str(WORK_DIR / f"{job_id}.%(ext)s")

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            pct = int(done * 100 / total) if total else 0
            set_job(job_id, stage="Baixando áudio", progress=pct)
        elif d.get("status") == "finished":
            set_job(job_id, stage="Convertendo áudio", progress=100)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        # rede instável / 5xx / fragmento perdido: deixa o próprio yt-dlp insistir
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "socket_timeout": 30,
        # O whisper só consome 16 kHz mono. Gerar mp3 VBR máxima era pagar duas vezes:
        # o encode caro no ffmpeg e depois o decode+resample na hora de transcrever.
        # wav 16k mono já sai no formato final (e o arquivo é temporário mesmo).
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "postprocessor_args": {"extractaudio": ["-ac", "1", "-ar", "16000"]},
    }
    # Aponta o yt-dlp pro ffmpeg explicitamente (não depende só do PATH).
    if FFMPEG_DIR:
        ydl_opts["ffmpeg_location"] = FFMPEG_DIR
    if COOKIES_FROM_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)

    title = "audio"
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if attempt > 1:
                    _reset_ytdlp_session_state(ydl)
                info = ydl.extract_info(url, download=True)
                title = info.get("title") or "audio"
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001 - qualquer falha de download vira retry
            last_exc = exc
            if attempt >= DOWNLOAD_ATTEMPTS or not _is_retryable(exc):
                break
            # limpa o pedaço baixado e tenta de novo com sessão limpa
            for f in WORK_DIR.glob(f"{job_id}.*"):
                try:
                    f.unlink()
                except OSError:
                    pass
            set_job(
                job_id,
                stage=f"Falhou o download — tentando de novo ({attempt + 1}/{DOWNLOAD_ATTEMPTS})",
                progress=0,
            )
            time.sleep(2 * attempt)

    if last_exc is not None:
        raise DownloadFailed(_friendly_download_error(last_exc, attempt)) from last_exc

    audio = WORK_DIR / f"{job_id}.wav"
    if not audio.exists():
        # fallback: pega qualquer arquivo gerado com esse job_id
        cands = list(WORK_DIR.glob(f"{job_id}.*"))
        if not cands:
            raise RuntimeError("Falha ao baixar o áudio (nenhum arquivo gerado).")
        audio = cands[0]
    set_job(job_id, source_title=title)
    return audio


def run_job(job_id: str, model_name: str, language: str | None, url: str | None, upload_path: Path | None):
    try:
        set_job(job_id, status="running", stage="Preparando", progress=0)

        if url:
            audio_path = download_audio(url, job_id)
        else:
            audio_path = upload_path

        if model_ja_baixado(model_name):
            set_job(job_id, stage="Carregando modelo", progress=0)
        else:
            set_job(
                job_id,
                stage=f"Baixando o modelo “{model_name}” (só na 1ª vez, pode levar minutos)",
                progress=0,
            )
        model = get_model(model_name)

        set_job(job_id, stage="Transcrevendo", progress=0)
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language or None,
            vad_filter=True,
            beam_size=BEAM_SIZE,
            condition_on_previous_text=CONDITION_ON_PREVIOUS,
        )
        total = info.duration or 0
        set_job(job_id, detected_language=info.language)

        segments = []
        parts = []
        for seg in segments_iter:
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
            parts.append(seg.text)
            if total:
                pct = min(99, int(seg.end * 100 / total))
                set_job(job_id, progress=pct, partial="".join(parts).strip())

        text = "".join(parts).strip()
        srt = build_srt(segments)
        set_job(
            job_id,
            status="done",
            stage="Concluído",
            progress=100,
            text=text,
            segments=segments,
            srt=srt,
        )
    except Exception as exc:  # noqa: BLE001 - queremos reportar qualquer erro na UI
        traceback.print_exc()
        set_job(job_id, status="error", stage="Erro", error=str(exc))
    finally:
        # limpa arquivos de áudio do job (o texto já está em memória)
        for f in WORK_DIR.glob(f"{job_id}.*"):
            try:
                f.unlink()
            except OSError:
                pass


# ----------------------------------------------------------------- routes ----
@app.route("/")
def index():
    return render_template("index.html", default_model=DEFAULT_MODEL, models=sorted(ALLOWED_MODELS))


@app.route("/transcribe", methods=["POST"])
def transcribe():
    model_name = request.form.get("model", DEFAULT_MODEL)
    if model_name not in ALLOWED_MODELS:
        model_name = DEFAULT_MODEL
    language = (request.form.get("language") or "").strip() or None
    url = (request.form.get("url") or "").strip() or None

    job_id = uuid.uuid4().hex
    upload_path = None
    display_name = None

    file = request.files.get("file")
    if file and file.filename:
        display_name = file.filename
        upload_path = WORK_DIR / f"{job_id}_{_safe_name(file.filename)}"
        file.save(upload_path)
    elif not url:
        return jsonify({"error": "Informe uma URL ou envie um arquivo."}), 400

    set_job(
        job_id,
        status="queued",
        stage="Na fila",
        progress=0,
        filename=display_name or url,
    )
    t = threading.Thread(
        target=run_job,
        args=(job_id, model_name, language, url, upload_path),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "job não encontrado"}), 404
        # não devolve os segments inteiros a cada poll (payload menor)
        payload = {k: v for k, v in job.items() if k != "segments"}
    return jsonify(payload)


@app.route("/download/<job_id>.<fmt>")
def download(job_id: str, fmt: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "transcrição não disponível"}), 404

    base = _safe_name(job.get("source_title") or job.get("filename") or "transcricao")
    base = os.path.splitext(base)[0]
    if fmt == "txt":
        content, mime = job["text"], "text/plain"
    elif fmt == "srt":
        content, mime = job["srt"], "application/x-subrip"
    else:
        return jsonify({"error": "formato inválido"}), 400

    path = WORK_DIR / f"{job_id}_out.{fmt}"
    path.write_text(content, encoding="utf-8")
    return send_file(path, as_attachment=True, download_name=f"{base}.{fmt}", mimetype=mime)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5005"))
    print(f"\n  Transcritor rodando em  http://127.0.0.1:{port}\n")
    # esquenta o modelo em background: o servidor já aceita requisição enquanto carrega
    threading.Thread(target=preload_model, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
