# EZmart Home Assistant Apps

<img src="ezmart_deco_optimizer/icon.png" alt="The Modern House" width="128">

**0.11.0-dev.1 — experimental.** Monitoramento e otimização sob demanda pelo Home Assistant, com transportes separados para **M9 Plus V2 em AP** e **X50 1.30 / 1.8.4 em Router**. Controle de visitantes validado no M9; a 0.11 prepara o piloto X50, ainda pendente de validação física.

Cria um dispositivo MQTT com 22 entidades: botões para otimizar se necessário ou verificar a última aplicação, sensores de resultado, canais, horários, disponibilidade e bloqueios. O app só analisa/aplica após um pedido explícito, inclusive de uma automação escolhida por você. Aplica somente quando o Deco recomenda; não existe otimização periódica interna.

O piloto opcional de visitantes acrescenta `switch.ezmart_deco_visitantes` ao mesmo dispositivo, preservando as entidades existentes. [Configuração e limites do controle M9](ezmart_deco_optimizer/DOCS.md#wi-fi-de-visitantes-no-m9--piloto-010). A 0.11 acrescenta o mesmo interruptor no X50 1.30 / 1.8.4 Router, mediante habilitação explícita; validação física pendente.

## Instalar pela loja do Home Assistant

[Adicionar este repositório ao Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fthemodernhousebr%2Ftmh-ezmart-deco-optimizer)

Ou abra **Configurações → Apps → Instalar app → menu ⋮ → Repositórios** e adicione:

```text
https://github.com/themodernhousebr/tmh-ezmart-deco-optimizer
```

Selecione **EZmart Deco Optimizer** na seção **EZmart Home Assistant Apps**. É um repositório personalizado da loja, não uma entrada automaticamente aprovada no catálogo oficial nem uma integração HACS. Requer Home Assistant com Supervisor, arquitetura amd64 ou aarch64 e acesso local ao Deco principal. Para as entidades, configure também Mosquitto e a integração MQTT.

Nesta distribuição, o Supervisor constrói a imagem a partir do código. Não há imagem GHCR pré-compilada; a instalação/atualização pode levar alguns minutos. Após instalar, mudar automações ou acionar botões não exige rebuild. As ações TMP e os comandos MQTT começam desabilitados; siga a [configuração](ezmart_deco_optimizer/AUTOMATION.md).

### Quem já usa a instalação local do piloto

O app instalado pelo GitHub terá um identificador diferente de `local_ezmart_deco_optimizer` e **não herdará automaticamente seus dados**. Não desinstale a versão local nem execute as duas juntas. A automação revisada funciona na 0.8.0-dev.1 existente: não é necessário migrar para testá-la. Uma migração futura deve preservar opções, identidade MQTT, histórico de ações e intervalos persistidos com backup e ambos os apps parados; a publicação não faz essa migração automaticamente.

## Compatibilidade e evidências

| Equipamento principal | Situação |
|---|---|
| M9 Plus V2, AP, firmware 1.9.1 Build 20250612 Rel. 45690 | Aplicação aceita e mudança 149 → 157 na banda 5_2; três leituras posteriores iguais e monitoramento contínuo no piloto |
| X50 como secundário na mesh do M9 | Aparece no inventário; isso não valida a aplicação quando o X50 é o principal |
| X50 principal, hardware 1.30, firmware 1.8.4 Build 26051413 Rel. 27460, modo Router | Monitoramento e otimização sob demanda por TPAP/TLS; sensores de canal representam backhaul. Ações bloqueadas fora desse perfil |
| M5 ou outros modelos como principal | Não validados para aplicação |

O protocolo de aplicação atual foi identificado no firmware do M9 V2. As imagens X50 1.8.0 e 1.8.4 mantêm o mesmo mapeamento de análise/aplicação, mas isso não garante o mesmo transporte ou autenticação. Na 1.8.4 examinada, TPAP substitui o SSH quando a compatibilidade antiga está desativada; apenas mudar a porta não adapta o cliente. A [pesquisa](RESEARCH.md) registra os pacotes oficiais analisados. Não se presume equivalência entre revisões de hardware. O nome `m9_acs` não é uma promessa de compatibilidade com X50. No X50, selecione `deco_model: x50` e informe seu certificado e conta proprietária. O app usa TPAP/TLS para monitoramento e ações; não tenta SSH nem HTTPS como alternativa. Firmware/hardware/modo diferentes permitem leitura, mas bloqueiam análise e aplicação até validação específica.

Na 0.8.0-dev.1, o piloto confirmou criação das entidades e envio/conclusão de um pedido de verificação pelo Home Assistant. A revisão dev.2 acrescenta avisos de falha MQTT, empacotamento e exemplos; ainda não foi instalada no HAOS do piloto. Aceite do comando e leituras estáveis não comprovam melhora de interferência, velocidade ou recuperação de uma câmera.

### Configuração do app e atualização única

A 0.9 reúne os dois transportes, sensores, aplicação e recuperação. As opções principais são modelo, IP, conta proprietária, identidade do equipamento e habilitação MQTT/comandos. A senha HTTPS e a chave SSH são exclusivas do M9; o certificado é exclusivo do X50. Deixe vazios os campos do outro modelo. O Mosquitto é obtido automaticamente do Supervisor quando nenhum servidor alternativo é informado.

IDs manuais, tempos, diagnóstico e MQTT alternativo são opções avançadas, sem valores obrigatórios em uma instalação nova. O Supervisor preserva valores já salvos numa atualização, portanto eles podem continuar aparecendo no formulário antigo. Não é necessário removê-los nem reconfigurar o M9. Veja a configuração por modelo em [DOCS.md](ezmart_deco_optimizer/DOCS.md).

X50 monitora a cada no mínimo 60 segundos, sem ACS periódico. Cada pedido explícito exige recomendação positiva e reserva persistente; há mínimo de 30 minutos entre pedidos/aplicações. A verificação usa apenas leituras autenticadas. Os sensores numéricos são nomeados **Canal backhaul**; CPU, memória e terceira banda não disponíveis permanecem desconhecidos. Nenhum comando parte da inicialização, mesmo sem MQTT.

O journal do diagnóstico em `/config` não é importado automaticamente pelo app em `/data`. Preserve-o, não execute os dois ao mesmo tempo e aguarde ao menos 30 minutos desde a última aplicação do diagnóstico antes do primeiro pedido pelo app. Uma aplicação do diagnóstico ainda incerta deve ser resolvida pelo próprio diagnóstico antes de habilitar pedidos no app. Não apague históricos para liberar tentativas.

### Diagnóstico X50 sem rebuild

[ezmart_deco_optimizer/x50_tpap.py](ezmart_deco_optimizer/x50_tpap.py) contém o cliente compartilhado pelo app e pelo diagnóstico independente. Baixe esse arquivo como `/tmp/x50_tls_probe.py`; `tools/x50_tls_probe.py` é apenas o atalho para um checkout completo. O diagnóstico é para o X50 US acima, executado no Terminal/SSH do HA que está na mesma rede. Os modos TLS e parâmetros requerem somente Python 3.9 ou superior; no app Terminal/SSH baseado em Alpine, `apk add --no-cache python3` instala o interpretador. Os modos de login, leitura e análise também requerem PyCryptodome. Depois de baixar o arquivo para `/tmp`:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1
```

Por padrão, ele faz uma conexão TLS/30001 com prazo total de aproximadamente oito segundos, valida a cadeia com a CA pública extraída do firmware oficial e informa a impressão SHA-256 do certificado do equipamento. Nesse modo não envia login, mensagens TPAP ou comandos de otimização e não instala a CA no sistema. Não usa descoberta UDP, fallback para SSH ou alterações de compatibilidade.

`state: tls_verified` significa cadeia aceita e nome de produto `Deco X50(US)` reconhecido. O certificado não identifica o IP privado, por isso `hostname_verified` permanece falso. Na primeira execução não há uma identidade individual previamente fixada; é possível comparar execuções com `--expected-sha256` seguido da impressão anterior. Uma divergência encerra com erro, sem tentativa insegura. `certificate_untrusted` também encerra com erro e mostra apenas o código de validação. Autenticação e otimização continuam marcadas como `not_tested`. Compartilhe a saída JSON; o diagnóstico não exibe MAC, serial, credenciais ou certificados completos.

O modo de negociação inicial consulta os parâmetros usados no login:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --pake-parameters
```

Substitua a impressão pelo `certificate_sha256` da primeira execução nesse X50. O Terminal pede o **e-mail da conta proprietária desse equipamento**, com entrada oculta. Use a conta proprietária da rede que será configurada. O e-mail não é argumento de linha de comando, não é salvo e não aparece no relatório; seu SHA-256 é enviado dentro da conexão TLS validada e fixada ao certificado informado.

Esse modo inicia apenas a negociação `pake_register` para uma conta existente e informa os algoritmos e tamanhos recebidos. Não cria conta, pede senha, envia prova de senha, abre canal TMP, solicita análise ou aplica canais. Há somente uma tentativa, com validação de enquadramento, sequência, checksum, tamanho e prazo. `pake_parameters_received` **não confirma login nem a existência da conta**: o firmware pode gerar parâmetros fictícios para usuário desconhecido. O relatório omite salts, nonces, pontos criptográficos, tokens e hashes de conta.

O diagnóstico também permite verificar **um login completo**:

```sh
apk add --no-cache python3 py3-pycryptodome
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --authenticate
```

O e-mail e a senha **da conta proprietária da rede** são digitados com entrada oculta no Terminal. Não coloque credenciais no comando nem compartilhe a senha. O diagnóstico preserva maiúsculas/minúsculas do e-mail e espaços da senha, não grava credenciais e não usa as do M9. A prova SPAKE2+ usa a derivação identificada no firmware, pontos P-256 da biblioteca PyCryptodome, aleatoriedade nova e confirmação criptográfica de ambos os lados. Só aceita o perfil observado: suíte 1, AES-128-CCM e ausência de `extra_crypt`; perfis diferentes encerram antes de enviar a prova. A conexão continua exigindo a CA do fabricante e a impressão individual conhecida.

Sucesso exige `state: authentication_verified`, `authentication: verified`, `account_credentials_verified: true` e `server_proof_verified: true`. Uma resposta API 0 sem prova válida nunca basta. `account_role_verified: false` significa que as permissões de proprietário/administrador não foram consultadas; não indica falha da senha. O token recebido é conferido apenas quanto à presença/formato e não aparece no relatório. `authentication_rejected` conserva apenas o código de erro, sem concluir que a causa é necessariamente senha incorreta. Não há nova tentativa automática. Após uma recusa, confira a conta e o resultado antes de repetir.

Os modos acima fecham a conexão sem abrir canal TMP. A revisão 4 acrescenta uma opção separada que autentica, abre esse canal e consulta uma vez modo de operação, canais e inventário:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --read-info
```

Use as credenciais da rede nos mesmos prompts ocultos. O prazo é de 20 segundos para o login e para cada uma das quatro operações TMP (alocação e três leituras), até aproximadamente 100 segundos no conjunto. Não há repetição automática. O relatório mantém `authentication: verified` se o login passou, mesmo que uma etapa posterior falhe. `tmp_session_verified: true` confirma a abertura TMP; `readonly_verified` exige as três respostas reconhecidas. `readonly_incomplete` significa que alguma leitura foi recusada ou apresentou formato desconhecido. `stage` identifica onde ocorreu uma eventual falha de transporte. Os canais são separados entre `radio`, `host` e `backhaul`, conforme disponíveis, sem confundir configuração e leitura do rádio.

O relatório omite senhas Wi-Fi, SSIDs, MACs, nomes de cômodos, IDs e tokens. Os modos acima não buscam interferência nem aplicam canais; `optimization` continua `not_tested`. O login pode estabelecer uma sessão e uma tentativa recusada pode contar para o bloqueio do equipamento. O diagnóstico não integra o runtime do app e **não exige rebuild**. A versão, transporte M9 e entidades MQTT permanecem iguais.

### Uma análise de interferência no X50

A revisão 5 adiciona um modo explícito de análise. Ele autentica, repete as três leituras e exige o perfil observado do principal: X50, hardware 1.30, firmware 1.8.4 Build 26051413 Rel. 27460 e modo Router. Então solicita **uma análise ACS**, aguarda a resposta e consulta os canais novamente:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --scan-only --scan-timeout 120
```

Essa chamada pode iniciar a busca de interferência no equipamento; não é uma consulta passiva. O prazo da análise é separado (30–180 segundos, padrão 120). Com os valores do exemplo, o limite do conjunto é aproximadamente quatro minutos, incluindo login, alocação, leituras e análise. Há um único pedido de análise por execução, sem reenviar após timeout ou falha de envio. Executar o comando novamente inicia outra sessão e pode iniciar outra análise; não há agendamento nem histórico persistente nesse diagnóstico.

`scan_response_received` indica resposta reconhecida, com `scan.need_optimize` e `scan.optimize_item.channel` booleanos coerentes. **Não confirma aplicação**: `apply_command_sent` permanece falso e o modo `--scan-only` não habilita aplicação. `estimated_apply_seconds` é a estimativa fornecida pelo Deco para uma eventual aplicação, não a duração da análise. Mesmo uma resposta demorada pode vir de cache: `fresh_scan_confirmed: false` e `result_may_be_cached: true` preservam essa limitação. O firmware também pode suprimir a recomendação por 1800 segundos após uma aplicação.

`reads.channels` contém a observação anterior; `channels_after_scan` contém a posterior. `channel_comparison` compara somente campos numéricos na mesma banda e seção (`radio`, `host` ou `backhaul`), identificando comparação completa, parcial ou indisponível. Mudança observada não é atribuída ao diagnóstico nem prova melhora. Se a observação posterior falhar, a resposta de análise recebida fica preservada em `scan`, mas o resultado geral não declara o diagnóstico completo. Um perfil diferente retorna `scan_target_unsupported` antes do pedido; uma leitura prévia incompleta retorna `scan_preflight_failed`.

### Aplicação piloto no X50 e recuperação

A revisão 6 acrescenta `--optimize` e `--verify-only`, ainda independentes do app. A aplicação só é habilitada em `--optimize`, após o mesmo perfil de principal ser conferido, um novo pedido de análise retornar recomendação positiva e a leitura de canais posterior estar completa. Não reutiliza como autorização um JSON antigo de análise. `optimization_not_needed` significa que essa execução não aplicou nada.

Execute uma vez no Home Assistant conectado à mesma rede do X50, com o diagnóstico atual em `/tmp`:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --optimize --request-id x50-opt-001 \
  --journal /config/ezmart_x50_apply_journal.json \
  > /config/x50_optimize_resultado.json
```

**Pode haver interrupção temporária do Wi-Fi.** As credenciais são digitadas com entrada oculta e não vão para os arquivos. A análise espera até 120 segundos e a resposta de aplicação até 60; ambos têm uma única tentativa. Depois, o diagnóstico espera 30 segundos e procura três leituras consecutivas iguais, com intervalos de dez segundos, no máximo seis observações dentro de uma janela de 120 segundos. Cada observação abre uma sessão autenticada nova, exclusivamente para leituras, mantendo o certificado fixado. Falhas transitórias reiniciam a contagem de estabilidade; rejeição de autenticação, identidade divergente ou protocolo inválido interrompem a verificação. Com esses prazos, reserve até aproximadamente oito minutos para o conjunto.

O registro `/config/ezmart_x50_apply_journal.json` e seu arquivo `.lock` impedem concorrência e repetição acidental. A tentativa é gravada e sincronizada antes do envio; `apply_command_attempted` é conservador, podendo ficar verdadeiro mesmo que um envio interrompido não tenha chegado ao equipamento. Repetir o mesmo ID retorna `already_requested`, sem conexão. Outro ID também é bloqueado enquanto houver aplicação anterior sem aceite e verificação estável; após ambos, há intervalo mínimo de 1800 segundos. Preserve esse registro: apagá-lo, mudar seu caminho ou usar outra instalação elimina essa proteção local. O JSON de resultado compartilhável é um arquivo separado.

Se o Terminal desconectar, a saída redirecionada pode ficar incompleta; a reserva persistente continua protegendo o pedido. Após reconectar, use **somente a verificação do mesmo ID e registro**:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --verify-only --request-id x50-opt-001 \
  --journal /config/ezmart_x50_apply_journal.json \
  > /config/x50_verificacao_resultado.json
```

Esse modo não envia análise nem aplicação. Se a resposta original de aceite se perdeu, a leitura posterior não a inventa. Não renomeie o pedido para contornar um resultado incerto.

Interpretação do relatório:

- `apply_ack.state: accepted`: o Deco retornou API 0 à aplicação; não prova melhora.
- `verification.state: stable`: três observações consecutivas iguais, cobrindo todos os parâmetros da referência anterior.
- `conclusion: acknowledged_with_channel_change`: houve aceite e estabilidade, com mudança numérica em algum canal observado. Consulte `verification.channel_comparison` para saber banda e seção.
- `acknowledged_without_observed_channel_change`: houve aceite e estabilidade, sem troca numérica observada. `verification.radio_changes` também mostra alterações de largura, quando presentes. Isso não é promessa de ganho ou de recuperação de câmera.
- `unconfirmed_*`, `verification_incomplete`, `apply_rejected` ou erro de registro: não considerar otimização confirmada; nenhuma reaplicação automática ocorre.

`elapsed_seconds` mede a ação completa, incluindo a verificação; `request_session_seconds` e os contadores de mensagens/bytes referem-se à sessão inicial. Uma verificação posterior preserva os horários da ação original e registra `last_verified_at`/`last_verification_seconds`. Os dados de WLAN continuam separados por `radio`, `host` e `backhaul`, sem atribuir leituras ausentes à rede principal.

## Automações e acompanhamento

- [Configuração, entidades, resultados e recuperação](ezmart_deco_optimizer/AUTOMATION.md)
- [Blueprint: repetir até confirmar uma nova aplicação](ezmart_deco_optimizer/examples/camera_until_confirmed.yaml) — repete com pausa de 15 minutos; recupera verificação pendente; nunca considera resultado antigo, timeout ou resposta negativa como sucesso
- [Blueprint: uma tentativa por indisponibilidade](ezmart_deco_optimizer/examples/camera_unavailable.yaml) — política alternativa, sem insistência
- [Cartão de painel](ezmart_deco_optimizer/examples/dashboard.yaml)
- [Documentação do protocolo e modo manual](ezmart_deco_optimizer/DOCS.md)
- [Evidências do piloto](PILOT.md), [pesquisa](RESEARCH.md) e [changelog](ezmart_deco_optimizer/CHANGELOG.md)

O blueprint de repetição continua mesmo se a câmera voltar, até uma aplicação aceita com verificação estável ou cancelamento manual. Se o Deco nunca recomendar aplicação, pode permanecer ativo indefinidamente. Reiniciar o HA/recarregar automações interrompe essa execução; não há retomada persistente. Os bloqueios do app continuam valendo.

## Desenvolvimento e suporte

Runtime: Python 3.14, SDK tplink-deco-api 2.1.1, Paramiko 5.1.0 e Paho MQTT 2.1.0. Para os testes, use um ambiente isolado com PyYAML, Jinja2, Paho MQTT e PyCryptodome:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

Os testes não acessam um Deco. O teste de comunicação MQTT abre somente loopback. A validação automatizada também constrói a imagem amd64. Temporários/dependências de desenvolvimento devem ficar fora de OneDrive/iCloud, por exemplo em `/private/tmp` no Mac. Firmwares e ferramentas transitórias ficam fora da distribuição e do histórico público.

[Relatar um problema](https://github.com/themodernhousebr/tmh-ezmart-deco-optimizer/issues): inclua modelo, versão de hardware, firmware, modo AP/roteador, versão do app e trechos filtrados do log. Não envie senhas, tokens ou `options.json`.

### Instalação X50 com certificado automático

A partir de 0.11.0-dev.2, `x50_certificate_mode: automatic` é o padrão. Deixe `x50_certificate_sha256` vazio em instalações novas: o app obtém o certificado sem login, valida fabricante/modelo e salva a identidade por instalação. Todas as conexões seguintes conferem essa identidade; mudanças são bloqueadas. Certificados já configurados têm prioridade e são preservados. `pinned` mantém o preenchimento manual. Não é necessário executar o diagnóstico independente para cadastrar uma instalação nova.
