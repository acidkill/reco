# 2026-08-19 — Antieco: o que o áudio real diz, e o que fazer

**Alvo:** o cancelamento de eco do Reco (`cancel_echo`, `_alinhar_canais`, a
diarização por dominância e a métrica `tools/medir_eco.py`).

**Origem:** o Gabriel perguntou por que a gravação de 19/08/2026 11:00 está com
eco e se o app não tem AEC configurado.

**Arquivo analisado:** `~/Documents/Reco/gravacao_reco_2026-08-19_11-00-54.mp3`
(32,5 min, 16 kHz estéreo, L=mic com ganho 8×, R=loopback com ganho 1×), mais
`gravacao_reco_2026-08-19_10-22-24.mp3` e `gravacao_reco_2026-08-18_15-16-55.mp3`
na validação.

> ⚠️ **LEIA O § 1.4 ANTES DE CITAR QUALQUER NÚMERO DAQUI.** A primeira versão
> deste roadmap (commit `76bcb9c`) afirmava que o `cancel_echo` destruía a voz do
> usuário em até 28 dB. **Isso estava errado** e foi derrubado pela execução da
> Fase 0 no mesmo dia: era artefato da métrica, não defeito do AEC. As seções
> abaixo já estão corrigidas; a história de como o erro apareceu está no § 1.4
> porque é o achado mais reaproveitável deste roadmap.

**Contexto que muda o regime medido em 29/07:** o `mic_gain` do Gabriel está em
**8,0×** (+18 dB) e isso é **necessidade, não descuido** — o mic dele (array
digital do Intel Smart Sound) entrega voz ~18 dB abaixo do nível do loopback.
Decisão do Gabriel nesta sessão: **o 8× fica**; a solução tem que ser antieco,
não abaixar o ganho.

---

## 1. O que foi medido (nada aqui é suposição)

Instrumento definitivo: **`tools/medir_aec.py`** (criado nesta fase). Scripts de
investigação ficaram no scratchpad da sessão: `analise_eco.py` (atraso, coerência,
três tratamentos), `analise_eco2.py` (estimativa sem viés de rótulo),
`controle2.py` (controle negativo), `aec_honesto.py`, `amostras.py`.

### 1.1 O acoplamento real é enorme, e a métrica antiga não mostra isso

`tools/medir_eco.py` mede acoplamento só em blocos rotulados `so_sys`:

```python
so_sys = (s >= lim_s) & (m < lim_m * 3)   # PC fala, voce (quase) calado
```

O segundo termo exige que **o mic esteja quase mudo**, então bloco com eco forte
cai em `ambos` e sai da conta. Neste arquivo: `so_sistema=1753`, `so_mic=4401`,
**`ambos=11448`** (59%), `silêncio=1875`.

| medida | valor | como foi obtido |
| --- | --- | --- |
| acoplamento pela métrica antiga | −30,9 dB | `medir_eco.py`, blocos `so_sys` |
| energia do mic explicada pelo loopback | **77% a 93%** | LS 128–320 taps, blocos de 1 s realinhados, janelas de 4 s |
| coerência mic↔loopback (alinhada) | 0,48 a 0,76 | Welch, referência alinhada |

Controle negativo (mesma estimativa com referência falsa — outro trecho do mesmo
canal, o canal invertido no tempo, ruído branco de mesmo RMS):

```
janela 0.0m   real 90.9% | outro 3.5% | invertido 1.4% | ruido 1.8%
janela 7.0m   real 83.4% | outro 1.5% | invertido 3.6% | ruido 2.0%
janela 22.8m  real 93.4% | outro 5.0% | invertido 5.4% | ruido 1.6%
janela 31.7m  real 76.7% | outro 2.7% | invertido 1.3% | ruido 0.9%
```

Falsa explica 1–5% (o esperado por azar, taps/N ≈ 1%); a real explica 77–93%.
**O canal do mic é dominado pelo áudio do PC.** Este resultado NÃO depende de
rotulagem, e é o único número desta análise que sobreviveu intacto ao § 1.4.

### 1.2 Os dois canais saem desalinhados por centenas de milissegundos

| medida | 19/08 11:00 | 18/08 15:16 |
| --- | --- | --- |
| atraso mic↔loopback | **+3241 amostras (+203 ms)** | **−2736 a −6385 (−171 a −399 ms)** |
| faixa ao longo do arquivo | 2142..3370 (134..211 ms) | cresce em módulo ao longo do arquivo |
| jitter entre janelas | 385 amostras (24 ms) | — |
| deriva | +21 ppm (+76 ms/hora) | — |

Eco acústico de caixa a um metro é ~3 ms. Isso é **latência de buffer** entre os
dois streams do WASAPI: o `DualRecorder` sincroniza o *início* dos streams
(`threading.Barrier` antes do `__enter__`), mas o loopback entrega o primeiro
bloco com offset próprio e o `_pump` pareia por **contagem de amostras** — o
offset fica gravado para sempre. **O sinal do offset varia entre gravações** (no
arquivo de 18/08 o loopback vem depois do mic).

Consequências, todas confirmadas:

1. **Percepção.** Acima de ~50 ms o ouvido para de integrar a cópia como
   reverberação e ouve **eco separado**. É a causa mais provável da queixa.
2. **O alinhamento do código saturava.** `_alinhar_canais` usava `maxlag_s=0.2`
   (3200 amostras). No arquivo de 18/08, com atraso de até 6385 amostras, o
   alinhamento saía errado e o AEC ficava com **ERLE negativo** (−1 a −6 dB: ele
   *somava* energia). Corrigido na Fase 0 — ver o § 3.
3. **Métrica.** Rotular por energia simultânea com os canais desalinhados é
   inválido, e foi o que produziu o diagnóstico errado do § 1.4.

### 1.3 O `cancel_echo` funciona — e os números honestos são estes

Medido com `tools/medir_aec.py` (rotulagem alinhada), pós-correção da Fase 0:

| arquivo | ERLE mediano | dano na voz (pior) |
| --- | --- | --- |
| 19/08 11:00 | **+15,5 dB** | **+0,5 dB** |
| 19/08 10:22 | +6,4 dB | +0,0 dB |
| 18/08 15:16 | +9,0 dB | +1,2 dB |

Decomposição no arquivo de 19/08 11:00: **+8,7 dB de cancelamento linear** e
**+6,8 dB de pós-supressão residual** (total +15,6 dB), com o mesmo +0,5 dB de
dano na voz nos dois casos. Ou seja: é um cancelador de verdade com um supressor
em cima, não um supressor disfarçado.

Isso também revisa para cima o "~7 dB" que o `CLAUDE.md` citava de 29/07: naquela
medição o ERLE era calculado com a rotulagem simultânea (§ 1.4) e com o
alinhamento saturado (§ 1.2).

### 1.4 ⚠️ O erro que este roadmap cometeu, e como ele apareceu

**A afirmação errada:** "o `cancel_echo` atenua a voz do usuário em até 28,4 dB
por falta de detector de double-talk".

**De onde veio:** o dano na voz era medido nos blocos rotulados `so_mic` (mic com
energia, sistema abaixo do limiar) — rótulo calculado com **energias
simultâneas**. Com o eco chegando ao mic **200 ms depois**, o bloco em que o
sistema já silenciou mas o mic ainda toca o rabo do eco é rotulado "só o usuário
falando". O AEC removia eco de verdade ali, e a métrica registrava como voz
destruída. O espelho do mesmo erro inflava `so_sys`: o mic aparecia em −51 dBFS
porque o eco daquele trecho ainda não tinha chegado.

**Como foi pego:** implementando a correção. O detector de double-talk (Fase 0.2)
derrubou o "dano" de 28,4 → 9,1 dB mas também matou o ERLE (+15 → +0,5), o que não
faz sentido para um detector que só devia congelar o filtro em double-talk.
Depurando a janela de 31,5 min, os blocos que "perdiam 34 dB de voz" perdiam
23,7 dB **mesmo com `residual=False`** — subtração linear pura só remove tanto
assim se o conteúdo estiver de fato na referência. Era eco. Rotulando com o
sistema alinhado, o mesmo código mediu +15,6 dB de ERLE com +0,5 dB de dano.

**A regra que fica:** rotulagem por energia simultânea **exige** canais
alinhados. Sem isso, "far-end-only" e "near-end-only" trocam de lugar nas bordas
de cada trecho de fala, e a métrica mede o oposto do que pensa medir. Registrado
em `docs/ARMADILHAS.md`.

**O que o erro NÃO invalidou:** o acoplamento de 77–93% (§ 1.1, medido por
regressão com controle negativo), o atraso de 200–400 ms (§ 1.2, medido por
correlação), o "sincronizar não cancela" (§ 1.5) e o fato de o MP3 ser gravado
cru (decisão de projeto, não bug).

### 1.5 Teto de um cancelador linear neste sinal

| tratamento | ERLE |
| --- | --- |
| alinhar + ganho escalar ótimo ("sincronizar") | **−1,1 dB** (não cancela nada) |
| LS 128–320 taps, bloco de 1 s, realinhado (in-sample) | +6,3 a +11,8 dB |
| filtro estimado em far-end-only, aplicado adiante (out-of-sample) | +2,2 a +2,8 dB |
| **`cancel_echo` vigente (linear + pós-supressão)** | **+6,4 a +15,5 dB** |

A queda de in-sample (~9 dB) para out-of-sample (~2,5 dB) mostra que o caminho de
eco muda mais rápido que a janela de estimativa. Três causas somadas:

1. **O mic tem processamento dinâmico próprio** (array do *Intel Smart Sound*, com
   AGC/supressão no APO): o ganho efetivo do caminho muda com o conteúdo.
2. **Deriva de clock** (+21 ppm aqui; −65,8 ppm em 23/07).
3. **Distorção não-linear da caixa** — o que sobra depois do filtro.

⚠️ Ressalva de amostra: o número out-of-sample vem de **2 janelas** (só elas
tinham trechos far-end-only suficientes na medição original, feita antes de
`escolhe_janelas` existir). Trate como indicativo.

---

## 2. Decisões

1. **O `mic_gain` 8× fica.** Decisão do Gabriel: é o que nivela a voz dele com o
   áudio do PC. Proposta que dependa de baixá-lo está fora.
2. **O eco audível no MP3 não é bug de configuração: o AEC nunca roda no
   arquivo.** `cancel_echo` só é chamado em `OVTranscriber._transcribe_channel`; o
   caminho de gravação (`DualRecorder._pump` → `MP3Writer.feed`) grava cru, por
   decisão de projeto (roadmap 29/07 § 2, decisão 5). Isso **não muda** — mas ganha
   um caminho de exportação limpa (Fase 2.3).
3. **Alinhar os canais NÃO é "processar o áudio"** e entra na gravação: é
   compensação de offset de buffer, não filtragem.
4. **ERLE sozinho é métrica proibida.** Todo número de AEC sai como par
   **(ERLE, dano na voz)**, com **rotulagem alinhada** — as duas metades da regra;
   a segunda foi aprendida no § 1.4.
5. **`tools/medir_aec.py` é o gate de regressão do AEC.** Piso: ERLE mediano
   ≥ 5 dB, dano na voz ≤ 2 dB em toda janela, zero janelas com a busca de atraso
   saturada.
6. **Detector de double-talk foi medido e REJEITADO** (§ 4). O problema que ele
   resolveria não existe neste sinal.

---

## 3. Fases

### Fase 0 — ✅ EXECUTADA em 19/08/2026 (ver § 6)

- [x] **0.1 Alargar a busca de atraso.** `_alinhar_canais`: `maxlag_s` 0.2 → 0.5,
      e `cancel_echo` passa a expor `maxlag_s` para a correção ser mensurável.
      *Resultado:* no arquivo de 18/08, ERLE saiu de **−1 a −6 dB para +4 a +10 dB**;
      no de 19/08 11:00 o efeito é nulo (o atraso ali cabia no limite antigo).
      Zero janelas saturadas nos 3 arquivos.
- [x] **0.2 Detector de double-talk — implementado, medido e revertido.** Ver § 4.
- [x] **0.3 Rede de segurança por bloco — revertida junto.** Sem dano na voz não há
      o que proteger, e o ERLE negativo que motivou o item era o alinhamento
      saturado (0.1), não o filtro divergindo.
- [x] **Instrumento:** `tools/medir_aec.py`, com o gate da decisão 5.

### Fase 1 — ✅ EXECUTADA em 19/08/2026 (ver § 8)

O Gabriel escutou `1_original.mp3` × `2_alinhado.mp3` (§ 5) e confirmou: **o
desalinhamento é o que incomoda**. Isso fechou a dúvida do § 7 e disparou a fase.

- [x] **1.1** offset inicial medido e compensado no `_pump` (`estimar_offset` +
      retenção do pareamento até saber o offset).
- [x] **1.2** reestimativa periódica (`ALIGN_RECHECK_S`, 60 s) para deriva e jitter.
- [x] **1.3** `tools/alinhar_gravacao.py` para as gravações que já existem.

Especificação original, para referência:

**1.1 Medir o offset no início da gravação.** No `DualRecorder`, após os primeiros
~10 s com energia nos dois canais, estimar o atraso (reusar `_alinhar_canais`,
`maxlag_s=0.5`) e **descartar as amostras do canal adiantado** antes do pareamento
no `_pump`. ⚠️ O offset pode ter **qualquer sinal** (medido: +203 ms num arquivo,
−399 ms noutro) — a implementação tem que tratar os dois casos.
*Pronto quando:* em gravação nova de 3 min com áudio tocando, o atraso medido por
`tools/medir_aec.py` fica |d| < 160 amostras (10 ms) em todas as janelas.

**1.2 Reestimar durante a gravação (deriva).** +21 ppm ≈ 76 ms/hora; numa reunião
de 2 h o alinhamento inicial não serve no fim. Reestimar a cada ~5 min e corrigir
por drop/insert de amostras no canal adiantado.
*Pronto quando:* em gravação de 60 min, o atraso varia menos de 32 amostras (2 ms)
entre janelas.

**1.3 Consertar arquivos antigos** por remux com deslocamento de um canal, no molde
de `tools/reparar_duracao.py` (valida antes de substituir; sem `--aplicar` só relata).

### Fase 2 — AEC melhor (offline, adaptativo)

Meta revisada pelos números do § 1.3: o AEC vigente já entrega +6 a +15 dB sem
dano. O ganho disponível está em **estabilizar o pior caso** (o arquivo de 10:22
fica em +6,4 dB), não em multiplicar o melhor.

**2.1 Filtro adaptativo por sub-banda**, cobrindo ≥ 200 ms de cauda, realinhado a
cada 0,5 s, com atraso fracionário para a deriva sub-amostral. Nunca substituir o
LS regularizado sem medir: o NLMS ingênuo já divergiu para −38 dB aqui.
*Pronto quando:* ERLE mediano ≥ 12 dB **nos três** arquivos, dano ≤ 1 dB.

**2.2 Compensar o AGC do mic** (§ 1.5): estimar ganho lento por bloco e normalizar
antes do filtro.
*Pronto quando:* a diferença in-sample × out-of-sample cair abaixo de 3 dB.

**2.3 Exportar limpo.** Ação na biblioteca que grava `<nome>_limpo.mp3` com o canal
do mic tratado (alinhado + AEC), preservando o original.
*Pronto quando:* duração idêntica ao original (header Xing — ver a regra do MP3 por
container no `CLAUDE.md`) e o par (ERLE, dano) reproduzido no arquivo exportado.

### Fase 3 — Consertar `tools/medir_eco.py`

**3.1** Rotular com o sistema **alinhado** (a correção do § 1.4) e por **predição**
(quanto da energia do mic a referência explica), não por energia simultânea.
**3.2** Reportar sempre o par (ERLE, dano na voz) — ou simplesmente delegar para
`tools/medir_aec.py` e manter `medir_eco.py` só para acoplamento/diagnóstico.
**3.3 Regravar os números** citados em `CLAUDE.md`, `docs/ARMADILHAS.md` e
`cerebro/projetos/reco.md`, marcando os antigos como medidos com métrica inválida.
*Pronto quando:* os números baterem com os do § 1.1 e § 1.3 (±1 dB).

### Fase 4 — Origem (custo zero, maior efeito)

Fone elimina o acoplamento; baixar o volume da caixa e aproximar o mic reduz a
necessidade dos 8×. Não é engenharia, é operação — mas é o único caminho que leva
o eco a "desprezível" neste hardware.

---

## 4. Descartado e impraticável

- **Detector de double-talk no `cancel_echo` — IMPLEMENTADO, MEDIDO E REVERTIDO
  (19/08/2026).** Três variantes: decisão por bloco de 2 s (dano 28,9 → 6,9 dB,
  ERLE 11,8 → 5,1), por bloco de 1 s (dano 11,5 — pior), e por quadro de 16 ms com
  janela deslizante de quadros far-end (dano 9,1, **ERLE +0,5**). Varredura de 24
  combinações de `dtd_limiar`/`suave`/`teto_bloco_db`/`bloco_s`: nenhuma passou o
  gate. Quando a métrica foi corrigida (§ 1.4), ficou claro por quê: **não havia
  dano para corrigir** (+0,5 dB), e o DTD só tirava do filtro os dados de que ele
  precisava — ERLE caiu de +15,5 para +0,5. Revertido por `git checkout reco.py`;
  só a correção 0.1 ficou. Se um dia houver dano de verdade medido com rotulagem
  alinhada, o desenho por quadro é o ponto de partida, com uma diferença: estimar o
  filtro **também** com quadros de double-talk (o congelamento é que matou o ERLE).
- **AEC de 20–40 dB por software neste sinal — impraticável.** Filtro estimado em
  far-end-only generaliza 2,2–2,8 dB out-of-sample: o mic aplica AGC própria e o
  caminho muda com o conteúdo. Mantém o veredito de 29/07, agora com a causa
  medida (antes era atribuído só à deriva de clock).
- **"Sincronizar" como antieco — descartado como cancelamento, aceito como
  percepção.** Alinhar + ganho escalar ótimo dá **−1,1 dB**. Mas os 203 ms são a
  causa provável da queixa auditiva, então o alinhamento entra na Fase 1 pelo
  motivo certo. Hipótese levantada pelo Gabriel; medida e reclassificada.
- **Baixar o `mic_gain` de 8× — recusado pelo Gabriel** nesta sessão: é o que
  nivela a voz dele com o áudio do PC. Registrado porque foi a primeira proposta do
  agente e erra na premissa.
- **WebRTC AEC3 em tempo real — segue descartado** (dependência binária no
  PyInstaller, 29/07). Nota: se o AEC for algum dia para a **captura**, é a rota
  mais promissora, porque resolve deriva por construção.
- **Windows Voice Capture DSP (`CLSID_CWMAudioAEC`) — não avaliado, não
  descartado.** É o AEC da plataforma, com acesso aos dois endpoints e compensação
  de deriva nativa. Exigiria reescrever a captura em Media Foundation (o
  `soundcard` abre WASAPI em shared mode sem `SetClientProperties`, categoria
  `Other`, e não pede loopback reference — nenhum AEC de plataforma entra hoje).
  Fica registrado para o Fable rever: é a única rota que poderia passar de 15 dB.
- **RNNoise / supressão de ruído — fora de escopo** (é ruído, não eco).
- **Medir com o arquivo inteiro na RAM — impraticável nesta máquina.** O decode de
  32,5 min (249 MB) estourou memória: a máquina tinha **0,4 GB livres de 15,4 GB**
  durante a sessão. Toda medição é por janela, via `seek`. Vale para qualquer script
  novo em `tools/`.

---

## 5. Amostras de escuta (geradas em 19/08/2026)

`C:\Dev\Reco\temp\` — 30 s do trecho de 27,5 min do arquivo de 19/08 11:00:

| arquivo | o que é | nível do canal do mic |
| --- | --- | --- |
| `1_original.mp3` | como o Reco gravou | −20,9 dBFS |
| `2_alinhado.mp3` | mesmos canais, 203 ms compensados (é o que a Fase 1 faz) | −20,9 dBFS |
| `3_alinhado_aec.mp3` | alinhado + LS 160 taps/1 s, sem pós-supressão (−9,5 dB de eco) | −30,4 dBFS |
| `4_atual_aec.mp3` | o `cancel_echo` do app | −38,2 dBFS |

⚠️ A primeira versão desta seção dizia que a diferença de 7,8 dB entre `3` e `4`
era "a voz do usuário que o app remove". **Falso** (§ 1.4): é eco a mais que a
pós-supressão remove — o dano medido na voz é +0,5 dB. O par `1` × `2` isola o
efeito do alinhamento, que é o que vale escutar.

---

## 6. Relatório de execução — Fase 0 (19/08/2026, sessão 40ca3f49)

**Passo 0.1 — `maxlag_s` 0.2 → 0.5.** `reco.py:_alinhar_canais` (default) e
`cancel_echo` (novo parâmetro `maxlag_s`, repassado, para permitir medir
antes/depois na mesma execução).
*Prova* (`python tools/medir_aec.py <mp3> 6 15`, coluna "maxlag 0.2 s"):

```
18/08 15:16   janela  atraso |  0.5 s ERLE | 0.2 s ERLE
              13.0m  -3885   |     +8.5    |    -1.1
              13.9m  -4736   |     +7.4    |    -5.9
              15.8m  -4932   |     +9.5    |    -4.2
              21.5m  -6385   |     +9.6    |    -1.3
19/08 11:00   sem diferença material (+15,6 → +15,6): o atraso ali (203 ms) cabia
              no limite antigo
janelas com a busca de atraso saturada: 0 nos 3 arquivos (gate: 0)
```

**Passos 0.2 e 0.3 — revertidos.** Detalhe completo no § 4 (primeiro item).
Reversão por `git checkout reco.py`, com o `maxlag_s` reaplicado depois.

**Instrumento novo — `tools/medir_aec.py`.** Par (ERLE, dano na voz), rotulagem
alinhada, escolha automática de janelas com material para os dois lados, exit code
como gate. Substitui `tools/medir_eco.py` para julgar AEC.

**Gate final (todos com `maxlag_s=0.5` vigente):**

| arquivo | ERLE mediano | dano na voz (pior) | saturadas | veredito |
| --- | --- | --- | --- | --- |
| 19/08 11:00 | +15,5 dB | +0,5 dB | 0 | PASSOU |
| 19/08 10:22 | +6,4 dB | +0,0 dB | 0 | PASSOU |
| 18/08 15:16 | +9,0 dB | +1,2 dB | 0 | PASSOU |

**Arquivos tocados:** `reco.py` (+14/−3), `tools/medir_aec.py` (novo),
`docs/ARMADILHAS.md`, `CLAUDE.md`, este roadmap, `roadmap/README.md`,
`cerebro/projetos/reco.md`, `cerebro/pessoal/diario/2026-08-19.md`.

**Desvios de contrato (2):**

1. **O gate da Fase 0, como escrito no contrato, media a coisa errada** — dano na
   voz por rotulagem simultânea. Não foi "critério afrouxado depois de ver o
   resultado": a rotulagem foi corrigida porque a depuração provou que ela
   classificava eco como voz (§ 1.4). O gate ficou **mais** exigente (ganhou a
   trava de saturação de busca).
2. **Tasklist no chat, não no Task system** — `TaskCreate`/`TaskUpdate` não estão
   disponíveis nesta sessão (não aparecem entre as deferred tools). Rastreado por
   lista numerada.

**O que ficou fora:** Fases 1 a 4 (não pedidas nesta invocação).

**Pendências:** ver § 7.

---

## 8. Relatório de execução — Fase 1 (19/08/2026, sessão 40ca3f49, `/goal`)

Disparada pela confirmação do Gabriel de que o `1_original.mp3` incomoda — o
desalinhamento é o problema audível, como o § 1.2 previa.

### 8.1 O que foi feito

**1.1 — offset inicial (`reco.py`).** Helper novo `estimar_offset(mic, sistema, sr,
maxlag_s)` (correlação cruzada em 16 kHz, devolve atraso **e qualidade**), e o
`_pump` ganhou um estágio de alinhamento antes do pareamento:

- estado `"coletando"`: **retém** o pareamento (nada é escrito) até ter
  `ALIGN_COLETA_S`=10 s dos dois canais, estima o offset, e descarta as amostras do
  canal adiantado. Reter é de propósito: alinhar depois de escrever deixaria um
  salto no meio do arquivo;
- se a correlação não fecha (`q < ALIGN_Q_MIN`=0.15 — fone, caixa muda, ninguém
  falou), o estado vai para `"tentando"` em `ALIGN_DESISTE_S`=20 s: **grava
  normalmente** e segue tentando o offset completo a cada reestimativa. ⚠️ Este
  caminho existe porque a primeira versão retinha até 60 s, o que deixaria o MP3
  vazio e o rascunho ao vivo mudo por um minuto em toda gravação de fone;
- canal único → `"off"`, sem tentativa (não há o que alinhar).

**1.2 — deriva e jitter.** `_al_corrigir_deriva` reestima a cada
`ALIGN_RECHECK_S`=60 s sobre uma janela de 20 s decimada a 16 kHz (`_al_acumular`),
e corrige descartando (mic atrasou) ou inserindo silêncio (loopback atrasou), com
teto de `ALIGN_MAX_AJUSTE`=2400 amostras por checagem. ⚠️ **Era 300 s e não
bastou:** a deriva é lenta (~76 ms/hora) mas o atraso tem **jitter de ~24 ms**, e
com 5 min entre checagens o residual medido numa gravação real ficou em **18 ms**
(gate: 10 ms). A 60 s ficou em −0,3 ms.

**1.3 — gravações antigas.** `tools/alinhar_gravacao.py`: reestima o deslocamento a
cada 30 s (corrigindo a deriva dentro do próprio arquivo), escreve
`<nome>_alinhado.mp3` ao lado e **confere o resultado no arquivo escrito**. Nunca
sobrescreve o original — diferente de `reparar_duracao.py`, aqui o áudio é
re-encodado. Streaming, um passe, sem `seek` (decodificar 32 min de uma vez são
249 MB, que já estouraram a memória desta máquina; e `seek` em MP3 é aproximado, o
que emendaria os trechos com salto).

### 8.2 Provas

**`tools/test_alinhamento.py`** (novo, sem hardware, 15 casos):

```
1) estimar_offset com atraso conhecido: 0, +480, +3200, +9744, -4800, -9744
   -> erro 0 amostras em todos, q entre 0.83 e 0.86
2) canais sem relacao (fone): q=0.030 -> nao alinha
3) canal mudo: (0, 0.0)
4) _pump com 3 s: nada escrito (retido); com 15 s: offset +9744 aplicado,
   residual do par escrito +0 amostras
5) loopback atrasado (-14400): offset negativo aplicado, residual +0
6) deriva de +288 amostras corrigida em 1 ajuste; nao reestima antes de 60 s
7) canal unico: grava sem tentar alinhar
TODOS OS TESTES PASSARAM
```

**`tools/test_gravacao_alinhada.py`** (novo, hardware real, tocando áudio pelos
alto-falantes por 150 s):

```
[align] offset inicial +1686 amostras (+35 ms), q=0.512
residual por janela de 15 s: +0, +1, +1, -5, +1, +1, +1, +1, +1, +1 amostras
pior janela: -5 amostras (-0.3 ms) em 45s
GATE (|atraso| < 160 amostras = 10 ms): PASSOU
```

**`tools/test_encoder.py`** (regressão obrigatória do `_pump`): TUDO OK — com uma
mudança no próprio teste: os blocos [2] e [4] agora setam `_al_estado = "off"`,
porque testam o **pareamento** e o alinhamento (novo) retém áudio nos primeiros
segundos, o que faria um pump de 1000 amostras não escrever nada. A nota está no
arquivo, junto do porquê.

**`tools/alinhar_gravacao.py --aplicar`** nas três gravações:

| arquivo | atraso encontrado (mediana, faixa) | residual depois |
| --- | --- | --- |
| 19/08 11:00 | +202 ms (+134..+211) | **−0,1 ms** |
| 19/08 10:22 | +52 ms (+43..+83) | **0,0 ms** |
| 18/08 15:16 | −149 ms (−399..+52) | **0,0 ms** |

Os `_alinhado.mp3` estão em `~/Documents/Reco/`, com os originais intactos.

### 8.3 Arquivos tocados

`reco.py` (helper + constantes + 4 métodos novos no `DualRecorder` + engate no
`_pump`), `tools/test_alinhamento.py` (novo), `tools/test_gravacao_alinhada.py`
(novo), `tools/alinhar_gravacao.py` (novo), `tools/test_encoder.py` (ajuste de
contrato), `CLAUDE.md` (arquitetura da captura + 4 linhas na tabela de
ferramentas), `docs/ARMADILHAS.md`, este roadmap.

### 8.4 Desvios e pegadinhas encontradas

1. **Retenção de 60 s era inaceitável para gravação de fone** — virou 20 s + estado
   `"tentando"`. Achado ao pensar no caso sem eco, não medido em campo.
2. **`test_encoder.py` [2] quebrou** por mudança de contrato do `_pump` (legítima).
   Corrigido no teste, com nota explicando.
3. **Console cp1252**: um `⚠️` num `print` derrubou o teste de hardware no meio com
   `UnicodeEncodeError`. Registrado em `docs/ARMADILHAS.md`.
4. **Reestimativa a 300 s não bastava** (item 1.2 acima) — o contrato dizia "a cada
   ~5 min", e a medição real forçou 60 s.

---

## 7. Pendências — decisão do Gabriel

Fechadas em 19/08: ele escutou as amostras e confirmou que **o desalinhamento é o
que incomoda**; a Fase 1 foi executada em seguida (§ 8).

Em aberto:

- **Ordem das fases restantes.** Recomendação: **Fase 3** (consertar
  `medir_eco.py`, cujos rótulos simultâneos são inválidos) → **Fase 2** (AEC melhor,
  ganho menor do que se pensava) → **Fase 4** (fone/operação). A Fase 2 está em
  último entre as técnicas porque o AEC vigente já entrega +6 a +15 dB sem dano.
- **Rodar `tools/alinhar_gravacao.py` no resto do acervo?** As três gravações
  recentes já saíram alinhadas (§ 8.2). A pasta tem outras ~13 gravações antigas; o
  comando aceita a pasta inteira (`tools/alinhar_gravacao.py <pasta> --aplicar`) e
  gera um `_alinhado.mp3` por arquivo, dobrando o espaço ocupado. Não rodei em massa
  porque duplicar ~500 MB é decisão dele, não minha.
- **Reter 10 s no início é aceitável?** É o preço de alinhar sem salto no meio: o
  MP3 só começa a crescer ~10 s após o start, e um crash nesse intervalo perde o
  trecho. A alternativa (escrever desalinhado e corrigir depois) reintroduz o salto.
- **A transcrição melhora?** O AEC não estava destruindo a voz, então a hipótese de
  que as falas dele estavam sendo perdidas **cai**. Mas a diarização por
  `dominancia_sistema` compara blocos de 100 ms e realinha a cada 30 s: com os
  canais já alinhados na gravação, ela passa a acertar mais por construção. Vale
  medir numa gravação nova (não medido nesta fase).

---

## 9. Medição pós-Fase 1 nas gravações de 20 e 21/08/2026

Pergunta do Gabriel em 21/08: *"veja se é necessário corrigir o eco dos áudios de
ontem e hoje"*. Oito gravações medidas com `tools/alinhar_gravacao.py` (relatório,
sem `--aplicar`) e `tools/medir_aec.py`.

| gravação | dur | trechos com `q` confiável | atraso mediano | faixa | veredito |
| --- | --- | --- | --- | --- | --- |
| 20/08 09:33 | 17,1 min | 35/35 | +0 ms | −46..+0 ms | alinhada |
| 20/08 16:31 | 22,8 min | 44/46 | +0 ms | −26..+106 ms | alinhada |
| 20/08 17:31 | 1,3 min | 3/3 | −2 ms | −2..+8 ms | alinhada |
| 21/08 10:41 | 11,0 min | 9/22 | **−199 ms** | −444..+83 ms | **corrigida** |
| 21/08 11:16 | 4,5 min | 9/9 | **+330 ms** | +68..+497 ms | **corrigida** |
| 21/08 14:16 | 17,1 min | 30/35 | +0 ms | −46..+24 ms | alinhada |
| 21/08 15:00 | 38,6 min | 21/78 | +4 ms | −60..+428 ms | eco fraco (ver abaixo) |
| 21/08 16:52 | 11,9 min | 19/24 | **−130 ms** | −359..+0 ms | **corrigida** |

**A Fase 1 funciona — e não segura salto.** Todas as gravações de 20/08 saíram
alinhadas de ponta a ponta. Em 21/08, três das cinco começam alinhadas e **saltam**
350-500 ms no primeiro ou segundo minuto, voltando a zero numa escada de 50 ms por
minuto — que é exatamente `ALIGN_MAX_AJUSTE` por `ALIGN_RECHECK_S`. Séries medidas
em trechos de 15 s (`q` = correlação normalizada):

- **16:52** — 0 / −16 / −24 ms até t=45 s → **−359 ms em t=60 s** → volta a 0 em t≈480 s.
- **11:16** — +118 ms até t=60 s → **+497 ms em t=75 s** → ainda em +281 ms no fim (4,5 min).
- **10:41** — +83 / ~0 ms até t=45 s → **−495 ms em t=165 s** → −149 ms em t≈390 s.

Causa provável do salto: perda de blocos num dos streams do WASAPI. O `_pump`
pareia por contagem de amostras, então amostra perdida vira offset permanente — o
mesmo mecanismo do offset inicial que a Fase 1 corrigiu, só que no meio da
gravação. Está registrado em `docs/ARMADILHAS.md` (entrada de 21/08).

**Correção aplicada nos três** (`tools/alinhar_gravacao.py --aplicar`, escreve
`_alinhado.mp3` ao lado, original intacto): residual **0,0 ms no pior trecho** em
10:41, 11:16 e 16:52. ⚠️ **Em 15:00 o mesmo comando não serve**: com 21/78 trechos
correlacionáveis o script herda o último deslocamento válido nos trechos mudos e o
residual do pior trecho ficou em **+318,5 ms** — pior que o original (mediana
+4 ms). O `_alinhado.mp3` desse arquivo foi gerado, medido e apagado. Régua que
sai daqui: **alinhar pós-fato só vale quando a maioria dos trechos correlaciona**;
abaixo disso o relatório sem `--aplicar` é a resposta final, não o primeiro passo.
Prova do ganho no caso limpo (11:16, 9/9 trechos confiáveis):

| | ERLE mediano | atraso por janela | dano na voz |
| --- | --- | --- | --- |
| original | +5,6 dB | +118 a +497 ms | +0,3 dB |
| `_alinhado` | **+12,2 dB** | **0 ms em todas** | +0,0 dB |

Em 16:52 o ERLE mediano não se move (+16,2 dB): as janelas que a métrica escolhe já
caíam no trecho pós-convergência. O ganho ali está nos ~7 primeiros minutos, que
deixam de ter eco separado — e na transcrição (abaixo).

**A transcrição de 16:52 é a prova de dano que faltava (§ 7, item "a transcrição
melhora?").** Feita a partir do MP3 desalinhado, ela repete a interlocutora na
faixa `Eu:` palavra por palavra, com atraso — *"Interlocutor(es): …não são
dinâmicas muito legais, porque a primeira é dividir as equipes"* / *"Eu: que a
primeira é dividir as equipes e tudo mais"* — e alterna com `"Obrigado."` onde o
AEC zera o mic. Ou seja: com os canais 359 ms fora, o `cancel_echo` não cancela e a
`dominancia_sistema` atribui ao Gabriel fala que não é dele. Retranscrita do
`_alinhado.mp3`, o resultado está no § 9.1.

**21/08 15:00 é outro caso, não este.** Acoplamento caixa→mic de **−31,5 dB**
(fraco), só 21/78 trechos com correlação confiável e ERLE mediano **negativo**
(−7,8 dB em `medir_aec.py`, −5,9 dB em `medir_eco.py`): não há eco relevante para
cancelar, e o AEC ligado por config soma energia em vez de tirar. Alinhar não muda
isso (a mediana já era +4 ms). Fica como pergunta aberta: **vale um guard que
desliga o `cancel_echo` quando o acoplamento medido é desprezível?**
