# Salto de alinhamento sob carga: o buffer de captura tem 22 ms

**Data:** 2026-08-21 · **Projeto:** Reco · **Origem:** *"veja se é necessário
corrigir o eco dos áudios de ontem e hoje"* → três das cinco gravações de 21/08
com 350-500 ms de defasagem entre mic e loopback, começando **no meio da
gravação**. O Gabriel confirmou o contexto: *"eu estava com alto uso de recursos
do PC durante as gravações"*.

Continuação do [roadmap do antieco](2026-08-19-melhoria-antieco-de-verdade.md)
(§ 9 tem a medição das oito gravações). Aqui está a **causa** do salto e o plano
de correção.

> **Auditado em 2026-08-30** (fable, card `c688f37215cbd`). A causa central foi
> **reverificada na máquina** e se confirma. O desenho tinha **três furos** e a
> operação de 21/08 tinha **cinco vieses de medição** — tudo em § 4. Fases
> renumeradas, provas reescritas, decisões técnicas tomadas em § 6.

---

## 0. Estado da execução (30/08/2026)

**Nenhuma linha de `reco.py` mudou.** Conferido hoje: `CHUNK = 1024`
(`reco.py:1360`), `blocksize=CHUNK` nos dois recorders (`reco.py:1676` e
`:1700`), `ALIGN_MAX_AJUSTE = 2400` (`:1396`), sem `CAP_BUFFER_S`, sem contador
de glitch, sem `AvSetMmThreadCharacteristicsW`, sem
`tools/test_gravacao_sob_carga.py`. **O defeito segue ativo**: toda gravação
feita com o PC pesado nasce com risco de salto.

| item | estado em 30/08 |
| --- | --- |
| Fases A–E deste md | nenhuma executada |
| E1 (`--aplicar` recusar) | não existe; não há `--forcar` em `tools/alinhar_gravacao.py` |
| E2 (`medir_aec.py` com janelas fixas) | não existe; `escolhe_janelas` ainda escolhe por energia |
| E3 (biblioteca conhecer o par `_alinhado`) | não existe |
| Push dos commits `691fdae`/`f5ec0a7` | **feito** (`origin/master` == `HEAD`, push de 28/08) |
| Documentação | feita e correta: 2 entradas em `docs/ARMADILHAS.md`, hub, diário 21/08 |
| 10:41 e 11:16 | ainda não transcritos (se forem, tem de ser do `_alinhado.mp3`) |

**Acervo:** `Documents\Reco` tem **59 MP3**, dos quais 6 são `_alinhado`. Ou
seja **53 gravações originais**, e nunca se mediu quantas têm salto.

---

## 1. O que está acontecendo (medido, não suposto)

### 1.1 O buffer de captura do WASAPI tem 22 ms

`DualRecorder._rec_mic`/`_rec_sys` (`reco.py:1676` e `:1700`) criam o recorder com
`blocksize=CHUNK`, e `CHUNK = 1024` (`reco.py:1360`). No `soundcard`
(`mediafoundation.py:549`) esse parâmetro vira **a duração do buffer** que o
WASAPI aloca:

```python
bufferduration = int(blocksize/samplerate * 10000000)  # hecto-nanossegundos
```

**Reverificado em 30/08/2026** nesta máquina, nos dois dispositivos reais:

| `blocksize` | mic | loopback |
| --- | --- | --- |
| **1024** (o valor de hoje) | 1058 frames = **22,0 ms** | 1056 frames = **22,0 ms** |
| 48000 | 48000 frames = 1000,0 ms | 48000 frames = 1000,0 ms |

(`deviceperiod`: default 10 ms nos dois; mínimo 2 ms no mic, 3 ms no loopback.)

Ou seja: o thread de captura tem **22 ms** para voltar ao `r.record()`. Se ele
demorar mais — quantum do scheduler do Windows (~15 ms), GIL disputado, GC,
iGPU ocupada, qualquer coisa que o "alto uso de recursos" implica — o buffer
circular enche e o **WASAPI sobrescreve as amostras que ainda não foram lidas**.
Elas não voltam, e ninguém as conta.

**Quanto de folga é preciso, em número.** As amostras perdidas medem a parada:
salto de 350-500 ms = thread parado por ~370-520 ms (o buffer de 22 ms cobriu o
começo). É isso que decide o tamanho do buffer novo:

| `CAP_BUFFER_S` | folga | perderia nos eventos medidos de 21/08 |
| --- | --- | --- |
| 0,022 (hoje) | 22 ms | 350-500 ms — o defeito |
| 0,25 | 250 ms | ainda 120-270 ms |
| **1,0** | 1000 ms | **nada** |

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

### 1.6 ⚠️ NOVO (30/08) — há uma SEGUNDA via de dessincronização, e o overrun não a explica

Lendo `_record_chunk` (`mediafoundation.py:735-756`): quando o WASAPI não tem
pacote nenhum, o `soundcard` **não bloqueia** — ele faz polling
(`sleep(minimum_block_length/4)`, ~0,75 ms) e, passados
`default_block_length * 4` ≈ **40 ms** sem dado, **fabrica silêncio medido pelo
relógio**:

```python
num_frames = int(self.samplerate * elapsed_time_ns / 1_000_000_000)
self._idle_start_time += elapsed_time_ns
return numpy.zeros([num_frames * num_channels], dtype='float32')
```

Isso importa por três razões:

- **É assimétrico entre os canais.** O mic sempre entrega pacotes; o **loopback
  não entrega nada quando ninguém está tocando áudio**. Só o canal do sistema
  passa por esse caminho — e é o canal cuja contagem de amostras define o
  pareamento.
- **O truncamento (`int(...)`) descarta o resto fracionário, e o
  `_idle_start_time` avança pelo tempo inteiro.** O erro não é reincorporado:
  perde-se até 1 frame por disparo. Pior caso teórico com ociosidade contínua
  (um disparo a cada ~40 ms): ~25 frames/s ≈ **0,5 ms/s ≈ 31 ms/min**.
- **`AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY` não é levantado aqui.** Este
  caminho é **invisível** para a Fase B1.

Consequência de desenho: a hipótese "overrun do buffer" (§ 1.1) explica bem o
salto **abrupto** sob carga, mas **não é a única via**, e a via de § 1.6 age
justamente onde a correlação é cega (loopback mudo — fone, reunião sem áudio de
sistema). **Não medido ainda** — vira passo da Fase 0.

### 1.7 ⚠️ NOVO (30/08) — o modo ao vivo agrava a própria causa

`LiveTranscriber` roda **in-process**, no mesmo GIL, alimentado pelo `on_pair` do
`_pump`, e usa iGPU + CPU **durante** a gravação. Ou seja: ligar o rascunho ao
vivo é exatamente a "carga alta" que estoura um buffer de 22 ms. E como o
`on_pair` recebe o par **já pareado**, o rascunho ao vivo herda o desalinhamento
inteiro — a diarização e o AEC do rascunho erram junto.

Ninguém mediu isso. Nas oito gravações de 20-21/08 não se registrou se o modo ao
vivo estava ligado. É a variável de confusão mais óbvia do "20/08 saiu perfeito e
21/08 não" (§ 4.4).

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
5. **NOVO (30/08) — instrumentar antes de consertar.** Nenhuma das fases de
   conserto tem como provar que funcionou enquanto o app não contar glitch,
   deficit e residual. B1/B3 são baratas, não mudam comportamento nenhum, e
   viram o gate de todas as outras. Vêm primeiro.
6. **NOVO (30/08) — instrumento que escolhe sozinho o que medir não mede
   mudança.** Já custou uma conclusão falsa (`medir_aec`, § 4.5) e quase custou
   uma segunda (`alinhar_gravacao`, § 4.6). Vale para qualquer antes×depois
   deste projeto.

---

## 3. Fases

### Fase 0 — medir o que nunca foi medido (nenhuma linha de código muda)

Barata, sem risco, e é ela que decide o tamanho do resto. **Nada aqui altera
`reco.py` nem escreve MP3 novo.**

- [ ] **0.1. Varrer o acervo inteiro, só relatório.**
  `python tools/alinhar_gravacao.py "$env:USERPROFILE\Documents\Reco"` **sem**
  `--aplicar` — o script já aceita pasta e não escreve nada sem a flag. Anotar,
  por arquivo: duração, `n_medidos/n_trechos`, mediana, faixa. Salvar a saída em
  `temp/2026-XX-XX-varredura-acervo.txt`.
  *Prova:* existir a tabela das 53 gravações originais e a contagem de quantas
  têm **faixa > 100 ms** (o sintoma de salto), não mediana.
  *Por que importa:* é o único número que diz se o defeito é raro (dois dias
  ruins) ou crônico — e é ele que justifica ou dispensa as Fases B/C.
- [ ] **0.2. Medir a via de § 1.6 sem hardware de reunião.** Script novo
  `tools/test_relogio_captura.py`: abre os dois recorders por 120 s **com a caixa
  muda** (nenhum áudio tocando), conta frames entregues por canal e compara com
  `perf_counter`. Repetir com áudio tocando.
  *Prova:* um número em ms/min para "deriva do loopback com caixa muda". Abaixo
  de 1 ms/min, § 1.6 é irrelevante e sai do roadmap; em dezenas de ms/min, ela é
  co-causa e a Fase B2 vira obrigatória.
- [ ] **0.3. Medir o efeito do modo ao vivo na taxa de glitch.** Depende do
  contador de B1 — fica anotado aqui para não sumir, executa depois de B1.
  *Prova:* D1 rodado com e sem `live=true`, comparando a contagem de glitches.
- [ ] **0.4. Medir ERLE do `cancel_echo` no acervo.** `tools/medir_aec.py` em ~10
  gravações variadas, registrando o par (ERLE, dano na voz) **e** o acoplamento.
  *Prova:* a distribuição do ERLE e o limiar de acoplamento abaixo do qual o ERLE
  fica ≤ 0 dB — que é o número que falta para o guard (§ 6.2).

### Fase A — não perder amostra (prevenção)

- [ ] **A1. Buffer de captura de 1 s.** Em `reco.py`, constante nova
  `CAP_BUFFER_S = 1.0` ao lado de `CHUNK` (`:1360`), e nos dois recorders
  (`_rec_mic` `:1676`, `_rec_sys` `:1700`) trocar `blocksize=CHUNK` por
  `blocksize=int(CAP_BUFFER_S * CAPTURE_SR)`. **Continuar lendo
  `record(numframes=CHUNK)`** — o `soundcard` acumula pacotes até completar o
  pedido e guarda a sobra em `_pending_chunk` (`mediafoundation.py:800-820`,
  reconferido em 30/08), então a latência de leitura, o VU meter e o modo ao vivo
  não mudam. **1,0 s e não 0,25 s pelo motivo da tabela de § 1.1.**
  *Prova:* abrir os dois recorders e reportar `r.buffersize == 48000` nos dois
  canais, **e** `python tools/test_gravacao_alinhada.py` com pior janela < 10 ms.
- [ ] **A1b. ⚠️ Drenar a cauda ao parar — obrigatório junto com A1.** Hoje o
  laço de captura sai no `_stop_ev` e o que restou no buffer do WASAPI é
  descartado no `__exit__`: **até 22 ms**. Com A1 isso vira **até 1 s de áudio
  perdido no fim de toda gravação** — regressão real, e o md original não a
  previa. Depois do `while`, antes de sair do `with`, drenar simetricamente nos
  dois canais (uma passada de `r.record(numframes=None)` limitada por tempo, mais
  `r.flush()`), anexando o resto a `chunks`.
  *Prova:* `python tools/test_gravacao_real.py 30` com a duração do MP3 batendo o
  tempo de parede dentro de ±50 ms (com A1 e sem A1b, ela sairia ~1 s curta).
- [ ] **A2. Prioridade de áudio no thread de captura — SÓ SE A1 não bastar.**
  `AvSetMmThreadCharacteristicsW("Pro Audio", &idx)` (avrt.dll, ctypes) no início
  de `_rec_mic`/`_rec_sys`, com `try/except` silencioso e
  `AvRevertMmThreadCharacteristics` no fim (sem o revert, vaza handle).
  ⚠️ **Suspeita não medida, e provavelmente fraca:** os três threads do Reco
  disputam o **GIL**, não o scheduler do SO — prioridade de thread não compra
  bytecode. Só entra se D1 mostrar glitch remanescente **depois** de A1.
  *Prova:* D1 rodado duas vezes, com e sem A2, com diferença na contagem de
  glitches maior que a variação entre repetições do mesmo cenário.

### Fase B — detectar e corrigir na hora (rede de segurança)

- [ ] **B1. Escutar o warning de descontinuidade.** ⚠️ **O desenho original está
  errado, e isso foi verificado em 30/08:** `warnings.catch_warnings(record=True)`
  **não é por thread** nesta build (`warnings._use_context == 0`, Python 3.14.4) —
  o teste feito hoje mostrou o bloco de um thread capturando o warning emitido
  pelo outro. Com dois threads de captura, a atribuição do canal sairia errada e
  os dois sobrescreveriam o `showwarning` global um do outro.
  **Desenho correto:** no `DualRecorder.start()`, salvar `warnings.showwarning` e
  instalar **um** dispatcher que resolve o canal por
  `threading.current_thread()`, incrementando `self._glitch_mic`/`self._glitch_sys`
  (e repassando ao handler original o que não for `SoundcardRuntimeWarning`);
  restaurar no `_wind_down`. Precisa também de
  `warnings.simplefilter("always", SoundcardRuntimeWarning)` — senão o registro de
  deduplicação do módulo engole da segunda ocorrência em diante.
  *Prova:* teste novo sem hardware (`tools/test_glitch_contador.py`) que emite
  `warnings.warn(..., SoundcardRuntimeWarning)` de dois threads nomeados e confere
  que cada contador subiu no canal certo; mais gravação ociosa de 30 s com
  contagem 0.
- [ ] **B2. Corrigir pelo relógio, não pela correlação.** Cada canal passa a
  contar amostras entregues e o tempo de relógio ativo (`perf_counter`,
  descontando pausa). O **desequilíbrio relativo** entre os dois deficits é o
  offset em amostras: aplicar imediatamente no `_pump` (inserir silêncio no canal
  que perdeu). Usar o relativo, nunca o absoluto — a pausa e a latência de
  dispositivo afetam os dois canais juntos e não são erro.
  ⚠️ **Limitação descoberta em 30/08 (§ 1.6):** quando o loopback está mudo, o
  `soundcard` já preenche o canal do sistema com zeros contados pelo relógio —
  então o deficit medido daquele canal é ~0 **por construção**, mesmo que os
  frames sejam fabricados. B2 é sensível a perda no **mic** sempre, e no **sys**
  só enquanto o loopback entrega pacotes de verdade. Por isso B2 **não substitui**
  a Fase C: são sinais com pontos cegos complementares.
  *Prova:* no teste sob carga da Fase D o residual voltar a < 50 ms em menos de
  5 s após o glitch (hoje: ~10 min).
- [ ] **B3. Expor no estado e no log.** `alinhamento()` passa a devolver
  `glitches` e `deficit_ms` por canal.
  ⚠️ **Achado de 30/08:** `alinhamento()` existe desde 19/08 e **nunca é chamada
  fora dos testes** — nenhum ponto de `reco.py` a consome. Então B3 tem duas
  partes: (a) acrescentar os campos; (b) **chamá-la no `stop()`** e imprimir uma
  linha de resumo (`[align] estado=ok offset=+3 ms glitches=0/0 deficit=…`),
  senão o dado continua existindo e ninguém o vê.
  *Prova:* `python tools/test_gravacao_real.py 20` imprimindo a linha de resumo.

### Fase C — o alinhador reage a salto (paliativo independente)

Vale mesmo que A e B falhem, e é a rede para gravação **sem eco** (fone), onde a
correlação não ajuda mas o relógio de B2 ainda funciona **parcialmente** (§ 1.6).

- [ ] **C1. Duas leituras concordantes = salto, aplica inteiro — com as duas
  guardas que faltavam.** Em `_al_corrigir_deriva`, guardar a última estimativa;
  se duas consecutivas concordarem (mesmo sinal, diferença < 20 ms) com
  |d| > 100 ms, aplicar o offset inteiro (teto `ALIGN_MAXLAG_S`) e logar
  `[align] salto de X ms corrigido`.
  ⚠️ **Guarda 1 — janelas disjuntas.** O argumento "estimativa ruim não se repete
  idêntica" só vale se as duas janelas forem **material diferente**. Guardar o
  índice global de amostra da última estimativa e recusar o par se as janelas se
  sobrepuserem.
  ⚠️ **Guarda 2 — `q` alto, não `ALIGN_Q_MIN`.** Sinal periódico (música de
  espera, tom, ventilador) **repete o mesmo pico secundário** e passaria pelo
  critério de concordância. Exigir `q ≥ 0,30` para aplicar salto inteiro;
  `ALIGN_Q_MIN` = 0,15 continua valendo só para o ajuste de deriva, que é limitado
  a 50 ms e não faz estrago.
  *Custo de errar:* aplicar 500 ms de deslocamento num falso positivo estraga o
  áudio de vez. É o passo de maior risco do roadmap — por isso as guardas.
- [ ] **C2. Recheck mais curto enquanto o residual é grande.** `ALIGN_RECHECK_S`
  vira dinâmico: 15 s enquanto |última estimativa| > 50 ms, 60 s quando está
  alinhado.
  ⚠️ **C2 sabota C1 se entrar sozinho.** `ALIGN_JANELA_S` = 20 s: com recheck de
  15 s, duas estimativas consecutivas compartilham **75% do material** e deixam de
  ser leituras independentes — que é exatamente o que a Guarda 1 de C1 exige.
  Baixar `ALIGN_JANELA_S` junto (≤ 15 s no modo rápido) ou fazer C1 rejeitar o par
  sobreposto. Custo: correlação de ≤15 s decimados a cada 15 s (~30 ms de CPU).
  *Prova (C1+C2):* `python tools/test_alinhamento.py` com **três casos novos** —
  (i) "salto de 500 ms no meio" (deve corrigir em < 60 s de áudio);
  (ii) "sinal periódico com pico secundário estável" (deve **não** aplicar salto);
  (iii) "duas janelas sobrepostas concordando" (deve **não** aplicar salto).

### Fase D — provar sob carga

- [ ] **D1. Teste novo `tools/test_gravacao_sob_carga.py`:** grava 3 min com N
  processos queimando CPU (N = núcleos), tocando áudio pelos alto-falantes, e
  mede o residual por janela + a contagem de glitches (B1) + o deficit (B2).
  *Gate:* nenhum trecho acima de 50 ms por mais de 5 s; glitches podem ser > 0
  (o objetivo não é impedir o glitch, é sobreviver a ele).
  ⚠️ **Rodar uma vez ANTES de A1** para ter o baseline — sem ele não há como
  dizer que A1 resolveu; a comparação contra "as gravações de 21/08" não serve
  (§ 4.4).
- [ ] **D2. Recompilar** (`powershell -ExecutionPolicy Bypass -File
  C:\Dev\Reco\build.ps1`) — regra do projeto, o exe não reflete o fonte sozinho.

### Fase E — ferramenta que não deixa o operador errar

Saiu da própria execução de 21/08, não do desenho. Independente de A–D.

- [ ] **E1. `alinhar_gravacao.py --aplicar` deve recusar quando não dá para
  alinhar.** No arquivo `gravacao_reco_2026-08-21_15-00-51.mp3` só 21/78 trechos
  correlacionavam; `escreve_trecho` herdou `ultimo_d` nos 57 trechos mudos e
  escreveu um `_alinhado.mp3` com residual **+318,5 ms** — pior que o original
  (mediana +4 ms). Hoje a régua ("só vale quando a maioria dos trechos
  correlaciona") existe só na documentação. Vira guarda: abaixo do limiar de
  trechos confiáveis, `--aplicar` recusa com o motivo e manda rodar o relatório,
  a menos que venha um `--forcar` explícito.
  ⚠️ **Não usar 50% com `ALIGN_Q_MIN` = 0,15 sem calibrar.** Esse `q` foi
  calibrado para decidir "vale aplicar offset numa gravação em curso", não para
  "este trecho mede bem" (§ 4.3). Calibrar o par (limiar de `q`, fração mínima)
  contra a varredura da Fase 0.1, que já dá a distribuição real.
  *Prova:* o arquivo de 15:00 recusado; 10:41, 11:16 e 16:52 continuam passando.
- [ ] **E1b. O relatório tem de descrever o arquivo que seria escrito.** Hoje o
  print resume só os `medidos` (mediana e faixa dos trechos confiáveis), mas o
  áudio é escrito com `usados` — que inclui os deslocamentos **herdados**. Em
  10:41 o relatório fala de 9 trechos e a escrita aplica 22. Imprimir também
  `n_herdados` e a faixa de `usados`.
  *Prova:* rodar em 10:41 e ver as duas linhas divergirem explicitamente.
- [ ] **E2. `medir_aec.py` com janelas fixas.** `escolhe_janelas` escolhe pelo
  perfil de energia, e alinhar muda esse perfil — então antes×depois compara
  **trechos diferentes** (foi o que fez o arquivo de 10:41 "piorar" de +5,6 para
  +0,5 dB). Aceitar uma lista de janelas (ou um arquivo de referência de onde
  copiá-las). Já registrado em `docs/ARMADILHAS.md`.
  *Prova:* medir original e `_alinhado` de 10:41 nas mesmas janelas, e o ERLE não
  cair.
- [ ] **E3. A biblioteca não sabe que existe versão alinhada.** A view
  "Gravações…" lista `x.mp3` e `x_alinhado.mp3` como dois itens sem relação, e
  nada impede transcrever o desalinhado por engano — que é exatamente o defeito
  que gerou a transcrição contaminada de 16:52. Mínimo: marcar visualmente o par e
  transcrever o alinhado por padrão quando ele existir.
  *Prova:* com o par no disco, a ação "transcrever" no item original abrir o
  `_alinhado.mp3`; com só o original, comportamento inalterado.
- [ ] **E4. Guard do AEC quando não há eco** (decidido em § 6.2 — era pendência do
  Gabriel). Em `cancel_echo`, medir o ganho no próprio sinal e **devolver o mic
  cru** quando o resultado piora, em vez de aplicar um filtro que soma energia.
  Limiar vem da Fase 0.4.
  *Prova:* `tools/medir_aec.py` no arquivo de 15:00 com ERLE ≥ 0 dB (hoje: −7,8 dB)
  e sem regressão nos três arquivos de ERLE alto (11:16, 16:52, 10:41).

**Ordem decidida (§ 6.1):** 0 → A1+A1b → B1+B3 → C1+C2 → B2 → D → E. A Fase E é
independente e pode entrar em qualquer ponto; E1/E1b são as mais urgentes dela,
porque hoje a ferramenta de conserto **pode piorar o arquivo em silêncio**.

---

## 4. Auditoria de 30/08/2026 — furos do desenho e vieses da medição

### 4.1 Furo: `catch_warnings` não é por thread (B1)

Verificado com execução real nesta máquina: `warnings._use_context == 0`
(Python 3.14.4) e o `catch_warnings(record=True)` de um thread capturou o warning
emitido por outro. O passo B1 como estava escrito atribuiria a descontinuidade ao
canal errado. Desenho corrigido dentro do próprio B1.

### 4.2 Furo: C1 e C2 se anulam

C1 apoia-se em "duas leituras concordantes"; C2 encurta o recheck para 15 s sem
mexer na janela de 20 s, o que faz as leituras compartilharem 75% do material. O
md original pedia as duas juntas (*"Pronto quando (C1+C2)"*) sem notar. Guardas
adicionadas em C1 e C2.

Junto disso, a premissa de C1 — *"estimativa ruim não se repete idêntica; salto
sim"* — **é falsa para sinal periódico**, que repete o mesmo pico secundário.
Daí a Guarda 2 (`q ≥ 0,30`).

### 4.3 Furo: `q ≥ 0,15` está sendo usado fora do que foi calibrado

`ALIGN_Q_MIN` = 0,15 nasceu como limiar de decisão do alinhador **ao vivo**
(`reco.py:1394`). `tools/alinhar_gravacao.py` o reusa para rotular "trecho com
correlação confiável"; o § 9 do roadmap de 19/08 usa essa contagem como evidência
(`9/22`, `21/78`); e E1 ia usá-la como régua. Três usos, uma calibração só — e ela
não é de nenhum dos três. Calibrar na Fase 0.1.

### 4.4 Viés: "20/08 saiu perfeito" é consistente com a hipótese, não é prova

n=3 contra n=5, sem medir a carga da máquina, sem registrar se o modo ao vivo
estava ligado (§ 1.7), com conteúdos e durações diferentes. **A perna forte da
hipótese é mecanicista**, não estatística: o buffer de 22 ms é medido, o flag de
descontinuidade existe no driver, e a magnitude do salto bate com o tempo de
parada esperado (§ 1.1). O que converte correlação em causalidade é D1 (gravar
sob carga controlada, com e sem A1) — e ele é barato.

### 4.5 Viés: `medir_aec.py` escolhe as janelas pelo conteúdo (já documentado)

Fez o arquivo de 10:41 aparecer "piorando" de +5,6 para +0,5 dB depois de
alinhado (residual real 0,0 ms). Em `docs/ARMADILHAS.md` desde 21/08. Vira E2.

### 4.6 Viés novo: o relatório do `alinhar_gravacao.py` não descreve o arquivo que ele escreve

`medidos` (o que o print resume) e `usados` (o que a escrita aplica) são listas
diferentes: os trechos sem correlação **herdam `ultimo_d`** e entram só na
segunda. Em 10:41 o relatório fala de 9 trechos confiáveis e a escrita aplica 22
deslocamentos. É o mesmo erro de família do § 4.5 — o instrumento reporta uma
coisa e faz outra. Vira E1b.

### 4.7 Viés: mediana é o resumo errado para este defeito

O § 9 do roadmap de 19/08 julga "alinhada / corrigida" pela **mediana** dos
trechos. Numa gravação com salto a mediana engana nos dois sentidos: 10:41 tem
mediana −199 ms com série indo de +83 a −495 ms. A métrica que corresponde ao
dano é **pior janela** (que já é o gate de `test_gravacao_alinhada.py`) ou
**% do tempo acima de 50 ms**. Adotado na Fase 0.1.

### 4.8 O que a operação de 21/08 NÃO mediu e deveria

| não medido | onde entrou |
| --- | --- |
| quantas das 53 gravações do acervo têm salto | Fase 0.1 |
| se o modo ao vivo sofre e agrava (§ 1.7) | Fase 0.3 |
| se o overrun atinge os dois canais juntos | ver abaixo |
| a via de zeros fabricados pelo relógio (§ 1.6) | Fase 0.2 |
| taxa real de descontinuidade (nunca contada) | B1 |
| ERLE do `cancel_echo` no acervo | Fase 0.4 |

**"O overrun atinge os dois canais ao mesmo tempo?"** A hipótese assume que não,
e a evidência **corrobora**: são dois `_AudioClient` independentes em threads
independentes, o quantum do scheduler é por thread, e os saltos medidos têm
**sinais diferentes entre gravações** (−359, +497, −495 ms) — o padrão esperado
se o thread que perde for sorteado. Não é prova: perda simultânea e simétrica
seria invisível ao pareamento (e ao alinhador) por construção. **B1 resolve**: com
contador por canal, perda simultânea aparece como glitch nos dois com residual
zero.

### 4.9 O que a operação de 21/08 acertou

Registrado porque o roadmap deve proteger o que funciona:

- A causa foi **medida na máquina**, não inferida — e reverificou hoje.
- O achado do viés do `medir_aec` foi **autocrítica da própria sessão**, antes de
  virar conclusão publicada.
- O `_alinhado.mp3` de 15:00 foi gerado, **medido, reprovado e apagado** em vez de
  entregue.
- Os originais nunca foram sobrescritos.
- A documentação fechou completa no mesmo dia (2 armadilhas + hub + diário + § 9).

---

## 5. Descartado e impraticável

- **Aumentar `CHUNK` (a leitura) em vez do buffer.** Resolveria por acidente (o
  buffer é derivado dele), mas piora tudo o mais: o VU meter e o modo ao vivo
  passam a receber blocos de 1 s, e a pausa fica grosseira. Buffer e leitura são
  coisas diferentes e o `soundcard` só as amarra porque tem um parâmetro só.
- **Buffer de 250 ms como meio-termo.** Tentador por parecer conservador, mas os
  eventos medidos são de 350-500 ms — 250 ms ainda perderia 120-270 ms (§ 1.1). O
  custo de 1 s é 192 KB por canal; não há trade-off real a fazer.
- **Modo exclusivo do WASAPI** (`exclusive_mode=True`): tira o mixer do caminho e
  reduz glitch, mas toma o dispositivo — ninguém mais toca áudio na máquina
  enquanto o Reco grava, o que mata a razão de existir do loopback.
- **Encodar no thread de captura para "simplificar".** Já foi medido em 28/07: é
  exatamente o que estoura o buffer. O `_encode_loop` existe por isso.
- **Confiar só na correlação (Fase C sozinha).** Não funciona em gravação de fone
  (sem eco, `q` < 0,15) — e é justamente onde o salto passaria despercebido até
  alguém ouvir o arquivo.
- **Confiar só no warning (Fase B1 sozinha).** Descoberto em 30/08: a via de
  § 1.6 não levanta `DATA_DISCONTINUITY`. B1 é necessária, não suficiente.
- **Reprocessar o áudio no salvamento (alinhar tudo no `stop()`).** Contradiz a
  decisão 3 e reintroduz o custo que a Fase 1 tirou; além disso um arquivo de 2 h
  não cabe em memória (medido: 249 MB para 32 min em float32).
- **Timestamp do WASAPI por pacote** (`GetBuffer` devolve `pu64QPCPosition`): o
  `soundcard` descarta esse ponteiro (`_ffi.NULL` em `mediafoundation.py:699`,
  reconferido em 30/08) e usá-lo exigiria fork do pacote. O relógio de B2 dá a
  mesma informação com precisão suficiente (o erro que importa é de centenas
  de ms).
- **Alinhar o acervo inteiro em massa agora.** A Fase 0.1 é relatório e não
  duplica nada; só depois dela faz sentido decidir sobre os ~500 MB — e a decisão
  do espaço em disco é do Gabriel (§ 7).
- **A2 antes de A1.** Prioridade de thread não compra GIL; entra só se D1 mostrar
  glitch remanescente depois de A1 (§ Fase A).

---

## 6. Decisões tomadas pelo fable (30/08/2026)

Regra da casa: técnico com confiança alta se decide aqui, com o que reverteria.

### 6.1 Escopo e ordem da correção (card `s2bb49459`)

**Decisão:** Fase **0 → A1+A1b → B1+B3 → C1+C2 → B2 → D**; **A2 fica fora** até
D1 provar que faz falta. A recomendação anterior (A1+C) muda em dois pontos:
entra a Fase 0 na frente, e a instrumentação (B1+B3) vem **antes** de C.

**Motivo:** (a) A1 é a única mudança que remove a causa, e A1b impede que ela
crie um defeito novo; (b) C é o passo de maior risco do roadmap — aplicar 500 ms
inteiro num falso positivo estraga o áudio de vez — e sem B1/B3 não há como
distinguir "C acertou" de "C teve sorte"; (c) B1/B3 não mudam comportamento
nenhum, só contam e imprimem; (d) a Fase 0 pode encolher o roadmap inteiro: se o
acervo mostrar que só 21/08 teve salto, B2 vira desnecessária.

**Não é "B obrigatório desde já":** B2 tem ponto cego no canal do sistema
(§ 1.6) e C tem ponto cego em gravação de fone. Nenhum dos dois substitui o
outro; o que muda é a **ordem**, e C vem antes porque é mais barato e já tem
teste unitário sem hardware.

**O que reverteria:** Fase 0.2 mostrando deriva grande com a caixa muda — aí B2
sobe para logo depois de A1, porque a correlação nunca veria essa via.

### 6.2 Guard do `cancel_echo` sem eco (card `c931ed05c701f`)

**Decisão: sim, com guard — mas medido dentro do `cancel_echo`, não como opção
de config.** O filtro passa a comparar o resultado com a entrada e **devolver o
mic cru** quando o processamento piora. Virou o passo **E4**.

**Motivo:** o fato medido é inequívoco (acoplamento −31,5 dB e ERLE **−7,8 dB**
em 15:00: o filtro soma energia e piora o áudio que vai para o Whisper). Um
interruptor global obrigaria o usuário a adivinhar, arquivo a arquivo, uma coisa
que o código consegue medir. Guard interno é estritamente melhor e não pode
piorar: no pior caso não dispara e o comportamento é o de hoje.

**O que falta é o limiar, não a decisão** — sai da Fase 0.4. **O que reverteria:**
a distribuição do acervo mostrando ERLE negativo em arquivos onde o AEC hoje
ajuda a transcrição (aí o critério é outro, não o ERLE de bloco).

### 6.3 As duas transcrições de 16:52 (card `c2c45518347d4`)

**Decisão: renomear a contaminada, não apagar.**
`gravacao_reco_2026-08-21_16-52-20.txt` →
`gravacao_reco_2026-08-21_16-52-20.txt.desalinhado`, e a limpa
(`..._alinhado.mp3.txt`) fica como a boa. Mais a Fase E3, que é o conserto de
verdade.

**Motivo:** a recomendação anterior era apagar. Renomear resolve o mesmo risco —
a busca por conteúdo da biblioteca varre `.txt` e não pegaria mais o arquivo
contaminado, e ninguém abre por engano — **sem destruir** o registro de uma
reunião de trabalho real. O sufixo diz o porquê. É reversível; apagar não é.

**O que reverteria:** o Gabriel preferir o disco limpo — é arquivo dele, um
`del` resolve depois. A ordem (renomear agora, apagar se ele quiser) não tem
custo.

### 6.4 A sessão de 21/08 deveria ter alinhado os arquivos por conta própria?

**Delegado ao fable pelo decisor em 28/08. Decisão: sim, e a régua fica assim.**

- **Alinhar arquivo específico com queixa** (`--aplicar` em 1-3 arquivos): o
  agente decide e faz. É reversível — escreve `_alinhado.mp3` ao lado, original
  intacto — e o custo é um arquivo do mesmo tamanho.
- **Alinhar o acervo em massa** (dezenas de arquivos, ~500 MB): continua sendo do
  Gabriel, porque o critério é espaço em disco, não técnica.
- **Sempre medir o resultado antes de entregar.** Foi o que salvou o caso de
  15:00 — e agora vira guarda no código (E1), não disciplina do operador.

---

## 7. Pendente — decisão do Gabriel

- **Quando executar.** A Fase 0 é só medição (nenhuma linha de código, nenhum
  arquivo novo) e cabe numa sessão curta. A1+A1b+B1+B3 é o bloco que fecha o
  defeito e **exige recompilar o exe** (`build.ps1`).
- **Alinhar o acervo em massa depois da Fase 0.1?** Só faz sentido decidir com a
  tabela na mão — se forem 2 arquivos, é trivial; se forem 30, são ~500 MB
  duplicados. Fica em aberto **até** a Fase 0.1 rodar.
- **Transcrever 10:41 e 11:16?** Nunca foram transcritos. Se forem, tem de ser a
  partir do `_alinhado.mp3` — e depois de E3 isso passa a ser automático.

Fora do escopo deste md, ainda abertos de antes (roadmap de 19/08): Fase 2 (AEC
adaptativo), Fase 3 (consertar `tools/medir_eco.py`), Fase 4 (fone/operação),
teste de estresse de 20 min do modo ao vivo.

---

> **Auditado em 2026-08-30** (fable, card `c688f37215cbd`): premissa central
> reverificada na máquina (22,0 ms / 1000,0 ms nos dois dispositivos); § 0
> (estado real: nada executado, push já feito), § 1.6 e § 1.7 (duas vias novas),
> § 4 inteiro (3 furos de desenho + 5 vieses de medição); Fase 0 e passos A1b,
> E1b, E4 novos; B1 redesenhado (`catch_warnings` não é por thread — testado);
> C1/C2 com guardas de janela disjunta e `q ≥ 0,30`; § 6 com as 4 decisões
> técnicas tomadas. Conceitos do acervo aplicados: **via-negativa** (A1 remove a
> causa; A2 e B2 adicionam maquinaria — por isso ficam atrás de uma medição),
> **ancoragem-e-confirmação** (a sessão de 21/08 achou uma causa boa e parou de
> procurar outras; daí § 1.6) e **falácia-da-previsão** (C1 previa o
> comportamento da correlação sem medir; daí as duas guardas).
