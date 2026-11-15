# EZmart Home Assistant Apps

<img src="ezmart_deco_optimizer/icon.png" alt="The Modern House" width="128">

**0.8.0-dev.2 — experimental.** Monitoramento Deco e otimização sob demanda por entidades do Home Assistant. Piloto validado com **M9 Plus V2 principal em modo AP**.

Cria um dispositivo MQTT com 22 entidades: botões para otimizar se necessário ou verificar a última aplicação, sensores de resultado, canais, horários, disponibilidade e bloqueios. O app só analisa/aplica após um pedido explícito, inclusive de uma automação escolhida por você. Aplica somente quando o Deco recomenda; não existe otimização periódica interna.

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
| X50 como roteador principal, hardware informado pelo inventário 1.30, DHCP ativo, firmware 1.8.4 Build 26051413 Rel. 27460 | TLS/30001, login SPAKE2+ e leituras TMP de modo, canais e inventário confirmados no equipamento. Análise de interferência preparada no diagnóstico independente; análise física e aplicação ainda pendentes. O app atual **não implementa TPAP** |
| M5 ou outros modelos como principal | Não validados para aplicação |

O protocolo de aplicação atual foi identificado no firmware do M9 V2. As imagens X50 1.8.0 e 1.8.4 mantêm o mesmo mapeamento de análise/aplicação, mas isso não garante o mesmo transporte ou autenticação. Na 1.8.4 examinada, TPAP substitui o SSH quando a compatibilidade antiga está desativada; apenas mudar a porta não adapta o cliente. A [pesquisa](RESEARCH.md) registra os pacotes exatos: o inventário autenticado do cliente informou hardware **1.30** para os dois X50, apesar da etiqueta V1.0 anteriormente atribuída à unidade. Isso não implica intercambialidade de revisões. O nome `m9_acs` não é uma promessa de compatibilidade com X50. Até validar a aplicação e integrar o transporte X50 ao app, mantenha `tmp_enabled: false` e `mqtt_commands_enabled: false` nesse equipamento. Monitoramento HTTPS também depende do firmware e precisa ser conferido.

Na 0.8.0-dev.1, o piloto confirmou criação das entidades e envio/conclusão de um pedido de verificação pelo Home Assistant. A revisão dev.2 acrescenta avisos de falha MQTT, empacotamento e exemplos; ainda não foi instalada no HAOS do piloto. Aceite do comando e leituras estáveis não comprovam melhora de interferência, velocidade ou recuperação de uma câmera.

### Diagnóstico X50 sem rebuild

[tools/x50_tls_probe.py](tools/x50_tls_probe.py) é um diagnóstico independente para o X50 US acima, executado no Terminal/SSH do HA que está na mesma rede. Os modos TLS e parâmetros requerem somente Python 3.9 ou superior; no app Terminal/SSH baseado em Alpine, `apk add --no-cache python3` instala o interpretador. Os modos de login, leitura e análise também requerem PyCryptodome. Depois de baixar o arquivo para `/tmp`:

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

Substitua a impressão pelo `certificate_sha256` da primeira execução nesse X50. O Terminal pede o **e-mail da conta proprietária desse equipamento**, com entrada oculta. Não use a conta de outra instalação; o M9 residencial e o X50 do cliente são ambientes independentes. O e-mail não é argumento de linha de comando, não é salvo e não aparece no relatório; seu SHA-256 é enviado dentro da conexão TLS validada e fixada ao certificado informado.

Esse modo inicia apenas a negociação `pake_register` para uma conta existente e informa os algoritmos e tamanhos recebidos. Não cria conta, pede senha, envia prova de senha, abre canal TMP, solicita análise ou aplica canais. Há somente uma tentativa, com validação de enquadramento, sequência, checksum, tamanho e prazo. `pake_parameters_received` **não confirma login nem a existência da conta**: o firmware pode gerar parâmetros fictícios para usuário desconhecido. O relatório omite salts, nonces, pontos criptográficos, tokens e hashes de conta. O operador confirmou essa negociação no X50 do cliente: API 0, suíte 1, AES-128-CCM, 100.000 iterações, sem `extra_crypt`, com certificado correspondente ao primeiro teste.

A revisão 3 acrescentou o teste de **um login completo**, confirmado no X50 do cliente em 15/11/2026 (0,388 segundo, duas mensagens e 540 bytes enviados):

```sh
apk add --no-cache python3 py3-pycryptodome
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --authenticate
```

O e-mail e a senha **da conta do cliente** são digitados com entrada oculta no Terminal. Não coloque credenciais no comando nem compartilhe a senha. O diagnóstico preserva maiúsculas/minúsculas do e-mail e espaços da senha, não grava credenciais e não usa as do M9. A prova SPAKE2+ usa a derivação identificada no firmware, pontos P-256 da biblioteca PyCryptodome, aleatoriedade nova e confirmação criptográfica de ambos os lados. Só aceita o perfil observado: suíte 1, AES-128-CCM e ausência de `extra_crypt`; perfis diferentes encerram antes de enviar a prova. A conexão continua exigindo a CA do fabricante e a impressão individual conhecida.

Sucesso exige `state: authentication_verified`, `authentication: verified`, `account_credentials_verified: true` e `server_proof_verified: true`. Uma resposta API 0 sem prova válida nunca basta. `account_role_verified: false` significa que as permissões de proprietário/administrador não foram consultadas; não indica falha da senha. O token recebido é conferido apenas quanto à presença/formato e não aparece no relatório. `authentication_rejected` conserva apenas o código de erro, sem concluir que a causa é necessariamente senha incorreta. Não há nova tentativa automática. Após uma recusa, confira a conta e o resultado antes de repetir.

Os modos acima fecham a conexão sem abrir canal TMP. A revisão 4 acrescenta uma opção separada que autentica, abre esse canal e consulta uma vez modo de operação, canais e inventário:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --read-info
```

Use as credenciais do cliente nos mesmos prompts ocultos. O prazo é de 20 segundos para o login e para cada uma das quatro operações TMP (alocação e três leituras), até aproximadamente 100 segundos no conjunto. Não há repetição automática. O relatório mantém `authentication: verified` se o login passou, mesmo que uma etapa posterior falhe. `tmp_session_verified: true` confirma a abertura TMP; `readonly_verified` exige as três respostas reconhecidas. `readonly_incomplete` significa que alguma leitura foi recusada ou apresentou formato desconhecido. `stage` identifica onde ocorreu uma eventual falha de transporte. Os canais são separados entre `radio`, `host` e `backhaul`, conforme disponíveis, sem confundir configuração e leitura do rádio.

O operador confirmou `readonly_verified` no X50 do cliente em 15/11/2026: 2,114 segundos, modo Router, dois X50 hardware 1.30 com firmware 1.8.4, um principal e um secundário. Os números 10 e 36 vieram de `backhaul` nas bandas 2,4 e 5 GHz; `radio` retornou apenas HT40/HT80. Não são leituras numéricas de canal da rede principal dos clientes Wi-Fi.

O relatório omite senhas Wi-Fi, SSIDs, MACs, nomes de cômodos, IDs e tokens. Os modos acima não buscam interferência e nenhum modo aplica canais; `optimization` continua `not_tested`. O login pode estabelecer uma sessão e uma tentativa recusada pode contar para o bloqueio do equipamento. O diagnóstico não integra o runtime do app e **não exige rebuild**. A versão, transporte M9 e entidades MQTT permanecem iguais.

### Uma análise de interferência no X50

A revisão 5 adiciona um modo explícito de análise. Ele autentica, repete as três leituras e exige o perfil observado do principal: X50, hardware 1.30, firmware 1.8.4 Build 26051413 Rel. 27460 e modo Router. Então solicita **uma análise ACS**, aguarda a resposta e consulta os canais novamente:

```sh
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --scan-only --scan-timeout 120
```

Essa chamada pode iniciar a busca de interferência no equipamento; não é uma consulta passiva. O prazo da análise é separado (30–180 segundos, padrão 120). Com os valores do exemplo, o limite do conjunto é aproximadamente quatro minutos, incluindo login, alocação, leituras e análise. Há um único pedido de análise por execução, sem reenviar após timeout ou falha de envio. Executar o comando novamente inicia outra sessão e pode iniciar outra análise; não há agendamento nem histórico persistente nesse diagnóstico.

`scan_response_received` indica resposta reconhecida, com `scan.need_optimize` e `scan.optimize_item.channel` booleanos coerentes. **Não confirma aplicação**: `apply_command_sent` permanece falso e não existe comando de aplicação habilitado. `estimated_apply_seconds` é a estimativa fornecida pelo Deco para uma eventual aplicação, não a duração da análise. Mesmo uma resposta demorada pode vir de cache: `fresh_scan_confirmed: false` e `result_may_be_cached: true` preservam essa limitação. O firmware também pode suprimir a recomendação por 1800 segundos após uma aplicação.

`reads.channels` contém a observação anterior; `channels_after_scan` contém a posterior. `channel_comparison` compara somente campos numéricos na mesma banda e seção (`radio`, `host` ou `backhaul`), identificando comparação completa, parcial ou indisponível. Mudança observada não é atribuída ao diagnóstico nem prova melhora. Se a observação posterior falhar, a resposta de análise recebida fica preservada em `scan`, mas o resultado geral não declara o diagnóstico completo. Um perfil diferente retorna `scan_target_unsupported` antes do pedido; uma leitura prévia incompleta retorna `scan_preflight_failed`.

Esse modo passou pelos testes simulados e aguarda a análise física do X50 do cliente. A aplicação e integração TPAP ao app continuam pendentes.

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
