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
| X50 como roteador principal, DHCP ativo, firmware 1.8.4 Build 26051413 Rel. 27460 | Firmware oficial correspondente analisado: comandos ACS presentes, transporte TPAP. No teste do operador, TLS/30001 e negociação inicial PAKE passaram, com certificado conferido. Diagnóstico de login completo preparado; autenticação com senha e otimização ainda não validadas no equipamento. O app atual **não implementa TPAP** |
| M5 ou outros modelos como principal | Não validados para aplicação |

O protocolo de aplicação atual foi identificado no firmware do M9 V2. As imagens X50 1.8.0 e 1.8.4 mantêm o mesmo mapeamento de análise/aplicação, mas isso não garante o mesmo transporte ou autenticação. Na 1.8.4 examinada, TPAP substitui o SSH quando a compatibilidade antiga está desativada; apenas mudar a porta não adapta o cliente. A [pesquisa](RESEARCH.md) registra os pacotes exatos e a diferença entre a etiqueta V1.0 informada pelo operador e a página oficial V1.30 do build instalado. O nome `m9_acs` não é uma promessa de compatibilidade com X50. Até validar um X50 representativo, mantenha `tmp_enabled: false` e `mqtt_commands_enabled: false` nesse equipamento. Monitoramento HTTPS também depende do firmware e precisa ser conferido.

Na 0.8.0-dev.1, o piloto confirmou criação das entidades e envio/conclusão de um pedido de verificação pelo Home Assistant. A revisão dev.2 acrescenta avisos de falha MQTT, empacotamento e exemplos; ainda não foi instalada no HAOS do piloto. Aceite do comando e leituras estáveis não comprovam melhora de interferência, velocidade ou recuperação de uma câmera.

### Diagnóstico X50 sem rebuild

[tools/x50_tls_probe.py](tools/x50_tls_probe.py) é um diagnóstico independente para o X50 US acima, executado no Terminal/SSH do HA que está na mesma rede. Os modos TLS e parâmetros requerem somente Python 3.9 ou superior; no app Terminal/SSH baseado em Alpine, `apk add --no-cache python3` instala o interpretador. O modo de login também requer PyCryptodome. Depois de baixar o arquivo para `/tmp`:

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

A revisão 3 acrescenta o teste de **um login completo**, ainda aguardando validação física:

```sh
apk add --no-cache python3 py3-pycryptodome
python3 /tmp/x50_tls_probe.py 192.168.68.1 \
  --expected-sha256 IMPRESSAO_OBTIDA_NESTE_X50 \
  --timeout 20 --authenticate
```

O e-mail e a senha **da conta do cliente** são digitados com entrada oculta no Terminal. Não coloque credenciais no comando nem compartilhe a senha. O diagnóstico preserva maiúsculas/minúsculas do e-mail e espaços da senha, não grava credenciais e não usa as do M9. A prova SPAKE2+ usa a derivação identificada no firmware, pontos P-256 da biblioteca PyCryptodome, aleatoriedade nova e confirmação criptográfica de ambos os lados. Só aceita o perfil observado: suíte 1, AES-128-CCM e ausência de `extra_crypt`; perfis diferentes encerram antes de enviar a prova. A conexão continua exigindo a CA do fabricante e a impressão individual conhecida.

Sucesso exige `state: authentication_verified`, `authentication: verified`, `account_credentials_verified: true` e `server_proof_verified: true`. Uma resposta API 0 sem prova válida nunca basta. `account_role_verified: false` significa que as permissões de proprietário/administrador não foram consultadas; não indica falha da senha. O token recebido é conferido apenas quanto à presença/formato e não aparece no relatório. `authentication_rejected` conserva apenas o código de erro, sem concluir que a causa é necessariamente senha incorreta. Não há nova tentativa automática. Após uma recusa, confira a conta e o resultado antes de repetir.

Todos os modos fecham a conexão sem abrir canal TMP, buscar interferência ou aplicar canais. O modo de login pode estabelecer uma sessão de autenticação no X50 e uma tentativa recusada pode contar para o bloqueio do equipamento. O sucesso do login ainda não homologa otimização. O diagnóstico não integra o runtime do app e **não exige rebuild**. A versão, transporte M9 e entidades MQTT permanecem iguais.

## Automações e acompanhamento

- [Configuração, entidades, resultados e recuperação](ezmart_deco_optimizer/AUTOMATION.md)
- [Blueprint: repetir até confirmar uma nova aplicação](ezmart_deco_optimizer/examples/camera_until_confirmed.yaml) — repete com pausa de 15 minutos; recupera verificação pendente; nunca considera resultado antigo, timeout ou resposta negativa como sucesso
- [Blueprint: uma tentativa por indisponibilidade](ezmart_deco_optimizer/examples/camera_unavailable.yaml) — política alternativa, sem insistência
- [Cartão de painel](ezmart_deco_optimizer/examples/dashboard.yaml)
- [Documentação do protocolo e modo manual](ezmart_deco_optimizer/DOCS.md)
- [Evidências do piloto](PILOT.md), [pesquisa](RESEARCH.md) e [changelog](ezmart_deco_optimizer/CHANGELOG.md)

O blueprint de repetição continua mesmo se a câmera voltar, até uma aplicação aceita com verificação estável ou cancelamento manual. Se o Deco nunca recomendar aplicação, pode permanecer ativo indefinidamente. Reiniciar o HA/recarregar automações interrompe essa execução; não há retomada persistente. Os bloqueios do app continuam valendo.

## Desenvolvimento e suporte

Runtime: Python 3.14, SDK tplink-deco-api 2.1.1, Paramiko 5.1.0 e Paho MQTT 2.1.0. Para os testes, use um ambiente isolado com PyYAML, Jinja2 e Paho MQTT:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

Os testes não acessam um Deco. O teste de comunicação MQTT abre somente loopback. A validação automatizada também constrói a imagem amd64. Temporários/dependências de desenvolvimento devem ficar fora de OneDrive/iCloud, por exemplo em `/private/tmp` no Mac. Firmwares e ferramentas transitórias ficam fora da distribuição e do histórico público.

[Relatar um problema](https://github.com/themodernhousebr/tmh-ezmart-deco-optimizer/issues): inclua modelo, versão de hardware, firmware, modo AP/roteador, versão do app e trechos filtrados do log. Não envie senhas, tokens ou `options.json`.
