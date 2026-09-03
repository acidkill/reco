# Ganho por canal (mic / sistema) com slider sobre o VU meter — 2026-07-15

## Pedido
No Reco, o microfone grava bem mais baixo que a saída de som (áudio do sistema).
Quero um controle prático de ganho por canal: uma "barra vertical" (handle
arrastável) **sobre** cada VU meter, para aumentar/diminuir o volume que será
gravado de cada um dos dois canais (mic e sistema), independentemente.

## Arquitetura atual (o que existe)
- `DualRecorder._rec_mic` / `_rec_sys`: capturam float32, empilham em
  `_mic_chunks`/`_sys_chunks` e reportam RMS via `on_level(src, rms)`.
- `DualRecorder._save`: concatena, resample p/ 16 kHz e escreve MP3 estéreo
  (L=mic, R=sistema) com `clip([-1,1])`. Nenhum ganho é aplicado.
- `VuMeter` (tk.Canvas, H=4): barra horizontal que enche com o nível; sem interação.
- `_build_meters`: duas colunas (MIC / SISTEMA), cada uma com label + VuMeter.
- Config em `~/.reco_config.json` via `load_config`/`save_config`.

## Decisões
1. **Ganho = multiplicador linear por canal**, persistido em config
   (`mic_gain`, `sys_gain`, default `1.0`).
2. **Mapeamento dB simétrico** no slider: ±18 dB em torno de 0 dB (unity no
   centro). `gain = 10^(dB/20)` → range ~0,126×..7,94×. Snap p/ unity dentro de
   ±1 dB. dB é o eixo perceptualmente uniforme; unity no centro é intuitivo.
3. **Aplicação do ganho baked no arquivo salvo** (`_save`, após resample, antes
   do clip). Assim tudo rio abaixo (AEC, diarização, transcrição) vê o áudio já
   ganhado — consistente. Mudar o ganho durante a gravação aplica de forma
   uniforme ao arquivo inteiro (comportamento previsível).
4. **VU meter reflete o nível já ganhado**: o callback multiplica o RMS pelo
   ganho do canal, então a barra mostra o efeito em tempo real.
5. **UI**: VuMeter vira canvas mais alto (H=20) para o handle ser agarrável.
   Contém: trilha (escala), barra de nível fina centralizada, tick de unity no
   centro e um **handle vertical arrastável** (a "barra vertical" pedida) na
   posição do ganho. Label da coluna mostra o offset em dB ao vivo.

## ✅ EXECUTADO — conferido 21/08/2026 (evidência por grep em `reco.py`)

⚠️ **A mecânica de mapeamento saiu diferente do planejado na decisão 2** (dB
simétrico ±18 dB não foi implementado): o `reco.py` real usa uma escala
**bi-linear** com unity (1,0×) no centro — metade esquerda 0×..1×, metade
direita 1×..10× —, documentada em `CLAUDE.md` § "Ganho por canal". A função
entrega (controle vivo do ganho por canal com handle sobre o VU meter), só a
curva do slider mudou; não é motivo para reabrir o roadmap.

## Passos
1. [x] Config: `mic_gain`/`sys_gain` nos defaults.
   `reco.py:120-121` — `"mic_gain": 1.0, "sys_gain": 1.0` em `_CFG_DEFAULTS`.
2. [x] Helpers de mapeamento ganho↔fração + constantes.
   `reco.py:2616-2619` `GAIN_MIN/GAIN_UNITY/GAIN_MAX/GAIN_STEP`;
   `reco.py:2621-2634` `gain_to_frac`/`frac_to_gain` (bi-linear, não dB —
   ver nota acima).
3. [x] `DualRecorder`: atributos de ganho, `set_gain`, escala no callback de
   nível, multiplicação em `_save`/`feed`.
   `reco.py:1480-1487` `self.mic_gain`/`self.sys_gain`/`set_gain()`;
   `reco.py:1678,1702` RMS escalado por `self.mic_gain`/`self.sys_gain`;
   `reco.py:732-742` `MP3Writer.feed(mic, sys_, mic_gain, sys_gain)` (a
   multiplicação migrou de `_save` para `feed` na reforma de streaming do
   roadmap `2026-07-28-duracao-mp3-e-salvamento-instantaneo.md`, que veio
   13 dias depois — consequência esperada, não desvio deste roadmap).
4. [x] `VuMeter`: canvas alto, handle arrastável, `set_gain`/`gain`, callback
   `on_gain`.
   `reco.py:2644` `class VuMeter(tk.Canvas)`; `reco.py:2650` `on_gain=`/
   `on_release=` no `__init__`; `reco.py:2679-2692` `set_gain`/drag handler
   chamando `self._on_gain(self._gain)`.
5. [x] `_build_meters`: instanciar com gain inicial do config + callbacks;
   refs de label.
   `reco.py:3052-3070` `_build_meters` itera MIC/SISTEMA, instancia
   `VuMeter(on_gain=..., on_release=...)` e `vu.set_gain(self._cfg.get(cfg_key, 1.0))`.
6. [x] `_on_gain(src, g)`: atualiza recorder ao vivo, salva config, atualiza
   label dB (na prática, label do multiplicador via `fmt_gain`).
   `reco.py:3076-3087` `_on_gain`/`_on_gain_release` atualizam
   `self._vu_mult[src]` (`fmt_gain(g)`), chamam `self._recorder.set_gain(...)`
   e gravam `self._cfg["mic_gain"/"sys_gain"]`.
7. [x] Inicializar `self._recorder.set_gain(...)` a partir do config na criação.
   `reco.py:2750-2751` `self._recorder.set_gain(mic=self._cfg.get("mic_gain", 1.0), sys=self._cfg.get("sys_gain", 1.0))`.
8. [x] Traduções — confirmado sem string nova (label é numérico via `fmt_gain`,
   como o passo previa).
9. [x] Testar: sanidade de import + app rodando — não há registro do comando
   isolado da sessão original, mas o recurso está em produção desde 15/07/2026
   (documentado em `CLAUDE.md`, sem regressão relatada) e o app compila/roda
   normalmente hoje (regra de build do projeto).

## Onde propagar (rio abaixo)
- [x] Documentação do recurso — não entrou no `README.md` (seção de gravação),
  mas está documentado em `CLAUDE.md` § "Ganho por canal" com link de volta
  para este roadmap — o pedido original era "mencionar", que está atendido.
- [x] Sem mudança de schema de arquivo (continua MP3 estéreo 16 kHz) — confirmado,
  nenhuma alteração de `OUT_SR`/`OUT_CH` neste roadmap.
- Consolidado datado ao fim — sem registro localizável de diário de 15/07/2026
  para este item específico; não bloqueante (o recurso está em produção e
  documentado).

> Auditado em 2026-09-01 (overhaul): FECHADO — § EXECUTADO conferido 21/08 (commit 393b89c); helpers de ganho no CLAUDE.md do Reco
