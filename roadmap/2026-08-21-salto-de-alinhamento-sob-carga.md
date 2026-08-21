# Salto de alinhamento sob carga: o buffer de captura tem 22 ms

**Data:** 2026-08-21 · **Projeto:** Reco · **Origem:** *"veja se é necessário
corrigir o eco dos áudios de ontem e hoje"* → três das cinco gravações de 21/08
com 350-500 ms de defasagem entre mic e loopback, começando **no meio da
gravação**. O Gabriel confirmou o contexto: *"eu estava com alto uso de recursos
do PC durante as gravações"*.

Continuação do [roadmap do antieco](2026-08-19-melhoria-antieco-de-verdade.md)
(§ 9 tem a medição das oito gravações). Aqui está a **causa** do salto e o plano
de correção.

---

## 1. O que está acontecendo (medido, não suposto)

### 1.1 O buffer de captura do WASAPI tem 22 ms

`DualRecorder._rec_mic`/`_rec_sys` (`reco.py` ~1656 e ~1682) criam o recorder com
`blocksize=CHUNK`, e `CHUNK = 1024` (`reco.py:1350`). No `soundcard`
(`mediafoundation.py:549`) esse parâmetro vira **a duração do buffer** que o
WASAPI aloca:

```python
bufferduration = int(blocksize/samplerate * 10000000)  # hecto-nanossegundos
```

Medido nesta máquina, com o dispositivo de loopback real:

| `blocksize` | buffer que o Windows entrega |
| --- | --- |
| **1024** (o valor de hoje) | **1056 frames = 22,0 ms** |
| 48000 | 48000 frames = 1000,0 ms |

(`deviceperiod` = 10 ms default / 3 ms mínimo.)

Ou seja: o thread de captura tem **22 ms** para voltar ao `r.record()`. Se ele
demorar mais — quantum do scheduler do Windows (~15 ms), GIL disputado, GC,
iGPU ocupada, qualquer coisa que o "alto uso de recursos" implica — o buffer
circular enche e o **WASAPI sobrescreve as amostras que ainda não foram lidas**.
Elas não voltam, e ninguém as conta.

### 1.2 Amostra perdida vira offset permanente

O `_pump` pareia os canais **por contagem de amostras** (é o desenho, e é o mesmo
mecanismo que a Fase 1 de 19/08 corrigiu para o offset *inicial*). Se o mic perde
400 ms e o loopback não, a partir dali o par (mic[i], sys[i]) junta instantes
diferentes — para sempre. É por isso que o salto não se cura sozinho.

### 1.3 O `soundcard` SABE que isso aconteceu, e o Reco não escuta

`mediafoundation.py:771`:

```python
if flags & _ole32.AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY:
    warnings.warn("data discontinuity in recording", SoundcardRuntimeWarning)
```

O Reco não captura esse warning. Rodando pelo `Reco.exe` (PyInstaller, sem
console) ele não vai nem para o stderr: some. **Temos o sinal do defeito na mão e
o descartamos.**

### 1.4 A recuperação atual leva ~10 minutos

`_al_corrigir_deriva` reestima a cada `ALIGN_RECHECK_S` = 60 s e corrige no
máximo `ALIGN_MAX_AJUSTE` = 2400 amostras @48k = **50 ms por reestimativa** (teto
criado contra estimativa ruim). Um salto de 500 ms leva **10 rechecagens**. As
séries medidas (trechos de 15 s) mostram exatamente essa escada:

| gravação | alinhada até | salta para | quando | volta a ~0 em |
| --- | --- | --- | --- | --- |
| 21/08 16:52 | −24 ms | **−359 ms** | t=60 s | t≈480 s |
| 21/08 11:16 | +118 ms | **+497 ms** | t=75 s | não volta (arquivo tem 4,5 min) |
| 21/08 10:41 | ~0 ms | **−495 ms** | t=165 s | t≈540 s |

O teto está certo para o que ele foi feito (deriva de clock: dezenas de ppm). Ele
só não distingue **deriva** de **salto**.

### 1.5 O dano é real e chega ao texto

Transcrição do arquivo de 16:52 (canais 359 ms fora), faixas lado a lado:

```
Interlocutor(es): …não são dinâmicas muito legais, porque a primeira é dividir as equipes
Eu: que a primeira é dividir as equipes e tudo mais e aí
```

A faixa `Eu:` repete a interlocutora palavra por palavra — o `cancel_echo` não
cancela nada com os canais fora de fase, e a `dominancia_sistema` atribui ao
Gabriel fala que não é dele. Retranscrito do `_alinhado.mp3`, o eco cai
drasticamente (sobra resíduo curto no início de alguns turnos).

---

## 2. Decisões

1. **Prevenir vale mais que corrigir.** Buffer maior custa 192 KB por canal e
   elimina a classe inteira de defeitos; alinhamento pós-fato só remedia.
2. **Sinal do sistema > inferência estatística.** O warning de descontinuidade é
   um fato do driver; a correlação cruzada é uma estimativa que precisa de eco
   para funcionar (e em gravação de fone não funciona).
3. **Nada de reprocessar o áudio.** Vale a regra do roadmap de 19/08: alinhar é
   descartar/inserir amostra, não filtrar. O MP3 continua mic cru | loopback cru.
4. **Salto e deriva são coisas diferentes e merecem tratamento diferente.** O
   teto de 50 ms continua valendo para deriva; salto se aplica inteiro.

---

## 3. Fases

### Fase A — não perder amostra (prevenção)

- [ ] **A1. Buffer de captura de 1 s.** Em `reco.py`, constante nova
  `CAP_BUFFER_S = 1.0` ao lado de `CHUNK`, e nos dois recorders
  (`_rec_mic` ~1660, `_rec_sys` ~1685) trocar `blocksize=CHUNK` por
  `blocksize=int(CAP_BUFFER_S * CAPTURE_SR)`. **Continuar lendo
  `record(numframes=CHUNK)`** — o `soundcard` acumula pacotes até completar o
  pedido e guarda a sobra em `_pending_chunk`, então a latência de leitura não
  muda (confirmado em `mediafoundation.py:800-820`).
  *Pronto quando:* `r.buffersize` reportar ≥ 48000 frames nos dois canais e
  `tools/test_gravacao_alinhada.py` continuar com pior janela < 10 ms.
- [ ] **A2. Prioridade de áudio no thread de captura.**
  `AvSetMmThreadCharacteristicsW("Pro Audio", &idx)` (avrt.dll, ctypes) no início
  de `_rec_mic`/`_rec_sys`, com `try/except` silencioso — é o mecanismo que o
  Windows oferece para thread de áudio não ser preemptado por trabalho comum.
  *Pronto quando:* a chamada devolver handle não-nulo no Windows e o código seguir
  rodando sem ela em qualquer outro sistema.

### Fase B — detectar e corrigir na hora (rede de segurança)

- [ ] **B1. Escutar o warning de descontinuidade.** Em cada thread de captura,
  `warnings.catch_warnings(record=True)` em volta do laço + `simplefilter("always")`,
  checando a lista a cada N blocos (a lista acumula; não usar por bloco, é caro).
  Contar em `self._glitch_mic` / `self._glitch_sys` e imprimir uma linha
  `[capture] descontinuidade no canal X`.
  *Pronto quando:* um teste que satura a CPU durante a gravação registrar
  contagem > 0, e uma gravação ociosa registrar 0.
- [ ] **B2. Corrigir pelo relógio, não pela correlação.** Cada canal passa a
  contar amostras entregues e o tempo de relógio ativo (`perf_counter`,
  descontando pausa). O **desequilíbrio relativo** entre os dois deficits é o
  offset em amostras: aplicar imediatamente no `_pump` (inserir silêncio no canal
  que perdeu). Usar o relativo, nunca o absoluto — a pausa e a latência de
  dispositivo afetam os dois canais juntos e não são erro.
  *Pronto quando:* no teste sob carga da Fase D o residual voltar a < 50 ms em
  menos de 5 s após o glitch (hoje: ~10 min).
- [ ] **B3. Expor no estado.** `alinhamento()` passa a devolver `glitches` e
  `deficit_ms` por canal, para o teste e para o relatório.

### Fase C — o alinhador reage a salto (paliativo independente)

Vale mesmo que A e B falhem, e é a rede para gravação **sem eco** (fone), onde a
correlação não ajuda mas o relógio de B2 ainda funciona.

- [ ] **C1. Duas leituras concordantes = salto, aplica inteiro.** Em
  `_al_corrigir_deriva`, guardar a última estimativa; se duas consecutivas
  concordarem (mesmo sinal, diferença < 20 ms) com |d| > 100 ms e `q ≥
  ALIGN_Q_MIN`, aplicar o offset inteiro (teto `ALIGN_MAXLAG_S`) e logar
  `[align] salto de X ms corrigido`. Estimativa ruim não se repete idêntica; salto
  sim.
- [ ] **C2. Recheck mais curto enquanto o residual é grande.** `ALIGN_RECHECK_S`
  vira dinâmico: 15 s enquanto |última estimativa| > 50 ms, 60 s quando está
  alinhado. Custo: uma correlação de 20 s decimados a cada 15 s (~30 ms de CPU).
  *Pronto quando (C1+C2):* replay do arquivo de 11:16 pelo caminho do `_pump`
  (via `tools/test_alinhamento.py`, caso novo "salto de 500 ms no meio") sair
  alinhado em < 60 s de áudio.

### Fase D — provar sob carga

- [ ] **D1. Teste novo `tools/test_gravacao_sob_carga.py`:** grava 3 min com N
  processos queimando CPU (N = núcleos), tocando áudio pelos alto-falantes, e
  mede o residual por janela + a contagem de glitches.
  *Gate:* nenhum trecho acima de 50 ms por mais de 5 s; glitches podem ser > 0
  (o objetivo não é impedir o glitch, é sobreviver a ele).
- [ ] **D2. Recompilar** (`build.ps1`) — regra do projeto, o exe não reflete o
  fonte sozinho.

### Fase E — ferramenta que não deixa o operador errar

Saiu da própria execução de 21/08, não do desenho:

- [ ] **E1. `alinhar_gravacao.py --aplicar` deve recusar quando não dá para
  alinhar.** No arquivo de 21/08 15:00 só 21/78 trechos correlacionavam; o script
  herdou o último deslocamento válido nos trechos mudos e escreveu um
  `_alinhado.mp3` com residual **+318,5 ms** — pior que o original (mediana
  +4 ms). Hoje a régua ("só vale quando a maioria dos trechos correlaciona") existe
  só na documentação. Deve virar guarda: abaixo de ~50% de trechos confiáveis,
  `--aplicar` recusa com o motivo e manda rodar o relatório, a menos que venha um
  `--forcar` explícito.
  *Pronto quando:* o arquivo de 15:00 for recusado e os de 10:41/11:16/16:52
  continuarem passando.
- [ ] **E2. `medir_aec.py` com janelas fixas.** Ele escolhe as janelas pelo perfil
  de energia, e alinhar muda esse perfil — então antes×depois compara **trechos
  diferentes** (foi o que fez o arquivo de 10:41 "piorar" de +5,6 para +0,5 dB).
  Aceitar uma lista de janelas (ou um arquivo de referência de onde copiá-las)
  para que a comparação seja pareada. Registrado em `docs/ARMADILHAS.md`.
  *Pronto quando:* medir original e `_alinhado` nas mesmas janelas.
- [ ] **E3. A biblioteca não sabe que existe versão alinhada.** A view
  "Gravações…" lista `x.mp3` e `x_alinhado.mp3` como dois itens sem relação, e
  nada impede transcrever o desalinhado por engano — que é exatamente o defeito
  que gerou a transcrição contaminada de 16:52. Mínimo: marcar visualmente o par e
  transcrever o alinhado por padrão quando ele existir.

**Ordem recomendada:** A1 → C1/C2 → B → A2 → D. A1 é uma constante e tira 45× de
folga; C é barato e independente; B é o conserto correto e mais caro. A Fase E é
independente das outras e pode entrar em qualquer ponto.

---

## 4. Descartado e impraticável

- **Aumentar `CHUNK` (a leitura) em vez do buffer.** Resolveria por acidente (o
  buffer é derivado dele), mas piora tudo o mais: o VU meter e o modo ao vivo
  passam a receber blocos de 1 s, e a pausa fica grosseira. Buffer e leitura são
  coisas diferentes e o `soundcard` só as amarra porque tem um parâmetro só.
- **Modo exclusivo do WASAPI** (`exclusive_mode=True`): tira o mixer do caminho e
  reduz glitch, mas toma o dispositivo — ninguém mais toca áudio na máquina
  enquanto o Reco grava, o que mata a razão de existir do loopback.
- **Encodar no thread de captura para "simplificar".** Já foi medido em 28/07: é
  exatamente o que estoura o buffer. O `_encode_loop` existe por isso.
- **Confiar só na correlação (Fase C sozinha).** Não funciona em gravação de fone
  (sem eco, `q` < 0,15) — e é justamente onde o salto passaria despercebido até
  alguém ouvir o arquivo.
- **Reprocessar o áudio no salvamento (alinhar tudo no `stop()`).** Contradiz a
  decisão 3 e reintroduz o custo que a Fase 1 tirou; além disso um arquivo de 2 h
  não cabe em memória (medido: 249 MB para 32 min em float32).
- **Timestamp do WASAPI por pacote** (`GetBuffer` devolve `pu64QPCPosition`): o
  `soundcard` descarta esse ponteiro (`_ffi.NULL` em `mediafoundation.py:699`) e
  usá-lo exigiria fork do pacote. O relógio de B2 dá a mesma informação com
  precisão suficiente (o erro que importa é de centenas de ms).

---

## 5. Pendente — decisão do Gabriel

- **Executar agora ou depois?** O plano acima é ~1 dia de trabalho com os testes.
  A1 sozinho (uma constante + duas linhas) já derruba a probabilidade do defeito
  e cabe em minutos, mas exige recompilar o exe.
- **Guard do AEC quando não há eco.** Separado deste roadmap, veio da mesma
  medição: no arquivo de 21/08 15:00 o acoplamento é −31,5 dB e o `cancel_echo`
  entrega ERLE **negativo** (soma energia em vez de tirar). Vale desligar o AEC
  automaticamente quando o acoplamento medido é desprezível? Ver § 9 do roadmap de
  19/08.
