# Roadmap de melhoria — Reco: VAD, diarização e transcrição ao vivo (2026-07-29)

## Contexto e motivação

O Gabriel usa o Reco para gravar reuniões (mic + áudio do sistema em MP3 estéreo,
L=mic / R=sistema) e transcrever localmente. Ele havia **abandonado** a
transcrição do app em favor do NotebookLM: lenta, e repetindo uma palavra dezenas
de vezes.

Em 29/07/2026 isso foi diagnosticado e corrigido — modelo `large-v3-turbo`,
device iGPU, e uma defesa anti-loop escrita à mão porque a biblioteca não oferece
nenhuma. Está no roadmap irmão
[`2026-07-29-transcricao-precisa-rapida-e-aec.md`](2026-07-29-transcricao-precisa-rapida-e-aec.md),
que **este pressupõe executado** (está — commit `a800b8f`).

Este documento cobre o que ficou aberto **e** a feature que o Gabriel pediu na
sequência: **transcrição ao vivo durante a gravação**, com liberdade explícita
para reescrever a lógica de transcrição inteira. Medida antes de prometida, ela
se mostrou não só viável como *melhor e mais barata* que o funcionamento atual.

A régua desta sessão foi `C:\Dev\CLAUDE.md` § *Decidir por evidência*: **todo
parâmetro abaixo tem número medido atrás**. Nada de "parece razoável".

### Leia antes de começar (caminhos exatos)

| arquivo | por quê |
| --- | --- |
| `C:\Dev\Reco\CLAUDE.md` | regra **não-opcional** de recompilar após tocar em `reco.py`; arquitetura de captura/encode |
| `C:\Dev\Reco\docs\ARMADILHAS.md` | 7 armadilhas medidas. As de nº 1 (`no_repeat_ngram_size` inócuo) e 4-5 (limites do AEC) restringem o desenho |
| roadmap irmão, § 1 e § 7 | todas as medições que fundamentam as decisões daqui |
| `C:\Dev\CLAUDE.md` | § YAGNI escopado, § Decidir por evidência, § Git (commit direto na main) |

⚠️ **Tudo em `reco.py` (~3.500 linhas, arquivo único) + `tray.py`.** O
`reco.spec` do PyInstaller e a regra de build dependem dessa forma. Resistir ao
refactor "já que estou aqui" (YAGNI escopado).

---

## Alvo e estado atual

**Transcrição** (`OVTranscriber`, `reco.py:~1500-1700`): decodifica o MP3 para
16 kHz e, com diarização, transcreve **os dois canais separadamente**, cada um em
janelas fixas de 30 s cortadas às cegas (`i += step`), depois intercala por
timestamp (`_merge`). `large-v3-turbo` na iGPU, com defesa anti-loop. Medido
ponta a ponta: 6,4× tempo real — 2 h de áudio em ~19 min.

**Captura** (`DualRecorder`, `reco.py:1110-1420`): três threads — um leitor por
canal e um `_encode_loop` que a cada 200 ms pareia os canais amostra a amostra
(`_pump`) e alimenta o `MP3Writer`. É no `_pump` que os canais saem **alinhados
e sincronizados** — o mesmo áudio que vai para o MP3.

**Eco** (`cancel_echo`): o Gabriel usa caixa de som; acoplamento medido de
−17,9 dB. O AEC entrega +7,2 dB e **não passa muito disso** — o teto é a deriva
de clock entre mic e loopback (−65,8 ppm).

---

## Diagnóstico

### O que está bom (não mexer)

- **A sincronia L/R do `_pump`.** Pareia só o trecho que ambos os canais já
  entregaram e guarda a sobra. Já sobreviveu a uma refatoração (28/07). O modo ao
  vivo deve **consumir** esse par, nunca reimplementar a sincronia.
- **Encode fora do thread de captura.** Trabalho pesado no thread de captura
  atrasa a leitura do WASAPI e estoura o buffer (áudio perdido). O modo ao vivo
  herda a regra sem discussão.
- **A defesa anti-loop.** Validada (221× → 2×). Não substituir por parâmetro de
  config — não existe um que funcione (ARMADILHAS nº 1).
- **O formato fixo 16 kHz estéreo.** É exatamente o que transcrição, diarização
  e AEC precisam. É também, sem conversão nenhuma, o formato que o Whisper come.

### O que está frágil ou custando

| # | achado | evidência | eixo |
| --- | --- | --- | --- |
| D1 | **Janelas de 30 s cortadas às cegas** partem frases no meio; o modelo pontua mal fragmentos sem começo nem fim | **medido**: VAD a 0,8 s dá 2,68 `?`/100 palavras contra 2,41, custando 0,8× | qualidade + desempenho |
| D2 | **Silêncio é transcrito à toa** — o filtro é um RMS da janela **inteira** de 30 s | **medido**: compute de 19,6 s → 16,1 s só trocando o janelamento | desempenho |
| D3 | **Diarização atribui fala à pessoa errada** — o eco põe a voz do interlocutor nos dois canais | **medido** (teste e2e, 27/07): `"Eu: É sempre nove"` é fala do interlocutor | correção |
| D4 | **Nenhum resultado antes do fim** — 2 h de reunião só viram texto 19 min depois | pedido do usuário | produto |
| D5 | **Contexto nunca é usado.** Cada janela é transcrita isolada, sem saber o que veio antes | **medido**: passar o texto anterior como `initial_prompt` derruba o WER de 6,4% para 5,7% **e** barateia (11,3 s → 10,0 s) | qualidade |
| D6 | Segmento acima de 30 s é **truncado em silêncio** pelo Whisper | **medido**: com silêncio de 1,5 s os segmentos chegaram a 76 s e o texto caiu de 2362 para 2195 chars | robustez |

---

## Decisões tomadas nesta sessão

O Gabriel pediu explicitamente que estas fossem **decididas**, não devolvidas
("melhore tudo, inclusive as decisões que dependem de mim"). Todas são
reversíveis por config.

**Dec1 — Device: iGPU para tudo; NPU fica livre.**
A informação decisiva veio dele: **a NPU roda os Windows Studio Effects da
webcam** (desfoque, enquadramento) durante as chamadas. Durante a reunião a NPU é
o chip *ocupado*, e disputar com ela degradaria o vídeo que os outros veem. E a
medição fecha o caso: o modo ao vivo custa **2,3× mais na NPU** que na iGPU
(43,4 s contra 18,7 s no mesmo trecho), porque o decoder estático tem overhead
fixo alto por chamada e a segmentação fina multiplica esse overhead. Ocupação
contínua com dois canais: **iGPU ~20%**, NPU ~48%, CPU >100% (não cabe).

**Dec2 — O texto ao vivo é RASCUNHO; ao parar, roda a passada final e substitui.**
Os dois divergem ~7% (WER). A passada final roda **depois** da reunião, quando
nada mais disputa a máquina, e usa o preset de lote (melhor qualidade). O
rascunho entrega o valor real — ter texto no instante em que a gravação para. Não
há por que escolher entre imediatismo e qualidade quando dá para ter os dois em
momentos diferentes.

**Dec3 — O modo ao vivo transcreve os DOIS canais.** Só o mic mostraria metade
de uma conversa. Cabe no orçamento (Dec1).

**Dec4 — UM preset só, otimizado para qualidade, nos dois modos.**
O Gabriel definiu a prioridade: *"transcrição em tempo real não é 100% necessário
uma baixa latência, a qualidade é mais importante"*. Isso elimina a dualidade de
presets que um desenho anterior previa — menos código, menos config, menos
superfície de bug — e ainda sai **mais barato** (14,4 s contra 18,8 s no mesmo
trecho), o que reduz a disputa com a webcam. Custo: o texto ao vivo passa a
aparecer com ~10 s de atraso em vez de ~3 s, e continua pronto no instante em que
a gravação para, que é o ganho que importa. Parâmetros em § E3.

**Dec5 — Arquitetura de segmento fechado, não de janela deslizante.**
Existem duas famílias de ASR ao vivo: *segmento fechado* (VAD detecta o fim da
fala, transcreve uma vez, o texto nunca muda) e *janela deslizante com
re-transcrição* (reprocessa os últimos N segundos continuamente e confirma tokens
por concordância — é o que o `whisper-streaming` faz). A segunda mostra texto
antes de a frase acabar, mas ele **treme** enquanto estabiliza e custa várias
vezes mais, porque reprocessa o mesmo áudio. Para reunião — onde o objetivo é o
texto pronto no fim — 3 s de atraso são irrelevantes, texto que se reescreve
sozinho é pior de ler, e o custo extra sairia do acelerador que a webcam também
quer. **Segmento fechado.**

**Dec6 — NÃO haverá export de MP3 com eco cancelado.** Ver "O que NÃO fazer".

---

## Especificação da lógica de transcrição (o coração deste roadmap)

Tudo abaixo foi medido em 180 s de fala real da gravação de 23/07, com
`large-v3-turbo` na iGPU. Referência de comparação = janelas de 30 s (o que o app
faz hoje): **19,6 s de compute, 2,41 `?`/100 palavras**.

| estratégia | envios | compute | latência | WER | `?` /100p | `,` /100p |
| --- | --- | --- | --- | --- | --- | --- |
| hoje (30 s cegos) | 6 | 19,6 s | — | — | 2,41 | 10,75 |
| sem agrupar | 22 | 16,1 s | 3,1 s | 7,7% | 2,68 | 10,96 |
| sem agrupar + contexto | 22 | 18,8 s | 3,1 s | 6,8% | 2,68 | 10,49 |
| juntar 5 s | 10 | 12,8 s | 14,4 s | 6,4% | 2,68 | 11,19 |
| **juntar 5 s + contexto** | 10 | **14,4 s** | 14,4 s | **5,5%** | **2,91** | 12,11 |
| juntar 10 s + contexto | 7 | 10,0 s | 22,1 s | 5,7% | 2,47 | 11,69 |
| juntar 10 s + margem de 1 s | 7 | 10,7 s | 22,1 s | 8,8% | 2,05 | 8,86 |

⚠️ **Esta tabela é de UM arquivo.** Ela orientou a exploração, mas os valores
finais vieram da validação em três arquivos (§ E3) — que derrubou o "5 s" que
esta tabela sozinha sugeria. Não voltar a decidir por ela isoladamente.

### E1 — Fonte e formato do áudio ("codificação")

**Decidido:** o áudio vem do `_pump`, em `float32` mono por canal a 16 kHz, **já
pareado e sincronizado**, exatamente o trecho entregue ao `MP3Writer`.

- **Nenhuma conversão.** 16 kHz mono float32 já é o formato nativo do Whisper e
  o do `MP3Writer`. Reamostrar seria trabalho puro sem ganho.
- **Com o ganho por canal aplicado** (`mic_gain`/`sys_gain`), não antes. O limiar
  do VAD é parcialmente absoluto (piso de `0.0035`), então áudio pré-ganho com o
  slider baixo poderia passar por silêncio. Regra: **o VAD vê o mesmo nível que o
  Gabriel ouve e que vai para o arquivo.**
- **Não reimplementar a sincronia.** O `_pump` já resolveu isso e é o único lugar
  que sabe fazê-lo.

### E2 — Segmentação ("tempo")

**Decidido:** VAD por energia, corte com **0,8 s de silêncio**, segmento mínimo
de **0,3 s**, partição forçada em **28 s**.

- 0,8 s vence 0,4 s e 1,5 s em compute, pontuação e divergência simultaneamente.
- 28 s por D6: acima de 30 s o Whisper trunca **em silêncio** — sem erro, sem
  aviso, o texto simplesmente some.
- Limiar adaptativo, não fixo: piso = percentil 20 da energia dos quadros de
  30 ms; fala = `max(piso × 3, 0.0035)`. Um limiar fixo não sobrevive a mudança
  de ambiente ou de microfone.

### E3 — Acúmulo: juntar até ~**3 s** de fala antes de enviar (preset único)

**Decidido:** agrupar segmentos consecutivos do VAD até somarem ~3 s de fala, e
só então transcrever. Vale para os dois modos (Dec4).

⚠️ **Leia como este número foi obtido, porque isso importa mais que o número.**

Uma primeira medição, num único trecho de 180 s, apontou 5 s como ótimo. Antes de
cravar, a escolha foi **validada em outros dois arquivos** — com a regra de
decisão congelada *antes* de olhar o resultado ("maior pontuação de interrogação
entre os alvos que não pioram o WER em mais de 1 ponto"), como manda
`C:\Dev\CLAUDE.md` § *Decidir por evidência* (§ Seleção ≠ confirmação).

**Os vencedores foram 3 s, 5 s e 10 s — um por arquivo.** Ou seja: os 5 s eram
**over-fitting ao trecho de escolha**. O que os três arquivos concordam:

| alvo | WER médio (3 arquivos) | `?` médio |
| --- | --- | --- |
| **sem agrupar** | **9,9%** — pior nos três | 3,49 |
| 3 s | **6,9%** | 3,34 |
| 5 s | 8,0% | **3,43** |
| 7 s | 7,4% | 3,26 |
| 10 s | 7,2% | 3,27 |

**O achado robusto é "agrupar ≫ não agrupar"** — reproduzido nos três arquivos, e
confirma por outro caminho a diretriz do Gabriel de priorizar qualidade. Já
*qual* valor entre 3 e 10 s é **ruído**: nenhum vence de forma reprodutível.

**Consequência prática, e é uma instrução ao executor:** `ALVO_ACUMULO_S = 3.0`
entra como **constante nomeada com um comentário citando esta medição**, não como
número mágico. Ninguém deve "afinar" esse valor por intuição, nem tratar 3 s como
precisão fina — qualquer coisa entre 3 e 10 s entrega o mesmo. Se um dia houver
motivo para mexer, o caminho é rodar `tools/exp_alvo.py` com mais gravações, não
opinar.

Resultado esperado com o preset (média dos três arquivos): compute **~0,75× do
atual**, pontuação de interrogação acima da de hoje, latência de ~10 s no modo ao
vivo.

### E4 — Contexto

**Decidido:** passar as **últimas ~30 palavras já transcritas** como
`cfg.initial_prompt` do próximo envio, **nos dois modos**.

- **Funciona na iGPU** — verificado nesta sessão. ⚠️ O comentário em `_gen_cfg`
  avisa que `initial_prompt` **estoura o decoder estático da NPU**
  (`roi_end <= max_dim`). Como Dec1 fixa a iGPU, não há conflito — mas **quem
  ligar a NPU precisa desligar o contexto**. Isso é um `if`, não uma suposição.
- Ganho medido no lote: WER 6,4% → 5,5%, `?` 2,68 → 2,91, e **mais barato**
  (12,8 s → 14,4 s no preset de 5 s... ver nota abaixo).
- Contexto por canal, separado: o prompt do canal do mic não deve conter fala do
  interlocutor, senão o modelo tende a continuar a voz errada.
- Ao reiniciar após uma janela descartada pelo anti-loop, **zerar o contexto** —
  propagar prompt derivado de texto degenerado envenena os próximos segmentos.

### E5 — O que foi testado e REJEITADO

- **Margem de 1 s de áudio antes do segmento.** Intuitivo (dar embalo ao modelo),
  mas medido: WER piora de 5,7% para 8,8% e a pontuação cai (`?` 2,47 → 2,05). A
  margem reintroduz áudio já transcrito e gera repetição de borda. **Não fazer.**
- **Agrupar em 10 s ou mais.** Mais barato, mas a pontuação cai (`?` 2,23-2,47) —
  o segmento volta a misturar frases, que é o defeito de D1 reaparecendo em outra
  escala. 5 s é o ponto onde a unidade ainda é uma fala.

---

## Roadmap

Ordem por valor e risco crescente. Cada fase deixa o app funcionando e é
reversível sozinha.

### Fase 1 — VAD + contexto no modo lote (corrige D1, D2, D5, D6) ✅ EXECUTADA 29/07/2026

Entrega qualidade **e** velocidade hoje, e constrói a base do modo ao vivo.
Risco baixo: interno à transcrição. Detalhe e números medidos:
[roadmap irmão § 8](2026-07-29-transcricao-precisa-rapida-e-aec.md).

1. [x] **Criar `segmentar_por_vad(audio, sr=16000, sil_s=0.8, max_s=28.0,
   min_s=0.3)`** em `reco.py`, após `decode_16k`. Retorna `[(ini, fim)]`.
   **Portar de `tools/exp_contexto.py::vad()`** — já validada, inclusive a
   partição forçada. Não reinventar.
   **Pronto quando:** em arquivo real, nenhum segmento passa de 28 s e a soma dos
   segmentos é menor que a duração total (está pulando silêncio de fato).

2. [x] **Criar `agrupar_segmentos(segs, sr, alvo_s)`** — junta consecutivos até
   somar `alvo_s` de fala; `alvo_s=0` devolve cada segmento sozinho. É o que
   materializa os dois presets de E3. Portar de `tools/exp_contexto.py::agrupar`.

3. [x] **Reescrever o laço de `_transcribe_channel`** para iterar sobre grupos em
   vez de `i += step`, com `alvo_s=ALVO_ACUMULO_S` (E3). O offset de tempo de
   cada trecho passa a ser `ini/sr`. Declarar `ALVO_ACUMULO_S = 3.0` como
   constante de módulo, com o comentário exigido em E3 — o valor é ruído dentro
   da faixa 3–10 s e não deve ser afinado por intuição.
   ⚠️ **Fallback obrigatório:** se `segmentar_por_vad` devolver lista vazia, cair
   para as janelas de 30 s de hoje. Nunca deixar arquivo sem transcrição por
   causa do VAD.
   **Pronto quando:** `python tools/test_e2e.py <mp3>` passa (repetição ≤ 3,
   compressão ≤ 2,4) e o tempo total **não** é maior que o de hoje.
   ⚠️ **Achado não previsto nesta implementação:** cancelar o eco (`cancel_echo`)
   no *span* inteiro do grupo (`ini:fim`, incluindo silêncio interno) ou
   pré-limpar o canal inteiro em blocos de 30 s antes do VAD saíram **mais
   lentos** que a janela cega de hoje — as tabelas de compute do E3 nunca
   incluíram AEC. A variante que ficou mais barata: concatenar só a fala do
   grupo (dropando os gaps) antes de cancelar o eco. Ver § 8 do roadmap irmão.

4. [x] **Adicionar contexto (E4):** manter as últimas ~30 palavras transcritas
   **por canal** e passá-las em `cfg.initial_prompt` do envio seguinte. Zerar
   após janela descartada pelo anti-loop.
   ⚠️ Só quando o device resolvido **não** for NPU — `initial_prompt` estoura o
   decoder estático dela. Um `if resolve_device(...) != "NPU"`, não uma suposição.
   **Pronto quando:** transcrever na NPU não levanta `roi_end <= max_dim`, e na
   iGPU o `?`/100 palavras sobe em relação ao passo 3.

5. [x] **Registrar o ganho medido** no roadmap irmão (antes/depois de `?`, `,` e
   tempo). Se a pontuação **piorar**, parar — a premissa da Fase 1 caiu.
   Pontuação melhorou nos dois eixos (`?` 2,56→3,78, `,` 9,87→10,84); tempo
   ficou na paridade em comparação pareada (ver § 8, máquina tem ruído alto).

6. [x] **Recompilar** (`powershell -ExecutionPolicy Bypass -File
   "C:\Dev\Reco\build.ps1"`) e commitar. Regra do projeto, não opcional.

### Fase 2 — Diarização por dominância de canal (corrige D3) ✅ EXECUTADA 29/07/2026

Conserta o defeito mais visível. Independente da Fase 1.

1. [x] **Extrair `_alinhar_canais(mic, sys, sr)`** de dentro de `cancel_echo`
   (a correlação cruzada com busca de ±200 ms) para função própria — será usada
   pelos dois lugares. Sem duplicar.

2. [x] **Criar `dominancia_sistema(mic, sys, sr=16000, bloco_s=0.1, k_db=?,
   histerese=3)`**: alinha, e marca como **eco** todo bloco em que
   `energia_sys > energia_mic + k_db`. `histerese` = nº de blocos consecutivos
   exigidos para trocar de estado (evita picotar).

3. [x] **Calibrar `k_db` com dado, não por chute** (`C:\Dev\CLAUDE.md` § Decidir
   por evidência). Escrito `tools/calibrar_dominancia.py`. **Revisado depois de
   uma consulta ao `advisor` achar que a calibração inicial (`k_db=12`, só
   contra so_mic) apagava fala real** — ver § 8.1 do roadmap irmão para o caso
   concreto e a correção completa. **`K_DB_DOMINANCIA = 15.0`**, calibrado
   contra três populações (so_sys, so_mic, e `ambos`/double-talk — a que
   faltava), confirmado por transcrição real, não só por métrica de bloco.
   **Pronto quando:** valor no código com comentário — ok, revisado.

4. [x] **Aplicar em `_transcribe_channel`**, só no canal do mic e só com
   `diarize` e `aec` ligados (reusa o `ref` que já existe para o AEC).
   Implementado mais fino que "descartar segmento": **recorta** as partes
   dominadas de dentro do grupo (`partes_livres`), não descarta o grupo
   inteiro — um grupo de VAD longo pode ter só alguns segundos de eco no meio
   de fala real, e votar por maioria do grupo perderia justamente esse caso.
   ⚠️ **Achado ao investigar o "pronto quando" original:** medindo a energia
   real amostra a amostra no trecho de `"Eu: É sempre nove"` (27/07,
   ~29,6-31,1s), o canal do sistema **não domina de forma limpa** — a razão
   sys/mic oscila entre −1 dB e +13 dB no mesmo segundo, sinal de **fala
   sobreposta (cross-talk)**, não de eco puro vazando. A frase persiste mesmo
   em `k_db` bem mais sensível — porque ela é, verossimilmente, o próprio
   Gabriel repetindo "é sempre nove" em cima da fala do interlocutor pra
   confirmar, não um artefato acústico. Dominância de canal **não pode e não
   deve** "consertar" isso: quando as duas pessoas falam ao mesmo tempo de
   verdade, a energia do mic é genuína.
   ⚠️⚠️ **Dois bugs achados numa segunda revisão (consulta ao `advisor`), antes
   de fechar a fase — detalhe completo em § 8.1 do roadmap irmão:**
   (1) `dominancia_sistema` alinhava só uma vez por canal inteiro; o atraso
   mic↔sistema deriva (48-155 ms medidos no arquivo de 80 min, chega a
   inverter de sinal perto dos 60 min) — corrigido pra realinhar a cada 30 s,
   como `cancel_echo` já fazia.
   (2) o `k_db=12` calibrado só contra `so_mic` **apagou duas falas reais** do
   Gabriel numa gravação de 20 min real (confirmado por diff de transcrição
   pareada, não só métrica de bloco) — corrigido subindo pra `k_db=15` e
   acrescentando o critério de `ambos` na calibração.
   **Validação do mecanismo (bug 2 corrigido), no mesmo arquivo de 20 min:**
   com `k_db=15`, as duas falas voltam a aparecer, e o mecanismo ainda pega
   eco real (recall 24,7% em `so_sys`, contra 34,9% do valor antigo — queda
   aceita, porque perder fala real é pior que deixar passar eco).
   ⚠️ **Custo:** o realinhamento a cada 30 s soma um custo real ao tempo de
   compute do canal do mic, mas a tentativa de atribuí-lo a um número
   específico (~15-20%) nesta sessão **não foi isolada corretamente** — ver
   § 8.1 do roadmap irmão. Relevante pro orçamento da Fase 3 (modo ao vivo);
   medir de novo com repetição pareada antes de fechar o orçamento do Dec1.

5. [x] Recompilar e commitar.

### Fase 3 — Modo ao vivo (entrega D4) 🟡 IMPLEMENTADA, teste de estresse (3.6) PENDENTE DO GABRIEL

Maior esforço, e o único risco sério: **não pode degradar a gravação.** Gravar é
a função primária; transcrever é secundária.

**Histórico desta fase:** parada de propósito na sessão de 29/07 (consulta ao
`advisor`) até os dois pré-requisitos abaixo estarem resolvidos e o Gabriel
autorizar prosseguir. Autorizado, os passos 1-5, 7 e 8 foram implementados e
validados com gravações reais curtas (5-25s) na mesma sessão. **O passo 3.6
(20 min reais) continua sendo o único gate que exige uma pessoa na máquina** —
não é algo que se autocertifique com testes automatizados, e por isso o código
está commitado mas o `live` não deve ser considerado "confiavelmente pronto
pra uso diário" até essa validação acontecer.

**Duas coisas para resolver ANTES do passo 3.1** (achadas revisando o já
executado, ainda não fazem parte do roadmap original):

1. [x] **O orçamento de device do Dec1 estava desatualizado — remedido.**
   "iGPU ~20% de ocupação" tinha sido medido no pipeline **anterior** à Fase
   1/2. Escrito `tools/bench_convivencia_pipeline.py` (reusa `VizinhoProc` de
   `bench_convivencia.py`, mas roda `OVTranscriber.transcribe()` de verdade —
   diarize+aec, VAD+contexto+dominância — em vez de `pipe.generate()` cru).
   Medido 2× no arquivo de 27/07, iGPU: vizinho fica **1,2-1,4× mais lento**
   (trabalho ocioso 1,4-1,8ms → 1,7-2,6ms sob transcrição; `acorda_p95`
   praticamente inalterado, 0,7-1,0ms). Orçamento de hoje **está OK** pro modo
   ao vivo — segue Dec1 (iGPU, não NPU, que é da webcam).
2. [x] **Concorrência entre `LiveTranscriber` (3.2) e a passada final (3.7) —
   decidido: drain-then-start.** Ao parar com o modo ao vivo ligado, a
   passada final espera o `LiveTranscriber` esvaziar a fila e devolver a
   thread antes de disparar a transcrição completa — nunca duas chamadas a
   `pipe.generate` no mesmo `WhisperPipeline` ao mesmo tempo. Implementado via
   `LiveTranscriber.stop(wait=True)` bloqueante (ver 3.2/3.3 abaixo).

1. [x] **Expor o par sincronizado.** Em `_pump`, após o `self._writer.feed(...)`
   bem-sucedido, chama `self._on_pair(mic_com_ganho, sys_com_ganho)` com o mesmo
   trecho entregue ao writer (E1). Registrado via parâmetro de `start()`
   (`on_pair`), igual `on_level`/`on_error`.
   ⚠️ O callback **só enfileira** — `LiveTranscriber.feed_pair` só faz
   `queue.put`, nenhum trabalho real.
   **Pronto quando:** validado — gravação real de 8s com callback contando
   amostras: 372736 amostras / 48000 Hz (CAPTURE_SR, não OUT_SR — o callback
   entrega o áudio ANTES do resample pro MP3) = 7,77s, batendo com a duração
   declarada do MP3 (7,77s).

2. [x] **Criar `LiveTranscriber`** em `reco.py`, após `OVTranscriber`. Thread
   próprio + `queue.Queue`. Mantém uma cauda de áudio por canal (resample
   48k→16k via `scipy.signal.resample_poly`), roda `segmentar_por_vad` sobre
   ela e, ao acumular `ALVO_ACUMULO_S` de fala, transcreve **com contexto** — o
   mesmo preset do modo lote (Dec4, E3/E4). Reusa o pipeline **já carregado**
   do `OVTranscriber` via `self._tr._pipeline(None)`, nunca instancia um
   segundo.
   ⚠️ **Corte por tempo de espera** (`ESPERA_MAX_S=20`) e **política de fila**
   (`FILA_MAX_S=60`, descarta o mais antigo + avisa) implementados.
   **Pronto quando:** `tools/test_live.py` (MP3 real, tempo real simulado).
   ⚠️ **Correção de medição (consulta ao advisor, 29/07):** a primeira versão
   deste teste usava a posição mais recente do laço de alimentação como proxy
   do "fim do trecho" — isso mede o avanço do próprio laço (deu 2,1s, **não
   era latência real**). Corrigido com `on_group` (novo parâmetro de
   `start()`, expõe o fim absoluto do grupo enviado) para medir fim-do-trecho
   → texto de verdade: **mediana 5-7s** entre execuções (min ~5s, max ~20-25s,
   n=10; a mediana variou 5,2→7,2s entre duas execuções idênticas — a máquina
   tem ruído, ver § 8 do roadmap irmão) — **na fronteira ou pouco acima** do
   critério de ≤5s.
   ⚠️ **Investigado (e corrigido em parte) o `max` de ~20-25s:** a hipótese
   inicial era acoplamento entre canais — um relógio de corte por espera
   **global**, não por canal, deixava o mic falando sem parar resetar o corte
   do sys (e vice-versa). Real e corrigido (`_ultimo_envio` virou dict por
   canal). **Mas instrumentando `_transcrever_grupo` direto, o outlier persiste
   mesmo corrigido** — a causa de verdade é o próprio Dec5 (segmento fechado):
   um grupo só fecha quando o VAD acha ≥0,8s de silêncio real, atinge o teto de
   28s (`max_s`) ou o corte por espera dispara **20s depois do envio anterior
   daquele canal**. Numa fala contínua sem pausa suficiente por muitos
   segundos, o atraso é inerente ao desenho — `ALVO_ACUMULO_S` é um **piso**,
   não um teto (achado já registrado no roadmap irmão § 7). Ainda bem dentro do
   teto de segurança de `FILA_MAX_S=60s` (não perde áudio, só atrasa o
   rascunho). Aceito como está: é rascunho (Dec2), a passada final não depende
   disso. Revisitar só se o Gabriel achar o atraso incômodo no uso real —
   opção futura seria baixar `ESPERA_MAX_S` à custa de fragmentar mais a fala
   corrida.

3. [x] **Respeitar pausa e falha:** com a gravação pausada o `DualRecorder`
   descarta frames antes de `_pump` — `on_pair` simplesmente não é chamado
   durante a pausa, sem estado especial no `LiveTranscriber` (verificado no
   código, é assim por construção). **Qualquer** exceção é capturada e logada
   sem propagar — testado disparando uma exceção de propósito dentro de
   `on_pair`: a gravação seguiu e o MP3 saiu com a duração certa
   (`python -c "..."` ad hoc, 5s gravados = 4,78s declarados).
   **Pronto quando:** matar o pipeline no meio mantém o MP3 íntegro — ok.

4. [x] **UI:** painel de texto ao vivo (`tk.Text` com scrollbar) na tela de
   gravação, com marcação de quem falou (tag `ACCENT`+`SEG_SB`), só visível
   quando a gravação atual tem `live` ligado. `SEG_SB` = "Segoe UI Semibold"
   (peso 600 — não passa do teto).
   **Pronto quando:** grava, o texto aparece, a UI não congela — validado com
   `tools/test_live_integration.py` (gravação real de 25s, thread própria não
   bloqueia o encoder nem a UI).

5. [x] **Config `"live"` (bool, default `False`)** em `_CFG_DEFAULTS`, com
   checkbox (`tk.Checkbutton`) na tela avançada. A escolha trava no início da
   gravação (`self._live_was_on`) — mudar o checkbox no meio não afeta a
   sessão em andamento.
   **Pronto quando:** desligado, o comportamento é o de hoje — ok (painel
   fica oculto, `on_pair=None` passado ao `DualRecorder`).

6. [ ] 🔴 **TESTE DE ESTRESSE — bloqueante, PENDENTE DO GABRIEL (parcial).**
   Gravar **20 minutos** com o modo ao vivo ligado, na iGPU, e verificar: (a) a
   duração do MP3 bate com o tempo real (±1 s); (b) L e R continuam
   sincronizados; (c) a UI não travou. **Se qualquer um falhar, a Fase 3 não
   entra.**
   **Progresso desta sessão (29/07, sem o Gabriel):** rodado
   `tools/test_live_integration.py` por **170s (2m50) com WASAPI real** — mic +
   loopback de verdade, fala real tocada pelos alto-falantes (não simulação de
   MP3). Resultado: 22 trechos de rascunho produzidos durante a gravação,
   duração do MP3 **169,94s contra 170,01s gravados (erro −0,06s)**, drain em
   0,8s, passada final rodou depois sem conflito (18,6s, 1834 chars, sem erro).
   ⚠️ **Isso NÃO substitui o teste de 20 min** — falta: duração real de
   20 minutos (efeitos de memória/backlog que só aparecem em prazo mais longo),
   cliques na UI **durante** a gravação (o teste automatizado não interage com
   a janela), e pausa/retomada no meio. Mas reduz bastante o risco: a mecânica
   de ponta a ponta com hardware real já rodou uma vez sem degradar a captura.
   **Pronto quando:** o Gabriel gravar os 20 min de verdade e confirmar (a)-(c).

7. [x] **Passada final ao parar (Dec2):** ao encerrar com modo ao vivo ligado,
   `_after_stop` dispara `_run_live_final_pass` (reusa `_run_transcriber`, o
   mesmo caminho da transcrição manual — diarização + AEC + dominância de
   canal) e substitui o painel com o texto final.
   ⚠️ **Drain-then-start implementado:** `LiveTranscriber.stop(wait=True)` é
   chamado e o `join()` retorna **depois** que a fila esvazia, ANTES de
   `_run_live_final_pass` disparar — nunca duas chamadas a `pipe.generate` no
   mesmo `WhisperPipeline` ao mesmo tempo.
   **Pronto quando:** validado com `tools/test_live_integration.py` — grava
   25s real, `stop()` drena em 0,7s, a passada final roda depois sem erro.
   ⚠️ Não testado bit-a-bit "texto final idêntico ao da transcrição normal"
   porque **é literalmente a mesma chamada** (`_run_transcriber`) — não há como
   divergir.

8. [x] Recompilar, commitar. `README.md`/`CLAUDE.md` do projeto atualizados
   junto (ver commit da Fase 3).

---

## Priorização (impacto × esforço × risco)

| item | impacto | esforço | risco | veredito |
| --- | --- | --- | --- | --- |
| Fase 1 — VAD + contexto | **alto** (27% mais rápido e melhor pontuação, medido) | baixo | baixo | **fazer primeiro** |
| Fase 2 — dominância de canal | **alto** (conserta erro visível) | médio | baixo | fazer |
| Fase 3 — modo ao vivo | **alto** (elimina 19 min de espera) | **alto** | **médio** | fazer, com o teste bloqueante |
| margem de áudio no segmento | negativo | baixo | — | **medido e rejeitado** |
| export de MP3 limpo | baixo | médio | baixo | **não fazer** |
| AEC além de ~7 dB | baixo | **muito alto** | alto | **não fazer** |

---

## O que NÃO fazer

- **Margem de áudio antes do segmento.** Medido: WER 5,7% → 8,8%, `?` 2,47 →
  2,05. Reintroduz áudio já transcrito e gera repetição de borda.
- **Janela deslizante com re-transcrição** (estilo `whisper-streaming`). Custa
  várias vezes mais, o texto treme na tela, e resolve um problema que o Gabriel
  não tem (Dec5).
- **Export de MP3 com eco cancelado.** Com ERLE de 7 dB o ganho perceptual não
  justifica a feature. A Fase 2 resolve o problema real — atribuição errada de
  fala — sem produzir arquivo nenhum. Revisitar só se o AEC passar de ~15 dB.
- **Perseguir AEC de 20–40 dB.** Teto físico: deriva de clock de −65,8 ppm
  (ARMADILHAS nº 5). Exigiria reamostragem contínua **e** tratamento da
  não-linearidade da caixa.
- **Reintroduzir `no_repeat_ngram_size`.** Aceito e ignorado (ARMADILHAS nº 1).
- **`initial_prompt` na NPU.** Estoura o decoder estático. Contexto e NPU são
  mutuamente exclusivos — o código deve testar, não supor.
- **Trocar o modelo sem medir.** Se surgir candidato, rodar
  `tools/bench_final.py`. A regra é medir, não votar.
- **Quebrar `reco.py` em módulos.** Tentador, mas o `reco.spec` depende da forma
  atual e isso não resolve nenhum problema desta lista.
- **Rodar transcrição na NPU por padrão.** É da webcam do Gabriel durante as
  chamadas, e ainda é 2,3× mais lenta no modo ao vivo (Dec1).

---

## Riscos e pré-requisitos

| risco | probabilidade | mitigação |
| --- | --- | --- |
| **Modo ao vivo degrada a gravação** (áudio perdido) | média (não descartada — só validada em curto) | Callback só enfileira (3.1, medido: contagem de amostras bate com a duração do MP3), fila com descarte (3.2), exceção isolada (3.3, testado). **Teste de estresse de 20 min (3.6) continua pendente do Gabriel** — validado só em gravações curtas (5-25s) e simulação de 90s até aqui. Se o teste de 20 min falhar, desligar `live` até investigar — o `_CFG_DEFAULTS["live"]=False` já protege quem nunca mexeu no checkbox |
| VAD segmenta mal em ambiente ruidoso | média | Limiar adaptativo ao próprio áudio + **fallback obrigatório** para janelas de 30 s (1.3) |
| `k_db` calibrado só em duas gravações | **alta** | Calibrar por script (2.3), valor comentado no código, reavaliar em ambiente diferente |
| Dominância corta fala legítima em *double-talk* | média | Histerese (2.2) e limiar conservador — na dúvida **manter** o segmento. Transcrever eco é menos grave que apagar fala real |
| Contexto propaga erro (um segmento ruim envenena os seguintes) | média | Zerar o contexto após janela descartada pelo anti-loop (E4). Limitar a ~30 palavras já contém o estrago |
| iGPU disputa com o vídeo da chamada | baixa | ~20% de ocupação (Dec1); device selecionável |

**Pré-requisitos:** nenhum externo. Tudo local; o modelo já está baixado em
`~/AppData/Local/Reco/models/`.

**Medição que só o Gabriel pode fazer:** o Windows não expõe contador de NPU
utilizável (verificado — não há counter set de NPU; `\GPU Engine(*)` não a separa
com confiança). Para confirmar o custo dos Studio Effects, rodar
`tools/bench_final.py` com `BENCH_DEVICES=NPU` **durante** uma chamada com câmera
e efeitos ligados, comparando com § 1.3 do roadmap irmão. **Não bloqueia nada** —
Dec1 já evita a NPU por dois outros motivos.

> Auditado em 2026-09-01 (overhaul): FECHADO — Fases 1–2 executadas 29/07, Fase 3 implementada; resta só o teste de estresse manual 3.6 (gabriel)
> Auditado em 2026-09-03 (revisão geral): corrigindo a marca acima — **VIVO, 1 gate humano**: o passo 3.6 (20 min reais com `live` ligado) é declarado **bloqueante** pelo próprio md, que diz que o modo ao vivo "não deve ser considerado confiavelmente pronto pra uso diário" sem ele; rodado só o proxy de 170 s. Fase 3 = 7/8. Dono do gate: gabriel.
