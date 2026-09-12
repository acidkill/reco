# Ferramentas de apoio (`tools/`)

Para que serve cada script de `tools/`, com o gate e o número medido de cada um.
Texto movido do `CLAUDE.md` em 12/09/2026 (eixo H.6 do overhaul do harness) e,
no mesmo dia, tirado do `README.md` para cá — o `README.md` é a vitrine do repo
público, e esta tabela é doc interna (caminho de máquina, número medido aqui,
roadmap de repo privado). Nada foi reescrito.

- Como cada peça funciona por dentro: [ARQUITETURA.md](ARQUITETURA.md)
- O que **parece** funcionar e não funciona: [ARMADILHAS.md](ARMADILHAS.md)

---

Rodam pelo fonte, com o venv do projeto — não entram no executável. A "regra do
MP3 por container" citada na tabela está em
[ARQUITETURA.md](ARQUITETURA.md#mp3-por-container-o-bug-e-o-que-não-reintroduzir).

| script | para quê |
| --- | --- |
| `test_encoder.py` | testa o encoder sem hardware: duração declarada == real, header Xing, L/R separados, pareamento dos canais e `close()` instantâneo. Rodar **sempre** que mexer em `MP3Writer`/`_pump` |
| `test_gravacao_real.py [seg]` | grava de verdade pelos dispositivos padrão, mede o tempo do `stop()` e apaga o MP3 no fim |
| `reparar_duracao.py <pasta> [--aplicar]` | conserta a duração de MP3 antigos por remux (ver a regra acima) |
| `test_antiloop.py <mp3> [modelo] [device]` | roda as janelas mais fracas com e sem a defesa anti-loop. Rodar **sempre** que mexer em `_degenerado`/`_generate_sem_loop`. Critério: nenhum n-grama > 3× |
| `test_e2e.py <mp3>` | transcrição ponta a ponta pelo caminho real do app (decode + diarização + AEC + anti-loop), com tempo e extrapolação para 2 h |
| `test_alinhamento.py` | alinhamento dos canais **sem hardware**: offset positivo/negativo, retenção inicial, correção de deriva, fone (sem eco → não alinha), canal único. Rodar **sempre** que mexer em `estimar_offset`/`_al_*`/`_pump` |
| `test_gravacao_alinhada.py [seg]` | prova ponta a ponta do alinhamento: grava de verdade **tocando áudio pelos alto-falantes** (sem isso não há eco para correlacionar) e mede o atraso residual por janela. Gate: pior janela < 10 ms. Medido em 19/08: **−0,3 ms** (era +203 ms) |
| `alinhar_gravacao.py <mp3\|pasta> [--aplicar]` | conserta gravações **antigas**, escrevendo `<nome>_alinhado.mp3` ao lado (nunca sobrescreve — o áudio é re-encodado). Reestima o deslocamento a cada 30 s, então corrige a deriva dentro do arquivo. Streaming, um passe, sem `seek` |
| `varrer_acervo.py <pasta\|mp3> [--janela 15]` | **diagnóstico** do desalinhamento no acervo, somente-leitura: por janela, dá **pior janela**, **amplitude da faixa** (= salto) e **% do tempo acima de 50 ms** — não mediana, que engana neste defeito. Use este para "quantas gravações têm salto"; o `alinhar_gravacao.py` é para consertar uma. Medido em 30/08: **37% das gravações medíveis têm salto** |
| `varrer_aec.py <mp3...> [--acervo N]` | distribuição de **(acoplamento, ERLE, dano na voz)** em várias gravações, com a rotulagem alinhada do `medir_aec.py`. Foi ele que mostrou que **o acoplamento não prediz se o AEC ajuda** (ver `docs/ARMADILHAS.md`) |
| `test_relogio_captura.py [seg]` | deriva de cada canal contra o relógio de parede, sem escrever MP3 — abre os dois recorders como o `DualRecorder` e conta frames. **Mede** se o loopback estava mudo (RMS) em vez de confiar na lembrança, e compara a deriva com o que o alinhador já corrige (50 ms/min). Medido em 30/08 com a caixa muda: **+4,1 ms/min relativo** |
| `medir_aec.py <mp3> [janelas] [dur]` | par **(ERLE, dano na voz)** com rotulagem alinhada — é o gate de regressão do AEC. Use este, não o `medir_eco.py`, para julgar cancelamento de eco |
| `medir_eco.py <mp3>` | acoplamento caixa→mic e ERLE do `cancel_echo` **em áudio real**. Rodar **sempre** que mexer no AEC — validar em eco sintético já mascarou uma implementação que entregava 3 dB. ⚠️ **A métrica é enviesada** (19/08/2026): mede acoplamento só em blocos com o mic quase mudo, então subestima o eco por construção, e não mede dano na voz. Ver `docs/ARMADILHAS.md` e a Fase 3 do roadmap de 19/08; até lá, o número que ela imprime é piso, não valor |
| `bench_final.py <mp3> [n]` | device × modelo: velocidade, extrapolação p/ 2 h, e qualidade por divergência (WER) contra o melhor modelo disponível. `BENCH_MODELOS`/`BENCH_DEVICES`/`BENCH_MODO=fracas` filtram |
| `bench_convivencia.py <mp3> [n]` + `vizinho.py` | quanto a transcrição atrasa **outro app** (latência de um vizinho single-thread em processo separado). É o que decide iGPU × NPU |
| `bench_convivencia_pipeline.py <mp3>` | igual acima, mas com o pipeline REAL (`OVTranscriber.transcribe`, VAD+contexto+dominância) em vez de `pipe.generate()` cru — o que decide o orçamento de device do modo ao vivo |
| `calibrar_dominancia.py <mp3...>` | calibra `k_db` de `dominancia_sistema` contra `so_sys`/`so_mic`/`ambos` (double-talk) — rodar de novo com mais gravações se mexer no limiar; **sempre conferir por diff de transcrição real depois**, métrica de bloco sozinha já mascarou perda de fala real |
| `test_live.py <mp3> [seg]` | alimenta `LiveTranscriber` com um MP3 real em tempo real simulado (resample 16k→48k→16k), mede latência mediana do rascunho |
| `test_live_integration.py [seg]` | grava de verdade com `DualRecorder`+`LiveTranscriber` ligados, confere duração do MP3, tempo de drain e a passada final rodando sem conflito depois |
| `bench_latencia_stt.py [--modelo X] [--device AUTO\|CPU]` | latência de **frase curta** (5/10/20 s, `temp/bench-voz/clip*.wav`) pelo pipeline real, com aquecimento separado — o RTF de lote não responde "quanto demora uma frase". Criado em 28/08/2026 para decidir STT local × nuvem no jarvis da central (`central/roadmap/2026-08-28-jarvis-voz-e-notebook.md`, Fase 0). Saída em `temp/bench-voz/bench-local-<device>.json` |
| `transcrever.py <arquivo...>` | transcreve **qualquer áudio/vídeo** para `<arquivo>.txt` pelo pipeline real (decode PyAV → VAD → anti-loop), sem UI — feito para **agentes** (Claude Code) lerem áudio que o Gabriel manda no chat. Pula `.txt` existente (`--forcar` refaz); `--diarizar`/`--aec` só para gravações estéreo do próprio Reco |
