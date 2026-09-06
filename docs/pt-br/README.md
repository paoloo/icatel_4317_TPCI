# ICATEL / TPCI — Engenharia Reversa do Firmware, Reimplementação em Python e Simulador

> Tradução/versão em português do [`README.md`](../../README.md) (inglês, a
> versão canônica). Em caso de divergência, o README em inglês prevalece.

Engenharia reversa de `Icatel-43.17.bin` — o firmware 8051 de um telefone
público a cartão de 2004 (TPCI, *Telefone Público a Cartão Indutivo* pela
norma TELEBRÁS) fabricado pela ICATEL — mais uma reimplementação fiel em
Python, um motor didático simplificado e um simulador de mesa em tkinter
com a cara do orelhão de verdade.

Toda a análise foi feita com **radare2** (`r2 -a 8051`); o disassembly
completo está salvo em
[Icatel-43.17.r2.asm](Icatel-43.17.r2.asm).

---

## Arquivos

| Arquivo | O que é |
|---|---|
| `Icatel-43.17.bin` | Dump original do firmware, 64 KB (fonte da verdade) |
| `icatel_4317_reimplementation.py` | Reimplementação fiel e comentada do firmware, em Python |
| `simple_payphone.py` | Motor didático simplificado (mesmo comportamento observável) |
| `icatel_4317_simulator.py` | Simulador de telefone em Tkinter+ttk (interface gráfica) |
| `Icatel-43.17.r2.asm` | Disassembly linear completo do radare2 (46.020 linhas) |
| `Icatel-43.17.r2.functions.txt` | Lista de funções do radare2 (`afl`) |
| `icatel_4317_strings.txt` | Tabela de strings do visor (LCD) em português, com endereços verificados |

Os comentários e docstrings dentro dos arquivos `.py` permanecem em inglês
(citam o disassembly 8051 original lado a lado com a explicação); esta
documentação em português cobre a visão geral do projeto.

---

## Terminologia oficial (normas TELEBRÁS)

Os nomes usados neste projeto seguem, sempre que possível, as normas de
engenharia TELEBRÁS de suporte a telefone público a cartão indutivo:

| Sigla | Significado oficial | Norma |
|---|---|---|
| **TPCI** | Telefone Público a Cartão Indutivo | 245-300-707 |
| **CSA** | Centro de Supervisão Automatizada (o backend remoto de tarifação/falhas) | 245-300-701 / 245-300-709 |
| **UT** | Unidade de Tarifação — o valor, em moeda nacional, de cada pulso de cobrança enviado pelo equipamento de tarifação; equivale a um crédito | 245-300-707 |
| **Junto de Entrada** | Equipamento do lado da central (ou o próprio terminal) que emite o pulso de tarifação para o TPCI | 245-300-707 |
| **Método de Cobrança** | O aparelho debita *primeiro* uma UT do cartão, para só então liberar o tempo de conversação correspondente | 245-300-707 |
| **CAT** | Comando de Atualização da Tabela de tarifação (protocolo CSA↔TPCI) | 245-300-709 |

Nesta documentação, "UT" é reservado para o crédito tarifário do cartão
(`credit_units` / `eng.units`), para não confundir com as "unidades de
espera" internas do firmware — os ciclos do laço de temporização de
`0x7B6A` (ver `Timers.wait_units`), que não têm relação com tarifação.

### Cruzamento com o hardware: a placa UCI/100

A norma TELEBRÁS 560-400-301 (procedimentos de instalação) define **UCI/100**
como "a placa de controle do TP a cartão indutivo." É quase certamente a
placa que carrega o 8051 que este projeto reimplementa. O Anexo II dessa
norma é um desenho de posicionamento dos conectores e jumpers de
configuração dessa placa, redesenhado aqui em ASCII (posições dos
componentes aproximadas; os valores são literais):

```
+----------------------------------------------------------+
|[9]                                                        |
|[8]                                        o           o   |
|[7]                                                        |
|[6]                          +------------------------+    |
|[5]  <- CN4 (linhas A/B)     |  ESTRAPES DE POTENCIA   |    |
|[4]     bloco de terminais   |   DO MODEM (ST4)   ST1  |    |
|[3]                          |   o--o--o--o            |    |
|[2]                          |   1  2  3  4            |    |
|[1]                          +------------------------+    |
|                                                            |
|                 [ST3]                                     |
|                                                            |
|                                [ST2-A]     [ST2-B]         |
|                                                            |
|                        (CH1) <- botão interno               |
|                                                            |
|   o                                              o         |
|[ST5]                                                       |
+------------------------------------------+                |
                                            | (recorte        |
                                            |  mecânico)      |
                                            +-----------------+
```

* **CN4** — bloco de terminais das linhas telefônicas A/B (9 pinos).
* **ST4** (jumper, posições 1-4) — potência de transmissão do modem: ST4-A
  −9 dBm, ST4-B −12 dBm, ST4-C −15 dBm, ST4-D −18 dBm, ST4-E −21 dBm.
* **ST3** — modo de discagem: jumper presente = DTMF, retirado = decádica
  (pulso).
* **ST1** + **ST2-A**/**ST2-B** — seleção da sinalização de cobrança: tom de
  12 kHz vs. inversão de polaridade da linha.
* **CH1** — botão interno de configuração; pressioná-lo ao retirar o
  monofone do gancho abre o menu Contador (UT) / Teste / Instalação descrito
  no fluxograma anexo da norma.

Nada disso vem da imagem do firmware em si: é um cruzamento com a norma,
não uma marca `[INFERENCE]`.

### Cruzamento com uma fonte independente

Eu mesmo fiz, há uns 22 anos, uma pesquisa de hardware/firmware num aparelho
ICATEL relacionado, mas diferente (modelo 5000c/1), com acesso físico a um
telefone e a um dump de firmware que eu havia rotulado como versão
"46.17", não o `43.17` deste projeto. Várias dessas anotações antigas
batem, byte a byte, com `icatel_4317_strings.txt`, o que confirma algo que
eu já suspeitava na época: os aparelhos ICATEL são mesmo "95%+ similares"
entre modelos e revisões de firmware.

A ordem do autoteste de boot é uma das coincidências. Eu tinha anotado
"eeprom, ram, teclado, display, matriz (não identificada), tabela E2P,
leitora de cartões, modem", cada etapa retornando uma mensagem de
OK/falha. O `icatel_4317_strings.txt` tem a mesma sequência, na mesma
ordem, com os mesmos pares de string (`TESTANDO EEPROM`/`EEPROM
OK`/`FALHA EEPROM` … `TESTANDO MATRIZ`/`MATRIZ OK`/`FALHA NA MATRIZ` …
`TESTANDO E2P TAB` … `TESTANDO LEITORA` … `TESTANDO MODEM`). Nunca
descobri o que "matriz" testa, e esta imagem também não resolve isso;
meu melhor palpite é a matriz de varredura linha/coluna do teclado,
separada da checagem de tecla pressionada `TECLADO`/`TECLE`, mas isso é
uma `[INFERENCE]`, não algo verificado byte a byte.

As strings do menu técnico também batem. `ID TECNICO`, `TAB.TARIFACAO`,
`F.TARIFACAO`/`AUTOTARIFADO`, `NUMERO SERIE`, `TERMINAL SSTP`,
`TERMINAL TPCI`, `ATIVACAO`/`DESATIVACAO` correspondem a strings reais
desta imagem (`IDENT.TÉCNICO`, `TÉCNICO INVÁLIDO`, `TAB.TARIFAÇÃO`,
`F.TARIFAÇÃO`, `AUTOTARIFADO`, `NUMERO SÉRIE`, `TERMINAL SSTP`,
`TERMINAL TPCI`, `DESATIVAÇÃO OK`, `INSTALAÇÃO OK`). O aparelho também
chama o CSA de "SSTP" internamente, em toda string do LCD (`SSTP`,
`SSTP OCUPADO`), nunca "CSA". Vale saber: este repositório e as normas
TELEBRÁS usam "CSA" o tempo todo.

Os nomes dos modos de tarifação do antigo esquema de número de série
(`DECADICA`, `DTMF`, `INVERSÃO`, `12 KHz`, `AUTO-DDD`, `AUTOTARIFADO`)
também são todos strings reais aqui, mais um modo `16 KHz` que eu não
conhecia, o que é uma revisão posterior, não uma contradição.
`PORTA ABERTA` também é uma string real, condizendo com o que eu já havia
anotado sobre a mensagem de porta aberta, e confirma que não existe
temporizador de alarme de 3 minutos, um mito que eu já havia desmentido
na época.

Uma lacuna real apareceu frente à norma oficial: a TELEBRÁS 245-300-707
§8.21(i) exige que o LCD mostre `FORA DE SERVIÇO` no estado fora de
operação, mas este firmware mostra `FORA DE OPERAÇÃO`. Inofensivo, mas um
desvio real da letra da norma.

A única divergência clara é o display. Eu tinha registrado um LCD 2×16
(Solomon, sem luz de fundo) no 5000c/1, enquanto o endereçamento DDRAM
deste projeto, verificado byte a byte (`0x80`/`0xC0`, passo de linha de
40 colunas), torna o display da unidade 43.17 um 2×40. Os dois aparelhos
simplesmente divergem aí.

Minhas anotações antigas sobre números de peça específicos (EPROM, SRAM,
modem, RTC), frequências de cristal e fiação de switches na placa
descrevem a placa física do 5000c/1, não a do 43.17. Um dump de firmware
não confirma nem nega hardware desse tipo, por isso nada disso é
reproduzido aqui como fato.

---

## O firmware em uma página

* **CPU**: 8051, 11,0592 MHz (inferido; cristal padrão para UART), 64 KB de
  espaço de memória externa.
* **Vetores**: RST→`0x0033`, INT0→`0x41F7` (teclado), Timer0→`0x1B9A`
  (tick de ~1,034 ms, recarga `0xFC47`), Timer1→`0x2EF6` (fases de pulso de
  tarifação), UART→`0x4264` (protocolo de supervisão/tarifação, CRC-16 com
  realimentação `0x0240`).
* **Loop principal** (`0x026B`): `call_state` (XDATA `0x076E`) × 3 indexa uma
  tabela de `ljmp` → tratadores de ocioso / chamada ativa / encerramento /
  desligamento.
* **Persistência**: três blocos XDATA com checksum aditivo (`0x0000`/25 B,
  `0x0019`/95 B, `0x0078`/24 B) espelhados numa EEPROM I²C classe 24C64
  (SCL=P3.3, SDA=P1.7, dispositivo `0xA0`/`0xA1`, endereçamento de 2 bytes,
  páginas de 64 bytes). As escritas passam por `0x7E18/0x7E1E`, que verificam
  o trailer e regravam a sombra na EEPROM quando há divergência.
* **LCD**: 2×40, classe HD44780. Bit-bang em tempo de reset em P1.4–P1.7,
  janela mapeada em memória em tempo de execução `0x8000` (instrução)
  `0x8001` (dado) `0x8002` (busy) `0x8003` (leitura).
* **Tarifação**: T1 como contador de pulso externo (TMOD=`0x51`), divisão por
  6 (`0x0771`) antes de incrementar os contadores de UT de 16 bits
  (`0x009E/0x009F`); o saldo de crédito do cartão é consultado por `0x3B20`;
  cartão zerado → fase 7 → `FAVOR DESLIGAR` → desligamento.
* **Easter egg da build**: `0x0DC9` (`PUSH ACC; MOV A,#1; RRC A; POP; RET`)
  sempre retorna carry=1 — todo desvio condicionado por essa função, depois
  dela, é código morto nesta build.
* **Peculiaridade**: a caixa de comando XDATA `0x0708` é espelhada na
  **porta P1** a cada escrita (`0x415D`), enquanto P1.7 também é o pino SDA
  do I²C — a placa faz esse multiplexamento em hardware.

---

## `icatel_4317_reimplementation.py` — a reimplementação fiel

Um modelo executável em Python do firmware. Cada rotina traz o disassembly
8051 real como comentário, seguido da explicação do que ele faz. Exemplo:

```python
def tick_call_clock(self) -> None:
    """0x4DE2: advance 0x087F (sec) with mod-60 carry into 0x087E (min) ..."""
```

Estrutura:

* `Memory` — modelo de XDATA + E/S mapeada em memória (janela do LCD,
  latches, PCON idle).
* `Timers` / `timer0_isr` — o tick de 953 ciclos e o contrato do flag `0x26.3`.
* `I2cEeprom` — driver 24Cxx bit-banged (`0x8050` leitura de byte, `0x843E`
  leitura de bloco, `0x85F4` escrita de página com rajada de ack-poll de tWr).
* `Datastore` — o sistema de variáveis protegidas (`0x7D56` seleção de bloco,
  `0x7D9D` checksum aditivo, `0x7DAF` verificação, `0x7DC3` regravação na
  EEPROM).
* `Mailbox` — o byte de comando `0x0708` + espelho na porta P1 + o
  misturador LFSR de `0x40EA`.
* `Lcd` — as duas interfaces do LCD, modelo de cursor DDRAM, conjunto de
  caracteres acentuados customizado.
* `Serial` — o protocolo de quadros de `0x4264` (9 códigos de início, CRC-16,
  controle de fluxo via REN).
* `Payphone` — a máquina de estados: `reset()`, `dispatch()`, tratadores de
  estado, `set_tone_profile()` (`0x0D87`), `seize_line_and_dial()` (`0x0617`),
  `tick_call_clock()` (`0x4DE2` + tarifação), `hang_up_release_line()`
  (`0x073C`), armar/verificar o download CMT (`0x103E`/`0x1064`).

Execute standalone para um teste de fumaça:

```bash
python3 icatel_4317_reimplementation.py
```

### Modelo de tarifação (o que o simulador usa)

| Conceito | Firmware | Python |
|---|---|---|
| Saldo de crédito do cartão | consultado por `0x3B20` | `eng.credit_units` |
| Contador de UT consumidas | `0x009E/0x009F` | `eng.units` |
| Passo de tarifação | divisão por 6 (`0x0771`) na ISR de T1 | 1 UT a cada 6 s de chamada conectada |
| Cartão esgotado | fase 7 → `0x073C` | `_burn_unit()` força o desligamento |

---

## `simple_payphone.py` — o motor didático

Mesmo comportamento observável, uma única máquina de estados legível:

```
OCIOSO ──tira do gancho──> TOM DE DISCAR ──4+ dígitos──> CONECTADO ──crédito==0 / no gancho──> OCIOSO
```

* Sem EEPROM, sem CRC, sem memória bancada — só a máquina de estados, o
  buffer de dígitos discados (máx. 11, `*` limpa), o relógio por segundo
  (mod-60) e a tarifa de 1 UT a cada 6 segundos.
* Os códigos de fase espelham o firmware (`2` aguardando, `3` conectado,
  `4` discando, `7` pedido de desligamento, `8` fora de operação).
* Autoteste: `python3 simple_payphone.py` roda um script de asserções sem
  interface gráfica (tira do gancho → disca → conecta → chamada paga se
  sustenta → desliga → queda por falta de crédito).

---

## `icatel_4317_simulator.py` — o simulador

Aplicativo de mesa em Tkinter + ttk, com o visual da carcaça
Tropical/orelhão: corpo azul-aço, faixa amarela, LCD verde 2×40, teclado
metálico 4×3 com `*`/`#` em laranja, um cartão indutivo ICATEL clicável
deslizando na ranhura, um monofone clicável e botões de gancho/crédito.

### Uso

```bash
# padrão: motor fiel (icatel_4317_reimplementation)
python3 icatel_4317_simulator.py

# motor didático
python3 icatel_4317_simulator.py --engine simple
```

### Controles

| Controle | Efeito |
|---|---|
| Clicar no monofone / "Tirar do gancho" | Tira do gancho (inicia o fluxo) |
| Clicar no cartão (ou na área da ranhura) | Insere / remove o cartão indutivo |
| Teclado `0-9` | Disca (buffer em XDATA `0x0783`, máx. 11 dígitos) |
| Teclado `*` | Limpa o buffer discado (firmware `0x3ED4`) |
| "+1 crédito" | Adiciona uma UT pré-paga ao cartão (pulso de tarifação em T1) |
| "Colocar no gancho" / Esc | Coloca no gancho (encerra a chamada, desligamento `0x073C`) |

A barra de status mostra o estado do motor em tempo real:
`GANCHO | CARTÃO | CRÉDITO: n unid | GASTO: n | fase | estado`
(fase/estado espelham o firmware `0x0013`/`0x076E`).

### Uma chamada completa

1. Insira o cartão (clique nele). Tire do gancho.
   * **Sem crédito** → o LCD mostra `CARTAO SEM CREDITO` / `FORA DE OPERACAO`;
     a linha nunca é ocupada (o firmware verifica o saldo do cartão antes de
     discar).
2. Pressione **+1 crédito** algumas vezes → estado de tom de discar.
3. Disque ≥ 4 dígitos (`*` corrige) → trem de pulsos de ocupação de linha no
   latch `0x8060` (4000/1300/500 unidades, exatamente como em `0x0617`) →
   `EM CHAMADA MM:SS  UNID:nn` com o número discado na linha 2.
4. Converse: 1 UT é consumida a cada 6 segundos. O contador de UT restantes
   é atualizado ao vivo (como a atualização do dígito na unidade real,
   `0x768D`).
5. Crédito próximo de zero → `FAVOR DESLIGAR` na linha 2; em zero a chamada
   cai e o telefone volta ao estado ocioso. Colocar no gancho funciona a
   qualquer momento.

Dependências: Python ≥ 3.9 com tkinter (padrão no macOS/Windows; no Linux
`sudo apt install python3-tk`). Sem pacotes de terceiros.

---

## Como a engenharia reversa foi feita (método)

1. **Estrutura** — auto-análise `r2 -a 8051`, decodificação da tabela de
   vetores, checagem byte a byte de cada instrução citada.
2. **Prospecção em paralelo** — quatro agentes somente-leitura mapearam
   regiões disjuntas (loop principal / serial+download / display /
   EEPROM+checksums), cada um retornando fatos verificados byte a byte com
   marcadores `[INFERENCE]`.
3. **Cruzamento de referências** — o dump irmão `Tp2k_dump.src` (listagem
   DIS8051 de uma build *diferente*, com 37.693 bytes de diferença) foi usado
   apenas para corroborar a forma do código.
4. **Reescrita** — cada trecho de asm citado no Python foi conferido byte a
   byte contra a imagem (`75 8C FC/75 8A 47` @`0x1B9A`, `SETB 0x26.3`
   @`0x1ECD`, tabela de ljmp @`0x0277`, strings em `0x8AC4`+ …).

## Limitações conhecidas

* `simple_payphone.py` deliberadamente descarta as camadas de
  EEPROM/protocolo/memória bancada.
* O motor fiel modela a máquina de estados, o datastore, os contratos de
  I²C e LCD, mas não a temporização exata em ciclos nem a interface de
  linha analógica.
* O cristal (11,0592 MHz) e o baud nominal (9600, de TH1=`0xFA`) são
  `[INFERENCE]` — o único valor de recarga de taxa padrão presente na
  imagem.

---

## Licença

O código Python deste repositório (`icatel_4317_reimplementation.py`,
`simple_payphone.py`, `icatel_4317_simulator.py`) é distribuído sob a
[Licença MIT](../../LICENSE). Isso **não** cobre o `Icatel-43.17.bin` em si
(o dump original do firmware, citado aqui apenas para fins de análise) nem
os arquivos de disassembly/strings derivados byte a byte dele — esses
permanecem propriedade da ICATEL / do fabricante original.
