# Roadmap de melhoria — Reco: biblioteca de gravações + resumo IA (2026-08-12)

## Contexto e motivação

Segunda leva do dia, disparada pelo Gabriel após a auditoria de UX/UI/lógica:
ele apontou 4 concorrentes (Meetily, Char, anarlog, Off Grid AI Desktop) e deu
mandato — *"o que desses projetos deveríamos implementar? total liberdade pra
refazer tudo o que achar válido"*. Este md registra a análise de mercado, o
recorte escolhido e o contrato de execução (executado na própria sessão).

**Ler antes:** `Reco/CLAUDE.md` (regra de recompilar; arquitetura),
`roadmap/2026-08-12-melhoria-ux-ui-logica.md` (estado pós-execução: Lixeira,
check_i18n, links "Abrir transcrição" — esta leva constrói em cima).

## Análise de mercado — o que cada um tem, o que roubar

| projeto | o que é | o que vale roubar | o que NÃO |
| --- | --- | --- | --- |
| **Meetily** (MIT, Tauri/Rust, Whisper/Parakeet local, resumo via Ollama/Claude/…) | o concorrente mais direto do conceito | **biblioteca de reuniões + busca em transcrições**; resumo por LLM | Ollama embarcado (Reco já pesa 835 MB; diarização deles é "planejada" — a nossa por canal já existe) |
| **Char** (fechado, YC, $12+/mês) | notepad IA "chief of staff", integrações Gmail/Slack/… | a constatação de que **o produto é o pós-reunião**, não o áudio | notepad durante reunião, agente, integrações — outro produto, mata o local/grátis |
| **anarlog** (MIT, ex-Hyprnote open core) | notas de reunião local-first, SQLite, export md | confirma biblioteca + resumo como mesa mínima; export md | multi-provider LLM (decisão de 02/08: IA pessoal via `claude -p`, sem chave) |
| **OGAD / Off Grid** (AGPL, Electron) | grava tela+mic+sistema, OCR, journal, RAG | nada estrutural — é um Rewind-like | gravação de tela no Windows (decisão de 02/08 mantida: tela é mac-first, Fase 2 de lá) |

**Síntese:** o que TODOS têm e o Reco não tinha: (1) **biblioteca das
gravações passadas com busca**; (2) **resumo por IA**. O resto ou o Reco já
tem melhor (diarização por canal físico, AEC medido, NPU/iGPU) ou contradiz
decisões registradas (tela no Windows, nuvem, contas).

⚠️ **Reversão consciente de um "NÃO fazer" de hoje cedo:** o roadmap de UX
dizia "não reintroduzir a lista de gravações (Treeview); repropor só se o
Gabriel pedir" — o mandato desta leva é exatamente o Gabriel pedindo. Os
estilos `D.Treeview*` removidos na Fase 5 voltam, agora com consumidor real.

## Roadmap

### Fase A — biblioteca de gravações (view "Gravações…")

1. [x] 4ª view exclusiva `lib` ao lado de rec/tr/cv: link "Gravações…" na
   `_links_row`, seção com Treeview (colunas Arquivo/Data/Duração/📄),
   busca por nome E por conteúdo dos .txt (debounce 250 ms), ações por
   ícone com caption de hover (▶ reproduzir, ⚡ transcrever com ⬛ parar,
   📄 abrir .txt, ✕ excluir via Lixeira), "Abrir pasta" e "↺ Atualizar".
   Scan em thread (duração via PyAV com cache por (path, mtime)); estilos
   `D.Treeview*`/`D.Vertical.TScrollbar` reintroduzidos em `_apply_style` e
   aplicados TAMBÉM ao scrollbar do painel ao vivo (fecha a pendência 5 do
   hub). — **prova:** `python tools/check_i18n.py` verde; `python -c "import
   reco"`; manual: view lista as gravações de `Documents\Reco`, busca por
   palavra do transcript filtra, ✕ manda pra Lixeira.

### Fase B — resumo IA via `claude -p` (decisão de 02/08 honrada)

1. [x] Ação ✦ na biblioteca: com .txt presente, roda `claude -p <prompt>`
   (CLI do Claude Code achada por `shutil.which`; transcript via stdin;
   sem chave de API, sem rede própria do app) e salva
   `<gravação>.resumo.md` ao lado; se o resumo já existe, abre. Sem CLI →
   status explicando; app segue 100% funcional sem isso. Prompt fixo
   PT/EN (resumo + decisões + pendências), `PROMPT_RESUMO` module-level.
   — **prova:** check_i18n verde; import limpo; manual: ✦ numa gravação
   transcrita gera e abre o .resumo.md.

### Fase C — docs e fechamento

1. [x] README (EN+PT): bullets da biblioteca e do resumo (deixando claro:
   resumo é opcional e usa o `claude` local do usuário); `CLAUDE.md` §
   novo curto. — **prova:** `grep -in "resumo\|library" README.md`.
2. [x] `build.ps1` (exe fechado), commits, índice de roadmaps regenerado,
   diário + hub. — **prova:** build verde; `git log`.
   **Fechado em 12/08, em dois tempos:** commits, índice (gerador do cérebro
   corrigido para reconhecer `1. [x]`), diário e hub saíram primeiro;
   a recompilação ficou bloqueada por `Reco.exe` aberto (PID 13684 —
   processo em uso não se mata, precedente de 07/08) e rodou assim que o
   Gabriel fechou o app: build verde (835,2 MB), `--selftest` do exe
   congelado OK (`frozen=True`, backend openvino, AUTO→GPU).

> Executado em 12/08/2026, mesma sessão: smoke test com Tk real listou 30
> gravações e a busca filtrou; `check_i18n` verde (ganhou o padrão
> `_lib_action`); um commit de roadmap + um de código/docs.

## O que NÃO fazer (desta leva)

- **Gravação de tela no Windows** (OGAD faz): decisão de 02/08 de pé — tela
  é mac-first via ScreenCaptureKit (Fase 2 de lá). Reavaliar só depois do mac.
- **Notepad durante a reunião** (Char/anarlog): UX pesada pra Tk e o fluxo
  do Gabriel já tem Claude Code/cérebro pro pós-reunião.
- **LLM local embarcado (Ollama)** e **multi-provider**: `claude -p` cobre o
  uso pessoal sem chave, sem +GB no bundle.
- **Templates de resumo editáveis** (Meetily): prompt fixo primeiro; template
  só se o prompt fixo se provar insuficiente no uso.
- **SQLite/banco** (anarlog/OGAD): o filesystem (mp3+txt+resumo.md lado a
  lado) É o banco — greppável, portátil, sem migração.

## Riscos

- Scan de duração abre cada MP3 com PyAV — mitigado por cache por
  (path, mtime) e thread; pasta típica tem dezenas de arquivos.
- `claude -p` depende do binário no PATH e consome a assinatura do Gabriel —
  por isso é opt-in por clique, nunca automático.
- Recompilação exige `Reco.exe` fechado (pendência conhecida).

> Executado na mesma sessão (12/08/2026) — ver marcações `[x]` e o diário.

> Auditado em 2026-09-01 (overhaul): FECHADO — view Gravações e resumo via claude -p em reco.py (l.1266); build de 12/08
