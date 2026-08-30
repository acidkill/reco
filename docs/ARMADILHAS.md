# Armadilhas do Reco

Coisas que **parecem** funcionar e não funcionam, com o sintoma que elas produzem
e a causa real. Ler antes de "consertar" qualquer uma delas — várias já custaram
tempo e estão aqui justamente para não custarem de novo.

Padrão herdado do `nomura-bi`. Cada entrada: **sintoma → causa → o que fazer**.

---

## `no_repeat_ngram_size` não faz nada no `WhisperPipeline` (29/07/2026)

**Sintoma:** uma palavra ou frase se repete dezenas de vezes na transcrição
(caso real: `"o que é"` **147 vezes seguidas** num arquivo de 18/07/2026, e
`"que é"` **221 vezes** numa janela de outro). O código *parece* protegido:
havia um `cfg.no_repeat_ngram_size = 4` exatamente para isso.

**Causa:** o atributo **existe** em `WhisperGenerationConfig` — então atribuir
não levanta exceção, e um `try/except` em volta não denuncia nada — mas o
`WhisperPipeline` do `openvino_genai` **o ignora**. Medido: gerando com o
parâmetro desligado e com ele em 4, os textos saem **byte-idênticos**
(2590/2590 e 1427/1427 caracteres).

Pior: o `openvino_genai` 2026.2.1 **não expõe** `compression_factor_threshold`,
`logprob_threshold` nem `condition_on_prev_tokens` — é esse trio que o Whisper de
referência usa para perceber que uma janela degenerou e refazê-la com temperatura
maior. Ou seja, **não existe defesa nenhuma vinda da biblioteca**.

**O que fazer:** a defesa é nossa e vive em `OVTranscriber._degenerado()` +
`_generate_sem_loop()` (`reco.py`). Detecta pela taxa de compressão zlib (> 2,4,
o mesmo limiar do Whisper de referência) e por repetição consecutiva de qualquer
n-grama de 1 a 8 palavras (> 3×), e refaz a janela com temperatura 0,2 → 0,4 →
0,6. Se as três degenerarem, **descarta a janela** — texto ausente é melhor que
147 repetições. Validado: 221× → 2×.
Não reintroduzir `no_repeat_ngram_size` achando que resolve.

---

## Mudar `_CFG_DEFAULTS` é inócuo para quem já abriu o app (29/07/2026)

**Sintoma:** troca-se o default (por exemplo `"model"` de `small` para
`large-v3-turbo`), testa-se numa máquina limpa, funciona — e no computador do
usuário nada muda.

**Causa:** `load_config()` parte dos defaults e depois **deixa o arquivo salvo
sobrescrever** (`cfg.update(json…)`). Isso é o comportamento correto (a escolha
do usuário tem de ganhar), mas significa que qualquer default alterado só alcança
quem **nunca** salvou config. O `~/.reco_config.json` do Gabriel tinha
`"model": "small"` gravado, então a troca de modelo não teria chegado nele.

**O que fazer:** toda mudança de default que **precisa** alcançar usuários
existentes exige uma migração: subir `CFG_MIGRACAO` e tratar o caso em
`_migra_config()`. A migração roda uma vez (marcada em `_migracao` no próprio
JSON) e só promove valores que eram o **default antigo** — uma escolha deliberada
do usuário (ex.: `medium`) é preservada.

---

## A NPU leva ~7 minutos para compilar o modelo na primeira vez (29/07/2026)

**Sintoma:** ao transcrever pela primeira vez com um modelo novo na NPU, o app
fica parado por vários minutos sem sinal de vida. Parece travamento.

**Causa:** é compilação real do grafo para o acelerador. Medido: **415 s** para
`large-v3-turbo` na NPU (contra ~10 s na iGPU). Fica em `CACHE_DIR`
(`_user_data_dir()/ovcache`), então acontece **uma vez por (modelo, device)** —
mas uma vez por combinação, não uma vez na vida.

**O que fazer:** `_pipeline()` marca cada combinação já compilada com um arquivo
`.compilado-<size>-<device>` no cache e mostra um aviso explícito na primeira vez.
Não remover esse aviso; sem ele o usuário mata o app achando que pendurou.

---

## `cancel_echo`: 37 dB no sintético, 3 dB no áudio real (29/07/2026)

**Sintoma:** o cancelamento de eco estava documentado como "~37 dB ERLE
validado", mas o eco da caixa de som continuava audível e a diarização seguia
atribuindo ao usuário falas do interlocutor.

**Causa:** os 37 dB eram reais — em eco **sintético**, linear e invariante no
tempo. A implementação estimava **um** ganho complexo por bin de frequência para
a janela **inteira** de 30 s, o que assume que o caminho acústico é invariante
por 30 s e cabe num único quadro de FFT (64 ms). Numa sala real, medido nas
gravações do próprio Gabriel: **+3,2 dB** e **+3,7 dB**, com acoplamento
caixa→mic de −17,9 dB e −23,7 dB (abaixo de −35 dB seria desprezível).

**O que fazer:** a versão atual usa mínimos quadrados em blocos de 2 s com 6 taps
(~96 ms) + pós-supressão residual, e entrega **+7,2 dB** custando 0,3 dB da voz
do usuário. **Não tentar chegar a 20-40 dB alongando o filtro** — ver a armadilha
seguinte. E sempre medir ERLE em áudio **real**, nunca em eco sintético: é
exatamente esse teste que validou uma implementação que não funcionava.

---

## O teto do AEC é deriva de clock, não o filtro (29/07/2026)

**Sintoma:** melhorar o filtro de eco dá ganhos que estancam por volta de 7 dB,
e a melhora some em gravações longas.

**Causa:** microfone e loopback do sistema correm em **relógios de hardware
independentes**. Medido pelo atraso ótimo de correlação ao longo do arquivo:

| gravação | atraso mic↔loopback | desvio-padrão | deriva |
| --- | --- | --- | --- |
| 5,7 min | 608 amostras (38 ms) | **0** | 0,1 ppm |
| 80 min | 2283 amostras (143 ms) | **2314** | **−65,8 ppm** (≈ −237 ms/h) |

Em gravação curta o alinhamento é estável; em 80 minutos ele passeia centenas de
milissegundos. Nenhum filtro linear acompanha isso sem **reamostragem contínua**
de compensação.

**O que fazer:** aceitar o teto. Passar dele exigiria compensar a deriva *e* a
distorção não-linear da caixa — caro e incerto. Para o objetivo real (diarização
correta), o caminho barato é decidir o locutor por **dominância de energia entre
os canais**, que é robusto a eco residual e não depende de cancelar nada. Fone de
ouvido continua sendo a solução de custo zero que elimina o problema na origem.

---

## `np.linalg.solve` mudou de semântica no numpy 2.x (29/07/2026)

**Sintoma:** `ValueError: solve: Input operand 1 has a mismatch in its core
dimension 0, with gufunc signature (m,m),(m,n)->(m,n)`.

**Causa:** com `A` de forma `(lote, P, P)` e `b` de forma `(lote, P)`, o numpy 1.x
tratava `b` como pilha de vetores; o 2.x trata como matriz e o lote não bate.

**O que fazer:** passar `b[:, :, None]` e tirar a dimensão depois:
`np.linalg.solve(A, b[:, :, None])[:, :, 0]`.

---

## NLMS ingênuo diverge quando a referência silencia (29/07/2026)

**Sintoma:** um AEC adaptativo protótipo devolveu **ERLE de −38 dB** — ou seja,
amplificou o sinal em vez de limpá-lo.

**Causa:** o passo do NLMS é normalizado pela energia da referência
(`e / potência`). Quando o canal do sistema fica em silêncio, a potência tende a
zero, o passo explode e o filtro diverge.

**O que fazer:** usar mínimos quadrados com regularização de Tikhonov relativa à
energia **do próprio bloco** (solução fechada, estável por construção), como está
em `cancel_echo`. E manter a rede de segurança que devolve o áudio original se a
saída sair mais alta que a entrada — um AEC nunca deveria aumentar o sinal.

---

## Medir "impacto em outros apps" com numpy é medir a coisa errada (29/07/2026)

**Sintoma:** um benchmark de convivência acusou combinações rodando **mais
rápido** que a máquina ociosa e o app vizinho **acelerando** durante a
transcrição. Resultado incoerente, descartado.

**Duas causas somadas:**

1. O "vizinho" usava `numpy @ numpy`, que aciona **BLAS multi-thread** e satura
   todos os núcleos. Isso simula um segundo job pesado, não um app interativo — e
   ainda distorce a medida de velocidade do que se está medindo (a iGPU caiu de
   14,9× para 7,2× por causa dele).
2. O baseline "ocioso" é medido com a CPU em **frequência baixa**. Quando a
   transcrição carrega a máquina, o governor sobe o clock e o vizinho fica
   genuinamente mais rápido. O baseline é que estava lento.

**O que fazer:** medir **latência**, não throughput, com um vizinho
single-thread em **processo separado** que acorda periodicamente e faz trabalho
fixo (`temp/vizinho.py`) — é o perfil de um app de interface. E lembrar que esse
teste é puro CPU: ele **não** mede a iGPU disputando com renderização de tela e
vídeo, que é o cenário "transcrever durante reunião com câmera".

## `--diarizar` sem `--aec` não separa nada em gravação no alto-falante (03/08/2026)

**Sintoma:** rodamos `transcrever.py --diarizar` numa gravação real para
responder "quem falou esta frase — o Gabriel ou a interlocutora?". A saída veio
com **as duas faixas repetindo o mesmo texto**, deslocadas por uma fração de
segundo: cada frase aparece uma vez em `Interlocutor(es)` e de novo em `Eu`.
Inútil para atribuir autoria.

**Causa:** a gravação foi feita **sem fone**. O áudio do sistema saiu pelos
alto-falantes e voltou pelo microfone, então o canal do mic contém a fala do
outro lado — e `--diarizar` **sozinho não cancela eco**, só separa canais.

**A correção é usar as duas flags: `--diarizar --aec`.** Refeito assim, no mesmo
arquivo, a separação ficou limpa: blocos longos da interlocutora de um lado,
intervenções curtas do Gabriel do outro, e o `.txt` encolheu **38%** (69 KB →
42,6 KB) — o que sumiu era a duplicata de eco.

⚠️ **Meça antes de confiar.** `tools/medir_eco.py <mp3>` dá acoplamento e ERLE
reais **daquela gravação**. Neste arquivo: acoplamento caixa→mic **−20,5 dB** e
ERLE mediano **+17,6 dB** (min 1,4 / max 23,8) — resíduo em torno de −38 dB,
desprezível. O "~7 dB" citado no `CLAUDE.md` é o número de **uma** medição de
29/07, não uma constante: o ERLE varia com volume, sala e microfone. Abaixo de
~10 dB, a diarização volta a errar mesmo com `--aec`, e aí não há flag que
salve — só gravar de fone.

**Efeito colateral a conhecer:** onde o AEC zera o canal do mic (a interlocutora
falando sozinha), sobra silêncio — e o Whisper **alucina** nele, tipicamente
`"Obrigada."` repetido. Não é fala real perdida (é o oposto: o Gabriel
realmente não falava ali), mas polui a faixa `Eu:`. Ao ler, descartar linhas
`Eu:` curtas e genéricas que não respondem ao contexto.

## `sounddevice`/PortAudio não serve para captura neste hardware (06/2026)

**Sintoma.** Antes do Reco existir, o motor de captura (então em
`C:\Dev\mp4wav\gravador.py`) usava `sounddevice`. Três defeitos, todos medidos
na máquina do Gabriel (Realtek + Intel Smart Sound, Win 11, Python 3.14,
sounddevice 0.5.5):

- **O mesmo microfone aparecia 2–4× na lista.** PortAudio expõe cada dispositivo
  físico uma vez por host API (MME, DirectSound, WASAPI, WDM-KS), com nomes
  ligeiramente diferentes — MME trunca em 31 caracteres, WDM-KS acrescenta
  `" 1 ()"`. Deduplicar por nome exato não resolve, porque os nomes divergem.
- **Alto-falante entrava na lista de microfones.** Endpoints de *render* do
  WDM-KS reportam `max_input_channels=2` — falso.
- **Nenhum dispositivo WDM-KS abre** (`PaErrorCode -9999`), incluindo a
  "Mixagem estéreo" que o código antigo escolhia para o áudio do sistema. A
  exceção era engolida no `_rec_sys` e a faixa do sistema saía **vazia, em
  silêncio, sem erro**.

**Causa raiz.** `sounddevice` 0.5.5 **não expõe loopback WASAPI**:
`WasapiSettings` não tem o kwarg `loopback`, e abrir um dispositivo de render
como `InputStream` dá "Invalid number of channels". Sem loopback, só sobrava o
Stereo Mix — que é justamente o que não abre.

**Por isso `soundcard`.** Usa WASAPI exclusivamente: uma entrada por endpoint
(sem duplicata, sem alto-falante virando microfone) e
`sc.get_microphone(speaker.id, include_loopback=True)` dá loopback de verdade,
sem depender de Stereo Mix. Persistir dispositivo pelo `.id` (estável, tipo
GUID) e exibir o `.name`. Capturar as duas fontes a 48000 Hz fixo (nativo aqui)
e reamostrar ao salvar. Os gravadores funcionam em thread daemon sem init
manual de COM.

⚠️ Isso vale ao considerar trocar a lib de captura: `sounddevice` é a escolha
óbvia e mais popular, e **já foi testada e reprovada** neste hardware. Não é
questão de gosto.

## `tools/test_live.py` falha por GPU indisponível, não por regressão (12/08/2026)

**Sintoma.** Rodar `tools/test_live.py <mp3> 60` nesta máquina imprime
`FALHOU: critério = latência mediana <= 5s` (saiu 9,8s de mediana), acompanhado
de dezenas de linhas `onednn_verbose... errcode -59, CL_INVALID_OPERATION` e
`no opencl gpu device is available`.

**Causa.** O device resolvido pelo pipeline (`AUTO` → `GPU`) não conseguiu abrir
o backend OpenCL no momento do teste — cai pra CPU em silêncio, que é bem mais
lento pro modo ao vivo. **Não é regressão de código**: confirmado em
`roadmap/2026-08-12-melhoria-ux-ui-logica.md` Fase 1 (execução de 12/08)
rodando o mesmo teste antes e depois do patch (`git stash`) — falha idêntica
nos dois lados.

**O que fazer.** Não tratar uma falha desse teste como sinal de bug introduzido
sem antes descartar causa ambiental: rodar `git stash` (ou comparar contra o
commit anterior) e repetir o teste. Se a falha persistir idêntica dos dois
lados, é o ambiente (GPU ocupada/indisponível naquele momento), não o código.
O roadmap de 12/08 (Fase 1.4) cita esse script como prova de não-regressão do
`LiveTranscriber.stop(discard=True)` — ele serve pra isso (o app não trava, a
passada final roda), mas **não** serve como gate de latência enquanto esse
problema de GPU não for investigado à parte.

## `medir_eco.py` subestima o eco por construção (19/08/2026)

**Sintoma.** A métrica do projeto reporta acoplamento caixa→mic de −20 a −31 dB
("quase desprezível") em gravações cujo canal de mic é, na verdade, **dominado**
pelo áudio do PC — e o eco continua audível no MP3.

**Causa.** O rótulo de "só o PC falando" exige que o mic esteja quase mudo:

```python
so_sys = (s >= lim_s) & (m < lim_m * 3)
```

Como o eco entra no mic, todo bloco com eco **forte** é reclassificado como
`ambos` e sai da conta. No arquivo de 19/08 11:00: 1753 blocos `so_sistema`
contra **11448 `ambos`** (59% do arquivo). A medida acontece nos ~9% de blocos
onde o eco é mais fraco — é viés de seleção, não medição.

**O que fazer.** Medir por **predição**, não por rótulo de energia: estimar o
caminho de eco por mínimos quadrados (a voz do near-end é descorrelacionada da
referência, então o LS é não-viesado mesmo com fala em cima) e reportar a fração
da energia do mic que a referência explica. Medido assim, no mesmo arquivo:
**77% a 93%** (contra −30,9 dB da métrica antiga). E **sempre rodar o controle
negativo** — a mesma estimativa com referência falsa (outro trecho, canal
invertido, ruído branco) explica 1–5%; sem esse controle não há como distinguir
medição de sobre-ajuste. Números históricos (−17,9 / −23,7 / −20,5 / −30,9 dB)
herdam o viés: não citar como acoplamento real.

⚠️ Este é o viés do **acoplamento**. O mesmo script tem um segundo problema, pior
e independente: os rótulos são por energia **simultânea**, o que com canais
desalinhados troca eco por voz — ver a armadilha seguinte.

---

## Rotulagem por energia simultânea mede o OPOSTO quando os canais estão desalinhados (19/08/2026)

**Sintoma.** Medindo o `cancel_echo`, o AEC aparecia **destruindo a voz do
usuário em até 28,4 dB** nos blocos rotulados "só o usuário fala". O diagnóstico
que saiu disso — "o AEC não tem detector de double-talk e está apagando a voz" —
foi escrito, commitado (`76bcb9c`) e está **errado**.

**Causa.** O rótulo vinha de energias **simultâneas** (`mic` alto e `sistema`
abaixo do limiar, no mesmo bloco de 100 ms), e o eco chega ao mic **~200 ms
depois** (latência de buffer entre os streams). O bloco em que o sistema já
silenciou mas o mic ainda toca o rabo do eco é rotulado "só o usuário falando" —
o AEC remove eco de verdade ali, e a métrica registra como voz destruída. O
espelho do mesmo erro infla `so_sys`: o mic aparece em −51 dBFS porque o eco
daquele trecho ainda não chegou, e o "ERLE" ali é atenuação de ruído de piso.

**Como foi pego** (vale mais que a conclusão): a "correção" não fechava. O
detector de double-talk derrubou o dano de 28,4 → 9,1 dB mas matou o ERLE
(+15 → +0,5), o que nenhum DTD deveria fazer. Depurando a janela suspeita, os
blocos que "perdiam 34 dB de voz" perdiam 23,7 dB **mesmo com `residual=False`**
— subtração linear pura só remove tanto se o conteúdo estiver na referência. Era
eco. Rotulando com o sistema **alinhado**, o mesmo código mediu **+15,6 dB de
ERLE com +0,5 dB de dano**.

**O que fazer.**

- Rotular far-end-only / near-end-only **sempre** com o canal do sistema alinhado
  (`_alinhar_canais`) — é o que `tools/medir_aec.py` faz. `tools/medir_eco.py`
  **não** faz, e por isso não serve para julgar AEC.
- Reportar AEC como par **(ERLE, dano na voz)**. ERLE sozinho não distingue
  cancelar de abaixar o volume — isso continua verdade, e é por isso que o par
  existe; o que era falso é o número que ele acusou.
- Números honestos do `cancel_echo` (19/08, rotulagem alinhada): ERLE mediano
  **+15,5 / +6,4 / +9,0 dB** em três gravações, dano **≤ +1,2 dB**; a
  decomposição dá +8,7 dB de cancelamento linear e +6,8 dB de pós-supressão.
- E a lição de método: **quando a correção de um defeito "resolve" o número mas
  destrói o resultado que deveria preservar, suspeite da medição antes de aceitar
  o trade-off.**

---

## Os canais do MP3 saem desalinhados por ~200 ms (19/08/2026)

**Sintoma.** Ao ouvir a gravação, a fala do interlocutor aparece duas vezes com
separação nítida — soa como eco de sala grande, não como vazamento fraco.

**Causa.** Não é acústica: eco de caixa a um metro são ~3 ms. Medido no arquivo
de 19/08 11:00, o atraso mic↔loopback é de **3241 amostras (203 ms)**, com jitter
de 24 ms entre janelas e deriva de +21 ppm (+76 ms/hora). É **latência de buffer**
entre os dois streams: o `DualRecorder` sincroniza o *início* dos streams com
`threading.Barrier`, mas o loopback entrega o primeiro bloco com offset próprio, e
`_pump` pareia por **contagem de amostras** — o offset inicial fica gravado para
sempre. Acima de ~50 ms o ouvido deixa de integrar a reflexão como reverberação e
passa a ouvir eco separado, então esse offset é provavelmente a causa da queixa,
mesmo quando o acoplamento é modesto.

⚠️ **O sinal do offset varia entre gravações.** No arquivo de 18/08 15:16 o
loopback vem DEPOIS do mic, e o atraso chega a **−6385 amostras (−399 ms)** —
fora da busca de ±0,2 s que `_alinhar_canais` usava. Consequência medida: o AEC
saía com **ERLE negativo** (−1 a −6 dB, ou seja *somando* energia) naquele
arquivo. Corrigido em 19/08 subindo `maxlag_s` para 0.5 s; com a busca larga o
mesmo arquivo dá +4 a +10 dB. Implementação de alinhamento que assuma sinal
positivo está errada.

**O que fazer.** Compensar o offset na gravação (Fase 1 do roadmap de 19/08) —
alinhar não é "processar o áudio", é corrigir buffer, e é pré-requisito de
qualquer AEC e da diarização. ⚠️ E não confundir alinhar com cancelar: alinhar +
ganho escalar ótimo dá **−1,1 dB** de ERLE (não cancela nada); o ganho do
alinhamento é percepção e correção do resto do pipeline.

## `print` com emoji derruba script no console desta máquina (19/08/2026)

**Sintoma.** `UnicodeEncodeError: 'charmap' codec can't encode characters` no meio
de um teste de hardware que já tinha começado a gravar — perdendo a execução
inteira por causa de uma linha de aviso.

**Causa.** O console do Windows aqui é **cp1252**; `⚠️` (e qualquer caractere fora
dessa página) não tem representação, e o `print` levanta exceção em vez de degradar.

**O que fazer.** Nos **scripts de `tools/`**, prints em ASCII puro (`***`, `!`,
`->`). Emoji só em arquivo de texto (md, docstring que ninguém imprime) e na UI do
Tkinter, que é Unicode de verdade. Vale principalmente para script longo: falhar no
minuto 2 de um teste de 3 minutos por causa de um aviso é o pior custo possível.

## O alinhamento ao vivo demora ~10 min para se recuperar de um SALTO (21/08/2026)

**Sintoma.** Gravações do dia 21/08 com eco separado audível nos primeiros 5 a 10
minutos e limpas do meio para o fim — em 3 das 5 do dia. As de 20/08, com o mesmo
executável, saíram alinhadas de ponta a ponta (mediana +0 ms). Ou seja: não é
regressão de build, é intermitência.

**Causa.** O atraso mic↔loopback não só deriva devagar — ele **salta**. Medido
trecho a trecho (15 s, correlação cruzada):

| gravação | começa em | salta para | quando | volta a ~0 em |
| --- | --- | --- | --- | --- |
| 21/08 16:52 | 0 / −16 / −24 ms | **−359 ms** | t=60 s | t≈480 s |
| 21/08 11:16 | +118 ms | **+497 ms** | t=75 s | não volta (arquivo tem 4,5 min) |
| 21/08 10:41 | +83 / ~0 ms | **−495 ms** | t=165 s | t≈540 s |

E a volta é uma **escada de 50 em 50 ms, um degrau por minuto**: é exatamente
`ALIGN_MAX_AJUSTE` (2400 amostras @48k = 50 ms) por reestimativa, com
`ALIGN_RECHECK_S` = 60 s. O teto existe de propósito — uma estimativa ruim não
pode arrancar 400 ms do arquivo de uma vez —, mas ele não distingue **deriva de
clock** (dezenas de ppm, o que ele foi feito para corrigir) de **salto**.

⚠️ **A origem do salto foi encontrada no mesmo dia, e é uma constante nossa:** os
recorders são criados com `blocksize=CHUNK` (1024), e no `soundcard`
(`mediafoundation.py:549`) esse parâmetro vira a **duração do buffer** que o
WASAPI aloca — medido nesta máquina, **1056 frames = 22,0 ms** (com
`blocksize=48000` o Windows entrega 1000 ms). O thread de captura tem 22 ms para
voltar ao `record()`; sob carga (quantum do scheduler, GIL, GC, iGPU ocupada) ele
não volta, o buffer circular é sobrescrito e as amostras **somem sem ninguém
contar**. Como o `_pump` pareia por contagem de amostras, o que se perdeu vira
offset permanente. O Gabriel confirmou o contexto: *"eu estava com alto uso de
recursos do PC durante as gravações"* — e 20/08, sem carga, saiu perfeito com o
mesmo executável.

⚠️ **E o `soundcard` avisa — nós é que não escutamos.** `mediafoundation.py:771`
levanta `warnings.warn("data discontinuity in recording", SoundcardRuntimeWarning)`
exatamente nesse evento. Rodando pelo `Reco.exe` (sem console) o aviso não vai
nem para o stderr. Plano de correção (buffer de 1 s, escutar o warning, corrigir
pelo relógio em vez da correlação):
[roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](../roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md).

**Consequência medida.** No arquivo de 11:16, ERLE mediano do `cancel_echo` de
**+5,6 dB** com atraso por janela de +118 a +497 ms; alinhado por trecho com
`tools/alinhar_gravacao.py --aplicar`, o mesmo áudio dá **+12,2 dB com atraso 0 em
todas as janelas e dano na voz +0,0 dB**. No de 16:52, o ERLE mediano não muda
(+16,2 dB — as janelas que a métrica escolhe já eram do trecho pós-convergência),
mas os ~7 primeiros minutos deixam de ter eco separado.

**O que fazer.** Enquanto o código não distinguir os dois casos, gravação com
queixa de eco se conserta pós-fato com `tools/alinhar_gravacao.py <mp3> --aplicar`
(escreve `_alinhado.mp3` ao lado; residual medido: 0,0 ms no pior trecho).
Direção proposta para a correção, **não decidida ainda**: duas reestimativas
consecutivas concordando em magnitude alta (> ~100 ms) e com `q` acima do limiar
não são estimativa ruim — são salto, e aí o offset deve ser aplicado inteiro
(um salto no áudio custa menos que 10 minutos de eco). Alternativa mais barata:
encurtar `ALIGN_RECHECK_S` enquanto o residual for grande.

⚠️ **Não confundir com "o AEC piorou".** Nos trechos em que o áudio do sistema não
toca, a correlação vira ruído (`q` < 0,05) e tanto o atraso quanto o ERLE que as
ferramentas imprimem ali não significam nada. Julgar sempre pelos trechos com `q`
acima de `ALIGN_Q_MIN`.

## `medir_aec.py` não compara antes×depois: as janelas mudam (21/08/2026)

**Sintoma.** Depois de alinhar a gravação de 21/08 10:41 (residual medido 0,0 ms),
o `tools/medir_aec.py` mostrou o ERLE mediano **caindo** de +5,6 para +0,5 dB.
Lido de forma ingênua: "alinhar piorou o AEC" — conclusão falsa.

**Causa.** O script **escolhe as janelas pelo perfil de energia do arquivo** (as
que têm mais blocos rotuláveis como só-far-end e só-near-end). Alinhar muda esse
perfil, então o original foi medido em 2,9 / 3,8 / 4,0 / 4,4 / 4,9 / 6,5 min e o
alinhado em 2,8 / 5,0 / 6,5 / 9,0 / 9,5 / 10,5 min. **São trechos diferentes do
áudio** — e naquele arquivo os minutos finais não têm áudio do sistema tocando
(`q` < 0,05), então o "ERLE" ali é ruído medido contra referência inexistente.

**O que fazer.** Para comparar duas versões do mesmo áudio, comparar **as mesmas
janelas** (o script hoje não aceita janelas fixas — é o que falta implementar) ou
julgar pelo par que não depende da escolha: **atraso por janela** (0 em todas,
depois de alinhar) e a **transcrição**. O ERLE mediano só é comparável entre
arquivos medidos com o mesmo perfil de conteúdo. No arquivo de 11:16, onde 9/9
trechos correlacionam e as janelas caíram no mesmo material, a comparação vale e
deu +5,6 → +12,2 dB.

⚠️ Isto vale para **qualquer** comparação antes/depois neste projeto: instrumento
que escolhe sozinho o que medir não serve para medir mudança.

---

## Áudio comprimido antes de transcrever zera o arquivo inteiro — o VAD tem limiar global (26/08/2026)

**Sintoma:** um arquivo longo tratado com compressor/limiter (para recuperar fala
distante) volta da transcrição com **uma linha só** — `.` — ou duas ou três
palavras, sem erro, sem exceção, sem aviso. O log mostra `Transcrevendo… 0%` e o
processo termina normalmente. Pedaços de 5 min do **mesmo arquivo tratado**
transcrevem perfeitamente (800+ palavras), o que faz parecer defeito de duração
ou de memória. Não é: um arquivo de 80 min sem tratamento transcreve inteiro.

**Causa:** `segmentar_por_vad()` (`reco.py`) calcula o limiar de fala **sobre o
arquivo inteiro** — `piso = percentil 20 da energia dos quadros de 30 ms`,
`lim = max(piso × 3, 0.0035)`. O VAD depende do **contraste** entre silêncio e
fala. Compressor e limiter existem justamente para destruir esse contraste: eles
levantam o piso, o percentil 20 sobe junto, `lim` sobe com ele e quase nenhum
quadro passa como fala. Em trechos curtos o piso é recalculado localmente e o
contraste local sobrevive — daí o teste em pedaço enganar. Medida no caso real:
o áudio tratado não tinha **nenhum** silêncio detectável a −33 dB
(`silencedetect`), contra 6 no original.

**O que fazer:** ao transcrever áudio que passou por compressão dinâmica,
**fatiar em blocos** (5 min funciona) e transcrever bloco a bloco — o VAD
recalcula o piso em cada um. Passar todos os blocos numa única invocação do
`tools/transcrever.py` carrega o modelo uma vez só. E não confie em teste de
trecho para validar tratamento de arquivo longo: o trecho é justamente o caso
em que o defeito não aparece.

⚠️ **Corolário, medido no mesmo material:** tratamento não é ganho universal. Em
trechos com aplauso alto ou áudio de vídeo tocando na sala, o compressor esmaga
a fala e o **áudio cru rende mais** (425 contra 3 palavras num bloco de 5 min).
Em gravação estéreo de dois microfones, vale medir **canal a canal**: os dois
raramente prestam igual — no caso real o canal direito deu 302 palavras
distintas contra 149 do esquerdo no mesmo trecho, e o downmix ficou no meio
porque **soma o ruído do pior**. Mas tratar um canal isolado piora tudo: a
cadeia com compressor depende dos ~4 dB de SNR que a soma dos dois canais dá.
Régua prática: escolher a fonte **por bloco**, comparando palavras distintas.

---

## `warnings.catch_warnings` NÃO isola por thread — não serve para achar de qual canal veio o glitch (30/08/2026)

**Sintoma.** Um plano razoável para contar as descontinuidades do WASAPI é
envolver o laço de cada thread de captura em `warnings.catch_warnings(record=True)`
e ler a lista. Ele parece funcionar e atribui os avisos ao canal **errado**.

**Causa.** `catch_warnings` copia e restaura `warnings.filters` e
`showwarning` — que são **globais do processo**, não do thread. Nesta build
(Python 3.14.4, `warnings._use_context == 0`) o bloco de um thread captura o
que outro emitiu. Medido: dois threads, um dentro do `catch_warnings` e outro
só emitindo, e o warning do segundo apareceu na lista do primeiro. Com os dois
threads de captura do `DualRecorder`, cada um também sobrescreve o
`showwarning` do outro ao entrar e ao sair.

**O que fazer.** Instalar **um** `warnings.showwarning` no `start()` e resolver
o canal por `threading.current_thread()`, restaurando no `_wind_down`. E não
esquecer `simplefilter("always", SoundcardRuntimeWarning)`: o registro de
deduplicação do módulo engole a segunda ocorrência em diante.

## O `soundcard` fabrica silêncio pelo relógio quando o loopback está mudo (30/08/2026)

**Sintoma.** Gravação sem áudio de sistema tocando (fone, reunião silenciosa)
pode dessincronizar sem que nenhum aviso de descontinuidade apareça — e a
correlação cruzada é cega ali, porque não há eco para correlacionar.

**Causa.** `_record_chunk` (`mediafoundation.py:735-756`) **não bloqueia**
esperando o WASAPI: faz polling e, passados `deviceperiod_default * 4` ≈ 40 ms
sem pacote nenhum, devolve um bloco de zeros **dimensionado pelo relógio**
(`int(samplerate * elapsed_ns / 1e9)`). Só o **loopback** entra nesse caminho —
o mic sempre entrega pacote. O `int()` trunca e o `_idle_start_time` avança
pelo tempo inteiro, então o resto fracionário se perde: até 1 frame por
disparo, ou ~31 ms/min no pior caso de ociosidade contínua. E esse caminho
**não** levanta `AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY`.

**O que fazer.** Não tratar "contar o warning de descontinuidade" como cobertura
completa de perda de amostra: essa via é invisível a ele. Medir com contagem de
frames × relógio de parede por canal, com a caixa muda. Contexto e plano:
[roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](../roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md)
§ 1.6.

⚠️ **Corolário para quem for aumentar o buffer de captura:** o laço de captura
sai no `_stop_ev` e o que ficou no buffer do WASAPI é descartado no `__exit__`.
Com `blocksize=1024` isso são 22 ms; com um buffer de 1 s, passa a ser **até 1
segundo do fim de toda gravação**. Buffer maior exige drenar a cauda antes de
sair do `with`, nos dois canais.

## O acoplamento NÃO prediz se o `cancel_echo` vai ajudar ou piorar (30/08/2026)

**Sintoma.** Depois de achar uma gravação em que o AEC entrega **ERLE negativo**
(21/08 15:00: acoplamento −36,1 dB, ERLE −7,8 dB — o filtro *soma* energia), a
conclusão natural é "acoplamento fraco ⇒ desligar o AEC", e daí sai a tarefa de
calibrar um limiar de acoplamento. **Esse limiar não existe.**

**Causa.** Medidas 13 gravações de junho a agosto (`tools/varrer_aec.py`), as
duas populações se sobrepõem no eixo do acoplamento:

| gravação | acoplamento | ERLE |
| --- | --- | --- |
| 21/08 15:00 | −36,1 dB | **−7,8 dB** (piora) |
| 05/08 11:01 | **−14,4 dB** | **−0,5 dB** (piora) |
| 22/06 10:50 | −24,1 dB | +15,7 dB (ajuda muito) |
| 21/08 16:52 | −17,8 dB | +16,2 dB (ajuda muito) |

Um corte em −14,4 dB mata o AEC nos dois melhores casos do acervo; um corte em
−30 dB deixa passar o de 05/08. O acoplamento mede *quanta* energia do sistema
chega ao mic; se o filtro consegue **modelar** esse caminho é outra pergunta
(linearidade, AGC do mic, alinhamento, estacionariedade) e não se lê no primeiro
número.

**O que fazer.** Guard que mede o **ganho no próprio sinal** — comparar a saída
do `cancel_echo` com a entrada e devolver o mic cru quando piora — em vez de
qualquer régua baseada em acoplamento, e em vez de uma opção de config que
obriga o usuário a adivinhar arquivo a arquivo. **Não é caso raro:** 2 dos 13
(15%) têm ERLE ≤ 0. Desenho em
[roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](../roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md)
§ 6.2 (passo E4).

## `tools/test_alinhamento.py` está vermelho desde que nasceu (30/08/2026)

**Sintoma.** O `CLAUDE.md` manda rodá-lo "sempre que mexer em
`estimar_offset`/`_al_*`/`_pump`", e o roadmap de 19/08 o lista como prova da
Fase 1 — mas ele termina em `1 FALHA(S)`. Quem rodar pela primeira vez vai supor
que quebrou por causa da própria mudança.

**Causa.** O caso 8 ("gravação longa simulada: jitter de atraso corrigido na
reestimativa") deixa **+352 amostras (22 ms)** de residual na janela que contém a
transição (`0s=+0, 10s=+0, 20s=+0, 30s=+352, 40s=+0`), contra um gate de 160.
Determinístico — as seeds do gerador sintético são fixas. Verificado com
`git worktree` no `HEAD`, no `HEAD` sem trabalho não commitado de outras sessões,
e **no próprio commit que criou o teste** (`4714ee0`): falha nos três.

**O que fazer.** Antes de mexer na Fase C do roadmap de 21/08, decidir se os
22 ms são defeito real do `_al_corrigir_deriva` (é o mesmo fenômeno de
recuperação lenta que a Fase C existe para consertar, então C1 provavelmente já
o corrige) ou gate apertado demais para uma janela que contém a transição.
**Não relaxar o gate sem responder isso** — seria apagar o sinal em vez do
defeito. Enquanto estiver vermelho, "meus casos novos passam" e "o teste falha
pelo motivo de sempre" são indistinguíveis. Passo `C0` em
[roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](../roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md).

⚠️ De quebra, a linha de falha imprime `(pior: -1)` quando o valor que reprovou
é `+352` — o teste não reporta o número com que decidiu.

## Relatório que resume uma amostra vazia inventa conclusão (30/08/2026)

**Sintoma.** `tools/varrer_aec.py` imprimiu *"Nenhum arquivo com ERLE ≤ 0 nesta
amostra — o caso de 15:00 pode ser raro"* tendo medido **zero** arquivos. A frase
é gramaticalmente verdadeira e completamente enganosa; lida rápido, teria
encerrado a investigação do guard do AEC pelo motivo errado.

**Causa imediata.** A lista de caminhos vinha de um arquivo com CRLF, o
`xargs -d'\n'` deixou o `\r` colado no fim de cada path, e o filtro
`a.endswith(".mp3")` rejeitou todos em silêncio. **Causa de fundo:** o resumo
tratava "conjunto vazio" como um resultado em vez de como ausência de resultado.

**O que fazer.** Em qualquer script de medição daqui: (1) `.strip()` no caminho
vindo de arquivo, e falhar alto quando a lista de alvos fica vazia ou um alvo não
existe; (2) o bloco de conclusão testa `if not medidos` **antes** de qualquer
outro ramo e diz "amostra vazia, nada se conclui". É a mesma família de defeito
de `medir_aec.py` escolher as janelas sozinho e de `alinhar_gravacao.py` relatar
`medidos` e escrever `usados` — o instrumento descrevendo algo diferente do que
fez.

## A rede de segurança do `cancel_echo` mede o RMS global e nunca dispara (30/08/2026)

**Sintoma.** O `cancel_echo` tem, desde 29/07, um fallback que devolve o mic cru
"se a saída sair mais alta que a entrada" (`reco.py`, fim de `cancel_echo`) — e
esta armadilha até manda mantê-lo (§ NLMS acima). Mesmo assim, no arquivo de
21/08 15:00 o AEC entrega **ERLE de −17,4 a −3,9 dB nas 6 janelas medidas**: ele
soma energia e o fallback não reage.

**Causa.** A régua é **global** (`r_out > r_in * 1.05` sobre a chamada inteira) e
o dano é **local** — o ERLE mede só os blocos em que apenas o far-end toca. Voz
do usuário e silêncio diluem o aumento, e a razão global fica abaixo de 1,05.
Assinatura para conferir: quando o fallback dispara, a saída é o próprio mic e o
ERLE daquela janela sai **0,0 exato**; em 15:00 não há nenhum zero.

**O que fazer.** Não escrever um segundo guard: **corrigir a régua do que existe**
— comparar a energia do resíduo com a da entrada **por bloco de estimativa**
(o laço `for t0 in range(0, nt, passo)`) e zerar o filtro no bloco em que ele
piora. Passo `E4b` em
[roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](../roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md)
§ 4.10. E lembrar do contexto de § 8.5: **os arquivos com ERLE ≤ 0 são os que têm
salto de alinhamento** — o guard evita o dano, não conserta o eco.

## O ERLE publicado do `cancel_echo` mede um regime que o app não usa (30/08/2026)

**Sintoma.** Todo número de AEC deste projeto (inclusive o "+15,5 dB" do
`CLAUDE.md`) sai de `tools/medir_aec.py`, que mede em **janela contígua de 15 s**.

**Causa.** O pipeline real chama `cancel_echo` sobre outra coisa: as partes de um
grupo VAD livres de dominância, **concatenadas** (`_transcribe_channel`, o
`np.concatenate([audio[s:t] for s, t in partes])`) — ~3 s de fala em vários
retalhos. O eco chega ao mic ~200 ms depois da referência, então recortar só a
fala descarta o rabo do eco de cada retalho; e o `np.roll` global de
`_alinhar_canais` cruza as emendas, trazendo referência de outro instante.

**O que fazer.** Tratar os números de ERLE como **do laboratório**, não do
produto, até a Fase 0.6 do roadmap de 21/08 medir no regime real
(`tools/medir_aec_regime.py`). Vale para qualquer gate de AEC daqui: medir onde
o código roda, não onde é conveniente medir.

## A biblioteca não enxerga as transcrições do `tools/transcrever.py` (30/08/2026)

**Sintoma.** Gravação com transcrição pronta no disco aparece na view
"Gravações…" **sem o ✓** da coluna 📄, não é achada pela busca por conteúdo, e os
botões "Abrir transcrição" e ✦ Resumo IA ficam desabilitados. Contado em
30/08/2026 na pasta do Gabriel: **14 dos 47 `.txt` (30%)** estão nessa situação.

**Causa.** Existem **duas convenções de nome** e a biblioteca só lê uma:

| escritor | regra | resultado |
| --- | --- | --- |
| `reco.py` `_autosave_txt` (o app) | `audio.with_suffix(".txt")` | `x.mp3` → `x.txt` |
| `tools/transcrever.py` | `src.with_suffix(src.suffix + ".txt")` | `x.mp3` → `x.mp3.txt` |

Os cinco pontos de leitura da view (`_lib_sync_actions`, `_lib_scan_thread`,
`_lib_txt_content`, `_lib_open_txt`, `_lib_resumo`) usam `p.with_suffix(".txt")`,
que nunca resolve para `x.mp3.txt`. Cada convenção está certa sozinha — e o
`CLAUDE.md` documenta as duas, em seções diferentes, sem notar a colisão.

**O que fazer.** **Ler as duas convenções, escrever uma.** O conserto é uma função
de resolução (`_txt_de`) trocando os cinco pontos de leitura — não renomear os 14
arquivos, que é irreversível na prática e quebra o "pula `.txt` existente" do
`transcrever.py`. Passo `E3a` em
[roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md](../roadmap/2026-08-21-salto-de-alinhamento-sob-carga.md)
§ 4.14. ⚠️ Consequência que já mordeu: a transcrição **limpa** de 21/08 16:52
(`…_alinhado.mp3.txt`) é uma das 14 — a decisão de renomear a contaminada tirava a
errada da busca sem pôr a certa no lugar (§ 4.15 do mesmo roadmap).
