# vigia-dispositivos

![testes](https://github.com/GustavoHenriqueRocha/vigia-dispositivos/actions/workflows/testes.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![dependências](https://img.shields.io/badge/depend%C3%AAncias-nenhuma-39d98a)

Avisa quando um dispositivo USB ou serial que **deveria estar ligado** some, volta ou fica **mudo** (a porta continua lá, mas os dados pararam). Guarda o histórico de quedas em SQLite, mostra o estado numa página web e notifica por log, webhook, Telegram ou qualquer comando.

Só Python e biblioteca padrão. Funciona em Linux (lê o `/sys` direto) e Windows (via `Get-PnpDevice` ou `wmic`).

```text
28/10/2026 03:12:55 balanca-expedicao: SUMIU (desconectado desde 28/10/2026 03:12:40)
28/10/2026 03:13:20 balanca-expedicao: VOLTOU após 40 s fora
28/10/2026 04:02:10 clp-linha-2: MUDO (conectado, mas sem dados há 2 min 00 s)
28/10/2026 04:05:31 clp-linha-2: VOLTOU a enviar dados após 5 min 21 s
```

## Para que serve

Na fábrica, muita coisa depende de um cabo USB: o conversor serial da balança, o leitor de código de barras, o CLP programado por USB, o adaptador RS-485 do coletor de dados. Quando um deles cai, o sintoma aparece longe dali (o relatório fica sem pesagem, a contagem zera) e ninguém sabe desde quando.

Três situações que um "o dispositivo aparece no sistema?" ingênuo não resolve:

- **Reconexão relâmpago.** Cabo com mau contato, hub que reinicia, driver que reenumera: o aparelho some por 2 segundos e volta. Isso não pode virar alarme a cada vez, mas também não pode passar despercebido. O vigia só declara queda depois de uma tolerância e conta as "piscadas" à parte.
- **Porta presente, mas muda.** O conversor USB-serial continua enumerado, só que a balança do outro lado desligou ou o cabo RS-232 soltou. Para o sistema operacional está tudo certo. A checagem de silêncio olha se os dados continuam chegando.
- **Dois aparelhos iguais.** Dois conversores com o mesmo VID:PID trocam de `/dev/ttyUSB0` para `/dev/ttyUSB1` a cada boot. O vigia reconhece cada um pelo número de série, pelo nome do produto ou pelo caminho estável em `/dev/serial/by-id`.

## Uso rápido

```bash
python -m vigia_dispositivos listar            # o que está conectado agora
python -m vigia_dispositivos listar --toml     # o mesmo, já em blocos [[dispositivo]] para a config
python -m vigia_dispositivos vigiar exemplos/vigia.toml --uma-vez   # confere uma vez (sai com 1 se faltar algo)
python -m vigia_dispositivos vigiar exemplos/vigia.toml             # vigia; página em http://127.0.0.1:8791
python -m vigia_dispositivos historico exemplos/vigia.toml          # últimas quedas
```

Também dá para instalar com `pip install .` e usar o comando `vigia-dispositivos`.

```text
ID         SERIAL               PRODUTO                          PORTAS
0403:6001  A10K3XYZ             FTDI / FT232R USB UART           /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K3XYZ-if00-port0
2341:8036  SN0042               Fabricante / PLC USB             /dev/ttyACM0
```

## Configuração

```toml
[geral]
intervalo = 5               # segundos entre rodadas
tolerancia_queda = 15       # ausente só depois de 15 s seguidos
tolerancia_volta = 3        # presente de novo só depois de 3 s seguidos
banco = "vigia.db"
status_porta = 8791         # GET / e GET /status

[[dispositivo]]
nome = "balanca-expedicao"
id = "0403:6001"            # VID:PID
serial = "A10K3XYZ"         # separa dois conversores iguais

[[dispositivo]]
nome = "clp-linha-2"
produto = "PLC"             # trecho do nome do produto
tolerancia_queda = 30       # cada dispositivo pode ter a própria tolerância
silencio = { tipo = "arquivo", caminho = "/var/log/coletor/linha-2.log", max = 120 }

[[dispositivo]]
nome = "leitor-codigo"
porta = "/dev/serial/by-id/usb-*Scanner*"   # glob sobre as portas (no Windows: "COM7")

[[dispositivo]]
nome = "conversor-serial-ethernet"          # sem USB: só rede
silencio = { tipo = "tcp", host = "10.0.0.20", porta = 4001, max = 60 }
```

Um dispositivo casa quando **todos** os critérios dados batem (`id`/`vid`/`pid`, `serial`, `produto`, `porta`). O exemplo completo, com os quatro notificadores, está em [`exemplos/vigia.toml`](exemplos/vigia.toml).

### Checagem de silêncio

| tipo      | o que olha                                                     | quando usar |
|-----------|----------------------------------------------------------------|-------------|
| `arquivo` | o arquivo cresceu desde a última rodada?                       | outro programa já lê a porta e grava log ou captura. Não disputa a porta. |
| `tcp`     | conecta, espera `janela` s por algum byte e desconecta         | conversor serial/Ethernet ou balança que transmite sozinha. `enviar_hex` manda uma consulta antes. |
| `serial`  | abre a porta só leitura e espera `janela` s por algum byte (Linux) | porta ociosa ou espelhada. Sem `porta`, usa a do próprio dispositivo. |

Sem nada chegando por `max` segundos, o dispositivo fica **mudo**. Ao reconectar, o prazo recomeça. Num dispositivo só de rede (sem critério USB), conexão recusada conta como **ausente**.

Cuidado com `tcp` e `serial`: muitos conversores aceitam um cliente só, e ler uma porta serial tira os bytes de quem mais estiver lendo. Na dúvida, use `arquivo`.

### Notificações

```toml
[[notificar]]
tipo = "log"                        # uma linha por evento, com rotação
arquivo = "vigia-eventos.log"

[[notificar]]
tipo = "webhook"                    # POST com o evento em JSON
url = "https://exemplo.invalid/eventos"
cabecalhos_env = { Authorization = "VIGIA_WEBHOOK_AUTH" }

[[notificar]]
tipo = "telegram"                   # token e chat vêm do ambiente
token_env = "VIGIA_TELEGRAM_TOKEN"
chat_env = "VIGIA_TELEGRAM_CHAT"

[[notificar]]
tipo = "comando"                    # {dispositivo} {tipo} {situacao} {mensagem} {em} e variáveis VIGIA_*
comando = ["notify-send", "Vigia", "{mensagem}"]
```

Segredos nunca ficam no TOML: ele só diz o nome da variável de ambiente. Sem as variáveis, o Telegram fica desligado com um aviso no log em vez de derrubar o programa. Um notificador que falha (webhook fora do ar, por exemplo) é registrado e não impede os outros.

Corpo do webhook:

```json
{"dispositivo": "balanca-expedicao", "tipo": "voltou", "situacao": "presente", "anterior": "ausente",
 "em": "2026-10-28T03:13:20-03:00", "desde": "2026-10-28T03:13:20-03:00", "duracao": 40.0,
 "mensagem": "balanca-expedicao: VOLTOU após 40 s fora"}
```

## Como decide

```text
PRESENTE ──── fora por `tolerancia_queda` s ──────────────▶ AUSENTE
PRESENTE ──── conectado, sem dados por `max` s ───────────▶ MUDO
AUSENTE  ──── de volta por `tolerancia_volta` s ──────────▶ PRESENTE (ou MUDO, se segue calado)
MUDO     ──── chegou dado ────────────────────────────────▶ PRESENTE
```

- **Esperado começa como presente.** Se ele não está lá quando o vigia sobe, o aviso de "sumiu" sai depois da tolerância, com o horário de início real.
- **O horário é o do fato, não o do alarme.** "Sumiu" registra quando o aparelho desapareceu, e "voltou" traz quanto tempo ele ficou fora.
- **Falha ao listar não vira queda.** Se o `/sys` ou o PowerShell falharem numa rodada, ela é pulada e o erro aparece no `/status`; ninguém é marcado como ausente por isso.
- **Mudo que vira ausente é uma queda só** no histórico, com motivo `mudo+ausente`.

A lógica fica numa máquina de estados pura ([`estado.py`](vigia_dispositivos/estado.py)), sem USB, rede nem relógio de verdade, e a leitura do sistema fica isolada em [`plataforma.py`](vigia_dispositivos/plataforma.py). Os dois são testados sem hardware.

## Página e /status

`GET /` mostra cada dispositivo com a situação, há quanto tempo, as portas e as últimas quedas (atualiza a cada 10 s). `GET /status` devolve o mesmo em JSON, com `"ok": true` quando todos estão presentes, o que serve para plugar em qualquer monitoramento.

Por padrão o servidor escuta só em `127.0.0.1`. Para abrir na rede, defina `status_host = "0.0.0.0"` em `[geral]`.

## Rodar como serviço

- **Linux:** uma unit do systemd apontando para `python -m vigia_dispositivos vigiar /etc/vigia/vigia.toml`, com as variáveis do Telegram em `EnvironmentFile=`. O usuário precisa estar no grupo `dialout` se usar a checagem `serial`.
- **Windows:** uma tarefa agendada "ao iniciar" com o mesmo comando. As variáveis vão nas variáveis de ambiente do usuário da tarefa.

## Testes

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
```

- **Máquina de estados:** reconexão rápida que não alarma (e conta piscada), queda confirmada exatamente na tolerância, volta com duração, volta relâmpago ignorada, ausente desde o início, mudo e volta a falar, prazo de silêncio que recomeça ao reconectar, mudo que vira ausente.
- **Linux:** árvore `/sys` e `/dev` falsa num diretório temporário, com hub, interface, conversor FTDI com `by-id`, CDC ACM sem `by-id` e aparelho sem serial.
- **Windows:** saídas gravadas do `Get-PnpDevice` e do `wmic`, com nó FTDIBUS, interface `&MI_00`, serial inventado pelo Windows, hubs e objeto JSON solto; troca para `wmic` quando o PowerShell falha.
- **Silêncio:** servidor TCP que fala e que fica calado, conexão recusada, arquivo que cresce, porta serial simulada com pty.
- **Notificações:** webhook e Telegram contra um servidor HTTP local de verdade, erro HTTP, segredo ausente, rotação do log e comando externo com variáveis.
- **Ponta a ponta:** queda e volta indo para o SQLite e para os avisos, falha de listagem, `/status` e página por HTTP, CLI `listar`, `historico` e `vigiar --uma-vez`, e o exemplo da configuração.

## Licença

MIT
