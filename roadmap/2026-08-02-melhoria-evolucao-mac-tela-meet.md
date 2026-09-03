# Roadmap de melhoria — Reco: porte macOS, gravação de tela e Meet via extensão (2026-08-02)

## Contexto e motivação

O Reco hoje é um app Windows (Tkinter) que grava mic + áudio do sistema (WASAPI
loopback) num MP3 estéreo (L=mic, R=sistema) e transcreve localmente (OpenVINO/
Whisper). A visão de produto registrada em 02/08 (hub
`C:\Dev\cerebro\projetos\reco.md`) é o "OBS de conversas": local, privado,
custo zero por assento.

Este roadmap cobre as três frentes decididas com o Gabriel em 02/08:

1. **Porte macOS M1+** — o backend de transcrição já existe (`MLXTranscriber`);
   falta a captura de áudio do sistema e guards de plataforma.
2. **Gravação de tela opcional** (tela ou janela específica) — só no macOS por
   ora, porque a mesma API da captura de áudio (ScreenCaptureKit) entrega isso.
3. **Google Meet via extensão de Chrome** — áudio da aba + timeline de falantes
   com nome real, transcrição continua local. Sem bot, sem servidor, sem conta
   vinculada (decisão de 02/08: preserva o diferencial local/privado/grátis).

**Ler antes de começar:** `Reco/CLAUDE.md` (arquitetura essencial + regra de
compilar), `Reco/docs/ARMADILHAS.md` (anti-loop, AEC, deriva de clock),
`C:\Dev\cerebro\projetos\reco.md` (decisões recentes e pendências).

⚠️ **Fases 1, 2 e 4 executam NO Mac** (Claude Code instalado no próprio Mac,
clonando o repo). A sessão executora no Windows só consegue a Fase 3 e os
guards preparatórios da Fase 1. A regra "recompilar com `build.ps1`" é do exe
Windows — no Mac, v1 roda do fonte (venv), sem PyInstaller.

## Alvo e estado atual

- Código em `reco.py` (um arquivo) + `tray.py` (bandeja Win32 pura).
- Captura: `soundcard` (linha ~498). Mic em `DualRecorder._rec_mic` (~1477),
  sistema em `_rec_sys` (~1503) via `include_loopback=True` — **WASAPI-only**;
  no macOS o `soundcard` captura mic (CoreAudio) mas **não tem loopback**.
- Transcrição: `make_transcriber()` (~2286) já escolhe `MLXTranscriber` (~2180)
  em darwin/arm64 com `mlx_whisper` instalado. O MLX transcreve por janela cega
  de 30 s — **sem** VAD, `initial_prompt` nem `dominancia_sistema` (só split de
  canal + AEC). Anti-loop: `mlx_whisper` traz os limiares do Whisper de
  referência (`compression_ratio_threshold` etc.), que o OpenVINO não expõe.
- Modo ao vivo: `LiveTranscriber` (~1984) é acoplado ao `OVTranscriber`
  (recebe a instância, reusa o pipeline) — não funciona com MLX.
- Bandeja: `HAS_TRAY = (os.name == "nt")` (~507) — já degrada no Mac.
- Windows-specific soltos: `set_dark_titlebar` (~235, try/except ok),
  `_system_lang` (~247, fallback locale ok), `os.startfile` (~3740, quebra no
  Mac), atalho Start Menu (~3746, sem sentido no Mac), `_user_data_dir` (~540,
  cai em `Path.home()/Reco` — funciona, não é o padrão mac).

## Diagnóstico

### O que está bom (não mexer)

- Encoder MP3 streaming (`MP3Writer`/`_pump`): agnóstico de plataforma, com
  teste dedicado (`tools/test_encoder.py`). O contrato "chunks float32 mono a
  `CAPTURE_SR`" é a costura perfeita para plugar captura nova.
- `make_transcriber()` já é o seam de backend — o porte não mexe em quem chama.
- Bandeja e dark titlebar já se auto-desligam fora do Windows.
- Pipeline de decode (PyAV) lê webm/opus/mp4 — a Fase 3 não precisa de decoder
  novo.

### O que está frágil ou custando

- **Áudio do sistema no macOS não existe** — é o único bloqueador real do
  porte. Evidência: `_rec_sys` depende de `include_loopback` (WASAPI).
- **Qualidade da transcrição no Mac é inferior à do Windows** — janela cega de
  30 s contra VAD+contexto+dominância do OV. Suspeita não medida (sem hardware
  para medir ainda); vira medição na Fase 4.
- **`os.startfile` e o atalho de teclado** quebram/não existem no Mac —
  pequenos, mas travam o uso básico ("Reproduzir" morre com exceção tratada).
- **Meet hoje**: gravar reunião pelo Reco desktop mistura todos os
  interlocutores num canal só ("Interlocutor" genérico). A plataforma sabe quem
  fala; a informação é jogada fora.

## Roadmap

### Fase 0 — guards de plataforma (executável no Windows, hoje)

Preparo barato que não precisa de Mac e não muda comportamento no Windows.

1. [x] `reco.py` `_abrir_arquivo(path)`: helper que usa `os.startfile` no
   Windows, `subprocess.Popen(["open", path])` no darwin; trocar o uso em
   ~3740. Pronto quando: busca por `os.startfile` só aparece dentro do helper.
   **Executado em 07/08/2026** — o roadmap citava só a chamada de ~3740, mas
   havia mais 3 usos crus (`_open_url` ~2504, `_open_cv_folder` ~3661,
   `_open_tr_folder` ~3733); os 4 foram trocados pelo helper (novo, logo após
   `_bundled_models_dir`, ~552) para o próprio critério de pronto
   ("só aparece dentro do helper") bater. `grep -n "os.startfile" reco.py`
   confirma: só as 2 linhas do helper (chamada + docstring).
2. [x] Esconder o link de atalho de teclado (`_shortcut_path`/`_toggle_shortcut`,
   ~3746) quando `os.name != "nt"` — o widget nem é criado. Pronto quando: no
   Windows nada muda (conferir visualmente após recompilar).
   **Executado em 07/08/2026** — criação do `self._sc_link` (e a chamada a
   `_update_shortcut_link()`) envolvida em `if os.name == "nt":`. Confirmação
   visual pós-recompilação **não rodou** (ver item 4) — validado só a nível
   lógico: `os.name` é `"nt"` nesta máquina (mesma condição já usada por
   `HAS_TRAY`), então o guard novo é `True` e o comportamento no Windows é
   idêntico byte-a-byte ao de antes.
3. [x] `_user_data_dir()` (~540): ramo darwin →
   `Path.home()/"Library"/"Application Support"/APP_NAME`. Pronto quando:
   teste rápido em REPL Windows continua devolvendo `%LOCALAPPDATA%\Reco`.
   **Executado em 07/08/2026** — `python -c "import reco; print(reco._user_data_dir())"`
   devolveu `C:\Users\Gabriel dos Anjos\AppData\Local\Reco` (== `%LOCALAPPDATA%\Reco`).
4. [x] Recompilar (`build.ps1`) e commitar (pathspec: `reco.py`).
   **Concluído em 10/08/2026** — Gabriel fechou o app e autorizou; build verde
   (`dist\Reco\Reco.exe` de 10/08 12:03, pasta 835 MB), commits já pushados.
   ~~Bloqueado em 07/08/2026~~ — `Reco.exe` (PID 10608) estava rodando durante
   a execução desta fase e trava `dist\Reco\_internal\...` (`PermissionError:
   Acesso negado` no `shutil.rmtree` do PyInstaller). Não matei o processo —
   é o app que o Gabriel usa no dia a dia e pode estar gravando. O código
   (`reco.py`) foi commitado por conta própria (validado por `ast.parse` +
   `import reco`, sem recompilar); falta o Gabriel fechar o `Reco.exe` e rodar
   `powershell -ExecutionPolicy Bypass -File "C:\Dev\Reco\build.ps1"` para o
   `.exe` distribuído refletir esta mudança — enquanto isso não acontecer, o
   `dist\Reco\Reco.exe` que o Gabriel usa **não tem** os guards desta fase.

### Fase 1 — macOS: rodar do fonte com captura completa (executa NO Mac)

1. [ ] Bootstrap: clonar o repo no Mac, venv Python 3.11+, instalar
   `requirements.txt` + `mlx_whisper` + `pyobjc-framework-ScreenCaptureKit`.
   Rodar `python reco.py`: UI abre, mic grava (canal `sys` pode falhar — ok
   por enquanto, `_fail` preenche com silêncio). Registrar o que quebrar.
2. [ ] **Spike timeboxed (meio dia)** — captura de áudio do sistema via
   ScreenCaptureKit por PyObjC: `SCShareableContent` + `SCStream` só-áudio,
   converter `CMSampleBuffer` → numpy float32 mono. Critério: script
   `tools/mac/spike_sck_audio.py` grava 30 s de áudio do sistema com RMS > 0
   enquanto música toca. **Se o spike falhar/penar**: helper Swift
   (`tools/mac/RecoSysAudio.swift`, compilado com `swiftc`) que imprime PCM
   cru (float32, 48 kHz, mono) no stdout; o Python lê por `subprocess.Popen`.
3. [ ] Integrar como fonte do canal `sys`: em `DualRecorder._rec_sys`, ramo
   darwin que consome a fonte do passo 2 e alimenta `chunks` no MESMO contrato
   (float32 mono `CAPTURE_SR`, blocos ~`CHUNK`). Barrier, pausa (ler e
   descartar) e `_fail` preservados. Pronto quando: `tools/test_encoder.py`
   passa e `tools/test_gravacao_real.py 30` grava os dois canais no Mac.
4. [ ] Permissões TCC: primeira captura dispara os prompts de microfone e
   gravação de tela (SCK exige a segunda mesmo sem vídeo). Tratar recusa com
   mensagem clara no status (reusar o padrão "nenhuma saída de áudio", ~3166).
5. [ ] Modo ao vivo: garantir que `live` não liga com MLX — guard no ponto que
   instancia `LiveTranscriber` (só quando o transcriber é `OVTranscriber`).
   Pronto quando: config `live=True` num Mac não crasha, mostra aviso.
6. [ ] Validação de ponta a ponta no Mac: gravar ~5 min com fala real nos dois
   canais (chamada ou vídeo tocando), transcrever com diarização. Critérios:
   duração do MP3 bate (±1 s), L/R separados, transcript legível nos dois
   locutores. Registrar tempo de transcrição no md (baseline pro Fase 4).

### Fase 2 — gravação de tela opcional (executa NO Mac, depois da Fase 1)

1. [ ] Seletor: `SCContentSharingPicker` (picker nativo de tela/janela) ou, se
   o binding penar, lista própria via `SCShareableContent` num combo Tkinter.
   Toggle "Gravar tela" desligado por default, persistido em
   `~/.reco_config.json` (default novo em `_CFG_DEFAULTS`, ver armadilha de
   defaults no `CLAUDE.md`).
2. [ ] Pipeline de vídeo: frames do `SCStream` → PyAV H.264 (libx264,
   `preset=veryfast`, ~10-15 fps já serve para tela) → MP4 com trilha AAC
   estéreo (mesmo L=mic/R=sys). MP3 continua sendo o artefato canônico da
   transcrição; o MP4 é para humanos. Mesmo basename, extensão `.mp4`.
3. [ ] Ciclo de vida: iniciar/parar junto com a gravação de áudio; pausa
   descarta frames (mesma semântica dos canais); falha do vídeo **não** derruba
   o áudio (espelhar o padrão `_fail`).
4. [ ] Validação: gravar 2 min de uma janela com áudio, abrir no QuickTime.
   Critérios: A/V em sincronia (bater palma na frente da tela — desvio < 200 ms),
   MP3 continua íntegro, toggle off = comportamento idêntico ao da Fase 1.

### Fase 3 — extensão de Chrome para o Meet (executável no Windows, hoje)

Subprojeto novo `Reco/extensao/` (MV3). v1 **sem IPC**: a extensão grava e
baixa arquivos; o Reco processa offline. Nada de servidor.

1. [ ] Esqueleto MV3: `manifest.json` (permissões `tabCapture`, `offscreen`,
   `downloads`, host `meet.google.com`), popup com botão gravar/parar, ícone do
   logo existente (`logo/`). Pronto quando: carrega em `chrome://extensions`
   sem erro e o popup abre num Meet.
2. [ ] Captura de áudio da aba: `chrome.tabCapture` iniciado por gesto do
   usuário → `MediaRecorder` (webm/opus) num offscreen document. ⚠️ re-tocar o
   stream num `AudioContext` para o áudio da aba não mutar para o usuário.
   Pronto quando: webm de 1 min de um Meet toca com o áudio da reunião.
3. [ ] Timeline de falantes: content script coleta eventos com timestamp
   relativo ao início da gravação, de DUAS fontes: (a) legendas do Meet
   (nome + texto — pedir ao usuário para ligar legendas; é a fonte mais
   estável) e (b) indicador visual de fala ativa como fallback. Exportar
   `timeline.json`: `[{t0, t1, nome, texto?}]`. Pronto quando: reunião de
   teste com 2+ pessoas gera nomes e tempos coerentes.
4. [ ] Ao parar: baixar `meet-<data>.webm` + `meet-<data>.timeline.json` via
   `chrome.downloads` na pasta padrão.
5. [ ] Ponte no Reco: `tools/transcrever_meet.py <webm> [timeline.json]` —
   decodifica com o decode PyAV existente, transcreve pelo pipeline real
   (mesmo caminho do `tools/transcrever.py`), e funde: cada segmento
   transcrito recebe o nome de quem a timeline aponta como falante naquele
   intervalo (sem timeline → "Interlocutor"). Saída `<arquivo>.txt` no mesmo
   formato do Reco. Pronto quando: reunião de teste sai com nomes reais no
   transcript.
6. [ ] Documentar em `extensao/README.md`: instalação (modo desenvolvedor),
   fluxo, limitações (legendas precisam estar ligadas; DOM do Meet pode
   mudar — sintoma: timeline vazia, transcript cai para "Interlocutor").

### Fase 4 — unificar o pipeline de transcrição (qualidade mac; NO Mac)

Opcional, só depois da Fase 1 validada e SE a qualidade MLX incomodar.

1. [ ] Medir primeiro: transcrever o mesmo MP3 de teste no Windows (OV) e no
   Mac (MLX), diff dos transcripts. Se a divergência for irrelevante, **parar
   aqui** e marcar a fase como descartada por medição.
2. [ ] Se incomodar: extrair de `OVTranscriber` a orquestração por canal
   (VAD `segmentar_por_vad` + agrupamento + contexto + `dominancia_sistema`)
   para função compartilhada que recebe um callable `generate(audio, prompt)`;
   `MLXTranscriber` passa a usá-la. Gate obrigatório: `tools/test_e2e.py` e
   `tools/test_antiloop.py` no Windows sem regressão (mesmo texto do baseline).

## Priorização (impacto × esforço × risco)

| item | impacto | esforço | risco | veredito |
| --- | --- | --- | --- | --- |
| Fase 0 guards | baixo | mínimo | nulo | fazer já (destrava a 1) |
| Fase 1 porte mac | alto | médio (spike é a incógnita) | médio | núcleo do roadmap |
| Fase 2 tela | médio | baixo (API já paga pela Fase 1) | baixo | logo após a 1 |
| Fase 3 extensão Meet | alto | médio | médio (DOM do Meet) | independente — pode ser a primeira executada |
| Fase 4 unificação | médio | médio | médio (regressão Windows) | só após medir |

## O que NÃO fazer

- **Meet Media API do Google**: segue em developer preview e exige projeto,
  OAuth e TODOS os participantes inscritos no programa — inviável para uso
  real. Reavaliar quando sair do preview (aí entrega streams por participante
  e mata a leitura de DOM).
- **Bot participante** (estilo Fireflies/Recall): exige servidor rodando
  browsers, bot visível na call, e mata o diferencial local/grátis.
- **Gravação de tela no Windows**: API separada (Windows.Graphics.Capture),
  sem demanda hoje. Só se o uso no Mac provar valor.
- **Bandeja/menu bar no Mac v1**: `HAS_TRAY` já degrada; menu bar (rumps) é
  polimento para depois do porte validado.
- **Empacotar .app assinado/notarizado**: uso é pessoal, rodar do fonte basta.
  Assinatura só quando houver distribuição a terceiros.
- **Camada de IA (resumo/perguntas sobre transcrição pronta)**: fora deste
  roadmap por decisão de 02/08 — é pós-processamento desacoplado; quando vier,
  uso pessoal via Claude Code headless (`claude -p`), sem chave de API.
- **BlackHole/dispositivo virtual como solução final**: ok como fallback
  manual se o spike E o helper Swift falharem, mas exige config manual do
  usuário — não é o produto.

## Riscos e pré-requisitos

- **Hardware**: Fases 1, 2 e 4 precisam de um Mac M1+ com Claude Code — sem
  ele, executar só Fases 0 e 3.
- **Spike SCK/PyObjC pode falhar** (conversão `CMSampleBuffer`→numpy é o ponto
  fraco): o fallback Swift está no próprio passo 1.2 — não é fim de linha.
- **DOM do Meet muda sem aviso**: a Fase 3 prefere legendas (mais estáveis) e
  documenta o sintoma da quebra. Aceitar como custo estrutural da abordagem.
- **macOS pede permissão de gravação de tela até para só-áudio** via SCK —
  comunicar isso na UI para não parecer invasivo.
- Teste de estresse de 20 min do modo ao vivo (pendência anterior do hub)
  segue aberto e **não** é pré-requisito destas fases.

> Auditoria futura: se o Meet Media API sair do preview ou o Gabriel decidir
> distribuir o Reco a terceiros, este roadmap deve ser re-melhorado — as
> decisões "extensão sem servidor" e "sem assinatura de app" partem do uso
> pessoal.

> Auditado em 2026-09-01 (overhaul): VIVO — Fase 0 executada 07/08 (reco.py:538-558 darwin); F3 (extensão Meet) nunca iniciada; F1/F2/F4 congeladas sem Mac (card c931a399e754f)
