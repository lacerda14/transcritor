# MEMORY — Transcritor

> Memória curta do projeto: abra este arquivo primeiro para saber onde a gente parou, sem reler o código. Atualizado em 05/08/2026. O detalhe completo (variáveis de ambiente, tabela de stack, estrutura de pastas) está no `README.md`.

## O que é / estado atual

App que transcreve vídeo de qualquer plataforma (YouTube, Instagram, TikTok, X) ou arquivo enviado para texto — saída em `.txt` ou `.srt`.

- Stack: **Flask + yt-dlp + faster-whisper (ctranslate2) + ffmpeg**, Python 3.11+.
- Front: Jinja + HTML/JS vanilla em `templates/index.html`. Sem banco, sem Node — estado dos jobs fica em memória.
- Roda **100% local**, em <http://127.0.0.1:5005>. **Não há deploy em nuvem** e nunca houve: é projeto pessoal, offline por design (nenhum áudio ou texto sai da máquina).
- Não existe segredo no projeto: nenhuma chave de API, nenhum `.env` obrigatório.

## Como rodar

Pré-requisito único: `brew install ffmpeg`.

```bash
./run.sh          # cria o .venv na 1ª vez, instala deps, sobe e abre o navegador
```

Com override pontual de ambiente:

```bash
WHISPER_MODEL=medium PORT=5005 ./run.sh
```

Equivalente manual: `python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt && ./.venv/bin/python app.py`.

## Deploy

Não existe deploy remoto. O "deploy" aqui é instalar como serviço sempre-no-ar do macOS (sobe no login, reinicia sozinho):

```bash
cp com.pedro.transcritor.plist ~/Library/LaunchAgents/
./service.sh install
```

Controle depois disso: `./service.sh status | restart | stop | start | logs | uninstall`.

**O que NÃO fazer:**

- **Não instale o plist sem ajustar os caminhos.** O `com.pedro.transcritor.plist` está versionado com caminhos absolutos da máquina do Pedro. Em outra máquina, corrija `ProgramArguments`, `WorkingDirectory`, `StandardOutPath` e `StandardErrorPath` antes de instalar — senão o launchd **falha calado**.
- **Não remova `ProcessType=Interactive` do plist.** Sem isso o launchd trata como background, joga as threads nos E-cores e a transcrição fica ~3× mais lenta (139 s contra 45 s, mesmo código).
- `service.sh` e o plist são específicos de macOS: **não se aplicam dentro de container/Codespace**.

## Decisões e gotchas

- **O `yt-dlp` é o que quebra primeiro.** Quando o YouTube muda, o sintoma é falha no download, não bug no seu código. Conserto: `./.venv/bin/pip install -U yt-dlp && ./service.sh restart`.
- **`HTTP Error 403` ou verificação de robô/idade** = use os cookies do navegador: `./service.sh stop` e depois `YTDLP_COOKIES_FROM_BROWSER=chrome ./run.sh` (ou `safari` / `firefox`). O app já faz retry limpando a sessão do yt-dlp.
- **`downloads/` (hoje ~97 MB de áudio baixado) e `logs/` são gerados e ficam FORA do git** — junto com `.venv/` (~249 MB), transcrições (`*.srt`/`*.vtt`) e `cookies*.txt`. Isso é intencional: é mídia de terceiros, upload do usuário e sessão logada. Não force `git add` neles. Clone limpo não tem nada disso, e não precisa.
- **Modelos do faster-whisper vivem fora do repo**, em `~/.cache/huggingface/hub` (0,5–3 GB). Nunca versione. Em máquina nova, o primeiro job baixa o modelo — o boot parece travado, mas está só baixando.
- **`run.sh` não sobe processo duplicado:** se detecta o serviço launchd já no ar na porta, só abre o navegador e sai. Se "não subiu nada", provavelmente já estava rodando.
- **Otimizado para Apple Silicon:** threads travadas nos P-cores, `beam_size=1`, modelo pré-carregado no boot. Foi isso que levou um áudio de 10 min de 202 s para ~45 s. Em x86 compartilhado (Codespace) esses números não valem.
- **O `app.py` lê variáveis direto do ambiente e não carrega `.env` sozinho.** Para usar um `.env` local: `set -a && source .env && set +a && ./run.sh`.
- **Privacidade é a premissa do projeto.** Em Codespace o áudio é processado no servidor do GitHub — use a nuvem para mexer no código, não para transcrever material sensível.

## Onde paramos

- Branch **`main`**, árvore limpa, sincronizada com `origin/main` (repo privado `lacerda14/transcritor`).
- Último commit: `7c39f1e chore: migração inicial para a nuvem` — o repo foi criado/publicado com `.gitignore` endurecido, `README.md` completo e `.devcontainer/devcontainer.json`. O código do app em si (`app.py`, `run.sh`, `service.sh`, `templates/index.html`) já estava funcionando local antes disso.
- Estado funcional: rodando local na máquina do Pedro como serviço launchd.

## Pendências / próximos passos

- Nenhuma pendência de código aberta. O que existe é manutenção recorrente:
  - Atualizar `yt-dlp` quando um extractor quebrar (ver gotcha acima).
  - Limpar `downloads/` de tempos em tempos — cresce sem limite (97 MB hoje).
- Se for usar em máquina nova: ajustar os caminhos absolutos do plist antes de `./service.sh install`.

## Ambiente em nuvem

Abra por **Code › Codespaces › Create codespace on main** — o `.devcontainer` provisiona Python 3.11 + ffmpeg, instala o `requirements.txt` e encaminha a porta 5005 (`WHISPER_MODEL=base` lá dentro; prefira `tiny`/`base` em VM x86). O **repositório remoto é a fonte da verdade**: não dependa de nada que só existe na máquina local (`downloads/`, `logs/`, `.venv/`, modelos do Hugging Face) — tudo isso é regenerável.
