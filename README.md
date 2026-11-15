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
| X50 como roteador principal, DHCP ativo, firmware 1.8.4 Build 26051413 Rel. 27460 | Firmware oficial correspondente analisado: comandos ACS presentes, transporte TPAP. No teste do operador, SSH/20001 recusado e TCP/30001 acessível. A versão atual do EZmart **não implementa TPAP**; otimização ainda não validada |
| M5 ou outros modelos como principal | Não validados para aplicação |

O protocolo de aplicação atual foi identificado no firmware do M9 V2. As imagens X50 1.8.0 e 1.8.4 mantêm o mesmo mapeamento de análise/aplicação, mas isso não garante o mesmo transporte ou autenticação. Na 1.8.4 examinada, TPAP substitui o SSH quando a compatibilidade antiga está desativada; apenas mudar a porta não adapta o cliente. A [pesquisa](RESEARCH.md) registra os pacotes exatos e a diferença entre a etiqueta V1.0 informada pelo operador e a página oficial V1.30 do build instalado. O nome `m9_acs` não é uma promessa de compatibilidade com X50. Até validar um X50 representativo, mantenha `tmp_enabled: false` e `mqtt_commands_enabled: false` nesse equipamento. Monitoramento HTTPS também depende do firmware e precisa ser conferido.

Na 0.8.0-dev.1, o piloto confirmou criação das entidades e envio/conclusão de um pedido de verificação pelo Home Assistant. A revisão dev.2 acrescenta avisos de falha MQTT, empacotamento e exemplos; ainda não foi instalada no HAOS do piloto. Aceite do comando e leituras estáveis não comprovam melhora de interferência, velocidade ou recuperação de uma câmera.

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
