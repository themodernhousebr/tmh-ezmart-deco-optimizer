# Comparação de integrações — 14/11/2026

Pesquisa de código e documentação públicos; não substitui teste no M9 AP do piloto. Sem código copiado dos projetos.

| Projeto | Material examinado | Aplicação ao EZmart |
|---|---|---|
| amosyuen/ha-tplink-deco | README e api.py | Sessão e operações serializadas; recuperação após falhas; diagnóstico e controle de polling. README alerta que device_list pode afetar backhaul em alguns firmwares e que escrita Wi-Fi não é confiável entre modelos. Não encontramos nesses materiais comprovação de otimização ACS no M9 AP. |
| roquerodrigo/ha-tplink-deco | README e coordinator.py | Monitoramento local, sessão coordenada, inventário e métricas. Coordenador usa executor para SDK síncrono. Não oferece evidência, nesses arquivos, de otimização de canais homologada no M9 AP. |
| roquerodrigo/tplink-deco-api | wheel 2.1.1 e docs/endpoints/wireless.md | ACS pertence a admin/network_optimize; read consulta estado/resultados e write dispara scan/aplicação. SDK exige Python >=3.14. Disponibilidade por firmware não está garantida. |

Fontes:
- https://github.com/amosyuen/ha-tplink-deco/blob/main/README.md
- https://github.com/amosyuen/ha-tplink-deco/blob/main/custom_components/tplink_deco/api.py
- https://github.com/roquerodrigo/ha-tplink-deco/blob/main/README.md
- https://github.com/roquerodrigo/ha-tplink-deco/blob/main/custom_components/tplink_deco/coordinator.py
- https://github.com/roquerodrigo/tplink-deco-api/blob/main/docs/endpoints/wireless.md

Conclusão: reunir diagnóstico de inventário, capacidades, canais, desempenho e estado ACS antes de implementar mudanças. read aceito não significa otimização executada. Não testar write com parâmetros inferidos.

## Investigação de otimização — resultado do piloto e novo transporte

Piloto 0.3.0-dev.1: ACS em admin/network_optimize retornou HTTP 404, get_support falhou sem classificação suficiente. WLAN e desempenho funcionaram: canais 9/36/149, larguras HT40/HT80/HT80, CPU 0.10 e memória 0.32. Esses valores não medem interferência e não representam cada nó individualmente.

O projeto Zoogara/deco-network-optimize descreve otimização via TMP/App-V2 sobre túnel SSH para serviço loopback 20002. Sua validação declarada é X20-DSL 1.0.13; não comprova M9Plus AP. Essa é uma hipótese plausível para a diferença entre app oficial e HTTPS no piloto, não uma conclusão sobre o transporte do M9.

Arquivos examinados: README.md, PROTOCOL.md e deco_tmp.py. O script imprime token de sessão; o modo report inicia scan. Portanto não será executado diretamente nem incorporado sem revisão. Protocolo separa consulta de estado, início de scan e aplicação. Próximo teste: uma conexão TCP à porta SSH do principal, sem credenciais ou comandos. Depois, se acessível, projetar autenticação própria com credenciais fornecidas apenas na configuração, verificação de identidade, timeout, cooldown e projeção dos resultados. Não usar operações de arquivo, shell, firmware ou exploits.

Fontes:
- https://github.com/Zoogara/deco-network-optimize/blob/main/README.md
- https://github.com/Zoogara/deco-network-optimize/blob/main/PROTOCOL.md
- https://github.com/Zoogara/deco-network-optimize/blob/main/deco_tmp.py

As páginas HTTP do SDK descrevem outro transporte. Sua existência no catálogo não garante presença no firmware. Não insistir em variações de HTTP sem evidência.

## Correção do diagnóstico de transporte

A recusa TCP na porta 22 do M9 não descarta SSH/TMP em outra porta. mthsg/pydeco-tmp documenta SSH na porta 20001 e encaminhamento para loopback 20002, além de diferenças no handshake em relação a Zoogara. Não misturar as duas implementações sem validar a variante do equipamento. Próximo teste limitado a TCP 20001 e banner SSH, sem autenticação.

fullo/deco-mcp declara validação no M9 Plus 1.9.1 e distingue formas web e app. Essa observação é coerente com o HTTP 404 do piloto, mas não comprova qual transporte a otimização do M9 usa. Não usar chaves embutidas, exploits, shell ou operações de arquivo; autenticação futura somente com credenciais do proprietário e fluxo comprovado.

Fontes examinadas:
- https://github.com/mthsg/pydeco-tmp/blob/main/README.md
- https://github.com/fullo/deco-mcp/blob/main/README.md

## Implementação experimental consolidada

Porta 20001 do M9 respondeu SSH-2.0-dropbear no teste do usuário. Foram examinados src/pydeco/transport.py, client.py e opcodes.py de mthsg/pydeco-tmp: ALLOC e network_quality 0x4265, com framing business-v2 e transporte type-5. Implementação própria limitada a sessão e consulta, sem incorporar registro genérico de writes. Não há garantia de que o M9 use essa variante. Frame checksums, correlação de transação, tamanho máximo, fragmentos e prazo de resposta são verificados. Paramiko 5.1.0 fixado; não instalado no Python do Mac.

https://github.com/mthsg/pydeco-tmp/blob/main/src/pydeco/transport.py
https://github.com/mthsg/pydeco-tmp/blob/main/src/pydeco/opcodes.py

Piloto TMP: identidade, autenticação por senha e ALLOC funcionaram no M9. A leitura inicial sem params retornou api_code 1. O exemplo de consulta de Zoogara inclui params.scan_type [network_quality]; dev.2 incorpora esse seletor somente no opcode de leitura 0x4265. Isso ainda não comprova causa do erro nem suporte à consulta; teste pendente.

Piloto 0.5: envio de scan foi classificado scan_rejected, mas a versão não exportou o error_code da resposta. Não concluir incompatibilidade de scan com base nessa classificação: ela também abrangia ausência do campo. Diagnóstico corrigido localmente para separar recusa numérica de formato desconhecido, sem nova tentativa no equipamento. Usuário usa iPhone/iPad; observar tela e fluxo oficial antes de propor captura de tráfego cifrado ou nova chamada.

## Recusa de varredura no M9 — 15/11/2026

Piloto dev.2: opcode 0x4263 com params.scan_type network_quality recebeu error_code -4104. Não foi encontrada definição confiável do código nas buscas públicas; não classificá-lo como credenciais inválidas, falta de permissão ou ausência da função sem evidência adicional.

O catálogo atualizado de mthsg/pydeco-tmp nomeia 0x4263/0x4264 AVIRA_SCAN_START_SET/STOP_SET e associa tanto network_quality quanto network_security. O grupo não está comprovado como equivalente à tela iOS de interferência por bandas. Hipótese: há uma rotina específica de rádio distinta, ou requisitos/contexto ausentes; precisa de análise de firmware/app. A leitura 0x4265 optimal não deve ser apresentada como garantia de pouca interferência.

O catálogo HTTP de fullo/deco-mcp, validado no M9 1.9.1, não contém comandos de scan/ACS/optimization. Isso não prova inexistência no dispositivo; limita apenas essa referência.

Próximo dado necessário: versão de hardware do M9 principal para selecionar firmware público correto e inspecioná-lo offline. Nenhuma alteração de firmware, shell no Deco ou nova varredura foi realizada nesta investigação.

## Análise offline do firmware M9 V2 — evidência direta

Hardware confirmado pelo usuário: V2. Imagem pública oficial: M9Plus_2.0_2.20_en_1.9.1 Build 20250612 Rel. 45690_US_JP_EU_TW_CA_up.bin, 25.003.702 bytes, SHA-256 8a8116039866820cf99814cd529b5deac7e40616f75697883ccaef7be8c6fef2.

Página de origem: https://www.tp-link.com/us/support/download/deco-m9-plus/v2/
Arquivo público: https://static.tp-link.com/upload/firmware/2025/202507/20250729/M9Plus_2.0_2.20_1.9.1%20Build%2020250612%20Rel.%2045690.zip

Extração somente local: reconstrução dos volumes UBI e leitura do SquashFS. Nenhum firmware ou ferramenta foi executado no Deco. Firmware e bibliotecas de análise ficam em work/, excluído do Git.

Bytecode Lua 5.1 da imagem foi inspecionado com parser local, incluindo a extensão de constantes inteiras do firmware. Em luci/model/avira.lua, tmp_scan_start (linhas originais 4225–4338) verifica wireless.is_wlan_optimize_support() no ramo network_quality. O ramo negativo emite don't support wlan_optimize e retorna false com o upvalue 8. A ligação da closure usa o registro 40 da função principal, carregado com a constante -4104. Isso estabelece a origem desse código nesta rotina do firmware, coerente com o teste no piloto.

luci/model/wireless.lua implementa essa verificação consultando wireless.wlan_optimize_support no perfil. Não extrair daí a conclusão de ausência de toda otimização: existe outra implementação em luci/controller/admin/mobile_app/network_optimize.lua, com acs_optimize read/write e arquivos /tmp/network_optimize_scan_in_progress, /tmp/network_optimize_set_in_progress e /tmp/network_optimize_item; a rotina Avira usa /tmp/wlan_optimize_*.

A leitura genérica 0x4265 pode produzir estado default sem demonstrar equivalência com o fluxo oficial de interferência. A rota web HTTPS já testada retornou 404, apesar da presença do controlador mobile_app no firmware. Pendência técnica: mapear como o dispatcher do aplicativo e a tabela de opcodes alcançam o controlador network_optimize. Não tentar 0x4266, que pertence ao mesmo caminho legado recusado, nem alterar perfis de suporte no equipamento.

## Mapeamento ACS confirmado e correção da semântica de read — 15/11/2026

O perfil de fábrica contido na imagem oficial foi decodificado apenas offline. Ele declara network_optimize.support yes e permite 0x424f/0x4250. Não foram lidos backups do usuário, credenciais nem arquivos do Deco físico; nenhum perfil foi alterado no equipamento.

O executável ARM `/usr/bin/tmpsvr` contém uma tabela de registros de 16 bytes (opcode e três ponteiros para strings). Os segmentos ELF foram usados para converter os ponteiros em offsets; os dois registros consecutivos são:

| Offset no arquivo tmpsvr | Opcode | Controlador | Form | Operação |
|---|---|---|---|---|
| 0xd94c | 0x424f | admin.mobile_app.network_optimize | acs_optimize | read |
| 0xd95c | 0x4250 | admin.mobile_app.network_optimize | acs_optimize | write |

Hashes SHA-256 dos arquivos extraídos dessa imagem:

- usr/bin/tmpsvr: 94efa55796b3c3247648d771dbcddce95e06d2f351f95d736569636120b867c0
- usr/lib/lua/luci/controller/admin/mobile_app/network_optimize.lua: 66c5bdbf8a58571a5a76e1a9fa34658a3c27e79c386499c1804adce5958b1a2b
- usr/lib/lua/luci/model/app_wifi.lua: d61fbac0106a3ed5da74e335f4e37387183ba86778360163a3011bc5e201faa9

A closure de read corresponde às linhas de fonte 191–280 (metadados do bytecode). Quando não há operação marcada em andamento, o ramo no PC 122 escreve a marca de scan, e PC 128–129 chama o upvalue 7. A ligação da closure na raiz, PC 70, aponta para o registro 19, função de linhas 87–170. Essa função chama Wifi:get_best_channel_result(true), compara canais, grava o resultado e atualiza scan_count. O registro de write aponta separadamente para linhas 282–315, que chamam a aplicação de canais.

Portanto **read não é consulta passiva neste controlador**. A interpretação anterior nos documentos e no catálogo do projeto estava errada para este firmware. A versão 0.6 remove ACS das sondas HTTPS automáticas e só permite 0x424f como ação scan explícita, com ID persistente. Não tenta expor a rota mobile_app por HTTPS.

O retorno de read contém need_optimize (booleano), optimize_item.channel (booleano) e optimize_time 20. Não é uma lista de canais sugeridos. Quando scan/set está marcado em andamento, pode retornar o arquivo anterior após sleep 2; não há identificador de medição ou timestamp de coleta no retorno. Há também uma janela de 1800 segundos após a aplicação que suprime need_optimize. Assim, aceite e formato válido não provam varredura nova nem ausência de interferência.

Implementação 0.6.0-dev.1: seletor m9_acs explícito; params vazio, pois o callback ignora o argumento e não exige scan_type; uma única requisição 0x424f, sem polling/reenvio. Prazo de resposta completo configurável até 180 segundos, inclusive fragmentos; 0x4250/0x4266 bloqueados. Canal observado por HTTPS antes/depois, com projeção estrita. Falha em observar canais não produz conclusão sobre mudança. Relatórios preservam incerteza de atualização do resultado e nunca apresentam o estado optimal legado como equivalente ao veredito do app oficial.

Essas são evidências estáticas do firmware correspondente, não captura do tráfego do iPhone nem validação do novo caminho no dispositivo físico. Não houve scan, aplicação, modificação de firmware ou alteração de configuração no Deco nesta rodada. Próximo passo: uma rodada manual do pacote consolidado no HAOS, mantendo o histórico das ações.

## Primeira resposta ACS no M9 físico — 15/11/2026, 09:41–09:42

Fonte: log fornecido pelo usuário, versão 0.6.0-dev.1, timezone America/Sao_Paulo. A ação m9-acs-001/m9_acs iniciou a espera às 09:41:50,694; a resposta levou 26,842 segundos. Relatório final às 09:42:19,389: scan_response_received, api_code 0, need_optimize false, optimize_item.channel false, estimated_apply_seconds 20. Nenhum comando de aplicação foi enviado.

As duas observações WLAN reportaram 2,4 GHz canal 9/HT40, 5_1 canal 36/HT80 e 5_2 canal 149/HT80, sem mudança nos campos comparados. Inventário e modo FAP permaneceram disponíveis. A sonda opcional wifi_support continuou retornando api_code 1, sem impedir a chamada ACS.

Conclusão limitada: o transporte e a chamada 0x424F com params vazio funcionaram no M9 V2 do piloto, e o retorno correspondeu ao contrato extraído do firmware. Não houve recomendação nesse retorno. A duração é compatível com processamento, mas não prova medição nova; fresh_scan_confirmed false e result_may_be_cached true são metadados conservadores do nosso código, não flags enviadas pelo firmware. O log não homologa aplicação de canais, ganho de desempenho nem ausência de interferência.

Próxima validação: comparar com Buscar no app oficial em horário próximo, sem Otimizar e sem reenviar o mesmo ID. Nenhuma alteração de runtime foi necessária a partir deste log; atualização apenas da documentação, sem novo rebuild.

## Comparação com o app oficial — 15/11/2026, 09:45

Fonte: captura enviada pelo usuário (nome Captura de Tela 2026-11-15 à(s) 09.45.34.png, tela indicando 09:45) e relato de duração aproximada de 27 segundos. A tela Otimização de Rede mostrou Perfeito, Interferência de Wi-Fi Perfeito, Canal 9 é perfeito em 2,4 GHz e Canal 36 é perfeito em 5 GHz. A captura não contém a banda 5_2/canal 149.

Comparação com m9-acs-001: resultado sem recomendação (need_optimize false) e duração de 26,842 segundos foram concordantes com o app oficial cerca de três minutos depois. Os canais 9/36 também coincidiram. Isso reforça a identificação do caminho ACS correto para esse M9 V2 e valida uma rodada funcional de resultado sem necessidade de otimizar. Não comprova que as requisições iOS sejam idênticas nem que cada resposta contenha uma coleta nova. Não remover os indicadores conservadores de possível cache.

Aplicação de canais não foi testada ou liberada; não há dados de ganho de desempenho. O caso de recomendação positiva ainda precisa ser observado naturalmente para ampliar a validação. Registro de evidência e documentação atualizados, sem mudança de código, rebuild ou nova solicitação de scan.

## Aplicação M9 — implementação 0.7.0-dev.1, 15/11/2026

O usuário autorizou implementar e validar a aplicação da otimização. Os registros anteriores de bloqueio de aplicação descrevem versões até 0.6; a nova liberação é restrita ao caminho M9 e a uma ação manual explícita. Nenhum comando foi enviado ao equipamento durante a implementação local.

A análise estática continua baseada na imagem oficial e nos hashes acima. Em luci/controller/admin/mobile_app/network_optimize.lua, a função de linhas originais 282–315 (metadados do bytecode) corresponde a acs_optimize/write, alcançada pelo opcode 0x4250. Ela lê o arquivo /tmp/network_optimize_item, marca a aplicação em andamento, chama a função de linhas 172–178, atualiza o horário/contador da otimização e remove o arquivo. A função auxiliar chama Wifi:set_best_channel com o conteúdo gerado pela análise. Não existe necessidade demonstrada de enviar uma seleção de canais no payload: o controlador consome o resultado armazenado internamente. A implementação envia config_version e params vazio, coerente com esse callback.

Em luci/model/app_wifi.lua, set_best_channel (linhas 693–742) trata channel_2g, channel_5g, channel_5g_2 e channel_6g. Quando há alteração a salvar, executa /etc/init.d/repacd restart. Isso fundamenta a possibilidade de interrupção temporária do Wi-Fi. Essas operações são internas ao firmware; o EZmart não abre shell nem executa comandos do sistema no Deco.

Um retorno com error_code 0 é tratado como aceite, não como prova de troca ou melhoria. Não há contrato de progresso passivo confirmado para este caminho; chamar novamente 0x424F poderia iniciar outra análise. A implementação faz uma análise antes, aplica somente se need_optimize e optimize_item.channel forem booleanos positivos e acompanha apenas WLAN HTTPS depois. Preserva a ressalva de possível cache e não usa o estado legacy_network_quality como condição de sucesso.

A ação possui journal separado, escrito antes do envio, alvo vinculado por hash de IP/identidade SSH, trava local, intervalo mínimo entre tentativas e bloqueio de nova ação enquanto a observação anterior estiver incompleta. Uma reserva pode existir mesmo se o processo cair antes de transmitir; assume-se resultado incerto e nunca se reenvia automaticamente. Verify usa apenas HTTPS e o registro existente; aceite desconhecido continua desconhecido mesmo após observar mudança. Três observações iguais não comprovam conectividade da mesh inteira ou melhora de interferência.

Testes locais simulados cobrem os ramos positivo/negativo, recusa, formato inesperado, fragmentação, perda de resposta, interrupção do processo, falha de armazenamento, observação parcial, concorrência e recuperação. Não foi realizado build Docker local (Docker indisponível neste Mac), teste de aplicação no Deco, captura de tráfego iOS ou publicação. A rodada física deve usar o pacote 0.7, preservar os dados do app e registrar Diagnóstico tmp. Caso a recomendação continue negativa, a aplicação real continuará pendente; não induzir interferência para forçar esse caso.

## Primeira aplicação aceita e mudança observada — 15/11/2026, 10:08–10:10

Fonte: log do usuário, versão 0.7.0-dev.1 no HAOS do piloto; ID m9-opt-001, backend m9_acs. O registro estruturado está em evidence/2026-11-15-m9-opt-001.json, sem credenciais nem identificadores de clientes. É evidência fornecida pelo operador, não captura independente de pacotes.

A análise foi iniciada às 10:08:44,357 e durou 26,385 segundos. Retornou error_code 0, need_optimize true e optimize_item.channel true. Após observar os canais, o EZmart registrou uma tentativa de aplicação às 10:09:11,471 e recebeu aceite com API 0 em 14,214 segundos. O metadado result_type null indica ausência ou valor null no campo result; não permite distinguir essas duas formas. O contrato de aceite usa o error_code inteiro e não exige result verdadeiro, coerente com esse retorno físico.

Após espera de 30 segundos, as observações terminaram às 10:09:56,540, 10:10:07,117 e 10:10:17,708. Foram três leituras completas consecutivas iguais, sem falhas: 2,4 GHz 9/HT40, 5_1 36/HT80 e 5_2 157/HT80. Antes, 5_2 estava em 149/HT80. Canais de backhaul reportados 9/36/36 e as outras bandas permaneceram iguais. A conclusão foi acknowledged_with_channel_change e o ciclo normal terminou às 10:10:17,755.

Isso valida uma execução completa no M9 Plus V2 AP/firmware 1.9.1: recomendação positiva, envio, aceite e mudança de canal observada no principal. Não mede redução de interferência, throughput, duração real da interrupção Wi-Fi nem disponibilidade de todos os secundários. O resumo da mesh no fim do ciclo foi montado com inventário coletado antes da aplicação, portanto não deve ser interpretado como verificação posterior da mesh inteira. Os indicadores conservadores sobre possível cache da análise permanecem válidos. O bloqueio de repetição e a recuperação após resposta perdida têm testes locais, mas não foram exercitados fisicamente nesta rodada.

Antes dessa execução, o Supervisor revelou espaço inicial ASCII 32 em tmp_optimize_request_id; o campo estava preenchido visualmente, mas não passava na validação estrita. O usuário corrigiu o ID e o backend para m9_acs, sem rebuild. Não se alterou código para ignorar ou transformar IDs persistidos.

Esta atualização registra evidência e corrige a documentação de validação, sem mudança de runtime ou nova versão. O arquivo de distribuição 0.7.0-dev.1 já entregue e seu checksum permanecem imutáveis; ele foi empacotado antes do recebimento deste resultado. Não é necessário copiar documentação para o HAOS nem reconstruir a imagem.

## Continuidade após aplicação — 15/11/2026, 10:10–10:12

O usuário forneceu o trecho seguinte do mesmo processo 0.7.0-dev.1. Ciclo 2: 10:10:47,761–10:10:52,946; ciclo 3: 10:11:22,952–10:11:28,819; ciclo 4: 10:11:58,825–10:12:04,555. Todos consultaram get_device_list e get_device_mode e concluíram normalmente. Não há erro, novo login ou repetição da otimização nesse trecho. O ciclo 5 foi iniciado às 10:12:34,560, mas o trecho termina durante get_device_list; sua conclusão não foi observada.

Essa evidência complementa a aplicação aceita e a mudança 149→157: o monitoramento HTTPS continuou na sessão existente por três ciclos completos após a verificação. A ausência de novo resumo da mesh é compatível com o código que só o registra quando o resumo muda, mas não substitui medições de conectividade de cada nó. O fluxo de análise, aplicação, observação e continuidade de monitoramento está validado nesta execução do piloto M9 V2 em AP. Não implica estabilidade prolongada, ganho de desempenho ou homologação com X50/M5 como principal.

Dados acrescentados a evidence/2026-11-15-m9-opt-001.json. Sem alterações de código, pacote ou necessidade de rebuild.

## Acionamento por eventos no Home Assistant — 0.8.0-dev.1

Pedido do usuário: entidade para acionar otimização quando uma automação identificar uma necessidade, por exemplo câmeras indisponíveis, sem otimizações indiscriminadas. Integração MQTT existente confirmada pelo usuário. Implementado dispositivo via MQTT Discovery, botão de pedido de otimização e botão de verificação, com sensores de resultado/disponibilidade e blueprint opcional. O app não observa câmeras diretamente e não cria cronograma de otimização. A automação HA decide quando solicitar; o Deco ainda precisa recomendar mudança antes da aplicação.

Referências consultadas: https://www.home-assistant.io/integrations/button.mqtt/ ; https://www.home-assistant.io/integrations/mqtt/ ; https://www.home-assistant.io/integrations/sensor.mqtt/ ; https://contributors.home-assistant.io/apps/guides/services-discovery/ ; https://contributors.home-assistant.io/apps/supervisor-api/ ; https://www.home-assistant.io/docs/blueprint/schema/ ; https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html . O Supervisor permite GET /services/mqtt mediante services mqtt:want, sem hassio_api adicional. Discovery usa default_entity_id como sugestão, unique_id persistente e agrupamento por dispositivo.

Comandos usam MQTT 5, QoS 0 e sem retenção. A assinatura solicita retainAsPublished e retainHandling 2; comandos retidos ou duplicados e payload de conexão antiga são recusados. A sessão é limpa a cada reconexão e os IDs de aplicação são gerados localmente. O pedido é persistido antes de ficar disponível ao worker único. Reinício não retoma uma fila ou escrita; o cooldown e as reservas do protocolo são preservados. Verificação separada nunca solicita análise/apply. Mensagens de nascimento do HA só provocam novo anúncio de entidades/estado.

Testes incluem servidor MQTT 5 mínimo em loopback e biblioteca Paho real, isolada em /private/tmp, sem tocar Deco ou HA. Isso valida a comunicação com Paho e as propriedades de recepção, não substitui teste com o Mosquitto e a integração HA reais. O código do protocolo de otimização permanece o mesmo validado na 0.7. Blueprint e painel são artefatos para instalação pelo usuário; nenhuma automação real foi criada ou habilitada durante o desenvolvimento.
