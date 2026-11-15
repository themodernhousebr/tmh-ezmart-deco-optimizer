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


## X50 dos clientes — análise offline de 15/11/2026

Cenário informado: X50 principal em modo roteador, DHCP ativo e modem da operadora a montante. A foto identifica uma unidade **Deco X50 (US), Ver: 1.0**. O firmware instalado ainda era desconhecido nesta primeira etapa; a confirmação posterior está registrada abaixo. Não generalizar essa etiqueta para todos os clientes.

Referência baixada da [TP-Link Brasil, X50 V1](https://www.tp-link.com/br/support/download/deco-x50/v1/): **1.8.0 Build 25102213 Rel. 43970**, publicada em 08/12/2025. ZIP SHA-256 `f5891e871865d6213b1a6b05aa4d65051e19a0a37bee0b6550d9033fb4092a12`; binário original `075ed06721207220b722451bf33cbe8ed10f7c981e15bbe71daaa35dcdcb2ea6`.

A assinatura RSA-PSS foi verificada offline com chave pública de verificação presente em firmware oficial. O formato Cloud foi interpretado conforme a [implementação de referência](https://github.com/robbins/tp-link-decrypt/blob/main/src/rsa_verify/rsaVerify.c). Nenhum firmware foi executado/instalado, nenhum equipamento acessado. Downloads, decodificação e arquivos extraídos ficaram em pasta temporária externa às pastas sincronizadas; não integram este repositório.

No `tmpsvr` AArch64, resolvendo relocations de PT_DYNAMIC:

| Offset do registro | Opcode | Controlador | Método | Operação |
|---|---|---|---|---|
| 0x1c780 | 0x424F | admin.mobile_app.network_optimize | acs_optimize | read |
| 0x1c7a8 | 0x4250 | admin.mobile_app.network_optimize | acs_optimize | write |

O controlador Lua mantém resultado da análise e aplicação separados. A aplicação consome a recomendação armazenada e chama `set_best_channel`; há espera mínima interna de 1800 segundos após aplicação. A rotina Wi-Fi também pode reiniciar repacd e alterar largura de 2,4 GHz. Isso reforça compatibilidade de protocolo, **não homologa autenticação, transporte ou aplicação no X50 real**. SHA-256 de tmpsvr: `8bb230007719da1bc309ac1a564c00d3ba24259940225f86046b2e0217e5c197`; controlador: `48755fc546bd6ebca912b70c9bd3202f5ba8b7b20bc9326aaea4c88fc92090ab`.

Antes da etiqueta, também foi examinada a imagem oficial [X50 V3 1.7.0](https://www.tp-link.com/br/support/download/deco-x50/v3/), com rotinas ACS presentes. Essa variante não substitui a V1 identificada. Próxima validação exige firmware instalado e teste controlado no X50 principal; nenhum comando novo foi enviado nesta investigação.

## X50 principal com firmware 1.8.4 — transporte diferente confirmado parcialmente

Em 15/11/2026, o operador forneceu a tela do X50 principal com **1.8.4 Build 26051413 Rel. 27460** e informou que a etiqueta US V1.0 pertence à mesma unidade. As imagens apresentam identificadores MAC diferentes; a associação é informação do operador, não validação independente da revisão física. Não publicar os identificadores, nem inferir que V1.0 e V1.30 são intercambiáveis.

A [página oficial brasileira X50 V1.30](https://www.tp-link.com/br/support/download/deco-x50/v1.30/) oferece exatamente esse build, publicado em 26/06/2026. O [pacote oficial](https://static.tp-link.com/upload/firmware/2026/202606/20260626/X50v1_3_X55v1_3_XM50v1_HB6300v1_W8100v1_WM9000v1-ver1-8-4-P1%5B26051413-rel27460%5D.zip) contém uma imagem identificada como X50v1_3/X55v1_3 e outros modelos. Foi baixado para análise offline do software correspondente ao print, sem recomendar ou realizar atualização de firmware.

SHA-256:

- ZIP: `25365baf5fb55d41eab6af9ecbd79d2f350e629dd2bb006ced920e57f7eafb07`.
- Binário original: `f95c0003e52d3b2b1bd2f38315403cb2465ec1d9195df81fa5fa5e9c368337ce`.
- Binário decodificado após verificação RSA-PSS: `7c8c8e738bec2fb4419f3a68daabf62775a21c42f3fa953ef1ee23cc6ab408c5`.
- `/usr/bin/tmpsvr`: `fb22144e28c45a8e62a76c1683e47e08a6611578924760be673014fe08bab815`.
- Controlador `mobile_app/network_optimize.lua`: `48755fc546bd6ebca912b70c9bd3202f5ba8b7b20bc9326aaea4c88fc92090ab`.
- Modelo `app_wifi.lua`: `ba2873e0c5b04877b43e936b663e37300cca29682375a99f7110c92dc58d527f`.

O registro TMP mantém `0x424F → acs_optimize/read` e `0x4250 → acs_optimize/write` nos offsets `0x1c780` e `0x1c7a8`. O controlador de otimização é byte a byte idêntico ao da imagem 1.8.0 analisada anteriormente. A função `set_best_channel` (metadados de linhas 1054–1126 na 1.8.4) também mantém instruções e constantes, desconsiderando números de linha. Existem diferenças em outras funções Wi-Fi, incluindo seleção por interface e consulta de capacidades; não se conclui identidade integral de firmware ou de comportamento de rádio.

O transporte distingue os cenários:

- `/etc_ro/init.d/tpap` inicia antes do Dropbear, cria `/tmp/tpap_support` como `v1` se ausente e executa `tpapsvr`.
- `/etc_ro/init.d/dropbear` não inicia a instância normal quando esse indicador difere de `v0`, exceto no modo de fábrica. Nenhuma opção de compatibilidade ou modo de fábrica foi alterada no equipamento.
- A função Lua `libtpap.get_port` resolve a constante `30001`; o servidor contém TLS e autenticação SPAKE2+, além de um canal TMP. Isso exige um transporte/autenticação próprios; trocar a porta do cliente SSH não basta.
- O firmware examinado contém o controlador ACS em `admin/mobile_app/network_optimize`, mas não um arquivo de controlador `admin/network_optimize`. Não foi demonstrada uma rota HTTPS alternativa para otimização.

Testes feitos pelo operador no Terminal/SSH do HA na rede do X50: TCP/20001 retornou `Connection refused`, código 1; TCP/30001 conectou, código 0. Não foram enviados login, análise ou aplicação. A porta aberta é coerente com TPAP no firmware, mas não prova handshake, identidade, autenticação nem operação ACS funcionando no dispositivo.

Direção de compatibilidade: preservar o transporte SSH/TMP validado no M9 e acrescentar TPAP separadamente, compartilhando as regras de recomendação, IDs persistentes, cooldown, verificação e entidades MQTT. A 0.8.0-dev.2 ainda não implementa TPAP. Nenhuma mudança de runtime ou rebuild é necessária para registrar estes resultados; a homologação do X50 continua pendente.

## X50 — handshake TLS observado e diagnóstico independente

Em 15/11/2026 o operador instalou OpenSSL no app Terminal/SSH e executou `s_client -connect 192.168.68.1:30001 -brief`, com entrada encerrada e timeout externo de oito segundos. A saída confirmou `CONNECTION ESTABLISHED`, TLS 1.3, `TLS_AES_256_GCM_SHA384`, chave temporária X25519, certificado `CN=Deco X50(US), OU=HOMEWIFISYSTEM` e código final 0. A cadeia apresentou uma CA intermediária HOMEWIFISYSTEM CA da TP-LINK SYSTEMS INC.

O mesmo teste reportou `unable to get local issuer certificate` (código de verificação 20). O código final 0 do s_client sem `-verify_return_error` não prova confiança no certificado. A conexão está demonstrada, mas a cadeia ainda não foi validada no aparelho. Nenhum login ou comando ACS foi enviado nesse teste.

Na imagem oficial 1.8.4 foi localizada `/etc/tss/root_ca.pem`, certificado público autoassinado com sujeito TP-LINK SYSTEMS DEVICE ROOT CA. SHA-256 do certificado DER: `fa85d87d218313286f0ef67628d177a1270a446dbd1554b7d47d86aee2fdb216`. Validade declarada: 22/11/2024 a 15/11/2054. Apenas o certificado público foi extraído; nenhuma chave privada foi lida. Ele foi incorporado ao diagnóstico independente `tools/x50_tls_probe.py`, com validação obrigatória da cadeia, nome de produto e opção de comparação da impressão individual. A correspondência desta raiz com a cadeia do X50 físico permanece pendente do diagnóstico do operador.

O diagnóstico usa Python padrão, não depende da imagem do app e não altera seu runtime, versão, transporte M9 ou entidades MQTT. Limita-se a TLS e fecha a conexão antes de qualquer mensagem de aplicação. Uma falha de certificado não provoca reconexão sem validação. O nome do produto não é uma identidade individual nem prova correspondência com um IP; esses limites constam explicitamente da saída.

A análise estática de libtpap identificou mensagens de autenticação SPAKE2+ (`pake_register`, `pake_share`, `pake_confirm`) e enquadramento próprio sobre TLS; isso não constitui implementação ou validação de login. A descoberta do firmware contém rotinas de detecção/compatibilidade que podem persistir estado e reiniciar o Dropbear. Por isso não foi incluída uma sonda UDP genérica inferida de dispositivos Tapo nem ativada compatibilidade antiga para contornar TPAP. A investigação de autenticação segue separada do transporte M9 funcional.

Testes do diagnóstico cobrem CA pública exata, validação obrigatória, modelo inesperado, comparação de impressão, falhas de certificado sem fallback, timeouts, recusa TCP e ausência de dados de aplicação. São testes locais simulados; não substituem o teste físico nem homologam otimização no X50.

## X50 — cadeia validada no cliente e negociação de parâmetros preparada

Em 15/11/2026 o operador executou o diagnóstico v1 do commit `7b93593` no Terminal/SSH do Home Assistant da residência do cliente, usando Python 3.14.10. Resultado fornecido em captura: `state: tls_verified`, `vendor_chain_verified: true`, modelo `Deco X50(US)`, TLS 1.3, `TLS_AES_256_GCM_SHA384`, duração 0,043 segundos e zero bytes de aplicação enviados. Isso confirma que a CA pública incorporada valida a cadeia apresentada por esse X50. `hostname_verified: false` indica que não foi comparado um hostname/IP; `certificate_pin_matched: null` indica a primeira coleta, sem impressão individual prévia. Autenticação e otimização continuaram `not_tested`. A impressão individual foi entregue ao operador, mas não é embutida no código ou publicada neste documento.

O operador esclareceu que o M9 fica em sua própria residência e o X50 na residência de um cliente, com conta TP-Link diferente. Os diagnósticos não reutilizam credenciais entre esses ambientes. A revisão 2 solicita somente o e-mail do proprietário do X50 no Terminal e exige a impressão previamente observada no mesmo equipamento antes de transmitir uma mensagem.

Na imagem oficial 1.8.4 já identificada acima, a análise estática da biblioteca `libtpap.so` sustenta a negociação inicial:

- `prover_SendParamRequest` (`0x142fc`) gera 32 bytes aleatórios e envia JSON `method: login`, `params.sub_method: pake_register`, `passcode_type: userpw`, SHA-256 hexadecimal minúsculo do username, `cipher_suites: [1]` e `encryption: [aes_128_ccm]`. O username é fornecido pelo chamador; o diagnóstico usa o e-mail informado, sem tentar outras contas ou esquemas de hash.
- O cliente nativo em `0x1a178–0x1a1c4` monta cabeçalho de 24 bytes: versão 1, flags 1, tipo 1, comprimento em ordem de rede e sequência de quatro bytes. O campo canal ocupa oito bytes e é zerado na sonda, que não abre canais. `tscp_checksum_set` (`0x1acfc`) substitui o checksum pela constante `0x92bf30ed` em ordem de rede antes de calcular CRC32 sobre cabeçalho e corpo. O servidor responde com flags 2 e preserva a sequência dessa requisição (`server_handle_request`, `0x12df0–0x12e40`). Os flags distinguem direção; não são uma confirmação de autenticação.
- `verifier_HandleParamRequest` (`0x17090`) consulta os dados da conta existente e verifica o estado de bloqueio. `verifier_SendParamResponse` (`0x16bb0`) retorna `dev_random`, `dev_salt`, `iterations`, `cipher_suites`, `encryption`, `dev_share` e eventualmente `extra_crypt` para derivação de senha. O ramo `verifier_SendParamResponse_InvalidUser` retorna parâmetros fictícios; receber API 0 nesta etapa não valida o e-mail ou a senha.

O diagnóstico v2 implementa somente esse primeiro intercâmbio, opcional e sem retransmissão. Não implementa `pake_share`, `pake_confirm`, reutilização de sessão, canais TPAP/TMP ou operações ACS. Parâmetros sensíveis não saem no JSON resumido. O limite de 8192 bytes, validação estrita do cabeçalho/CRC/sequência, rejeição de JSON ambíguo e um prazo único impedem interpretar respostas truncadas ou inesperadas como negociação válida. A rotina principal de TLS v1 permanece disponível sem transmitir mensagens de aplicação.

Foi consultado o [protótipo TPAP de raymondlo84](https://github.com/raymondlo84/tapo-tpap-client/blob/main/tpap_proto.py) como comparação de campos SPAKE2+. Ele usa HTTP e hash de usuário diferente para dispositivos Tapo; seu transporte, derivações e comandos genéricos não foram executados nem incorporados ao diagnóstico Deco. A implementação da mensagem acima deriva da biblioteca do firmware X50, mas seu funcionamento físico ainda aguarda o teste do cliente. O runtime M9 e a versão do app permanecem inalterados.

## X50 — parâmetros físicos confirmados e diagnóstico de login completo

O operador executou o diagnóstico v2 do commit `27bbccd` no HA do cliente. A captura registra `pake_parameters_received`, `api_code: 0`, certificado fixado correspondente, TLS 1.3, 295 bytes de aplicação enviados e duração de 0,088 segundos. O perfil retornado foi suíte 1, `aes_128_ccm`, 100.000 iterações, aleatório e salt de 32 bytes, ponto não comprimido de 65 bytes e ausência de `extra_crypt`. `owner_account_verified: false` é esperado nesta etapa: nem a senha nem o papel da conta foram verificados. Isso resolve a pendência do enquadramento inicial v2, mas não a autenticação completa.

A preparação do diagnóstico v3 usa a mesma imagem oficial 1.8.4 e análise estática de código, sem ler arquivos de conta de qualquer equipamento:

- `luci/model/accountmgnt.lua`, função `set_tpap_authmgmt` (linhas originais 636–707), decodifica e-mail e senha de Base64 e passa **e-mail + `/` + senha** a `libtpap.generate_verify`. A função Lua nativa em `0x1b334` chama `spake2p_generate_verifier` (`0x16298`), que deriva 80 bytes com PBKDF2-HMAC-SHA256 e separa duas metades de 40 bytes. `spake2p_bin2bn` reduz os escalares pela ordem de P-256. Não é o esquema de hashes do transporte SSH/M9.
- `spake2p_setup` (`0x16470`) calcula SHA-256 de `PAKE V1` concatenado com os 32 bytes aleatórios do cliente e os 32 bytes do servidor. Esse digest é o contexto do transcript; os identificadores adicionais de prover/verifier ficam vazios nesse fluxo. Os pontos M/N encontrados em `0x21060`/`0x21018` correspondem aos pontos P-256 publicados no [RFC 9383](https://www.rfc-editor.org/rfc/rfc9383.html).
- `spake2p_get_ZV` (`0x1778c`) usa as operações SPAKE2+ e incorpora contexto, identificadores, M, N, X, Y, Z, V e w0 ao transcript com prefixos de comprimento de oito bytes little-endian. Os pontos são não comprimidos; w0 é codificado com **32 bytes big-endian**, incluindo zeros iniciais. `spake2p_get_K_shared` (`0x17688`) usa HKDF-SHA256 com extração e expansão, salt vazio e rótulo `ConfirmationKeys` para obter duas chaves de 32 bytes; as confirmações são HMAC-SHA256 sobre o ponto da outra parte.
- `prover_SendShare` (`0x17ab4`) envia `pake_share` com `user_share` e `user_confirm`. `prover_HandleShareResponse` (`0x18a78`) exige resposta de login, confere `dev_confirm` e encontra `stok` antes de marcar a autenticação concluída. Não é necessário enviar uma terceira mensagem `pake_confirm` nesse fluxo. O diagnóstico termina após essas duas mensagens, sem reutilização de sessão nem canal TMP/ACS.

A opção `--authenticate` exige a impressão conhecida e uma biblioteca de curvas mantida ([PyCryptodome ECC](https://www.pycryptodome.org/src/public_key/ecc)); ela não implementa aritmética de curva manualmente. O teste pede as credenciais localmente com eco desativado e interrompe se não conseguir garantir entrada oculta. O perfil observado é validado antes da prova, incluindo ponto na curva, não identidade, salt de 32 bytes e limite de custo de PBKDF. O escalar efêmero é novo a cada execução. Não são tentados perfis alternativos nem repetidos logins recusados. A prova recebida é comparada em tempo constante, sobre os bytes decodificados, preservando a distinção de maiúsculas/minúsculas da codificação Base64.

O relatório diferencia credenciais verificadas, prova do servidor, token recebido, envio tentado/completo e eventual erro de protocolo. Não exporta credenciais, material da derivação, confirmações ou tokens. Autenticação bem-sucedida não atesta papel de proprietário/admin nem autorização para otimização; essa distinção aparece em `account_role_verified: false`. O transporte do app e a versão 0.8.0-dev.2 não foram alterados, e a biblioteca adicional é usada apenas no diagnóstico e nos testes.

Validação local: vetor P-256/SHA256 completo do RFC 9383, Apêndice C (X e as duas confirmações esperadas), verificador independente em memória com PBKDF2/PAKE, respostas fragmentadas, senha errada, prova inválida apesar de API 0, token ausente, perfil não suportado, ponto inválido/identidade, limite de custo, certificado divergente, falha de envio sem repetição e entrada oculta. Esses testes não substituem a execução do novo modo no X50 do cliente. Nenhuma autenticação com senha ou otimização física do X50 foi concluída nesta revisão.

## X50 — autenticação física confirmada e leitura TMP preparada

O operador executou a revisão 3 do commit `6ab7c75` no HA do cliente, com Python 3.14.10 e PyCryptodome 3.23.0 do Alpine. A captura de 15/11/2026 mostra `authentication_verified`, certificado individual correspondente, `server_proof_verified: true`, `account_credentials_verified: true` e `session_token_received: true`. Foram duas mensagens e 540 bytes enviados, em 0,388 segundo. `account_role_verified: false` preserva a ausência de consulta de permissões; `optimization: not_tested` continua correto. Essa evidência resolve a pendência de autenticação descrita na seção anterior, apenas para o equipamento e perfil testados.

A revisão 4 acrescenta `--read-info` como diagnóstico independente, com base na mesma imagem oficial 1.8.4:

- `spake2p_get_K_shared` (`0x17688`) também deriva 32 bytes com HKDF-SHA256, salt vazio e rótulo `SharedKey`. O vetor do [RFC 9383, Apêndice C](https://www.rfc-editor.org/rfc/rfc9383.html#appendix-C) passou a conferir essa saída, além dos pontos e confirmações. A chave só chega ao transporte depois da prova mútua válida e permanece em memória.
- `session_key_generate` (`0x12818`) deriva a chave AES com salt `tp-kdf-salt-aes128-key` e info `tp-kdf-info-aes128-key`, ambos de 22 bytes, usando os primeiros 16 bytes da saída HKDF. Deriva o prefixo IV com salt/info correspondentes terminados em `-iv`, ambos de 21 bytes, usando os primeiros oito bytes. Não inclui terminadores NUL. O nonce CCM tem 12 bytes: prefixo de oito e sequência de quatro exatamente como transmitida. `session_encrpyt`/`session_decrpyt` (`0x12a68`/`0x12908`) e `aes_ccm_encrypt`/`aes_ccm_decrypt` (`0xd4d0`/`0xd5d4`) mostram tag de 16 bytes, sem AAD.
- `tpap_channel_write` (`0x1a68c`) envia cabeçalho TPAP de 24 bytes, tipo 2, nome de canal `TMP` preenchido até oito bytes, payload cifrado e tag. A sequência nativa é little-endian. Diferentemente da resposta de login, o tráfego de canal usa flag 1 nos dois sentidos: `tpap_channel_read_timeout` chama `tscp_flag_verify` com tipo 1 (`0x1aa28`). O CRC cobre cabeçalho com seed `0x92BF30ED` e corpo cifrado.
- O callback de abertura TMP (`0x1bf98`) conecta a `127.0.0.1:20002`; envio (`0x1bf14`) encaminha os bytes e recepção (`0x1bd24`) reconhece o transporte TMP type-5 e controles de quatro bytes. Isso sustenta o encapsulamento do framing TMP já usado pelo M9, sem modificar aquele transporte. Compatibilidade do canal no X50 físico continua pendente.

Os registros ELF64 de `/usr/bin/tmpsvr`, resolvidos pelas relocações, confirmam a lista fixa:

| Offset do registro | Opcode | Controlador | Forma | Operação |
|---|---|---|---|---|
| `0x1bab0` | `0x40A0` | `admin.mobile_app.device` | `sysmode` | `read` |
| `0x1a2f0` | `0x4009` | `admin.mobile_app.wireless` | `wlan` | `read` |
| `0x1a3b8` | `0x400F` | `admin.mobile_app.device` | `device_list` | `read` |

Os callbacks Lua foram inspecionados: `device.lua` linhas originais 3067–3077 chama `app_sysmode.get_sys_mode`; 1870–2947 reúne inventário; `wireless.lua` 1455–2008 reúne WLAN, incluindo seções `host`, `radio` e `backhaul`. O inventário também chama `speed_diagnose.sh`: o script examinado consulta velocidades negociadas e compara limites, sem iniciar teste de velocidade nem alterar canais. Leituras podem atualizar caches internos do firmware; não são enviados comandos de configuração. A resposta WLAN pode conter credenciais, portanto só campos de rádio selecionados são projetados para a saída, nunca JSON bruto.

Após ALLOC e token TMP de formato válido, são enviadas as três consultas com `config_version` e `params: {}`. Não existe opção de opcode arbitrário; `0x424F` (inicia análise), `0x4250` (aplica), reinicialização e alterações WLAN são excluídos. São conferidos CRCs de ambas as camadas, tag CCM, canal, limites de memória/quantidade de frames, transação, opcode, fragmentos e prazos. Respostas parciais usam pull da mesma leitura; não repetem a operação. Cada etapa tem prazo próprio limitado, sem renovação a cada fragmento. Uma falha de protocolo/timeout encerra; erros de API ficam separados por consulta.

Validação inclui servidor simulado independente que deriva a chave a partir do transcript PAKE, decifra os pedidos reais, responde com fragmentação TLS/TPAP/TMP e intercala keepalives. Testes cobrem prova inválida sem abertura de canal, integridade CCM, CRC, sequência repetida, correlação TMP, limites, erros de API, omissão de dados privados e bloqueio de comandos de análise/aplicação. Não há acesso ao Deco nos testes. A versão 0.8.0-dev.2, o runtime M9 e as entidades MQTT permanecem inalterados; nenhuma leitura TMP ou otimização física do X50 foi confirmada nesta revisão.

## X50 — leituras físicas confirmadas e análise ACS preparada

O operador executou a revisão 4 do commit `d7d5995867e982759fd6702a07b1182bc7d731b2` no HA do cliente. As três capturas de 15/11/2026 registram `readonly_verified`, `stage: complete`, `tmp_session_verified: true` e as três leituras disponíveis com API 0. Foram seis mensagens e 976 bytes de aplicação enviados, quatro frames de canal recebidos, em 2,114 segundos. Cadeia do fabricante, impressão individual e prova mútua continuaram verificadas. A leitura TMP física resolve a pendência da seção anterior; `optimization: not_tested` continua correto.

Dados sanitizados retornados:

- Modo `Router`.
- Dois nós X50, ambos com `hardware: 1.30` e `firmware: 1.8.4 Build 26051413 Rel. 27460`; papéis `master` e `slave`.
- `band2_4`: `radio.htmode: HT40`, `backhaul.channel: 10`.
- `band5_1`: `radio.htmode: HT80`, `backhaul.channel: 36`.

A revisão de hardware agora vem do inventário autenticado e corresponde à página oficial V1.30 do firmware examinado. Ela difere da etiqueta V1.0 anteriormente atribuída pelo operador à mesma unidade; não se conclui equivalência entre revisões nem se sobrescreve essa evidência anterior. Os números de canal vieram apenas de `backhaul`. Não há número de canal `host`/`radio` no relatório recebido e eles não são promovidos a tais campos.

A revisão 5 acrescenta `--scan-only` para o próximo teste, sem mudar o app nem exigir rebuild. A base estática continua sendo a imagem oficial X50 1.8.4: o registro `0x1c780` de `tmpsvr` aponta `0x424F` para `admin.mobile_app.network_optimize`, formulário `acs_optimize`, operação `read`. No controlador dessa imagem, o callback tem metadados de linhas 197–286; sua função auxiliar (91–176) chama `Wifi:get_best_channel_result(true)`, persiste a recomendação e atualiza `scan_count`. Embora o nome da operação seja `read`, essa chamada pode iniciar uma análise ativa. O callback `write`/opcode `0x4250` é separado e não foi habilitado no diagnóstico.

O retorno de análise contém `need_optimize`, `optimize_item.channel` e estimativa `optimize_time`. A revisão exige API inteira 0 e os dois booleanos coerentes, omite campos desconhecidos e mantém `fresh_scan_confirmed: false`/`result_may_be_cached: true`: o controlador pode servir recomendação anterior quando scan/set está em andamento e suprime recomendação durante 1800 segundos após aplicação. Não há ID nem instante de medição no contrato analisado. A duração, sozinha, não prova frescor nem melhora de interferência.

O diagnóstico só habilita a análise após autenticação com certificado fixado, ALLOC e três leituras reconhecidas. Restringe o principal ao perfil físico observado: X50/1.30/1.8.4 Build 26051413 Rel. 27460 em Router. Realiza um pedido ACS com `params: {}` na mesma conexão, prazo próprio de 30–180 segundos, e reserva a tentativa antes do envio. Fragmentos são buscados com pull da mesma transação; um segundo pedido inicial de análise é bloqueado inclusive após envio incerto. Não há reconexão nem repetição automática. Reexecutar o programa é uma nova execução, sem deduplicação persistente, e pode disparar nova análise.

Depois da resposta, faz uma leitura WLAN para comparação com a anterior. A comparação usa exclusivamente canais numéricos da mesma banda e seção, registra escopos comparados e distingue cobertura completa, parcial ou indisponível. Falha dessa observação não apaga a resposta de análise nem vira sucesso geral. Mudança observada não implica que a sonda aplicou configuração; `apply_command_sent` permanece falso, `optimization` permanece `not_tested` e nenhum opcode de aplicação, reinicialização ou escrita WLAN é permitido.

Os testes simulados acrescentam a forma efetivamente recebida do X50 (hardware 1.30 e canais apenas em backhaul), recomendação positiva/negativa, perfil divergente, leituras prévias recusadas, API/JSON inválidos, envio e resposta interrompidos sem repetição, conservação de resposta após falha posterior, comparação por seção e entrada oculta. A lista de operações anterior continua bloqueando ACS em `--read-info`; o novo modo habilita apenas a análise, não aplicação. Validação física da análise e aplicação no X50 ainda pendentes. O runtime M9, sua otimização validada e as entidades MQTT permanecem iguais.

## X50 — recomendação ACS física confirmada e aplicação piloto preparada

O operador forneceu o JSON da revisão 5 do commit `4b420ce035da4a28acafd64ff8ab0958602e34c2`, executada no HA do cliente. O resultado foi `scan_response_received`, API 0, `need_optimize: true`, `optimize_item.channel: true`, estimativa de aplicação de 20 segundos e resposta em 13,414 segundos. O conjunto levou 15,049 segundos: oito mensagens, 1216 bytes enviados e seis frames de canal recebidos. TLS, impressão individual, autenticação e sessão TMP continuaram verificados.

A leitura manteve dois X50 1.30/1.8.4 Build 26051413 Rel. 27460, principal Router, canais de backhaul 10/36 e larguras radio HT40/HT80. A comparação das mesmas seções antes/depois foi completa e sem mudança numérica. `apply_command_sent: false` e `optimization: not_tested` confirmam o escopo do teste. `fresh_scan_confirmed: false`/`result_may_be_cached: true` continuam ressalvas do diagnóstico; 13,414 segundos não demonstram medição nova. Essa evidência valida transporte e contrato da recomendação ACS no X50 observado, ainda sem aplicação.

A revisão 6 prepara aplicação sob demanda com o mesmo firmware oficial já analisado. O registro `0x1c7a8` de `tmpsvr` mapeia `0x4250` para `acs_optimize/write`. A inspeção do callback Lua (metadados de linhas 288–321) mostra leitura da recomendação persistida pelo firmware, marca de operação em andamento, chamada da rotina de aplicação, atualização do horário/contador e remoção da recomendação após sucesso. Não recebe uma lista arbitrária de canais nos parâmetros. Por isso o diagnóstico envia `params: {}` somente após um pedido de análise reconhecido e positivo, no mesmo fluxo autenticado, e uma referência WLAN disponível. O programa não carrega uma recomendação de um arquivo antigo como autorização para escrever.

A aplicação tem habilitação própria, separada da análise, e reserva durável antes de qualquer byte de escrita. Um registro em `/config` associa ID, alvo derivado de IP+impressão e relatório sanitizado. Usa lock não bloqueante, escrita temporária com modo 0600, sincronização do arquivo, troca atômica e sincronização do diretório. Falha de persistência impede o envio ou conserva a reserva anterior incerta; nunca causa repetição. O mesmo ID é consumido antes do login/análise. Um ID novo não contorna tentativa anterior sem aceite e verificação estável, nem o intervalo mínimo de 1800 segundos. A proteção depende da preservação desse registro local; não é deduplicação oferecida pelo firmware.

Após a tentativa, inclusive com resposta perdida ou recusada, o diagnóstico observa sem reenviar. Aguarda 30 segundos, abre sessões autenticadas exclusivamente de leitura, exige três observações consecutivas iguais cobrindo os parâmetros da referência, e limita a seis observações/janela de 120 segundos. Mantém número de canal e largura nas seções originais. Falha transitória reinicia a contagem; recusa de credenciais, identidade divergente ou erro de integridade/protocolo interrompe a verificação. `--verify-only` recupera apenas essa observação a partir do registro; não tem acesso a um leitor com comandos ACS habilitados. Aceite, estabilidade, mudança e melhoria de qualidade permanecem conceitos separados; uma observação não recupera um ACK perdido.

Testes simulados verificam a reserva gravada antes do comando real cifrado, uma única aplicação e sessões posteriores sem ACS, resposta positiva/negativa/malformada, parâmetros sem mudança, canais e larguras alterados, timeout, interrupção, recuperação só de leitura, bloqueio de concorrência/repetição/outro ID, intervalo, registro inválido e falhas de armazenamento antes/depois do envio. Os temporários dos testes ficam em `/tmp`, fora de OneDrive/iCloud. Esta revisão não altera a imagem, versão, entidades MQTT ou transporte M9 do app. Aplicação física do X50 e incorporação TPAP ao runtime seguem pendentes.

## X50 — aplicação física confirmada e incorporação ao app 0.9

Relatório fornecido pelo operador em 15/11/2026: diagnóstico revisão 6, pedido `x50-opt-001`, perfil X50 hardware 1.30 / firmware 1.8.4 Build 26051413 Rel. 27460, Router, dois nós. A conta pertence à instalação do cliente, separada do piloto M9 residencial. Certificado e prova mútua conferidos; nenhuma credencial foi incluída no relatório.

A análise respondeu API 0, recomendação positiva, em 13,579 s. Houve uma tentativa de aplicação, aceite API 0 em 16,543 s; após 30 s, três leituras consecutivas estáveis, nenhuma falha. Conclusão `acknowledged_with_channel_change`, duração total 85,999 s. A mudança observada foi `band2_4.backhaul.channel` de 10 para 4; `band5_1.backhaul.channel` permaneceu 36, larguras radio HT40/HT80 mantidas. Não foram reportados canais numéricos host/radio nem medidas de interferência, velocidade ou disponibilidade das câmeras. A origem nova/cache da recomendação continua não confirmada.

A 0.9 incorpora o mesmo cliente ao app, com seleção explícita M9/X50, journal separado, reserva de autenticação, controle MQTT e projeção de backhaul. O código M9 de protocolo/aplicação é preservado. O diagnóstico não é importado automaticamente para o histórico do app; uma tentativa incerta deve ser resolvida no registro de origem. Aplicação física do diagnóstico está confirmada; instalação da nova imagem e o fluxo X50 completo pelo Home Assistant ainda dependem do teste no cliente.
