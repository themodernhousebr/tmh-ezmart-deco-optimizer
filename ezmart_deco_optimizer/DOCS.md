# EZmart Deco Optimizer — 0.10.0-dev.2

Monitoramento local e otimização sob demanda para os perfis testados: M9 Plus V2, firmware 1.9.1 Build 20250612 Rel. 45690, em AP; e X50 hardware 1.30, firmware 1.8.4 Build 26051413 Rel. 27460, em Router. Outros perfis X50 e M5 principal não estão validados.

Esta versão reúne análise, aplicação condicional, verificação e recuperação de resultado incerto. A aplicação pode interromper temporariamente o Wi-Fi. Não há otimização automática por intervalo ou ao surgir interferência. O padrão continua `tmp_action: read` e `tmp_enabled: false`.

## Home Assistant e automações

A 0.8 adiciona MQTT Discovery, botões para pedidos sob demanda e sensores de acompanhamento. Consulte [AUTOMATION.md](AUTOMATION.md) para configurar tudo em uma atualização, instalar o modelo para câmeras e adicionar o cartão de painel. Com mqtt_enabled true, a ação TMP de inicialização descrita abaixo é desativada; pedidos passam a vir dos botões e não exigem reinício. Não há otimização periódica.

## Instalar uma vez

Para instalações pela loja, atualizar os repositórios e usar **Atualizar** na página do EZmart. Essa operação constrói a nova imagem. Não desinstalar: preserve opções, identidade MQTT e histórico. Na instalação local de desenvolvimento, copiar a pasta inteira `ezmart_deco_optimizer` para `/local_apps/ezmart_deco_optimizer` e atualizar a loja antes de usar Atualizar. Não fazer atualização e reconstrução em sequência.

Na cópia local, incluir todos os módulos, inclusive `guest_wifi.py`. Confirmar `Versão 0.10.0-dev.2` no log. O Supervisor pode fazer downloads adicionais; não há garantia de duração do build. Alterações nas opções abaixo exigem apenas salvar e reiniciar **este app**, sem novo rebuild.

## Wi-Fi de visitantes no M9 — piloto 0.10

Recurso opcional, inicialmente restrito ao principal M9 Plus V2 com firmware **1.9.1 Build 20250612 Rel. 45690**, modo AP/FAP. Implementação e testes automatizados concluídos; ligar/desligar no equipamento físico ainda precisa ser validado. O X50 não recebe este interruptor nesta etapa.

Use a rede de visitantes já configurada no app Deco, com o nome, senha, isolamento e duração desejados. Acrescente à configuração existente do EZmart:

```yaml
guest_control_enabled: true
```

Mantenha `deco_model: m9_plus`, `mqtt_enabled: true`, `mqtt_commands_enabled: true` e `tmp_enabled: true`, com as mesmas credenciais e impressão SSH do M9. A opção também aparece em “Mostrar opções de configuração opcionais não utilizadas”. Salve e reinicie apenas o EZmart. Surge **Wi-Fi de visitantes**, ID sugerido `switch.ezmart_deco_visitantes`, no mesmo dispositivo MQTT. As 22 entidades existentes preservam suas identidades.

- **Ligar** solicita visitantes em 2,4 e 5 GHz; **desligar** desativa ambas. Não há configuração por nó: o comando vai ao principal, que gerencia a malha.
- O estado ON significa que pelo menos uma dessas bandas está ativa; os atributos `bands` mostram cada uma. O estado vem da leitura do principal, também refletindo mudanças feitas no app Deco. Não confirma individualmente a emissão ou o acesso à internet em cada satélite.
- O app envia uma única escrita parcial, preservando o isolamento lido e omitindo SSID, senha, segurança, VLAN, duração e rede principal. Compara essas configurações em memória após a mudança. Nenhuma delas é publicada no MQTT ou nos relatórios. O firmware pode renovar o prazo de uma rede temporária ao ligá-la.
- Desde a `0.10.0-dev.2`, a preparação e a confirmação usam a leitura WLAN completa via TMP, que inclui isolamento. A consulta HTTPS permanece no monitoramento periódico. A `dev.1` tentava validar a resposta HTTPS incompleta e podia encerrar com `guest_configuration_unavailable` antes de enviar a alteração.
- Confirmação exige duas leituras consecutivas com o estado solicitado e as configurações protegidas iguais. São até 12 leituras, espaçadas em 5 segundos, além do tempo das consultas. Perda de resposta nunca repete a escrita. Não há comando de visitantes na inicialização ou reconexão MQTT.
- Intervalo próprio de 60 segundos; comandos recusados não ficam na fila. Visitantes e otimização usam o mesmo trabalhador; uma aplicação de canais pendente bloqueia a mudança de visitantes. O histórico de otimização permanece separado.

No primeiro teste, confira o estado inicial no HA e no app Deco. Acione uma vez, aguarde a confirmação e confira conexão de um visitante também por um secundário. Após o intervalo, teste a operação inversa. O firmware pode recarregar o Wi-Fi ao aplicar a mudança; execute em um momento apropriado para uma possível interrupção breve.

Os atributos do interruptor mostram `result`, `blocked_reason`, `write_attempted`, `observed_at`, `bands` e `verification_scope: main_deco`. Resultados:

| Resultado | Significado |
|---|---|
| `guest_enabled_verified` / `guest_disabled_verified` | Comando aceito e estado confirmado por duas leituras |
| `guest_already_set` | Estado já correspondia ao pedido; nenhuma escrita |
| `guest_state_confirmed_without_ack` | Estado confirmado, mas aceite do comando não confirmado |
| `guest_outcome_uncertain` | Não houve confirmação suficiente; não reenviou o comando |
| `guest_settings_changed` | A comparação detectou mudança adicional; conferir no app Deco, sem reversão automática |
| `guest_profile_not_validated` | Modelo, hardware, firmware ou modo fora do piloto; nenhuma escrita |
| `guest_configuration_unavailable` | Nenhuma escrita; `stage` no relatório diferencia falha de leitura (`configuration_read`) de configuração incompleta/não reconhecida (`configuration_validation`) |
| `interrupted_no_replay` | Houve reinício durante o pedido; nenhuma retomada automática |

Relatórios filtrados ficam em `/data/guest_wifi_report.json` e `/data/guest_wifi_state.json` **dentro do EZmart**. O `/data` do Terminal é outro diretório. Para o piloto, os atributos do interruptor e os logs de conclusão já permitem compartilhar o resultado sem credenciais. Desabilitar `guest_control_enabled` remove o interruptor da descoberta MQTT; não altera a rede de visitantes no Deco.

## Configuração diária por modelo

A instalação nova tem 10 campos principais. Não é necessário preencher os campos do outro modelo, nem definir IDs e prazos. Opções antigas permanecem aceitas; configurações já salvas não são removidas automaticamente. Os campos opcionais seguem o [schema do Supervisor](https://developers.home-assistant.io/docs/apps/configuration/#options--schema).

| Campo | M9 Plus V2 | X50 1.30 / 1.8.4 Router |
|---|---|---|
| `deco_model` | `m9_plus` (padrão; preserva instalação antiga) | `x50` |
| `host` | IP do M9 principal | IP do X50 principal |
| `tmp_email`, `tmp_password` | Conta proprietária desta rede | Conta proprietária desta rede; não reutilizar a de outra instalação |
| `password` | Senha de acesso HTTPS | Vazio; ignorado |
| `tmp_host_key_sha256` | Chave SSH previamente conferida | Vazio; ignorado |
| `x50_certificate_sha256` | Vazio; ignorado | `certificate_sha256` conferido no diagnóstico deste X50 |
| `tmp_enabled` | Ativar para permitir ações | Ativar para permitir ações |
| `mqtt_enabled` | Ativar para publicar entidades | Ativar para publicar entidades |
| `mqtt_commands_enabled` | Ativar quando quiser aceitar os botões/automações | Ativar quando quiser aceitar os botões/automações |

O servidor MQTT e suas credenciais são obtidos automaticamente do serviço Mosquitto do Supervisor. Os campos `mqtt_host`, `mqtt_username`, `mqtt_password`, `mqtt_port` e `mqtt_tls` só são necessários para um broker alternativo. Não confundir a conta MQTT com a conta Deco.

No M9 existente, preserve `tmp_scan_backend: m9_acs`. Numa instalação nova com MQTT, ele é o padrão interno quando a opção está ausente. Campos `tmp_action` e IDs só servem para o modo manual M9 abaixo; no X50 são ignorados. O X50 nunca executa otimização na inicialização.

O padrão interno do monitoramento é 30 s no M9 e mínimo 60 s no X50. Para o X50, análise/aplicação/verificação usam 120/60/120 s quando ausentes; a conexão usa 10 s. Verificação tem espera fixa de 30 s, intervalo de 10 s e até seis leituras; `tmp_verify_timeout_seconds` tem máximo efetivo de 300 s. As opções M9 de atraso/intervalo de verificação e diagnóstico HTTPS não se aplicam ao X50.

## Primeiro uso do X50 no Home Assistant

1. Instalar/atualizar o pacote 0.9 uma vez. Selecionar `x50`, IP, conta desta rede e certificado conferido. Ativar MQTT; manter `mqtt_commands_enabled: false` durante a conferência inicial.
2. Salvar e iniciar. Conferir log X50 TPAP/TLS, inventário, modo Router e sensores **Canal backhaul**. Não devem aparecer análise/aplicação apenas por iniciar ou reconectar MQTT.
3. Para habilitar ações sob demanda, ativar `tmp_enabled` e `mqtt_commands_enabled`, salvar e reiniciar o app. A automação pode chamar o mesmo botão Otimizar se necessário usado no M9.
4. Aguardar pelo menos 30 minutos desde a última aplicação pelo diagnóstico. Preservar `/config/ezmart_x50_apply_journal.json`; ele não é importado pelo app, cujo registro é `/data/x50_optimize_journal.json`. Não usar ambos simultaneamente. Resolver primeiro qualquer aplicação incerta do diagnóstico.
5. Após uma necessidade real, conferir aceite API 0, verificação stable e ID do novo pedido. Verificar última aplicação observa somente a tentativa registrada **neste app**; antes da primeira aplicação pelo app, fica indisponível.

O X50 bloqueia ações se modo, hardware ou firmware diferirem do perfil validado. Não há tentativa automática de SSH, HTTPS ou certificado diferente. Toda autenticação é reservada em disco antes de conectar; falha deixa pausa de 15 minutos, inclusive após reinício. Aplicações e pedidos de otimização têm mínimo de 30 minutos; resultado incerto bloqueia nova aplicação e permite apenas recuperação por leitura. Canais reportados são de backhaul; o perfil atual não oferece número de canal host/radio nem consulta de CPU/memória pelo transporte TPAP.

A seção seguinte documenta exclusivamente o modo manual M9, preservado para instalações anteriores.

## Configurar aplicação manual M9 sem MQTT

Manter host, credenciais HTTPS/TMP e impressão digital SSH já conferidos. Fechar a tela de otimização do app Deco durante a rodada. Configurar:

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

Salvar e iniciar/reiniciar apenas o EZmart Deco Optimizer. Não reiniciar HAOS, Proxmox ou Deco. A ação roda uma vez por processo e por ID persistido, não a cada `poll_seconds`.

O modo optimize envia uma análise M9, exige resposta reconhecida com `need_optimize: true` e `optimize_item.channel: true`, observa os canais e só então envia uma única aplicação. Se não houver recomendação, termina em `optimization_not_needed`. Não força canais nem aplica um resultado antigo do journal. A nova análise ainda pode receber cache do próprio firmware; não há identificador de coleta para eliminar essa incerteza.

Antes de enviar aplicação, o ID e a reserva são sincronizados em disco. Uma queda de conexão ou reinício não provoca reenvio, nem quando a resposta se perde. O mesmo ID devolve `already_requested` antes de autenticar via TMP. Outro ID fica bloqueado se uma tentativa anterior não tiver verificação concluída ou ainda estiver dentro de 900 segundos. O registro associa ID, IP e identidade SSH; reutilizar ID em outro alvo é recusado. Uma trava local impede duas aplicações/verificações simultâneas no mesmo diretório de dados.

## Verificação e recuperação

Após resposta ou timeout da aplicação, aguarda-se `tmp_verify_delay_seconds`. Em seguida, o app consulta canais via HTTPS na sessão existente, sem novo scan, login TMP ou aplicação. Três leituras consecutivas iguais, contendo todas as bandas anteriormente observadas, concluem a verificação como `stable`. Uma leitura incompleta ou indisponível reinicia a contagem. São preservados os contadores e a última observação filtrada.

Essa verificação mede apenas os canais reportados pelo principal, não a interferência, a velocidade ou a conectividade de todos os secundários. O próximo ciclo normal consulta o inventário. Não se chama ACS novamente para “confirmar”, pois essa chamada pode iniciar outra análise e o firmware suprime recomendações por 1800 segundos após aplicação.

Se a aplicação perder a resposta, se o app for interrompido ou se a leitura HTTPS não se recuperar, manter o ID e mudar:

```yaml
tmp_action: verify
tmp_optimize_request_id: m9-opt-001
```

Salvar e reiniciar o app. `verify` retoma somente observações HTTPS a partir do registro persistido; não abre SSH/TMP e não reenvia comandos. É permitido durante o cooldown de aplicação. Se a sessão HTTPS anterior expirou, o reinício permite autenticar novamente, respeitando o cooldown de autenticação. Os prazos podem ser ampliados nas opções sem rebuild. Resultado ausente ou registro inválido bloqueia a operação; não apagar arquivos para contornar o bloqueio.

Após concluir, voltar a `tmp_action: read` ou `tmp_enabled: false`. Outra otimização deliberada exige um ID novo, análise positiva, verificação anterior concluída e intervalo mínimo de 15 minutos desde a última tentativa de aplicação. Não alterar o ID para tratar um resultado incerto.

## Relatórios de aplicação

| Campo / estado | Significado |
|---|---|
| optimization_not_needed | Análise válida sem recomendação; nenhuma aplicação enviada |
| preflight_failed | Análise recusada, desconhecida ou sem resposta; não aplica |
| baseline_unavailable | Não foi possível observar canais antes da aplicação; não aplica |
| apply_command_attempted | Reserva persistida para tentativa de envio; true não comprova entrega ao dispositivo |
| apply_acknowledged | Resposta com error_code inteiro igual a zero |
| apply_rejected | Resposta com código numérico de recusa; não tenta outro comando |
| apply_outcome_uncertain / apply_dispatch_uncertain | Resposta ausente/desconhecida ou processo interrompido; usar verify, sem reaplicar |
| verification.state: stable | Três observações completas consecutivas com os mesmos canais |
| acknowledged_with_channel_change | Aceite recebido e mudança observada; não comprova melhoria de desempenho |
| acknowledged_without_observed_change | Aceite recebido, sem mudança de canal observada |
| unconfirmed_with_channel_change / unconfirmed_without_observed_change | Observação concluída, mas sem aceite reconhecido; preservar incerteza |
| verification_incomplete | Observações interrompidas ou prazo esgotado; usar verify com o mesmo ID |
| previous_apply_unverified | Há tentativa anterior sem observação concluída; verify com previous_request_id |
| apply_cooldown | Espera entre tentativas, inclusive com IDs diferentes |
| action_storage_or_state_error / invalid_optimize_journal | Falha de persistência ou registro inválido; sem repetição automática |
| target_mismatch | ID pertence a outro IP ou identidade SSH |

Uma recusa explícita continua `apply_rejected` mesmo que outra causa altere canais durante a observação. Metadados `shape` descrevem apenas tipos e presença de campos, sem respostas brutas. Não há estado que afirme ganho de velocidade.

Arquivos internos do app:

- `/data/status.json`: último ciclo e diagnósticos, cada um com seu horário.
- `/data/tmp_optimize_report.json`: última ação de aplicação/verificação.
- `/data/tmp_optimize_journal.json`: histórico persistente, até 1000 IDs, com reservas e resultados.
- `/data/tmp_scan_report.json` e `/data/tmp_scan_journal.json`: análise manual isolada, histórico separado.

Esse `/data` pertence ao contêiner do EZmart, não ao Terminal & SSH. Para validar o piloto, enviar a linha `Diagnóstico tmp` dos logs. Não enviar `options.json`, senhas, e-mail ou tokens.

## Demais modos

`tmp_action: scan` com `tmp_scan_backend: m9_acs` e um `tmp_scan_request_id` exclusivo envia apenas uma análise `0x424F`, nunca aplicação. O prazo vale para a resposta completa, inclusive fragmentos. Não há polling dessa operação ativa. `scan_response_received` indica formato reconhecido; `fresh_scan_confirmed: false` e `result_may_be_cached: true` mantêm explícitos os limites do firmware. `estimated_apply_seconds` vem da estimativa do dispositivo, não é a duração da análise nem uma contagem regressiva para aplicar.

`tmp_action: read` lê somente o estado legado `0x4265`, identificado como `legacy_network_quality`, sem equivalência com a tela de otimização oficial. O scan legado `0x4263` foi recusado com -4104 no M9 do piloto; não repeti-lo nesse equipamento. O backend legacy é mantido para pesquisa em outros equipamentos, sem homologação adicional, e não permite optimize/verify. O comando legado de aplicação `0x4266` continua bloqueado.

A opção `diagnose_acs` é aceita por compatibilidade, mas não faz requisições: o nome read não garante ausência de ações. Se habilitada, o log informa `disabled_active_operation`.

## Opções

| Opção | Padrão | Função |
|---|---|---|
| host | vazio | IP do principal |
| username / password | admin / vazio | Login HTTPS, preenchido no Supervisor |
| poll_seconds | 30 | Pausa após cada ciclo de inventário; 30–86400. Usar 900 para operação prolongada |
| diagnose_acs | false | Obsoleta: aceita, mas não faz chamadas |
| diagnose_wifi_support | true | Capacidades Wi-Fi; no piloto retorna erro API 1 |
| diagnose_channels / diagnose_performance | false | Diagnósticos de canais e CPU/memória |
| diagnostic_seconds | 0 | Zero: diagnósticos uma vez por processo. Para repetir, prefira 300 ou mais |
| tmp_enabled | false | Habilita a ação TMP selecionada ou a verificação HTTPS |
| tmp_action | read | read, scan, optimize ou verify; uma ação por execução do app |
| tmp_scan_backend | legacy | Para o M9 do piloto, selecionar m9_acs explicitamente |
| tmp_scan_request_id | vazio | ID de análise isolada; não usado em optimize/verify |
| tmp_optimize_request_id | vazio | ID de aplicação; manter o mesmo para verify. Ambos os IDs: 1–48 letras, números, hífens ou underscores |
| tmp_scan_timeout_seconds | 90 | Prazo da resposta de análise M9, 30–180 s |
| tmp_apply_timeout_seconds | 60 | Prazo da resposta à aplicação, 10–180 s. Timeout nunca provoca reenvio |
| tmp_verify_delay_seconds | 30 | Espera antes da observação, 5–180 s |
| tmp_verify_timeout_seconds | 120 | Janela de observação após a espera, 30–600 s; requisição já em curso pode acrescentar seu próprio timeout |
| tmp_verify_interval_seconds | 10 | Pausa entre observações, 5–60 s. A janela precisa comportar pelo menos dois intervalos |
| tmp_timeout_seconds | 10 | Conexão/autenticação SSH, 5–30 s |
| tmp_email / tmp_password | vazio | Conta proprietária TP-Link, distinta do admin HTTPS |
| tmp_host_key_sha256 | vazio | SHA-256 hexadecimal, 64 caracteres, da chave SSH conferida |

## Sessão e privacidade

A sessão HTTPS é reutilizada em memória. Cooldown de autenticação de 900 segundos é persistido antes do login e removido após sucesso. Falhas conhecidas de transporte preservam a sessão; falhas de autenticação e erros desconhecidos impõem pausa. Reinício após autenticação bem-sucedida não exige 15 minutos. O cooldown de aplicação é separado de `poll_seconds` e dos controles de autenticação.

TMP usa SSH 20001 com túnel para 127.0.0.1:20002, sem shell. Nos modos read/scan, a primeira conexão sem impressão digital apenas informa a chave pública, sem transmitir senha. Conferir a identidade em rede confiável e configurar a chave antes da autenticação. Optimize exige uma impressão digital válida já configurada. Mudança da chave bloqueia credenciais. Há uma única tentativa de senha, sem fallback interativo; cooldown TMP separado após falha. Token ALLOC é descartado.

Logs e relatórios não incluem respostas brutas, exceções, senhas, e-mail, tokens ou identificadores de clientes. Canais são projetados por lista fixa; o vínculo com o alvo usa hash interno. O SDK HTTPS não valida o certificado, portanto usar em rede local confiável. Credenciais nas opções e backups do Supervisor são privadas. Dependências rodam no contêiner, sem instalar o SDK no Mac.

## Validação e limites

O caminho ACS foi identificado por análise offline do firmware oficial correspondente ao M9 V2. A 0.6 foi construída no HAOS e recebeu `api_code: 0`, `need_optimize: false` em 26,842 segundos, com canais 9/36/149 inalterados. O app Deco, cerca de três minutos depois, mostrou “Perfeito”, canais 9/36 e aproximadamente 27 segundos. Isso valida uma rodada de análise, não aplicação nem ganho de desempenho.

A 0.7 tem 50 testes locais de envio único, recusa, perda de resposta, interrupção, journal, concorrência, observação parcial, retomada e ausência de segredos nos relatórios. Em 15/11/2026, a execução no HAOS foi confirmada pelo log do piloto. A ação m9-opt-001 recebeu recomendação positiva em 26,385 segundos e aceite da aplicação (API 0) em 14,214 segundos. A banda 5_2 passou de 149 para 157; três leituras posteriores concordaram, sem falhas. As bandas 2,4 GHz e 5_1 permaneceram em 9 e 36; larguras e canais de backhaul reportados não mudaram. Conclusão: acknowledged_with_channel_change. Esta execução valida aceite e mudança observada no principal, sem comprovar ganho de velocidade/interferência ou saúde de todos os secundários. Recuperação de resposta perdida e bloqueio de repetição permanecem cobertos por simulação, sem teste físico específico.

Há entidades MQTT e acionamento por automações na 0.8, descritos em AUTOMATION.md. Não há painel web próprio nem otimizações agendadas pelo app. Instalação por repositório personalizado e limites de compatibilidade estão no README do repositório. Esta distribuição usa construção local pelo Supervisor; imagens GHCR não são publicadas nesta revisão.
