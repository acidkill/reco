# Roadmap de melhoria — Reco: UX, UI e lógica (2026-08-12)

## Contexto e motivação

Auditoria completa de `reco.py` (4089 linhas) + `tray.py` (314), pedida pelo
Gabriel via `/melhore` em 12/08/2026: UX, UI e lógica, em busca de melhorias e
boas práticas. O Reco é uso diário (gravação de reuniões) e a visão de produto
é "OBS de conversas" (hub, 02/08) — polir o app Windows atual é pré-requisito
de qualquer distribuição futura.

Este roadmap NÃO toca no que o roadmap de 02/08
(`roadmap/2026-08-02-melhoria-evolucao-mac-tela-meet.md`) já cobre: porte
macOS, gravação de tela e extensão Meet seguem lá. Aqui é o app de hoje.

**Ler antes de começar (obrigatório):**

- [`CLAUDE.md`](../CLAUDE.md) — arquitetura essencial, regra de recompilar
  (`build.ps1`) e armadilha dos defaults de config (`CFG_MIGRACAO`).
- [`docs/ARMADILHAS.md`](../docs/ARMADILHAS.md) — anti-loop, AEC, deriva de
  clock. Nada aqui mexe nesses subsistemas; a leitura é para NÃO mexer.
- Hub `C:\Dev\cerebro\projetos\reco.md` — decisões recentes e pendências.

**Regras do projeto que valem para TODO passo:** ao mexer em `reco.py`/
`tray.py`, recompilar com `powershell -ExecutionPolicy Bypass -File
"C:\Dev\Reco\build.ps1"` ao fim da fase (exige `Reco.exe` fechado — em uso, o
PyInstaller falha com `PermissionError` no `dist/`); commit por pathspec,
nunca `git add -A`; fonte com peso ≤600.

## Alvo e estado atual

- App desktop Tkinter num arquivo (`reco.py`) + bandeja Win32 pura (`tray.py`).
- Grava mic + loopback WASAPI em MP3 estéreo com encode em streaming
  (`DualRecorder` → `MP3Writer`), transcreve local (`OVTranscriber`, VAD +
  anti-loop + diarização + AEC), rascunho ao vivo opcional (`LiveTranscriber`).
- UI: janela frameless compacta com 3 views exclusivas (gravar / transcrever /
  converter), VU meters com slider de ganho, opções expansíveis, bandeja com
  hover-to-show. Bilíngue PT/EN via dicionário `_TR_EN` chaveado pelo PT.
- Distribuído como `dist\Reco\Reco.exe` (PyInstaller, modelo `small` bundlado).

## Diagnóstico

### O que está bom (não mexer)

- **Arquitetura de captura/encode** (3 threads, pareamento `_pump`, pausa em
  lockstep, `_fail` com silêncio): invariantes documentados em comentário e
  testados (`tools/test_encoder.py`). Não tocar.
- **Pipeline de transcrição**: VAD, agrupamento (`ALVO_ACUMULO_S`), anti-loop,
  `dominancia_sistema` (k_db=15) e AEC — tudo **medido**, com armadilhas
  documentadas. Qualquer "melhoria" aqui exige re-medição; fora de escopo.
- **Config**: escrita atômica + mecanismo de migração (`CFG_MIGRACAO`).
- **Bandeja** (`tray.py`): hover com hot zone, DPI-aware, TaskbarCreated.
  Código denso mas correto e bem comentado.
- **Marshalling de thread → UI** via `queue` + `_drain_ui`: padrão certo p/ Tk.
- **Tema com contraste automático** (`apply_theme`/`_best_fg`): conceito bom
  (um bug pontual de default-arg, ver abaixo).
- **Modos headless** (`--selftest`, `--transcribe`) e a suíte `tools/`.

### O que está frágil ou custando

Cada item marca a evidência: **(lido)** = verificado por leitura do código,
não reproduzido; **(morto)** = confirmado por grep de uso.

**Lógica / robustez**

- **F1. Menu do ⚡ com item duplicado e ação inalcançável (lido+morto).**
  `reco.py:2722-2725`: os itens "⚡ Salvar + Transcrever" e "🔤 Transcrever"
  chamam AMBOS `_conclude_and_transcribe`. `_conclude_transcribe_and_delete`
  (`~3393`) nunca é chamado e a string "⚡  Transcrever + excluir" (`:279`)
  está morta. O fluxo transcrever-e-apagar-o-áudio é inatingível pela UI.
- **F2. Exclusão permanente sem confirmação (lido).** `_conclude_delete`
  (`~3384`) e o ramo `delete_after` (`~3526`) fazem `unlink()` — um clique no
  ✕ (que tem só caption de hover, não confirmação) destrói uma reunião de 2 h
  sem passar pela Lixeira.
- **F3. Corrida live × lote no mesmo `WhisperPipeline` (lido).** `_start_rec`
  (`~3192`) não checa `self._transcribing`: começar a gravar com "ao vivo"
  ligado enquanto uma transcrição roda gera `pipe.generate` concorrente no
  mesmo pipeline — exatamente o que o drain-then-start do stop existe para
  impedir. Cenário real: transcreve a reunião anterior e a próxima começa.
- **F4. Timeout do drain pode reabrir a mesma corrida (lido).**
  `LiveTranscriber.stop(wait=True, timeout=15)` (`~2059`) + fila de até 60 s
  (`FILA_MAX_S`): se o backlog demorar mais que 15 s, o `join` expira e a
  passada final começa com o worker ainda gerando. Agravante: drenar o
  backlog para o rascunho é trabalho inútil — a passada final substitui tudo.
- **F5. Instalação nova usa `small` em silêncio (lido).** `_find_model_dir`
  (`1074-1089`): pedido `large-v3-turbo` sem match, cai em `return valid[0]`
  — o `small` bundlado. `ensure_ov_model` nunca baixa porque recebeu um dir.
  Máquina nova transcreve para sempre com o modelo que loopava (221
  repetições), achando que usa turbo. A do Gabriel não é afetada (turbo já
  baixado em 29/07).
- **F6. Sem instância única (lido).** O atalho Ctrl+Shift+R é um `.lnk` que
  LANÇA o exe: apertar com o Reco aberto abre uma 2ª instância (bandeja
  duplicada, disputa dos mesmos dispositivos). `RegisterClassW` até tolera a
  classe repetida (err 1410, `tray.py:132`).

**UX / feedback**

- **F7. Transcrição disparada da tela de gravação não tem cancelamento
  (lido).** O botão ⬛ Parar da transcrição só existe na view "tr"
  (`_tr_show_stop`), e `_show_view` (`~3574`) bloqueia trocar de view
  enquanto `_transcribing` — uma transcrição de 12 min iniciada pelo ⚡ não
  tem como ser cancelada pela UI.
- **F8. Cliques bloqueados sem feedback (lido).** Durante gravação/
  transcrição, clicar em "Transcrever…"/"Converter…" é no-op silencioso
  (`_show_view` retorna sem status).
- **F9. Erro com a janela oculta na bandeja é invisível (lido).** Falha de
  stream/salvamento atualiza `_status` numa janela `withdraw()`n; o tooltip
  da bandeja segue "pronto". `tray.py` já tem `szInfo` no `NOTIFYICONDATA` —
  balão nunca é usado.
- **F10. O .txt pronto não tem acesso de um clique (lido).** Ao fim, o status
  diz "Transcrição salva: X" — abrir exige ir à view de transcrição e "Abrir
  pasta". O produto do app é o .txt; ele merece um link direto.

**i18n / textos**

- **F11. Modo ao vivo inteiro sem tradução EN (morto).** Nenhuma destas
  strings está em `_TR_EN`: linhas 2056, 2085, 2890, 3300, 3339, 3348, 3361,
  3363 (rascunho atrasado, worker parou, checkbox, "Fechando rascunho…",
  "refinando…", "Rascunho mantido…", "Transcrição final pronta…"). Usuário EN
  vê PT. Não há verificação automática de cobertura.
- **F12. Strings e textos mortos/mentirosos (morto).** Docstring do módulo
  (`:13`) diz "faster-whisper" (backend é OpenVINO); bloco do instalador
  faster-whisper (`:439-459`), cabeçalhos de tabela de arquivos
  (`:394-396`), rótulos de formato ("Canais:", "Taxa:", "Modelo:", "Mono",
  "16.000 Hz"… `:302-331` parcial) — fluxos removidos, strings ficaram. O
  checkbox do live (`:2890`) hardcoda "iGPU" no rótulo, mas o device é o
  resolvido (pode ser NPU/CPU).

**UI / código morto**

- **F13. `_link` congela a cor do tema no default-arg (lido).**
  `def _link(..., fg=SUBTLE, ...)` (`:2626`) avalia `SUBTLE` no import, com o
  tema escuro default. Após trocar para tema claro, todo link sem `fg`
  explícito ("⚙ Opções", "↺ Atualizar dispositivos", atalho, "Abrir pasta")
  fica no cinza do tema escuro.
- **F14. Vestígios (morto, confirmado por grep):** `_tr_win`
  (`:2451,2996,3000` — nunca recebe janela), estilos `D.Treeview*`
  (`:2590-2600` — nenhum Treeview existe), global `OUTPUT_DIR` (`:101,1397`
  — App sempre passa `out_dir`; o global não segue a config).
- **F15. Persistência a cada pixel de arrasto (lido).** `_on_gain` (`~2690`)
  roda `save_config` (com `fsync`) em CADA evento `<B1-Motion>` do slider —
  dezenas de escritas síncronas por arrasto.
- **F16. Timer duplica o loop em pausa/retoma rápida (lido).** `_resume_rec`
  chama `_tick_timer`/`_blink_dot` sem cancelar o `after` pendente — retomar
  antes do tick agendado disparar cria dois loops (cosmético). `_elapsed` usa
  `time.time()` (vulnerável a ajuste de relógio; `time.monotonic()` é o
  correto).

**Docs**

- **F17. Docs desatualizadas (lido).** `CLAUDE.md` aponta
  `docs/CONSOLIDADO-2026-07-15.md`, que não existe (docs/ tem só ARMADILHAS e
  screenshot); README promete "fully offline / model bundled" mas o default
  `large-v3-turbo` não está no bundle (ver F5); `docs/screenshot.png` é de
  20/06 — sem readout de ganho, sem pausa, sem links de converter.

**Conceitos do acervo aplicados** (sabatina): `via-negativa` — a Fase 5 corta
código/strings mortos antes de qualquer feature nova, e lista de gravações /
tag ID3 foram para "O que NÃO fazer"; `ancoragem-e-confirmacao` — todo achado
"(lido)" ganha prova executável própria no passo que o corrige, em vez de
confiar no diagnóstico.

## Roadmap

Cada fase termina com: `python -c "import reco"` limpo, recompilação
(`build.ps1`, exe fechado) e commit por pathspec dos arquivos tocados.
Rodar os comandos com o Python do venv do projeto (`C:\Dev\Reco`).

### Fase 1 — lógica: bugs e riscos reais

1. [x] **Menu ⚡ (F1):** em `reco.py:~2722`, trocar o item
   `("🔤  Transcrever", self._conclude_and_transcribe)` por
   `("⚡  Transcrever + excluir", self._conclude_transcribe_and_delete)`
   (string já existe em `_TR_EN:279`). — **prova:**
   `grep -n "_conclude_transcribe_and_delete" reco.py` → 2 linhas (def +
   call); visual pós-build: menu do ⚡ com 3 ações distintas.
2. [x] **Lixeira (F2):** helper `_excluir_gravacao(path) -> bool` que no
   Windows usa `SHFileOperationW` com `FOF_ALLOWUNDO|FOF_NOCONFIRMATION|
   FOF_SILENT` (ctypes, struct `SHFILEOPSTRUCTW`, double-null no path;
   sem dependência nova) e fora do nt faz `unlink`. Usar em
   `_conclude_delete` e no ramo `delete_after` de `_transcribe_recording`.
   — **prova:** script temporário no scratchpad que cria um txt em
   `Documents\Reco`, chama `reco._excluir_gravacao` e confere que o arquivo
   saiu da pasta; manual: item aparece na Lixeira (shell API não é
   automatizável barato).
3. [x] **Guard live × lote (F3):** em `_start_rec`, condicionar
   `self._live_was_on` também a `not self._transcribing`; quando suprimir,
   `self._status(t("Rascunho ao vivo desativado — transcrição em andamento."))`
   (string nova nas DUAS línguas). — **prova:**
   `grep -n "_transcribing" reco.py` mostra o guard dentro de `_start_rec`;
   `python -c "import reco"` limpo.
4. [x] **Stop do live descarta o backlog (F4):** `LiveTranscriber.stop`
   ganha `discard: bool = False`; com `True`, seta um evento que faz `_loop`
   esvaziar a fila sem transcrever (só o `generate` em curso termina) — o
   `join` passa a ser limitado por UM grupo, não por 60 s de backlog. O app
   chama `stop(wait=True, discard=True)` no `do_stop` (`~3301`); tools/testes
   continuam com o default. — **prova:** `python tools/test_live.py <um
   gravacao_reco_*.mp3 de Documents\Reco> 60` segue passando (comportamento
   default intacto); `grep -n "discard" reco.py` mostra assinatura + call.

### Fase 2 — i18n e textos

1. [x] **`tools/check_i18n.py` (F11):** extrai por AST todo literal dentro de
   `t("…")`/`tf("…")` em `reco.py` e `tray.py`, compara com `_TR_EN` e
   imprime `FALTANTES` (usadas sem tradução) e `MORTAS` (na tabela sem uso).
   Exit code 1 se houver faltante. — **prova:** primeira execução lista
   exatamente as 8 strings do F11 como faltantes.
2. [x] **Cobrir as faltantes (F11):** adicionar as traduções EN à `_TR_EN`.
   — **prova:** `python tools/check_i18n.py` → 0 faltantes.
3. [x] **Remover as mortas (F12):** apagar da `_TR_EN` o que o checker listar
   como morto (instalador, tabela de arquivos, rótulos de formato…),
   conferindo cada uma no grep do checker antes. — **prova:** checker → 0
   mortas (as deliberadamente mantidas, se houver, documentadas no próprio
   script como allowlist).
4. [x] **Textos que mentem (F12):** docstring do módulo (faster-whisper →
   OpenVINO GenAI/MLX); rótulo do checkbox live → `"Transcrição ao vivo
   (rascunho)"` sem hardcode de device (ajustar as duas línguas). —
   **prova:** `grep -n "faster-whisper" reco.py` → 0; checker verde.

### Fase 3 — feedback e affordances

1. [x] **Cancelar da tela de gravação (F7):** durante transcrição iniciada
   pelo fluxo de gravação, empacotar um botão "⬛ Parar" (reusar
   `_stop_transcription`) na linha de status da view rec; desempacotar no
   done. — **prova:** manual pós-build (Tk não se automatiza barato):
   ⚡ numa gravação curta → botão aparece → clicar → "Transcrição cancelada.".
2. [x] **Feedback de navegação bloqueada (F8):** `_show_view` bloqueado →
   status "Termine a gravação para trocar de tela." / variante p/
   transcrição (strings novas nas duas línguas). — **prova:** checker i18n
   verde; manual: clicar "Converter…" gravando mostra a mensagem.
3. [x] **Balão de bandeja (F9):** `Tray.balloon(titulo, msg)` com `NIF_INFO`
   (flag `0x10`, `szInfo`/`szInfoTitle` já existem na struct); chamar em
   `_on_stream_error` e `_after_stop` quando `self._hidden`. — **prova:**
   manual: gravar pela bandeja com janela oculta, parar pela bandeja → balão
   "Salvo: …".
4. [x] **Abrir o .txt em um clique (F10):** após transcrição concluída
   (fluxos rec e tr), mostrar link "Abrir transcrição" que chama
   `_abrir_arquivo(txt)`. — **prova:** manual: transcrever → link abre o
   .txt no editor padrão.
5. [x] **Debounce do ganho (F15):** persistir no `<ButtonRelease-1>` do
   `VuMeter` (ou `after(500)` cancelável); `set_gain` do recorder continua
   imediato. — **prova:** manual: arrastar 3 s → mtime de
   `~/.reco_config.json` muda 1 vez, não dezenas (conferir com
   `(Get-Item ~/.reco_config.json).LastWriteTime` antes/depois).
6. [x] **Timer (F16):** guardar o id do `after` de `_tick_timer`/`_blink_dot`
   e cancelar antes de reagendar em `_resume_rec`; trocar `time.time()` por
   `time.monotonic()` em `_start_ts`/`_elapsed`/`_pause_rec`/`_resume_rec`.
   — **prova:** `grep -n "time.time()" reco.py` → 0 no caminho do timer;
   manual: pausa/retoma rápida não acelera o pisca-pisca.

### Fase 4 — comportamento: modelo e instância única

1. [x] **Modelo exato, fallback explícito (F5):** `_find_model_dir(size)`
   passa a devolver `None` quando `size` foi pedido e não há match (remover o
   fallback `valid[0]` do caminho com `size`). `ensure_ov_model` então baixa
   (fluxo e strings de progresso já existem); se o download falhar (offline),
   usar o melhor dir válido existente com status explícito "Sem internet —
   usando o modelo '{size}' embutido." (string nova, 2 línguas). —
   **prova:** `tools/test_model_dir.py` novo: monta árvore fake
   (`tmp/models/whisper-small-int8-ov/` com o sentinel
   `openvino_encoder_model.xml`), monkeypatcha `_user_data_dir`/
   `_bundled_models_dir` e afirma: pedido `small` → acha; pedido
   `large-v3-turbo` → `None`. Roda no CI de bolso: `python
   tools/test_model_dir.py` → `OK`.
2. [x] **README junto (F5/F17):** ajustar a promessa "fully offline": o
   bundle traz `small` (fallback offline); o default `large-v3-turbo` baixa
   ~0,8 GB na primeira transcrição. — **prova:** `grep -in "offline"
   README.md` reflete o novo texto nas duas línguas.
3. [x] **Instância única (F6):** no `__main__` (antes de `App()`),
   `CreateMutexW(None, False, "Local\\Reco.SingleInstance")`; se
   `GetLastError()==183` (ERROR_ALREADY_EXISTS), fazer
   `PostMessageW(HWND_BROADCAST, RegisterWindowMessageW("Reco.Show"), 0, 0)`
   e `sys.exit(0)`. `tray._wnd_proc` trata a mensagem registrada chamando
   `on_click` (mostrar/ativar). Só no nt; guardar o handle do mutex vivo no
   processo. — **prova:** manual: `python reco.py` 2× — a 2ª ativa a janela
   da 1ª e sai; `--selftest`/`--transcribe` NÃO criam o mutex (headless não
   pode bloquear o app).

### Fase 5 — limpeza (via-negativa) e docs

1. [x] **Código morto (F14):** remover `_tr_win` (3 pontos), estilos
   `D.Treeview*`/`D.Vertical.TScrollbar` se o grep confirmar zero uso,
   global `OUTPUT_DIR` (fallback de `_new_writer` vira
   `default_output_dir()`). — **prova:** `grep -n "_tr_win\|D.Treeview\|
   OUTPUT_DIR" reco.py` → 0; `python -c "import reco"`; build verde.
2. [x] **`_link` com cor viva (F13):** assinatura `fg=None, font=None` e
   resolver `fg = fg or SUBTLE` / `font = font or SEG_XS` no corpo. —
   **prova:** manual: tema claro → "⚙ Opções" usa o cinza do tema claro
   (`#5C5C66`), conferível de olho contra os links vizinhos.
3. [x] **Ponteiros de doc (F17):** no `CLAUDE.md` do projeto, trocar a
   referência a `docs/CONSOLIDADO-2026-07-15.md` pelo destino vivo (a seção
   de ganho do próprio CLAUDE.md se basta; senão apontar o roadmap
   `2026-07-15-ganho-por-canal.md`) e registrar as decisões novas (lixeira,
   instância única, fallback de modelo). — **prova:**
   `pwsh C:\Dev\cerebro\scripts\check-links.ps1` não acusa link novo quebrado.
4. [x] **Screenshot (F17):** rodar o app recompilado e recapturar
   `docs/screenshot.png` (janela principal, tema default). — **prova:**
   manual; `git log -1 --stat docs/screenshot.png` mostra a troca.
5. [x] **Fechamento:** regenerar o índice (`python
   C:\Dev\cerebro\scripts\gerar_indice_roadmaps.py C:\Dev\Reco\roadmap
   --escrever`), marcar `[x]` aqui, consolidar no diário do dia. —
   **prova:** `roadmap/README.md` lista este arquivo como fechado.

## Priorização (impacto × esforço × risco)

| item | impacto | esforço | risco | veredito |
| --- | --- | --- | --- | --- |
| Fase 1 (menu, lixeira, corridas) | alto | baixo | baixo | fazer já |
| Fase 2 (i18n + checker) | médio | baixo | nulo | logo após — o checker protege as fases seguintes |
| Fase 3 (feedback/affordances) | médio | médio | baixo | terceira |
| Fase 4 (modelo, instância única) | médio | médio | médio | muda comportamento de instalação nova — README junto |
| Fase 5 (limpeza + docs) | baixo | baixo | nulo | fecha o ciclo; protege o executor futuro |

## O que NÃO fazer

- **Reintroduzir a lista de gravações (Treeview).** Já existiu e foi removida
  (sobraram estilos e strings — saem na Fase 5). Repropor só se o Gabriel
  pedir; a view atual (arquivo escolhido + "Abrir pasta") cobre o uso real.
- **`RECO_TAG` por tag ID3 em vez de nome de arquivo.** Renomear é raro; tag
  também morre em remux de terceiros; custo médio p/ ganho marginal.
- **Streaming no `decode_16k`** (hoje carrega o arquivo inteiro: ~460 MB p/
  2 h estéreo). Suspeita NÃO medida de problema; medir antes se um dia
  incomodar — a transcrição é offline e a máquina aguenta.
- **Auto-refresh de dispositivos (WM_DEVICECHANGE).** Sem demanda; o link
  "↺ Atualizar dispositivos" resolve.
- **Guard do `update_model_if_newer` contra transcrição concorrente.** Janela
  de risco de ~1,5 s pós-boot; o pipeline segura o modelo em memória após o
  load. Não vale o código.
- **Trocar Tk por outra stack de UI.** O roadmap do Mac depende do Tk atual;
  reescrita de UI é outro projeto, com outra decisão.
- **Qualquer ajuste em VAD, `k_db`, AEC, `ALVO_ACUMULO_S`,
  `no_repeat_ngram_size`.** Tudo medido/calibrado; armadilhas documentadas —
  re-medição obrigatória antes de tocar, e não há sintoma pedindo.
- **Confirmação modal na exclusão.** A Lixeira (Fase 1.2) é o undo correto;
  modal em cima dela seria fricção dupla.

## Riscos e pré-requisitos

- **Recompilação exige `Reco.exe` fechado** — em uso, o PyInstaller falha
  (`PermissionError` no `dist/`, caso real de 07/08). Coordenar com o Gabriel
  ao fim de cada fase.
- **Provas manuais**: os passos de UI Tk (menus, balão, tema) declaram prova
  manual porque automatizar Tkinter+bandeja não paga o custo; cada um diz
  exatamente o gesto e o resultado esperado.
- **Fase 4.1 muda instalação nova** (download de ~0,8 GB na primeira
  transcrição em máquina limpa). Na máquina do Gabriel nada muda (turbo já
  baixado). O passo 4.2 (README) é obrigatório na MESMA fase.
- **Fase 4.3 (mutex)**: cuidado com os modos headless — `--selftest` e
  `--transcribe` não podem nem criar o mutex nem ser bloqueados por ele.
- **`MLXTranscriber._repo`** (`:2210-2212`): o comentário promete
  special-case p/ `large-v3-turbo` que o código não tem — só é verificável
  com Mac + rede; anotar na Fase 1 do roadmap de 02/08 quando ela executar
  (não é passo deste roadmap).
- Achados "(lido)" não foram reproduzidos em runtime (`ancoragem-e-
  confirmacao`): se uma prova de fase contradisser o diagnóstico, vale a
  prova — corrigir o md, não forçar o passo.

> Auditado em 2026-09-01 (overhaul): FECHADO — 5 commits de 12/08 (hub); mutex em reco.py:5049, tools/check_i18n.py existe
