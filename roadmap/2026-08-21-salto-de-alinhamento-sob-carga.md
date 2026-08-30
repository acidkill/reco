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

| item | estado em 30/08 (2ª passada, fim do dia) |
| --- | --- |
| **Fase 0** | **0.1, 0.2 e 0.4 EXECUTADAS** — resultados em § 8. 0.3 depende de B1 |
| Fases A–E deste md | nenhuma executada (nenhuma linha de `reco.py` mudou) |
| E1 (`--aplicar` recusar) | não existe; não há `--forcar` em `tools/alinhar_gravacao.py` |
| E2 (`medir_aec.py` com janelas fixas) | não existe; `escolhe_janelas` ainda escolhe por energia |
| E3 (biblioteca conhecer o par `_alinhado`) | não existe |
| Push dos commits `691fdae`/`f5ec0a7` | **feito** (`origin/master` == `HEAD`, push de 28/08) |
| Documentação | 2 entradas novas em `docs/ARMADILHAS.md` (acoplamento não prediz o AEC; relatório de amostra vazia) |
| 10:41 e 11:16 | ainda não transcritos (se forem, tem de ser do `_alinhado.mp3`) |

**Acervo:** `Documents\Reco` tem **59 MP3**, dos quais 6 são `_alinhado` — **53
gravações originais**. ~~e nunca se mediu quantas têm salto~~ **Medido em 30/08:
16 das 43 medíveis (37%) têm salto, espalhadas de 20/07 a 21/08** (§ 8.1).

**Ferramentas criadas nesta passada** (todas somente-leitura, nenhuma toca
`reco.py` nem escreve MP3): `tools/varrer_acervo.py`, `tools/varrer_aec.py`,
`tools/test_relogio_captura.py`.

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

- [x] **0.1. Varrer o acervo inteiro, só relatório.** ✅ **FEITA em 30/08 —
  resultado em § 8.1: 16 das 43 medíveis (37%) têm salto, 12 delas fora de 21/08.**
  ⚠️ Feita com `tools/varrer_acervo.py` (**novo**), não com
  `alinhar_gravacao.py --aplicar`-menos-a-flag como este passo dizia: aquele
  relatório resume por **mediana**, que § 4.7 já apontara como o resumo errado
  para este defeito, e não expõe a série por janela. O script novo é somente-leitura
  e emite pior janela, amplitude da faixa e % do tempo acima de 50 ms.
  *Prova:* `temp/2026-08-30-varredura-acervo.txt` / `.json` (série completa, com
  o `q` de cada janela — insumo da calibração que E1 precisa, § 4.3).
- [x] **0.2. Medir a via de § 1.6 sem hardware de reunião.** ✅ **FEITA em 30/08 —
  resultado em § 8.3: +4,1 ms/min de deriva relativa, 12× abaixo da capacidade de
  correção atual. § 1.6 existe, mas NÃO sobe a Fase B2.**
  `tools/test_relogio_captura.py 120` (**novo**), duas corridas com a caixa muda.
  ⚠️ O critério que este passo escreveu ("abaixo de 1 ms/min irrelevante; dezenas
  de ms/min co-causa") deixa **uma zona cinza** entre 1 e "dezenas", e a medida
  caiu exatamente nela. O critério que decide é comparar com o que o alinhador já
  corrige — `ALIGN_MAX_AJUSTE`/`ALIGN_RECHECK_S` = 50 ms/min; o script passou a
  imprimir essa comparação em vez do limiar fixo.
  ⚠️ O script **mede** se o loopback estava mudo (RMS do canal) em vez de confiar
  na lembrança do operador — sem isso a medição não é interpretável.
  *Falta:* a repetição **com áudio tocando**, que sai de graça dentro de D1.
- [ ] **0.3. Medir o efeito do modo ao vivo na taxa de glitch.** Depende do
  contador de B1 — fica anotado aqui para não sumir, executa depois de B1.
  *Prova:* D1 rodado com e sem `live=true`, comparando a contagem de glitches.
- [x] **0.4. Medir ERLE do `cancel_echo` no acervo.** ✅ **FEITA em 30/08**
  (`tools/varrer_aec.py`, novo — reusa a rotulagem alinhada de `medir_aec.py`).
  13 gravações medidas de junho a agosto; resultados e a consequência em § 8.2.
  *Prova:* `temp/2026-08-30-aec-acervo.txt` / `.json`.
  ⚠️ **A prova que este passo pedia não existe, e isso é o achado.** Ele mandava
  calibrar "o limiar de acoplamento abaixo do qual o ERLE fica ≤ 0 dB" —
  pressupondo que o acoplamento separa os dois casos. **Ele não separa:** o AEC
  piora em −14,4 dB e ajuda (+15,7 dB de ERLE) em −24,1 dB, acoplamento mais
  fraco. Qualquer corte por acoplamento erra numa das pontas. Consequência
  direta em § 6.2 — o guard tem de medir o ganho no próprio sinal, e agora isso
  está provado, não só preferido.

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

- [ ] **C0. ⚠️ PRÉ-REQUISITO, descoberto em 30/08: `tools/test_alinhamento.py`
  está VERMELHO hoje, e nunca passou.** O caso 8 ("gravação longa simulada:
  jitter de atraso corrigido na reestimativa") deixa **+352 amostras (22 ms) de
  residual** na janela de transição (t=30 s), contra um gate de 160 amostras —
  `residual por janela: 0s=+0, 10s=+0, 20s=+0, 30s=+352, 40s=+0`. Verificado com
  `git worktree` em três pontos: no `HEAD`, no `HEAD` sem o trabalho não
  commitado de outras sessões, e **no próprio commit que criou o teste**
  (`4714ee0`, Fase 1 de 19/08). É determinístico (as seeds do gerador sintético
  são fixas). Ou seja: **nasceu vermelho e ninguém viu** — apesar de o
  `CLAUDE.md` mandar rodá-lo "sempre que mexer em `estimar_offset`/`_al_*`/
  `_pump`" e de o roadmap de 19/08 o listar como prova da Fase 1.
  **Por que bloqueia C:** a prova de C1+C2 é *"`test_alinhamento.py` com três
  casos novos"*. Com a base vermelha, o executor não consegue distinguir "meus
  três casos passam" de "o teste continua falhando pelo mesmo motivo de sempre" —
  que é exatamente o "C acertou × C teve sorte" que a decisão de § 6.1 quis
  evitar ao pôr B1/B3 na frente.
  **O que fazer antes de tocar em C1:** decidir se os 22 ms residuais na janela
  de transição são (a) defeito real do `_al_corrigir_deriva` — plausível, é o
  mesmo fenômeno de recuperação lenta que C existe para consertar — ou (b) gate
  apertado demais para uma janela que contém a própria transição. Se for (a), C1
  provavelmente já o corrige e o teste vira prova de C. **Não** relaxar o gate
  sem responder isso: seria apagar o sinal em vez do defeito.
  *Prova:* `python tools/test_alinhamento.py` com `0 FALHA(S)` **antes** de
  começar C1, e a razão do 352 escrita aqui.
  ⚠️ Menor, mas conserta junto: a linha de falha imprime `(pior: -1)` enquanto o
  valor que reprovou é `+352` — o resumo do teste não reporta o número que ele
  usou para decidir. Mesma família de § 4.5/4.6.
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
  ⚠️ **C2 sabota C1 se entrar sozinho.** `_al_acumular` (`reco.py:1782`) mantém uma
  janela **deslizante** dos últimos `ALIGN_JANELA_S`, então a sobreposição entre
  duas estimativas consecutivas é `(J − R)/J`: hoje (J=20 s, R=60 s) é **0%**;
  com C2 como escrito (J=20 s, R=15 s) passa a **25%** — e aí as leituras deixam
  de ser independentes, que é exatamente o que a Guarda 1 de C1 exige.
  ⚠️ **Correção de 30/08 (2ª passada, card `s2bb49459`):** o número certo é **25%**,
  não os 75% que a 1ª auditoria escreveu aqui e em § 4.2 — a janela nova traz 15 s
  de material inédito e só 5 s dos 20 s antigos. **E as duas saídas oferecidas na
  linha seguinte não são equivalentes:** "fazer C1 rejeitar o par sobreposto" com
  J=20 s/R=15 s rejeita **todo** par (a sobreposição nunca é zero), o que
  **desativa C1 permanentemente** sob C2. A única saída que preserva os dois é
  **baixar `ALIGN_JANELA_S` para 15 s junto com o recheck rápido** (J=R ⇒ 0% de
  sobreposição). Não é opção de gosto: é requisito.
  Custo: correlação de ≤15 s decimados a cada 15 s (~30 ms de CPU).
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
mexer na janela de 20 s, o que faz as leituras compartilharem material. O md
original pedia as duas juntas (*"Pronto quando (C1+C2)"*) sem notar. Guardas
adicionadas em C1 e C2.

⚠️ **Corrigido em 30/08 na 2ª passada (card `s2bb49459`):** esta seção dizia
**75%**; a sobreposição real é `(J − R)/J` = **25%** (J=20 s, R=15 s), porque a
janela de `_al_acumular` é deslizante e a leitura nova traz 15 s inéditos. O erro
subestimava a independência das leituras em 3×. Mais relevante que o número: a
saída "fazer C1 rejeitar o par sobreposto", oferecida em C2 como alternativa
equivalente a encurtar a janela, **desativa C1 por completo** enquanto C2 estiver
ativo — com J > R a sobreposição nunca chega a zero, então todo par é rejeitado.
Baixar `ALIGN_JANELA_S` para 15 s passa a ser **requisito** de C2, não escolha.

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

~~**O que falta é o limiar, não a decisão** — sai da Fase 0.4.~~ **O que reverteria:**
a distribuição do acervo mostrando ERLE negativo em arquivos onde o AEC hoje
ajuda a transcrição (aí o critério é outro, não o ERLE de bloco).

⚠️ **Atualizado em 30/08 pela Fase 0.4 (card `s2bb49459`) — a decisão fica, e
agora tem prova; a pendência do limiar MORRE.** A frase riscada acima supunha um
limiar de acoplamento a calibrar. Medidas 13 gravações (§ 8.2), as duas
populações **se sobrepõem** no acoplamento:

| caso | acoplamento | ERLE |
| --- | --- | --- |
| 21/08 15:00 — AEC **piora** | −36,1 dB | **−7,8 dB** |
| 05/08 11:01 — AEC **piora** | **−14,4 dB** | **−0,5 dB** |
| 22/06 10:50 — AEC ajuda muito | −24,1 dB | +15,7 dB |
| 21/08 16:52 — AEC ajuda muito | −17,8 dB | +16,2 dB |

Um corte em −14,4 dB mataria o AEC nos dois melhores casos do acervo; um corte
em −30 dB deixaria passar o de 05/08. **Não há limiar de acoplamento que
funcione** — o guard tem de medir o ganho no próprio sinal, exatamente como
E4 já estava desenhado. O que era a via preferida virou a única via.

Corolário que muda o tamanho de E4: **2 dos 13 arquivos (15%) têm ERLE ≤ 0** —
o caso de 15:00 não é exceção exótica, é uma fração do uso normal.

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

## 8. Fase 0 EXECUTADA (30/08/2026, card `s2bb49459`) — resultados

Três dos quatro passos rodaram (0.3 depende do contador de B1 e continua aberta).
Nenhuma linha de `reco.py` mudou. Ferramentas novas, todas somente-leitura:
`tools/varrer_acervo.py`, `tools/varrer_aec.py`, `tools/test_relogio_captura.py`.

> Os `temp/*.txt|.json` citados como prova **não são versionados** (`temp/` está
> no `.gitignore`) — são o dado bruto na máquina do Gabriel. Todo número que
> sustenta uma decisão está reproduzido aqui; para refazer, os comandos são os
> das próprias linhas 0.1/0.2/0.4 do § 3.

### 8.1 — 0.1: o salto é crônico, não foram "dois dias ruins"

Varridas as **53 gravações originais** em janelas de 15 s
(`temp/2026-08-30-varredura-acervo.txt`/`.json`), com a métrica que § 4.7 elegeu
(pior janela e % do tempo acima de 50 ms; **salto** = amplitude da faixa > 100 ms,
que é o quanto a defasagem mudou *dentro* do arquivo):

| veredito | n | do que é medível |
| --- | --- | --- |
| **salto** (amplitude > 100 ms) | **16** | **37%** |
| desalinhado (pior janela > 50 ms, sem salto) | 17 | 40% |
| ok | 10 | 23% |
| sem correlação (fone/caixa muda — não mensurável) | 10 | — |

**43 medíveis, e 33 deles (77%) têm dano audível em algum trecho.** Os saltos vão
de 20/07 a 21/08 e **12 dos 16 são fora de 21/08** — os piores são 18/08 08:58
(566 ms) e 21/08 10:41 (566 ms). Ou seja: **a premissa que abriu esta investigação
("três das cinco de 21/08") era um recorte, não o fenômeno.** § 4.4 desconfiou do
viés; aqui ele está medido.

**E o alinhamento ao vivo não resolveu.** Ele entrou em 19/08; separando o acervo
nessa data: **antes, 10 de 30 (33%) com salto; depois, 6 de 13 (46%)**. A amostra
de depois é pequena e a diferença não é significativa — o que importa é a direção:
não caiu. Coerente com § 1.4 (o teto de 50 ms/reestimativa leva ~10 min para
absorver 500 ms) e com o fato de 20/08 16:31 também ter salto (132 ms), contra o
"as três de 20/08 saíram alinhadas" que o card afirmava.

> **Consequência para o escopo:** a decisão de § 6.1 previa que *"a Fase 0 pode
> encolher o roadmap inteiro: se o acervo mostrar que só 21/08 teve salto, B2 vira
> desnecessária"*. **O acervo mostrou o contrário** — o bloco A1+A1b+B1+B3+C1+C2
> fica inteiro e ganha urgência: 37% das gravações do Gabriel nascem com salto.

### 8.2 — 0.4: o limiar que este passo mandava calibrar não existe

13 gravações de junho a agosto (`temp/2026-08-30-aec-acervo.txt`/`.json`), par
(ERLE, dano) com rotulagem alinhada + acoplamento. **2 das 13 (15%) têm ERLE ≤ 0** —
o `cancel_echo` piorando o áudio que vai para o Whisper. Detalhe e a tabela que
prova a sobreposição: § 6.2 (atualizado). Em uma linha: o AEC piora em −14,4 dB de
acoplamento e ajuda **+15,7 dB** em −24,1 dB, acoplamento mais fraco — nenhum corte
por acoplamento funciona, então **E4 tem de medir o ganho no próprio sinal**, como
já estava desenhado. Dano na voz ficou ≤ 1,7 dB em toda a amostra (gate: ≤ 2 dB).

### 8.3 — 0.2: a via de § 1.6 existe, é pequena, e não muda a ordem

`tools/test_relogio_captura.py 120`, duas corridas, **com a condição medida e não
lembrada** (o script reporta o RMS do loopback: `0.000000` = mudo de verdade):

| | mic | loopback |
| --- | --- | --- |
| buffer real | 1058 frames (22,0 ms) | 1056 frames (22,0 ms) |
| deriva absoluta | +23,0 ms/min | +27,1 ms/min |

**Deriva relativa (a única que desalinha): +4,1 ms/min**, reproduzida nas duas
corridas. A absoluta atinge os dois canais juntos e é inofensiva.

O critério de decisão certo **não** é o "1 ms/min" que o passo 0.2 escreveu, e sim
a capacidade de correção que já existe: `ALIGN_MAX_AJUSTE` (50 ms) por
`ALIGN_RECHECK_S` (60 s) = **50 ms/min**. Os 4,1 ms/min medidos estão **12× abaixo
disso** — o alinhador de hoje absorve essa deriva sem esforço, e ela é *acumulada*,
não abrupta, então não explica salto de centenas de ms.

> **Consequência para o escopo:** a condição de reversão de § 6.1 era *"Fase 0.2
> mostrando deriva grande com a caixa muda → B2 sobe para logo depois de A1"*.
> **Ela NÃO se cumpriu.** A ordem decidida (0 → A1+A1b → B1+B3 → C1+C2 → B2 → D)
> fica de pé, e B2 permanece depois de C.
>
> ⚠️ Uma correção a § 1.6: ele previa que só o loopback passaria pela via dos zeros
> fabricados. Medido, **o mic também deriva** (+23,0 ms/min) — a assimetria real é
> de 4,1 ms/min, muito menor que os ~31 ms/min que § 1.6 estimou para o pior caso.

### 8.4 — o que a Fase 0 mudou, em uma tabela

| pergunta aberta | resposta medida | efeito |
| --- | --- | --- |
| o salto é raro ou crônico? | **37% dos medíveis**, 12 de 16 fora de 21/08 | escopo **não** encolhe; ganha urgência |
| o alinhamento de 19/08 resolveu? | não (33% → 46%, amostra pequena) | C1+C2 seguem necessários |
| § 1.6 é co-causa? | +4,1 ms/min, 12× abaixo da correção atual | **B2 fica onde está** |
| qual o limiar de acoplamento de E4? | **não existe** — as populações se sobrepõem | E4 mede o ganho no sinal |
| o AEC piorando é exceção? | 2 de 13 (15%) | E4 sobe de prioridade |

**Não medido ainda:** 0.3 (efeito do modo ao vivo na taxa de glitch) — depende do
contador de B1, executa junto com D1. E a Fase 0.2 só rodou com a caixa **muda**;
a corrida com áudio tocando fica para D1, que já grava com áudio.

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

## Linhagem

> Escrita pelo maestro ao fim de cada rodada (`registrar_linhagem`). É o
> registro de QUEM fez o quê neste roadmap: o arquiteto que o desenhou, os
> executores que o cumpriram, custo e commit de cada passo. Serve à revisão
> do fable — ele lê o desenho E a execução, não só o resultado.

| quando | card | papel | modelo | custo | sinal | commit |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-30 03:50 | `c688f37215cbd` | arquiteto | opus | US$ 8.56 | feito | `eafe14b` |
