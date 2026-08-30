# Roadmap de melhoria — modo nota ("speech to IA") (2026-08-13)

## Estado em 17/08/2026

- **Nada rodou ainda.** Auditoria de código em 17/08 confirma: `reco.py` não
  tem `nota_dir` em `_CFG_DEFAULTS` (l.104), não existe `_start_nota`,
  `DualRecorder._new_writer`/`start` não tem parâmetro `prefix=`,
  `is_reco_recording` (l.133) segue idêntica. `git log` de `reco.py` desde
  13/08 só tem 2 commits, ambos de ícones Lucide (`27dae58`, `1a6cc9e`) — nada
  de modo nota. Todas as âncoras de linha citadas no "Alvo e estado atual"
  acima foram reconferidas por grep e **não tiveram deslocamento**
  (`_excluir_gravacao` l.554, `_pick_output_dir` l.3154, `_start_rec` l.3390,
  `_after_stop` l.3540, `_run_live_final_pass` l.3564,
  `_conclude_transcribe_and_delete` l.3615, `_transcribe_recording` l.3738) —
  o roadmap pode ser executado como escrito, sem reconferir premissas.
- **Próxima fase executável: Fase 1 (núcleo do fluxo)** — só `reco.py`, sem UI
  nova; entrega o fluxo de ponta a ponta por trás (gravar mic-só →
  transcrever → `.md` → clipboard → excluir mp3), testável por
  `test_encoder.py` + o teste manual curto do passo F1.6.
- **Depende de:** nada, para rodar com o default (`nota_dir=None` → mesma
  pasta das gravações — a Fase 1 inteira funciona sem o roadmap-par). Só a
  variante "apontar `nota_dir` pro cérebro" (passo F2.2, onde o Gabriel
  escolhe o destino nas opções avançadas) depende da Fase 1 de
  [C:\Dev\roadmap\2026-08-13-melhoria-transcricoes-no-cerebro.md](../../roadmap/2026-08-13-melhoria-transcricoes-no-cerebro.md)
  já ter criado `cerebro/transcricoes/` + a exceção de órfão em
  `check-orfaos.py` — sem isso, a primeira nota salva lá quebra o gate de
  pre-commit no push automático do hook de backup (a nota vira página órfã,
  piora o ratchet do `gate_vault.py`).

## Contexto e motivação

Pedido do Gabriel (13/08/2026): um modo no Reco para **falar com a IA por
voz** — iniciar gravação → captura **só o microfone** → ao parar, transcreve
→ salva **só o texto** (o mp3 é descartado) → copia o **caminho completo do
texto, entre aspas**, para o clipboard, pronto para colar num chat do Claude.

Par desta frente: o roadmap
[C:\Dev\roadmap\2026-08-13-melhoria-transcricoes-no-cerebro.md](../../roadmap/2026-08-13-melhoria-transcricoes-no-cerebro.md)
cria `cerebro/transcricoes/` — o destino custom que o Gabriel vai apontar. O
modo nota funciona sem ele (default = pasta de gravações), mas a Fase 1 de lá
deve existir antes de apontar o destino pro cérebro.

**Ler antes de executar:** [CLAUDE.md](../CLAUDE.md) (inteiro — em especial
§ arquitetura, § ícones Lucide, § config e a REGRA de recompilar),
[docs/ARMADILHAS.md](../docs/ARMADILHAS.md), e o hub
[cerebro/projetos/reco.md](../../cerebro/projetos/reco.md).

## Alvo e estado atual

Tudo em `reco.py` (arquivo único) + build PyInstaller. O que **já existe** e
o modo nota reusa (não reescrever nada disso):

- **Gravar só o mic**: `DualRecorder.start(mic_id, None, …)` (~l.1397) — canal
  ausente sai do pareamento e é preenchido com silêncio.
- **MP3 mono**: `start(…, out_channels=1)`; `MP3Writer.feed` (~l.732) no ramo
  mono soma mic+sys com ganho — sys silencioso ⇒ sai só o mic, sem −6 dB de
  downmix na transcrição.
- **Transcrever + excluir**: `_transcribe_recording(delete_after=True)`
  (~l.3738) e `_conclude_transcribe_and_delete` (~l.3615) já fazem 80% do
  fluxo; `_run_live_final_pass` (~l.3564) mostra como chamar
  `_run_transcriber` com um `done` próprio.
- **Exclusão pra Lixeira**: `_excluir_gravacao` (~l.554, `SHFileOperationW`).
- **Config + picker de pasta**: `_CFG_DEFAULTS` (~l.104), bloco "Pasta:" das
  opções (~l.3024-3033) e `_pick_output_dir` (~l.3154) — molde da opção nova.
- **Ícones Lucide**: `assets/icons_src/*.svg` → `tools/gerar_icones.py` →
  `App._icon()`; `reco.spec` já bundla `assets/icons` inteira (sem mudança).
- **i18n**: `t()`/`tf()` + tabela `_TR_EN`; checker `tools/check_i18n.py`.
- **Filtro de diarização**: `is_reco_recording` (~l.133) é por token no nome —
  prefixo `nota` não tem o token `reco` ⇒ transcrição simples, sem AEC.

O que NÃO existe: prefixo de nome configurável no writer, config `nota_dir`,
o fluxo automático pós-stop e o clipboard.

## Diagnóstico

### O que está bom (não mexer)

- Formato de gravação fixo, encode em streaming, drain-then-start do live —
  intocados; o modo nota só passa parâmetros que já existem.
- A biblioteca "Gravações…" não precisa conhecer o modo: mp3 de nota que
  sobreviver a uma falha aparece lá normalmente (recuperável com ⚡).

### O que está frágil ou custando (o que o modo resolve)

- Fluxo atual de uma nota de voz: gravar (mic+sistema) → parar → escolher
  "transcrever + excluir" → abrir pasta → copiar caminho → colar. Cinco
  passos manuais e o mp3 nasce estéreo com canal inútil.
- Sabatina (`via-negativa`): a feature REMOVE passos de um fluxo diário real;
  o escopo foi podado do que não remove passo nenhum (ver "O que NÃO fazer").

## Roadmap

### Fase 1 — núcleo do fluxo (reco.py, sem UI nova)

1. [x] `DualRecorder._new_writer` + `start`: parâmetro `prefix=None` (None →
   comportamento atual `gravacao_reco`/`recording_reco`). Nota usa
   `nota`/`note`. — **prova:** `python tools/test_encoder.py` verde e
   `python -c "from reco import is_reco_recording; assert not
   is_reco_recording('nota_2026-08-13_10-00-00.mp3')"` (rodar com o venv do
   projeto, de dentro de `C:\Dev\Reco`).
2. [x] `_CFG_DEFAULTS["nota_dir"] = None` (None → mesma pasta das gravações).
   Chave NOVA com default: NÃO precisa de `CFG_MIGRACAO` — mas **verificar em
   `load_config()`** que default novo faz merge com config existente (regra
   do CLAUDE.md § config). — **prova:** apagar `nota_dir` do
   `~/.reco_config.json` de teste e conferir que `load_config()` devolve a
   chave.
3. [x] `_start_nota()`: guards de `_start_rec` (~l.3390) — captura
   disponível, **mic obrigatório** (sem mic → status de erro, não grava),
   nada gravando/transcrevendo. Seta `self._nota = True`, **ignora o config
   `live`** (nota é curta; rascunho não paga o acelerador), chama
   `recorder.start(mic_id, None, out_channels=1, prefix=…,
   out_dir=self._out_dir)`. ⚠️ O MP3 vai SEMPRE para `_out_dir` (pasta de
   gravações), NUNCA para `nota_dir`: o destino custom será o cérebro, cujo
   hook de backup pusha sem revisão — áudio não pode nascer lá. — **prova:**
   passo 6.
4. [x] Branch em `_after_stop` (~l.3540): `self._nota` → pular o "Escolha o
   que fazer" e disparar a transcrição direto (espelhar o encadeamento de
   `_conclude_transcribe_and_delete`, estados STOPPED→IDLE via `done`).
5. [x] `done` da nota (novo, molde em `_transcribe_recording.done`):
   - erro/cancelado → mp3 FICA em `_out_dir` (status "Nota preservada:
     `<nome>`" + balão se escondido), `self._nota = False`, IDLE;
   - sucesso → gravar `nota_<ts>.md` em `nota_dir` resolvido (None →
     `_out_dir`; criar pasta se faltar), `clipboard_clear()` +
     `clipboard_append(f'"{caminho}"')`, `_excluir_gravacao(mp3)`, status
     "Nota pronta — caminho copiado" + balão, IDLE.
   Extensão **`.md`** de propósito: no cérebro a nota vira página do vault
   (buscável no Obsidian); conteúdo é texto puro, sem frontmatter.
6. [ ] Teste real curto (manual — Tk + hardware, mesmo racional do diário
   12/08): rodar pelo fonte, gravar nota de ~5 s falando, conferir: `.md` no
   destino com o texto; `powershell Get-Clipboard` devolve o caminho entre
   aspas; o mp3 sumiu da pasta de gravações (Lixeira). Repetir com
   transcrição forçada a falhar (ex.: modelo removido) → mp3 preservado.
   **Pulado nesta execução (sessão headless, sem humano falando/confirmando
   UI) — ver "Pendências de decisão" abaixo.** Proxy automatizado rodado no
   lugar: gravação real de 3 s só-mic/mono/`prefix="nota"` confirma prefixo,
   1 canal e `is_reco_recording()==False` (script descartável em
   `temp/2026-08-21-verificar-nota-prefix.py`, fora do versionamento).

### Fase 2 — UI e config

1. [ ] Botão "Nota" na view de gravação, ao lado de "Gravar" (achar por
   `t("Gravar")`): ícone Lucide novo (sugestão `notebook-pen`; alternativa
   `audio-lines`) — baixar SVG em `assets/icons_src/`, rodar `python
   tools/gerar_icones.py`; `compound="left"` se tiver palavra visível
   (armadilha do CLAUDE.md § ícones). Desabilitado durante
   gravação/transcrição (mesmos guards dos outros botões). — **prova:** botão
   visível e funcional no teste manual; com Pillow ausente cai no texto
   (rede do `_icon()`).
2. [ ] Opções avançadas: linha "Notas:" espelhando o bloco "Pasta:"
   (~l.3024-3033): label com o caminho atual (None → t("(mesma pasta das
   gravações)")), links "Alterar…" (novo `_pick_nota_dir`, molde
   `_pick_output_dir` ~l.3154) e "Padrão" (volta a None). É aqui que o
   Gabriel aponta `C:\Dev\cerebro\transcricoes\`. — **prova:** trocar a pasta
   nas opções, gravar nota, `.md` aparece no destino novo; "Padrão" volta.
3. [ ] i18n: TODA string nova de PT com par em `_TR_EN`. — **prova:**
   `python tools/check_i18n.py` limpo (zero faltante, zero morta).

### Fase 3 — validação, build e docs

1. [ ] Regressão: `python tools/test_encoder.py` e `python
   tools/check_i18n.py` verdes; repetir o teste real da F1.6 no fluxo
   completo com botão. — **prova:** saídas limpas.
2. [ ] Recompilar: `powershell -ExecutionPolicy Bypass -File
   C:\Dev\Reco\build.ps1` (⚠️ Gabriel precisa fechar o Reco.exe — exe em uso
   segura o `dist/`). — **prova:** build verde + `dist\Reco\Reco.exe
   --selftest` OK.
3. [ ] Docs: seção nova "Modo nota (speech to IA)" no
   [CLAUDE.md](../CLAUDE.md) (fluxo, `nota_dir`, por que o mp3 nunca nasce no
   destino custom, clipboard Tk — ver Riscos); decisão datada no hub
   [cerebro/projetos/reco.md](../../cerebro/projetos/reco.md); diário
   13/08. Marcar `[x]` aqui. — **prova:** `powershell -NoProfile -File
   cerebro/scripts/check-links.ps1` sem quebra nova.
4. [ ] Commit por pathspec (reco.py, tools, assets, docs, roadmap) — push só
   com autorização.

## Priorização (impacto × esforço × risco)

| item | impacto | esforço | risco | veredito |
| --- | --- | --- | --- | --- |
| F1 núcleo | alto (o fluxo inteiro) | médio | baixo (só parâmetros novos) | já |
| F2 UI/config | alto (sem UI não existe pro usuário) | baixo | baixo | já, após F1 |
| F3 build/docs | — (gate obrigatório do projeto) | baixo | baixo | fecha |

## O que NÃO fazer

- **Hotkey global / item na bandeja** para iniciar nota: valor real, mas é
  outra feature — só se o Gabriel pedir depois de usar o botão.
- **Rascunho ao vivo na nota**: nota é curta; a passada final resolve em
  segundos e o live ocuparia o acelerador à toa.
- **Frontmatter/YAML gerado pelo app**: acoplaria o Reco ao vault; o Reco é
  produto genérico (visão de 02/08), a nota é texto puro.
- **Gravar o mp3 direto no `nota_dir`**: hook de backup pusharia áudio sem
  revisão (ver F1.3).
- **Indexação automática das notas no `_INDICE.md` do cérebro**: a exceção de
  órfão da Fase 1 do roadmap-par já isenta; indexar é curadoria humana.
- **`.txt` em vez de `.md`**: `.txt` não é página no Obsidian — a nota
  ficaria fora de busca/grafo do vault, que é metade do valor.

## Riscos e pré-requisitos

- **Clipboard Tk morre com o processo**: `clipboard_append` é ownership do
  app — fechar o Reco esvazia o clipboard no Windows. Na prática o Reco vive
  na bandeja; documentar no CLAUDE.md, não "consertar".
- **Mono via `DualRecorder` validado por leitura de código**, não por teste
  existente: se `test_encoder.py` não cobrir `out_channels=1` pelo caminho do
  recorder, o teste real da F1.6 é o gate — não pular.
- **`nota_dir` apontando pro cérebro** pressupõe a Fase 1 do roadmap-par
  executada (pasta + exceção de órfão no gate); antes disso, usar o default.
- Sabatina: conceitos aplicados — `via-negativa` (escopo podado acima);
  demais conceitos do acervo não se aplicam a este alvo.

## Relatório de execução — Fase 1 (2026-08-21, sessão fbd7aeb5)

Executado via card `6d5bebccb1cd`, modo maestro central (sem humano lendo o
terminal — decisão pulável foi pulada e registrada, não tomada por conta
própria).

**Passos 1-5: feitos, provas verdes.**

1. `DualRecorder._new_writer`/`start` ganharam `prefix=None` (comportamento
   antigo preservado quando ausente). Prova: `python tools/test_encoder.py`
   → `TUDO OK` (4 blocos, inclui duração/header Xing/separação de canais/
   pump); `is_reco_recording('nota_2026-08-13_10-00-00.mp3')` → `False`,
   `is_reco_recording('gravacao_reco_...')` → `True`.
2. `_CFG_DEFAULTS["nota_dir"] = None`. Prova: `load_config()` com um
   `~/.reco_config.json` de teste sem a chave `nota_dir` devolveu
   `cfg["nota_dir"] is None` — confirma o merge (`dict(_CFG_DEFAULTS)` +
   `cfg.update(saved)`) sem precisar de `CFG_MIGRACAO` (chave nova com
   default, não mudança de default existente).
3. `_start_nota()` novo: guards (captura disponível, mic obrigatório,
   `self._state == IDLE and not self._transcribing`), seta `self._nota =
   True`, ignora `live`, chama `recorder.start(mic_id, None, out_channels=1,
   prefix="nota"/"note", out_dir=self._out_dir)`.
4. `_after_stop`: branch `self._nota` inserido ANTES do `elif
   self._live_was_on` — pula o "Escolha o que fazer" e chama
   `_run_nota_final_pass(path)`.
5. `_run_nota_final_pass` (novo, ao lado de `_run_live_final_pass`): erro →
   mp3 preservado em `_out_dir`, status "Nota preservada: `<nome>` — `<erro>`"
   + balão, `self._nota = False`, `IDLE`. Sucesso → `.md` (nome =
   `<stem-do-mp3>.md`, reaproveita o timestamp já no nome) em `nota_dir`
   resolvido (`None` → `_out_dir`, `mkdir(parents=True)` se faltar),
   `clipboard_clear()` + `clipboard_append(f'"{md}"')`,
   `_excluir_gravacao(path)` (Lixeira), status "Nota pronta — caminho
   copiado" + balão, `IDLE`. i18n: 6 strings novas com par em `_TR_EN`
   (não exigido pela Fase 1 — check_i18n.py é item da Fase 2 — mas feito
   por não deixar par faltando).

**Passo 6: pulado — ver "Pendências de decisão" abaixo.** Proxy automatizado
rodado no lugar (não é o gate declarado, é a validação possível sem humano):
`temp/2026-08-21-verificar-nota-prefix.py` gravou 3 s reais só-mic pelo
`DualRecorder.start(mic_id, None, out_channels=1, prefix="nota")` (mesma
chamada que `_start_nota()` faz) — confirma prefixo `nota_`, 1 canal e
`is_reco_recording() == False` no arquivo real gerado. **Não** cobre: o botão
Tk (não existe ainda — é Fase 2), a transcrição de fato, o clipboard via
`clipboard_append`, nem o cenário de erro forçado (modelo removido).

**Regra do projeto (recompilar sempre que `reco.py` muda):** `build.ps1`
disparado; ver resultado na notificação de background — se verde, o
`dist\Reco\Reco.exe --selftest` foi conferido também; se precisou do Gabriel
fechar o app em uso, está registrado abaixo.

**Arquivos tocados:** `reco.py` (único arquivo versionado alterado);
`temp/2026-08-21-verificar-nota-prefix.py` (script descartável,
`temp/` é gitignored — não entra no commit); este roadmap (checkboxes 1-5 +
esta seção).

## Pendências de decisão (execução 2026-08-21)

1. **F1.6 (teste manual real) não pode rodar numa sessão headless.** O passo
   pede um humano falando ~5 s no microfone e conferindo `Get-Clipboard`/o
   `.md`/a Lixeira — não há UI (botão) ainda nesta fase, então mesmo o
   disparo do fluxo dependeria de chamar `_start_nota()` fora do app real ou
   de esperar a Fase 2. Opções: **A)** Gabriel roda manualmente (via um
   REPL/`python -i reco.py` ou aguardando a Fase 2 ter o botão) — mais fiel
   ao gate declarado; **B)** aceitar o proxy automatizado já rodado (grava
   real, mono, prefixo certo) como suficiente para destravar a Fase 2 e
   adiar a confirmação completa (transcrição + clipboard + Lixeira) para o
   teste manual da Fase 3 (que já reexecuta "o teste real da F1.6 no fluxo
   completo com botão"). **Recomendo B** — a Fase 3 já tem esse teste
   completo no roteiro (item 1), então adiar não perde cobertura, só a
   adia para quando existir botão de verdade para clicar (mais realista que
   simular via REPL). Não decidido: o checkbox 6 continua `[ ]`.

## Linhagem

> Escrita pelo maestro ao fim de cada rodada (`registrar_linhagem`). É o
> registro de QUEM fez o quê neste roadmap: o arquiteto que o desenhou, os
> executores que o cumpriram, custo e commit de cada passo. Serve à revisão
> do fable — ele lê o desenho E a execução, não só o resultado.

| quando | card | papel | modelo | custo | sinal | commit |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-21 23:29 | `6d5bebccb1cd` | executor | sonnet | US$ 2.50 | esperando | `f5ec0a7` |
| 2026-08-30 20:09 | `6d5bebccb1cd` | executor | sonnet | US$ 0.52 | esperando | `894840f` |
