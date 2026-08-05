# Transcritor

`Python 3.11+` · `Flask` · `yt-dlp` · `faster-whisper (ctranslate2)` · `ffmpeg` · `100% local` · `macOS launchd`

## O que é

Transcritor local de vídeo para texto: você cola o link (YouTube, Instagram, TikTok, X) ou sobe um arquivo e recebe a transcrição em `.txt` ou `.srt`, sem nada sair da sua máquina.

Por baixo, é um app web em Flask que usa o `yt-dlp` para baixar o áudio de qualquer plataforma (ou aceita upload direto), o `ffmpeg` para extrair em WAV 16 kHz mono e o `faster-whisper` para transcrever offline na CPU, com progresso ao vivo e download do resultado em texto puro ou legenda com timestamps.

Tudo é otimizado para Apple Silicon — threads travadas nos P-cores, `beam_size=1`, modelo pré-carregado no boot e `ProcessType=Interactive` no launchd —, o que levou um áudio de 10 minutos de 202 s para cerca de 45 s. Roda como serviço sempre-no-ar em <http://127.0.0.1:5005> e já trata os bloqueios típicos do YouTube (403 / verificação de robô) com retry limpando a sessão do `yt-dlp` e suporte a cookies do navegador.

## Stack

| Camada | O que usa |
|---|---|
| Servidor | Python 3 + Flask (`app.py`), estado dos jobs em memória |
| Front-end | Jinja + HTML/JS vanilla (`templates/index.html`) |
| Download de mídia | `yt-dlp` |
| Áudio | `ffmpeg` (WAV 16 kHz mono) |
| Transcrição | `faster-whisper` sobre ctranslate2, modelo local, CPU |
| Serviço | launchd no macOS (`com.pedro.transcritor.plist` + `service.sh`) |

Sem banco de dados, sem Node, sem serviço remoto: nenhum áudio ou texto sai da máquina.

## Como rodar

Pré-requisito único: **ffmpeg**.

```bash
brew install ffmpeg
```

Modo dev (cria o `.venv` na primeira execução, instala o `requirements.txt`, sobe em <http://127.0.0.1:5005> e abre o navegador):

```bash
./run.sh
```

Se o serviço launchd já estiver no ar, o `run.sh` detecta e só abre o navegador — não tenta subir outro processo na mesma porta.

Equivalente manual, sem os atalhos do script:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python app.py
```

Com overrides de ambiente:

```bash
WHISPER_MODEL=medium PORT=5005 ./run.sh
```

### Manutenção

O `yt-dlp` é o que quebra primeiro quando o YouTube muda:

```bash
./.venv/bin/pip install -U yt-dlp && ./service.sh restart
```

Se der `HTTP Error 403` ou o YouTube pedir verificação de robô/idade, use os cookies do navegador:

```bash
./service.sh stop
YTDLP_COOKIES_FROM_BROWSER=chrome ./run.sh    # ou safari / firefox
```

## Deploy

Não existe deploy remoto — o app é 100% local por design. O "deploy" aqui é instalá-lo como serviço sempre-no-ar do macOS: sobe no login e reinicia sozinho se cair.

```bash
cp com.pedro.transcritor.plist ~/Library/LaunchAgents/
./service.sh install
```

Depois disso, o controle é todo pelo `service.sh`:

```bash
./service.sh status     # está no ar?
./service.sh restart    # reinicia
./service.sh stop       # para (não volta sozinho até dar start)
./service.sh start      # sobe de novo
./service.sh logs       # últimas linhas de out.log e err.log
./service.sh uninstall  # remove o serviço de vez
```

> **Atenção ao clonar em outra máquina:** o `com.pedro.transcritor.plist` tem caminhos absolutos (`/Users/<seu-usuario>/.../transcritor/...`). Ajuste `ProgramArguments`, `WorkingDirectory`, `StandardOutPath` e `StandardErrorPath` antes de instalar, ou o launchd falha calado.

Não remova `ProcessType=Interactive` do plist: sem isso o launchd trata o serviço como background, joga as threads nos E-cores e a transcrição fica ~3× mais lenta (139 s contra 45 s, no mesmo código).

## Estrutura de pastas

```
transcritor/
├── app.py                       # servidor Flask: rotas, jobs, download e transcrição
├── requirements.txt             # flask, yt-dlp, faster-whisper
├── run.sh                       # dev: cria venv, instala deps, sobe e abre o navegador
├── service.sh                   # start/stop/restart/status/logs/install/uninstall (launchd)
├── com.pedro.transcritor.plist  # definição do serviço launchd (caminhos absolutos)
├── templates/
│   └── index.html               # UI (Jinja + JS vanilla, progresso ao vivo)
├── downloads/                   # gerado: áudios .wav/.mp3 temporários dos jobs (ignorado)
├── logs/                        # gerado: out.log e err.log do launchd (ignorado)
└── .venv/                       # gerado: ambiente virtual (ignorado)
```

Fora do repo, os modelos do `faster-whisper` são baixados em `~/.cache/huggingface/hub` (0,5–3 GB dependendo do modelo). Nunca versione nada disso.

## Variáveis de ambiente

Nenhuma é obrigatória — o app roda com os padrões e **não há segredo nenhum aqui** (nada de chave de API, o projeto é offline). As chaves reconhecidas:

| Chave | Para quê |
|---|---|
| `PORT` | Porta do servidor Flask (padrão 5005) |
| `WHISPER_MODEL` | `tiny` / `base` / `small` / `medium` / `large-v3` (padrão `small`) |
| `WHISPER_BEAM` | Beam search: `1` é rápido, `5` é mais preciso em áudio difícil |
| `WHISPER_THREADS` | Threads de CPU; `0` detecta os P-cores automaticamente |
| `WHISPER_COMPUTE` | Tipo de compute do ctranslate2 (padrão `int8`) |
| `WHISPER_DEVICE` | `cpu` (padrão) ou `cuda`, se houver GPU |
| `WHISPER_CONDITION` | `1` liga `condition_on_previous_text` (mais contexto, mais lento) |
| `MAX_UPLOAD_MB` | Teto de upload em MB (padrão 1024) |
| `DOWNLOAD_ATTEMPTS` | Tentativas de download antes de desistir (padrão 3) |
| `YTDLP_COOKIES_FROM_BROWSER` | `chrome` / `safari` / `firefox` — destrava 403 e verificação de robô |
| `HF_HOME` | Onde guardar os modelos baixados (padrão `~/.cache/huggingface`) |

O `app.py` lê direto do ambiente (não carrega arquivo `.env` sozinho). Duas formas de definir:

- **Pontual:** na frente do comando — `WHISPER_BEAM=5 ./run.sh`.
- **Permanente:** no bloco `EnvironmentVariables` do `com.pedro.transcritor.plist`, para o serviço.

Se você quiser manter um arquivo de referência versionado, use um `.env.example` **apenas com as chaves acima, sem valores** — o `.gitignore` já bloqueia `.env` e `.env.*` e libera só o `.env.example`. Um `.env` local você carrega no shell antes de subir:

```bash
set -a && source .env && set +a && ./run.sh
```

## Desenvolvimento em nuvem

O projeto abre direto no **GitHub Codespaces**: no repositório, clique em **Code › Codespaces › Create codespace on main**. O `.devcontainer/devcontainer.json` já provisiona Python 3.11 + ffmpeg, instala o `requirements.txt` e encaminha a porta 5005 — é só rodar `python app.py` e abrir a porta encaminhada.

Duas ressalvas honestas sobre a nuvem:

- **Velocidade:** os ganhos de performance descritos aqui são de Apple Silicon (P-cores). Numa máquina virtual x86 compartilhada, prefira `WHISPER_MODEL=tiny` ou `base` para iterar.
- **Privacidade:** a premissa do projeto é "nada sai da máquina". Num Codespace, o áudio é processado no servidor do GitHub. Use a nuvem para mexer no código, não para transcrever material sensível.

O serviço launchd (`service.sh`, plist) é específico do macOS e não se aplica dentro do container.
