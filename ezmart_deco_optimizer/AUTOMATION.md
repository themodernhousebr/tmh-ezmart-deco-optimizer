# Home Assistant sob demanda — 0.10.0-dev.1

O Home Assistant solicita a otimização por um botão MQTT. Não existe agenda de análises ACS nem otimização periódica. Cada pedido aceito faz uma análise e só envia aplicação quando o Deco recomenda mudança. Reiniciar o app ou reconectar o MQTT não cria um pedido.

A configuração mínima do X50 e os campos opcionais estão em [DOCS.md](DOCS.md#configuração-diária-por-modelo). O exemplo abaixo preserva as opções da instalação M9 existente.

## Uma atualização para todas as funções

1. Parar o EZmart e copiar a pasta inteira `ezmart_deco_optimizer` para `/local_apps/ezmart_deco_optimizer`, incluindo `ha_control.py`, `mqtt_bridge.py`, traduções e Dockerfile.
2. Atualizar a loja e fazer uma atualização OU uma reconstrução. Não fazer ambos e não desinstalar: preservar opções, identidade MQTT e histórico de ações.
3. Manter host, credenciais Deco e chave SSH conferida. Ajustar as opções abaixo, salvar e iniciar o app.

```yaml
mqtt_enabled: true
mqtt_commands_enabled: true
request_cooldown_seconds: 900
mqtt_host: ""
tmp_enabled: true
tmp_scan_backend: m9_acs
poll_seconds: 30
diagnose_wifi_support: false
diagnose_channels: true
diagnose_performance: true
tmp_scan_timeout_seconds: 120
tmp_apply_timeout_seconds: 60
tmp_verify_delay_seconds: 30
tmp_verify_timeout_seconds: 120
tmp_verify_interval_seconds: 10
```

Com `mqtt_host` vazio, o app obtém as credenciais do Mosquitto pelo Supervisor. Não copie a senha do Deco para campos MQTT. O serviço Mosquitto e a integração MQTT do Home Assistant precisam estar ativos. Esta versão adiciona Paho MQTT 2.1.0, portanto a primeira construção pode refazer a camada de dependências.

`tmp_action`, `tmp_scan_request_id` e `tmp_optimize_request_id` são ignorados para acionamentos quando MQTT está habilitado. Não precisa trocar IDs nem reiniciar para cada pedido. Os botões geram IDs automaticamente; verificar recupera o ID da última tentativa de aplicação neste Deco. A configuração antiga de optimize ao iniciar é suprimida no modo MQTT, inclusive se os comandos estiverem desabilitados.

Ativar “Iniciar na inicialização” na página do app se quiser manter o serviço após reiniciar o HAOS. Isso inicia monitoramento e MQTT; não inicia otimização. Alterações de opções exigem reiniciar apenas o EZmart; uso dos botões e edição das automações não exigem reinício nem rebuild.

## Entidades disponíveis

Após o log `MQTT conectado`, abrir Configurações → Dispositivos e serviços → MQTT e localizar **EZmart Deco Optimizer**, fabricante The Modern House. No X50 os sensores de canal indicam explicitamente backhaul; CPU, memória e banda 5 GHz 2 ficam desconhecidos quando não disponíveis. São 22 entidades agrupadas no mesmo dispositivo. Os IDs abaixo são sugeridos; Home Assistant pode acrescentar sufixos caso já existam ou preservar nomes que você renomeou.

| Entidade sugerida | Uso |
|---|---|
| button.ezmart_deco_otimizar | Solicitar análise e aplicação somente se recomendada |
| button.ezmart_deco_verificar | Observar a última aplicação sem enviar scan/aplicação |
| binary_sensor.ezmart_deco_pronto_para_otimizar | Condição para suas automações: on quando um pedido pode ser aceito |
| binary_sensor.ezmart_deco_em_execucao | Pedido aguardando execução ou operação em andamento |
| binary_sensor.ezmart_deco_principal_acessivel | Inventário/modo consultados recentemente; não prova conectividade de todas as câmeras |
| binary_sensor.ezmart_deco_recomendacao | Recomendação da última análise, não medição contínua de interferência |
| binary_sensor.ezmart_deco_canais_alterados | Mudança observada na última aplicação |
| sensor.ezmart_deco_estado | Fase/estado da operação |
| sensor.ezmart_deco_resultado | Resultado da última ação, com ID, verificação e mudanças nos atributos |
| sensor.ezmart_deco_ultimo_pedido | Pedido aceito, em execução ou motivo de recusa |
| sensor.ezmart_deco_bloqueio | Motivo pelo qual o próximo pedido está bloqueado, ou ready |
| sensor.ezmart_deco_ultima_acao | Horário da última ação; durante a ação pode mostrar o início |
| sensor.ezmart_deco_proximo_pedido | Fim do intervalo mínimo; outros bloqueios ainda podem existir |
| sensor.ezmart_deco_ultimo_monitoramento | Horário do último ciclo bem-sucedido |
| sensor.ezmart_deco_leitura_canais | Horário dos canais exibidos |
| sensor.ezmart_deco_canal_2_4 / canal_5_1 / canal_5_2 | Canais atuais observados no principal |
| sensor.ezmart_deco_nos | Quantidade de nós retornados no inventário, não contagem de nós online |
| sensor.ezmart_deco_modo | Modo reportado pelo principal |
| sensor.ezmart_deco_cpu / memoria | CPU/memória em %, quando o diagnóstico está habilitado |

As entidades ficam indisponíveis quando a conexão MQTT do app cai. Botões também ficam indisponíveis enquanto seu pedido não pode ser aceito. A liberação aparece no sensor Pronto para otimizar; não use apenas o relógio do próximo pedido. Canais são consultados periodicamente no modo MQTT sem iniciar ACS. Outros diagnósticos habilitados usam no mínimo 60 segundos. Leituras e última análise são informações distintas.

Um cartão pronto está em `examples/dashboard.yaml`. Adicione um cartão Manual ao painel, cole o conteúdo e confira os IDs. O histórico padrão do Home Assistant pode registrar estados e resultados dessas entidades, conforme suas opções do Recorder. Não há necessidade de um novo painel web dentro do app.

## Acionar de uma automação existente

A ação é a mesma de pressionar o botão:

```yaml
conditions:
  - condition: state
    entity_id: binary_sensor.ezmart_deco_pronto_para_otimizar
    state: "on"
actions:
  - action: button.press
    target:
      entity_id: button.ezmart_deco_otimizar
```

Isso é um trecho para integrar à sua automação, não uma automação completa sem trigger. A condição deve fazer parte da automação. Se duas automações dispararem juntas, o app aceita no máximo uma tarefa; as demais não ficam em uma fila para executar depois.

## Modelo pronto para câmera indisponível

O arquivo `examples/camera_unavailable.yaml` é um blueprint. Copie-o para **a pasta de configuração do Home Assistant**, no caminho `blueprints/automation/tmh/camera_unavailable.yaml`. Essa pasta não é `/local_apps`; pode aparecer como `/homeassistant` ou `/config` conforme o acesso usado. Depois recarregue as automações/blueprints e crie uma automação a partir dele em Configurações → Automações e cenas → Blueprints.

Selecione as câmeras ou sensores que realmente indiquem conectividade, o estado de falha e o tempo mínimo (padrão: 180 s). Para uma câmera, o estado pode ser `unavailable`; para um sensor de conectividade, `off`. Algumas integrações mantêm a entidade camera em idle mesmo sem imagem: nesse caso, use seu sensor de conectividade. Acrescente condições para impedir ações durante manutenção ou falta de energia, se fizer sentido no seu ambiente.

O modelo reage a uma transição de um estado conhecido para a falha, aguarda o tempo contínuo e confere Pronto para otimizar. Ignora a transição inicial de unknown/unavailable, executa uma vez e mantém a automação em modo single por 15 minutos. Não insiste enquanto a câmera continuar indisponível. O tempo do trigger é reiniciado se o HA/automação for reiniciado; não há compensação de pedidos perdidos. Não inclui publicação periódica nem reenvio por timer.

A câmera indisponível é o motivo para pedir análise, não prova de interferência. O Deco pode não recomendar mudança; nesse caso o resultado será `optimization_not_needed`. A aplicação pode interromper temporariamente o Wi-Fi e deixar câmeras indisponíveis durante a troca: o bloqueio do app evita que isso gere outra aplicação imediatamente.

## Repetir até confirmar uma aplicação

O blueprint `examples/camera_until_confirmed.yaml` oferece a política de repetição solicitada para o piloto. Pode ser importado pela URL do arquivo no GitHub. Selecione `camera.cozinha` e confira os IDs padrão das entidades EZmart usados no YAML. Ele funciona com o app **0.8.0-dev.1 já instalado**; importar/editar a automação não exige rebuild.

Após 3 minutos de `unavailable`, aguarda o app ficar disponível e registra os IDs anteriores. Solicita uma análise e eventual aplicação; se houver aplicação pendente de verificação, solicita somente a verificação. Entre tentativas aguarda 15 minutos, além de qualquer bloqueio do app (no X50, pelo menos 30 minutos entre pedidos optimize). Não remove bloqueios nem repete o mesmo comando de aplicação.

Encerra com sucesso somente quando o relatório e o último pedido têm o mesmo ID novo, o Deco aceitou a aplicação (`api_code: 0`) e a verificação está `stable`. Os resultados aceitos são `acknowledged_with_channel_change`, `acknowledged_without_observed_change` (M9) e `acknowledged_without_observed_channel_change` (X50): os dois últimos confirmam aceite e leituras estáveis, **sem afirmar que houve troca de canal ou ganho de desempenho**. Um novo pedido manual concorrente pode satisfazer essa condição; não há exclusividade da automação sobre o dispositivo.

`optimization_not_needed`, recusa, timeout, falha de autenticação e resultado antigo **não** encerram como sucesso. Se o firmware nunca recomendar uma aplicação, a execução pode permanecer ativa indefinidamente. Continua mesmo se a câmera se recuperar, até a confirmação ou cancelamento manual. Para cancelar, desligue esta automação. Reiniciar o HA ou recarregar automações interrompe o loop; esta versão não persiste nem retoma uma execução interrompida. Falhas de configuração continuam aparecendo como erro, nunca como sucesso.

Para validar, confira o rastreamento da automação: deve terminar no passo **Nova aplicação aceita pelo Deco e verificação estável confirmadas**, com um ID diferente do histórico registrado no início. Conferir também os atributos de Último resultado. O botão Executar ações pode efetivamente analisar/aplicar; não é uma simulação. Não use simultaneamente o blueprint de tentativa única para a mesma câmera.

## Controles de execução e recuperação

- Uma tarefa por vez, executada na mesma sequência do monitoramento Deco. MQTT não acessa o Deco em uma segunda thread.
- Intervalo mínimo persistido de 900 s no M9 e 1800 s no X50 entre pedidos optimize, mesmo quando não há recomendação. Configurável até 86400 s. O intervalo de aplicação já existente permanece adicional e não pode ser contornado com outro ID.
- Uma tarefa aceita precisa começar em até 60 s e com monitoramento disponível; caso contrário, é descartada. Não há fila persistente nem retomada de aplicação após reinício.
- MQTT 5 com sessão limpa, sem fila de comandos offline, comandos QoS 0 e sem retenção. Mensagens retidas, marcadas como duplicadas ou pertencentes a outra conexão são ignoradas. Não criar automações que publiquem diretamente comandos retidos.
- A identidade das entidades é persistida em `/data/mqtt_identity.json`. Discovery e estado podem ser retidos; comandos não. Reconexão e o nascimento do Home Assistant republicam entidades/estado, sem pedir otimização.
- O controle de admissão fica em `/data/automation_state.json`; os relatórios M9 continuam no journal da versão 0.7; X50 usa `/data/x50_optimize_journal.json`. Não apagar dados para contornar bloqueios. O histórico de aplicação admite até 1000 IDs M9 ou 128 IDs X50 e bloqueia ao atingir o limite; não descarta reservas antigas automaticamente.
- Se houver `previous_apply_unverified`, usar o botão Verificar última aplicação. Não reaplica, funciona durante o intervalo de otimização e exige no mínimo 30 s entre pedidos de verificação.
- O botão de otimização também respeita a pausa de autenticação TMP (`tmp_auth_cooldown`). No M9, a verificação usa HTTPS e não depende de nova autenticação TMP. No X50, cada leitura usa uma nova sessão TLS/TPAP e também respeita a pausa de autenticação.
- MQTT indisponível não interrompe o monitoramento HTTPS e não gera uma ação automática ao retornar. O app tenta reconectar e obter novamente as credenciais do serviço sem registrar senhas/tokens.

Para suspender os comandos, desabilitar `mqtt_commands_enabled` e reiniciar somente o app. Somente no M9, para voltar ao modo manual antigo, desabilitar `mqtt_enabled` e conferir primeiro `tmp_action` e seus IDs: nesse modo a ação configurada volta a ser considerada na inicialização.

## Validar sem uma otimização desnecessária

1. Conferir versão 0.10.0-dev.1, conexão MQTT e criação do dispositivo/entidades. Nenhuma ação deve iniciar apenas por isso.
2. Como o piloto já tem m9-opt-001 aplicado, pressionar **Verificar última aplicação**. Acompanhar Em execução e Último resultado. Esse teste apenas observa canais e não chama ACS/aplicação.
3. Conferir canais e horário de monitoramento; comparar com o relatório do app. Um resultado reconhecido da ação anterior continua com sua incerteza original, se houver.
4. Configurar a automação escolhendo as entidades reais. Deixar a próxima necessidade real acionar Otimizar se necessário. Para um teste deliberado do botão, considerar que ele pode aplicar uma recomendação positiva e interromper temporariamente o Wi-Fi.
5. Enviar o log MQTT e a linha `Pedido Home Assistant concluído`, sem opções/credenciais. Não é necessário exportar todos os tópicos do broker.

A 0.7 já teve aplicação física validada: canal 149→157 e continuidade do monitoramento. A 0.8 preserva esse protocolo; os novos caminhos de MQTT/HA têm testes locais e de comunicação MQTT 5 em loopback, sem acesso ao Deco. O piloto confirmou a criação das entidades e a conclusão de um pedido verification_only via Home Assistant na 0.8.0-dev.1. A automação de câmera e a revisão dev.2 ainda precisam ser executadas nesse HAOS. O X50 1.30/1.8.4 Router usa os mesmos botões MQTT, com transporte TPAP/TLS e verificação dos canais de backhaul. M5 e outros perfis X50 não estão validados.

Referências oficiais: [MQTT Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery), [botão MQTT](https://www.home-assistant.io/integrations/button.mqtt/), [serviço MQTT do Supervisor](https://contributors.home-assistant.io/apps/guides/services-discovery/), [blueprints](https://www.home-assistant.io/docs/blueprint/schema/).

## Interruptor de visitantes M9

Na 0.10, `guest_control_enabled: true` adiciona `switch.ezmart_deco_visitantes` às 22 entidades. Use as ações normais `switch.turn_on` / `switch.turn_off` do Home Assistant. O interruptor publica o estado observado; comandos não retidos e vinculados à conexão MQTT atual. Não modifica o resultado/ID de otimização usado pelos blueprints acima. [Configuração e piloto físico](DOCS.md#wi-fi-de-visitantes-no-m9--piloto-010). Nenhuma automação de visitantes é criada pelo app.
