# Piloto M9 Plus V2 — 0.7.0-dev.1

Objetivo: analisar, aplicar uma recomendação positiva uma única vez e observar os canais após a aplicação. Primeira aplicação aceita e mudança observada no piloto em 15/11/2026, às 10:08–10:10; veja o resultado abaixo. O controlador pode reiniciar o serviço Wi-Fi; execute quando uma interrupção temporária for aceitável.

## Atualizar uma vez

1. Parar somente o app EZmart Deco Optimizer.
2. Copiar a pasta inteira `ezmart_deco_optimizer` deste repositório para `/local_apps/ezmart_deco_optimizer` pelo SFTP do Termius. Incluir o novo `m9_actions.py` e o Dockerfile atualizado.
3. Atualizar a loja e usar Atualizar para 0.7.0-dev.1, ou fazer uma única reconstrução após a cópia. Atualizar já constrói a imagem; não repetir rebuild depois. Não desinstalar: preserve opções e histórico.
4. Aguardar a conclusão. Conferir versão e log de inicialização.

```sh
ha apps info local_ezmart_deco_optimizer | grep -E '^(version|version_latest|state):'
ha supervisor logs | grep -F "App 'local_ezmart_deco_optimizer' successfully rebuilt" | tail -n 1
```

A última mensagem de rebuild pode pertencer a uma execução anterior. Confira o horário; instalação e atualização podem usar mensagens diferentes. Ao iniciar, a linha `Versão 0.7.0-dev.1` confirma o código em execução.

## Rodada manual completa

Mantenha host, credenciais e chave TMP já validados. Configure:

```yaml
diagnose_acs: false
tmp_enabled: true
tmp_scan_backend: m9_acs
tmp_action: optimize
tmp_optimize_request_id: m9-opt-001
tmp_scan_timeout_seconds: 120
tmp_apply_timeout_seconds: 60
tmp_verify_delay_seconds: 30
tmp_verify_timeout_seconds: 120
tmp_verify_interval_seconds: 10
```

`tmp_scan_request_id` não é usado no modo optimize; pode manter o valor anterior. Feche a tela de otimização do app Deco para não disputar a mesma análise. Salve e inicie/reinicie **somente o app EZmart Deco Optimizer**, sem reiniciar HAOS, Proxmox ou Deco.

A sequência é automática dentro desta única ação manual:

1. Autenticar e fazer uma análise M9. O teste anterior levou aproximadamente 27 segundos; o prazo é configurável.
2. Se a recomendação for negativa, encerrar com `optimization_not_needed`, sem enviar aplicação.
3. Se for positiva, registrar os canais, gravar a reserva da ação em disco e enviar uma única aplicação. Resposta perdida nunca gera reenvio.
4. Após a resposta ou timeout, aguardar 30 segundos e observar os canais via HTTPS. Exigir três leituras consecutivas iguais, com todas as bandas presentes no registro anterior.
5. Registrar aceite, mudança observada e eventual incerteza em separado. O inventário normal continua depois.

O tempo total inclui autenticação, análise, resposta à aplicação e observação. O limite de observação é 120 segundos após a espera inicial, além do tempo das requisições em curso. Não reiniciar enquanto estiver verificando, salvo para interromper deliberadamente. `poll_seconds` não agenda otimizações nem altera esses prazos.

## Interpretar e enviar o resultado

Enviar a linha completa `Diagnóstico tmp` e, se ocorrer, a primeira conclusão de verificação. Não enviar credenciais ou `options.json`.

| Resultado | Significado / próximo passo |
|---|---|
| optimization_not_needed | Não há recomendação neste retorno; aplicação não enviada. Caso provável se a rede continuar como no último teste “Perfeito” |
| apply_acknowledged + acknowledged_with_channel_change | Comando aceito e mudança de canal observada no principal; conferir o próximo inventário da mesh |
| acknowledged_without_observed_change | Comando aceito, mas os canais observados não mudaram; não declarar ganho ou troca confirmada |
| apply_rejected | Controlador recusou; enviar api_code/shape do relatório, sem tentar outra aplicação |
| apply_outcome_uncertain / apply_dispatch_uncertain | Resposta ausente ou execução interrompida; não reenviar. Usar verify com o mesmo ID |
| verification_incomplete | Não foi possível obter três leituras completas e iguais; usar verify |
| already_requested | O mesmo ID já foi consumido; apenas devolve o relatório, sem nova análise/aplicação |
| previous_apply_unverified | Uma ação anterior ainda precisa de observação; usar verify com previous_request_id |
| apply_cooldown | Ainda não decorreram 15 minutos da tentativa anterior; aguardar |
| preflight_failed / baseline_unavailable | Não foi possível validar análise ou canais anteriores; nenhuma aplicação enviada |

`verification.state: stable` significa apenas três leituras iguais dos canais do principal. Não mede velocidade, interferência nem estabilidade de todos os nós. Se o comando não teve confirmação, a conclusão continua `unconfirmed_*`, mesmo que os canais mudem.

## Recuperar a verificação sem rebuild e sem reaplicar

Se houver resposta perdida, interrupção do app ou falha de leitura após a aplicação, manter o ID e mudar apenas:

```yaml
tmp_action: verify
tmp_optimize_request_id: m9-opt-001
```

Salvar e reiniciar o app. Esse modo usa a sessão HTTPS e o registro persistido; não abre conexão TMP, não analisa novamente e não envia aplicação. Os prazos de observação podem ser ajustados nas opções, sem rebuild. Uma falha de autenticação HTTPS ainda respeita o cooldown próprio.

Depois de uma rodada concluída, voltar a `tmp_action: read` ou `tmp_enabled: false`. Uma nova otimização deliberada exige outro `tmp_optimize_request_id`, por exemplo `m9-opt-002`, e uma nova análise positiva. Após uma tentativa de aplicação, são necessários 15 minutos e uma verificação concluída antes de permitir outro ID. Não apagar o histórico nem desinstalar para contornar esses controles. Não provocar interferência apenas para obter recomendação positiva.

## Evidência disponível

Piloto: HAOS 18.4 / Core 2026.11.1 amd64 no Proxmox, principal M9 Plus V2 em AP, firmware 1.9.1 Build 20250612 Rel. 45690; inventário com 3 M9Plus, 1 M5 e 1 X50.

Em 15/11/2026, a 0.6 recebeu a análise M9 em 26,842 segundos: `api_code: 0`, `need_optimize: false`, canais 9/36/149 inalterados. Uma busca no app Deco às 09:45 levou cerca de 27 segundos e mostrou “Perfeito”, canais 9/36. Essa rodada da 0.6 não executou aplicação; a primeira rodada positiva da 0.7 está registrada abaixo. A resposta pode refletir cache; o firmware também suprime recomendações por 30 minutos após aplicação.

Detalhes: [DOCS.md](ezmart_deco_optimizer/DOCS.md). Evidências de firmware: [RESEARCH.md](RESEARCH.md).

## Primeira aplicação concluída — 15/11/2026, 10:08–10:10

Fonte: log fornecido pelo usuário. Versão 0.7.0-dev.1; ação m9-opt-001, backend m9_acs. Análise em 26,385 s, recomendação positiva, uma tentativa de aplicação e aceite API 0 em 14,214 s. Após a espera de 30 s, três leituras concordaram, sem falhas. Conclusão acknowledged_with_channel_change.

| Banda | Antes | Depois | Largura reportada |
|---|---|---|---|
| 2,4 GHz | 9 | 9 | HT40 |
| 5 GHz — band5_1 | 36 | 36 | HT80 |
| 5 GHz — band5_2 | 149 | 157 | HT80 |

Os canais de backhaul reportados permaneceram 9/36/36. O ciclo de monitoramento terminou normalmente. O Resumo da Mesh emitido no fim deste primeiro ciclo usa inventário coletado antes da ação; não comprova sozinho recuperação dos secundários. O log posterior confirmou os ciclos 2, 3 e 4 completos, consultando inventário e modo sem erros, entre 10:10:47 e 10:12:04. Não houve novo login nem repetição da otimização no trecho. Isso confirma continuidade do monitoramento; não mede conectividade individual de cada secundário. Não é necessário novo rebuild ou nova aplicação.

O ID m9-opt-001 já foi consumido. Reiniciar com o mesmo ID não reaplica. Para uma conferência adicional deliberada, usar verify com o mesmo ID; para manter somente o monitoramento HTTPS, desabilitar tmp_enabled e reiniciar apenas o app. A validação atual cobre esta execução e firmware, não ganho de desempenho nem todos os modelos.

A falha preliminar optimize_id_required foi explicada por espaço ASCII 32 no início do ID salvo. Corrigir para m9-opt-001 e selecionar m9_acs resolveu o bloqueio sem novo build.

Registro estruturado: [evidência desta execução](evidence/2026-11-15-m9-opt-001.json).

## Inicialização da 0.8 — 15/11/2026, 10:37–10:38

O log fornecido pelo usuário confirma a versão 0.8.0-dev.1 em modo Home Assistant, com ação TMP de inicialização desativada. Login HTTPS e primeiro ciclo concluídos; inventário de cinco nós, modo FAP, canais 9/36/157, CPU 6% e memória 32%. A consulta opcional wifi_support continua com erro API 1, sem interromper o ciclo. Não há pedido de otimização nesse trecho.

O trecho cobre aproximadamente 17 segundos e não contém a mensagem de conexão MQTT. Isso ainda não comprova falha do broker nem criação das entidades: conexão, discovery e acionamento pelo Home Assistant permanecem pendentes de confirmação. Não houve alteração de código ou novo pacote a partir deste log.

## Conexão MQTT confirmada — 15/11/2026, 10:46

Após corrigir o endereço salvo de `mqtt://core-mosquitto` para `core-mosquitto` e conferir as credenciais manuais, o log da mesma versão 0.8.0-dev.1 registra conexão MQTT às 10:46:41.681. Não foi necessário rebuild. A mensagem do callback confirma conexão aceita pelo broker; a presença das entidades no HA e o recebimento de seus estados ainda precisam de confirmação na interface.

O primeiro ciclo HTTPS terminou às 10:46:57.210, com cinco nós, modo FAP e canais 9/36/157. Nenhum pedido de otimização aparece no trecho. Próxima validação: botão Verificar última aplicação, para observar a ação m9-opt-001 existente sem nova análise ACS ou aplicação. A execução do botão e o retorno do resultado ao HA ainda estão pendentes.

As capturas posteriores da página do dispositivo confirmam discovery e recebimento de estados no Home Assistant: botões disponíveis, canais 9/36/157, cinco nós, principal acessível, Pronto para otimizar on, Em execução off, CPU/memória e horários de monitoramento atualizados. O relatório anterior foi recuperado: apply_acknowledged e acknowledged_with_channel_change, com Última ação cerca de 38 minutos antes. Último pedido recebido none e Próximo pedido permitido após Unknown são coerentes com nenhum pedido admitido ainda pelo novo controle MQTT. Pronto indica admissão liberada, não necessidade atual de otimização; a recomendação positiva exibida pertence à análise anterior. A validação do envio de comando pelo botão continua pendente.

## Pedido de verificação pelo Home Assistant — 15/11/2026, 10:49

Capturas fornecidas pelo usuário mostram Em execução on, fase verifying, ambos os botões temporariamente indisponíveis e Pronto para otimizar off durante a conferência. O histórico de Último pedido recebido registra running às 10:49:04 e verification_only às 10:49:58, cerca de 54 segundos. Isso valida envio do pedido por entidade MQTT, execução pelo app e retorno do estado ao Home Assistant. O caminho verification_only não envia nova análise ACS nem aplicação. Esse estado, isoladamente, não comprova três amostras estáveis; os atributos verification ou o relatório detalhado da rodada ainda não foram enviados.

Próxima etapa: configurar a automação por indisponibilidade persistente usando o blueprint incluído. A escolha das entidades de câmeras/conectividade e dos respectivos estados de falha depende do ambiente do usuário. Nenhuma automação de câmeras foi instalada ou ativada ainda.


## Migração e botões da 0.9 — 15/11/2026, 19:38–19:44

Na casa do operador, reinstalação pela loja após remoção dos registros antigos criou entidades com os IDs esperados, sem sufixo `_2`. Logs confirmam monitoramento M9 em FAP, cinco nós, canais 9/36/157 e CPU/memória. O pedido por botão terminou em `optimization_not_needed` às 19:44:22. Nenhuma automação de câmera foi criada; o operador adiou essa etapa.

## Visitantes M9 — implementação da 0.10, teste físico pendente

O controle de visitantes foi implementado inicialmente para principal M9; suporte X50 será avaliado em uma etapa posterior. A 0.10 inclui interruptor opt-in, comando parcial identificado no firmware, reserva de escrita e confirmação por leituras. Testes automatizados cobrem sucesso, perda de resposta, duplicidade, reinício, preservação de configurações e isolamento do histórico de otimização. Ainda não houve comando físico de visitantes com esta implementação. Próximo teste: habilitar a opção, conferir o estado inicial, ligar/desligar deliberadamente e verificar a rede também por um secundário. Leitura do principal não comprova propagação a todos os nós.
