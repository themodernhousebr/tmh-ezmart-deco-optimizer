# Changelog

## 0.11.0-dev.13

- Recusa do perfil M5 antes de otimização inclui diagnóstico filtrado da leitura TMP: etapa, tipo de falha, código de API, perfil público reconhecido e metadados numéricos de enquadramento, conforme disponíveis.
- Mantém exigência de perfil exato e bloqueio antes de iniciar ACS/aplicação. Não publica corpo bruto, identificadores de cliente nem mensagens arbitrárias de exceção.

## 0.11.0-dev.12

- Piloto de ações M5 3.0/3.2, firmware exato 1.9.5 Build 20260602 Rel. 25369, AP/FAP: visitantes com preservação e otimização condicional via SSH/TMP. Monitoramento HTTP preservado; confirmação física das ações pendente.
- Perfil conferido ao vivo antes da análise ACS e da escrita de visitantes. Porta dedicada de leitura de metadados não habilita alterações. Router, outros hardwares/builds e inventário sem um único master permanecem bloqueados.
- Mantém proteção de autenticação, intervalos de 10 minutos/um minuto, agendamento, reserva persistente e verificação. Nenhuma ação M5 na inicialização.

## 0.11.0-dev.11

- Piloto M5 somente leitura HTTP, com opção de modelo própria e intervalo padrão de 300 segundos. Coleta hardware e papel dos nós quando disponíveis; esses campos também enriquecem o inventário HTTP M9 existente.
- Otimização, visitantes, ações de inicialização e transporte TMP permanecem bloqueados no perfil M5, inclusive com opções habilitadas por engano. Nenhuma revisão é presumida ou homologada por esse piloto.
- Preserva os transportes e ações M9/X50 já existentes. Confirmação física da coleta M5 pendente.

## 0.11.0-dev.10

- Comandos de visitantes durante o cooldown ou outro comando de visitantes ficam agendados; apenas o último estado solicitado é mantido e executado uma vez após liberação.
- Intenção não enviada persiste por alvo, com horário e expiração em atributos e logs. Consumo persistido antes de executar impede replay. Falha/incerteza da ação ativa, expiração, troca de alvo ou desativação cancelam o agendamento. Estado do switch continua observado.

## 0.11.0-dev.9

- Otimização M9/X50: padrão e mínimo de 10 minutos entre pedidos/aplicações. Intervalos maiores explicitamente configurados continuam respeitados; pausas de pedidos concluídos com relatório correspondente são recalculadas pelo novo intervalo, sem apagar históricos ou liberar resultados incertos.
- Visitantes mantém 60 segundos, reservados na aceitação e renovados antes da escrita. Não herda intervalo de aplicação estável de canais; operações em andamento ou sem verificação continuam bloqueadas. Proteção de autenticação inalterada.

## 0.11.0-dev.8

- Piloto X50 principal hardware 1.0, firmware exato 1.11.1 Build 26081714 Rel. 32981, Router: otimização sob demanda e visitantes. Monitoramento já observado; confirmação física das ações pendente.
- Comparação offline do firmware oficial confirmou controlador ACS idêntico, mesmas funções de leitura/escrita Wi-Fi e aplicação de canais e mesmos registros TMP. Testes simulados cobrem aplicação única, observação posterior, recomendação negativa, visitantes e bloqueio de combinações não suportadas.
- Preserva perfil 1.30/1.8.4, transporte M9, identidades, certificados, históricos, intervalos e bloqueio após resultado incerto. Nenhuma ação automática adicionada.

## 0.11.0-dev.7

- Falha de consulta periódica X50 após autenticação confirmada respeita o intervalo de consultas, em vez de aplicar pausa de autenticação de 15 minutos.
- Falha de conexão de visitantes inclui diagnóstico filtrado e não provoca consulta imediata adicional. Não repete escrita nem reduz proteção de autenticação não confirmada.

## 0.11.0-dev.6

- Leitor TMP aceita fragmentos com checksum da mensagem completa, exigindo o mesmo checksum nos fragmentos e validação final antes de interpretar os dados. Preserva checksum por fragmento, CRC externo, autenticação CCM, limites e correlação de transação. Hipótese de compatibilidade para inventários maiores; confirmação física pendente.

## 0.11.0-dev.5

- Respostas TMP rejeitadas incluem metadados numéricos de versão, tamanho e fragmentação e resultados booleanos de integridade, sem conteúdo ou identificadores. Mantém todas as validações; não aceita respostas inválidas nem reenvia comandos.

## 0.11.0-dev.4

- Diagnóstico X50 inclui etapa exata e códigos de protocolo permitidos, sem mensagens arbitrárias. Falha de leitura após prova de senha confirmada usa o intervalo de consultas; rejeição ou incerteza de autenticação mantém a proteção de 15 minutos. Não altera a interpretação das respostas nem libera ações sem inventário validado.

## 0.11.0-dev.3

- Falhas de conexão X50 agora registram um diagnóstico filtrado com estado da conexão, confirmações de autenticação e códigos de leitura. Não expõe credenciais ou respostas brutas e mantém os intervalos de proteção.

## 0.11.0-dev.2

- X50 obtém automaticamente o certificado validado do fabricante na primeira conexão e o salva por instalação, sem login durante a obtenção. Modo automático por padrão; modo manual pinned permanece disponível.
- Preserva certificados já configurados e bloqueia mudanças de identidade. Falhas de obtenção aguardam 60 segundos, sem repetição rápida do processo.

## 0.11.0-dev.1

- Controle de visitantes opt-in no X50 1.30 / 1.8.4 Build 26051413 Rel. 27460 Router. Reutiliza o interruptor MQTT e exige certificado fixado, autenticação mútua e perfil confirmado antes da escrita. Validação física X50 pendente.
- Leitura completa WLAN e escrita parcial 0x4008: somente enable e isolamento já lido, para 2,4 e 5 GHz. Comparação de preservação em memória, uma tentativa, duas leituras de confirmação e nenhum replay após perda de resposta ou reinício.
- Histórico guest vinculado ao IP/certificado X50; proteção própria de autenticação, intervalo guest de 60 s, bloqueio durante aplicação de canais não verificada ou intervalo X50 de 30 minutos. Otimização e guest compartilham exclusão de ações.
- Monitoramento guest usa a leitura WLAN periódica existente e publica apenas booleanos. Sem ACS, sem ação na inicialização e sem mudança de SSID/senha no payload. M9 mantém transporte e controles existentes.

## 0.10.0-dev.5

- Controles M9 disponíveis enquanto o app estiver conectado e comandos habilitados, independentemente de falhas no inventário ou leitura antiga. Disponibilidade MQTT passa a representar o serviço; sensores de prontidão e motivos de bloqueio mantêm intervalos e recusas.
- Pedidos explícitos M9 entram no trabalhador mesmo sem monitoramento saudável e antes do próximo ciclo/login HTTPS. A execução mantém autenticação, validação ao vivo, reserva antes de escrever e ausência de replay. Sem referência WLAN válida, otimização não aplica; visitantes exige leitura TMP completa.
- Visitantes conserva o último estado observado e informa `observation_fresh` e `observed_at` nos atributos. O estado não é confirmação otimista. Falha de leitura não representa rede desligada.
- X50 mantém seus critérios anteriores de prontidão e disponibilidade nesta etapa.

## 0.10.0-dev.4

- M9 usa `tmp_host_key_mode: automatic` por padrão, inclusive quando a opção ainda não existe em configurações antigas. Obtém e aceita a chave SSH apresentada ao conectar, confiando no IP reservado; não autentica a identidade por fingerprint.
- Modo `pinned` mantém comparação obrigatória com a chave configurada. Certificado X50 permanece verificado, sem alteração.
- Não altera o fingerprint salvo nem inicia consultas periódicas de chaves. Preserva a identidade dos históricos existentes ao trocar o modo, mantendo o valor salvo. Autenticação, comandos explícitos e proteções contra reenvio permanecem.

## 0.10.0-dev.3

- Falhas transitórias no inventário/modo M9 não interrompem as demais leituras do ciclo. Visitantes fica disponível somente com leitura WLAN recente, independentemente do inventário.
- Inventário ausente não vira contagem zero; otimização continua bloqueada enquanto o monitoramento completo estiver indisponível. Falhas de autenticação continuam interrompendo o ciclo.
- Não modifica os comandos de visitantes, não inicia ações automaticamente e não mascara falhas na própria leitura WLAN.

## 0.10.0-dev.2

- Corrige o bloqueio `guest_configuration_unavailable` no controle de visitantes M9: a consulta HTTPS de monitoramento não fornece isolamento, necessário para preservar a configuração ao escrever. A preparação e a confirmação agora usam a leitura WLAN completa pelo mesmo canal TMP autenticado.
- Mantém a exigência de configuração completa, uma única tentativa de escrita e duas leituras de confirmação. Falhas distinguem leitura e validação no relatório, sem incluir SSID ou senha.
- Acrescenta regressões para a resposta HTTPS incompleta, consulta TMP, falha de leitura e confirmação interrompida. Validação física de ligar/desligar continua pendente; atualização preserva configurações e entidades.

## 0.10.0-dev.1

- Controle opcional da rede de visitantes existente, inicialmente no M9 Plus V2 / 1.9.1 Build 20250612 Rel. 45690 em AP. Validação física deste recurso ainda pendente; X50 mantém somente as funções anteriores.
- Interruptor MQTT `switch.ezmart_deco_visitantes`: liga as bandas 2,4 e 5 GHz ou desliga ambas. Estado observado pelo principal, sem confirmação otimista; mudanças feitas pelo app Deco aparecem no monitoramento.
- Comando parcial preserva isolamento e omite SSID, senha, segurança, VLAN, duração, rede principal e canais. Confere configuração antes/depois em memória; relatórios omitem dados privados. Firmware pode renovar o prazo de uma rede temporária ao ligá-la.
- Reserva persistente antes da escrita, uma tentativa, confirmação com duas leituras, sem reenvio após perda de resposta/reinício. Intervalo próprio de 60 s, uma tarefa por vez e bloqueio durante aplicação de canais pendente. Histórico de visitantes separado da otimização.
- Mantém identidade e IDs das 22 entidades existentes. Acrescenta apenas o interruptor quando `guest_control_enabled: true`; desabilitar a opção remove sua descoberta MQTT. Nenhuma ação de visitantes na inicialização.
- Acrescenta testes de payload restrito, perfil, admissão, recuperação, privacidade e MQTT real em loopback.

## 0.9.0-dev.1

- Incorpora ao app o transporte X50 TPAP/TLS com certificado fixado e autenticação mútua, reutilizando o cliente do diagnóstico físico. Seleção explícita por modelo; transporte e histórico M9 preservados.
- Perfil de ações X50 restrito a hardware 1.30, firmware 1.8.4 Build 26051413 Rel. 27460 e modo Router; verificação por canais de backhaul.
- Mesmos botões e IDs de entidades; sensores X50 rotulados como backhaul, sem inventar dados de CPU/memória ou terceira banda. Poll X50 mínimo 60 s, apenas leitura.
- Pedidos X50 explícitos, mínimo 30 minutos, reserva durável, verificação sem reaplicação e pausa persistida antes de toda autenticação. Parada durante espera preserva o resultado e não reenvia aplicação.
- Configuração nova com 10 campos principais; campos manuais, prazos e diagnósticos opcionais. Preserva valores salvos e o modo manual M9. Nenhuma ação X50 na inicialização.
- Blueprint de repetição reconhece o resultado X50 aceito e estável sem mudança observada, mantendo exigência de ID novo. Não interpreta estabilidade como ganho de desempenho.
- Acrescenta PyCryptodome 3.23.0 ao pacote; uma atualização/reconstrução para todas as funções. Diagnóstico independente passa a ser baixado de `ezmart_deco_optimizer/x50_tpap.py`; atalho em `tools` exige checkout completo. URLs antigas fixadas em commits permanecem válidas.

## 0.8.0-dev.2

- Expõe recusas MQTT de autenticação/autorização e falhas de conexão TCP/TLS antes silenciosas, sem mensagens brutas ou credenciais. Limita avisos repetidos a um por minuto. O pacote 0.8.0-dev.1 já distribuído permanece inalterado.

- Blueprint opcional de repetição: aguarda aplicação aceita e verificação estável de um novo ID; não encerra por timeout, resposta negativa ou sucesso antigo. Intervalo de 15 minutos e recuperação da verificação pendente. Compatível com o runtime 0.8.0-dev.1, sem rebuild para editar a automação.
- Estrutura de repositório personalizado para a loja do Home Assistant, URLs de instalação/suporte, testes automatizados e distinção explícita entre M9 AP validado e X50 principal ainda não validado.
- Evidência do piloto 0.8.0-dev.1: entidades criadas e pedido de verificação recebido/concluído por MQTT. Esta revisão de empacotamento ainda não foi instalada no HAOS do piloto.

## 0.8.0-dev.1

- MQTT Discovery com 22 entidades, dispositivo The Modern House, botões de otimização/verificação e sensores de resultado, canais, horários, disponibilidade e bloqueio.
- Acionamento sob demanda sem reiniciar app, sem IDs manuais e sem análise/otimização periódica. Modo MQTT suprime a antiga ação TMP de inicialização.
- Uma tarefa por vez; pedidos novos respeitam intervalo mínimo persistido de 900 s, inclusive após análise sem recomendação. Não enfileira repetições, não retoma escritas ao reiniciar e descarta pedido não iniciado em 60 s.
- MQTT 5 com sessão limpa, comandos QoS 0 sem retenção, recusa de comandos retidos/duplicados e identificador de conexão que invalida comandos antigos. Discovery/estado persistentes, anúncio de indisponibilidade e recuperação após reinício do HA/broker.
- Obtém credenciais MQTT via serviço Supervisor sem ampliar permissões; oferece servidor alternativo com TLS validado. Não publica senhas, e-mail, tokens ou respostas brutas.
- Reutiliza protocolo e journal da 0.7, mantém aplicação condicional e verificação sem replay. Atualiza inventário após uma ação e consulta canais periodicamente por HTTPS passivo.
- Blueprint para câmera/sensor indisponível, condição Pronto para otimizar, tempo mínimo de falha e condições adicionais. Cartão de painel e tradução de opções para português.
- Acrescenta Paho MQTT 2.1.0. Testes de regressão, admissão, persistência, worker, discovery e cliente real MQTT 5 em loopback. Criação das entidades e acionamento no HA do piloto ainda pendentes.
- Sem publicação GitHub/GHCR. Temporários de validação fora das pastas sincronizadas.

## 0.7.0-dev.1

- Adiciona ação manual optimize para M9 Plus V2: análise 0x424F e aplicação 0x4250 somente com recomendação positiva e canais anteriores disponíveis.
- Não altera padrões read/TMP desabilitado. O modo scan continua sem aplicação; comando legado 0x4266 permanece bloqueado.
- Registra ID, alvo e reserva em disco antes do envio. Impede repetição após timeout, queda de conexão ou reinício; trava local contra ações concorrentes.
- Intervalo de 900 segundos entre tentativas, inclusive IDs distintos; bloqueia novas aplicações enquanto a anterior estiver sem verificação concluída.
- Observação HTTPS após aplicação, três leituras completas e iguais, comparação de canais e resultados separados para aceite, recusa e incerteza. Sem nova análise ou reenvio durante a verificação.
- Ação verify retoma a observação pelo mesmo ID, sem conexão TMP. Prazos de resposta, espera e verificação configuráveis, sem rebuild.
- Relatórios filtrados e metadados de formato para diagnosticar respostas desconhecidas sem logar segredos.
- 50 testes locais, incluindo caminho de autenticação/ALLOC/análise/aplicação, frames, interrupções, persistência, retomada e resposta perdida. Execução no HAOS e primeira aplicação física confirmadas pelo log do piloto em 15/11/2026: recomendação positiva, aceite API 0, banda 5_2 de 149 para 157 e três leituras posteriores iguais, sem falhas.
- Dependências inalteradas; documentação e roteiro de piloto consolidados. A aplicação pode reiniciar o serviço Wi-Fi no firmware analisado.

## 0.6.0-dev.1

- Adiciona método manual m9_acs (0x424F), identificado no firmware M9 V2 1.9.1; não envia 0x4250/0x4266.
- Uma única solicitação por ID, sem polling ativo; timeout completo de 30–180 segundos e tratamento de resultado incerto.
- Resposta ACS estritamente filtrada, possibilidade de resultado em cache explícita, observações de canais antes/depois sem segredos.
- Verifica IDs já usados e configuração antes de conectar; sincroniza o journal em disco antes da ação.
- Remove ACS do monitoramento automático: read pode iniciar análise neste firmware.
- Identifica a leitura 0x4265 como estado legado, sem equivalência comprovada com a tela oficial.
- Consolida documentação e roteiro de atualização; dependências inalteradas. Primeiro teste HAOS/M9 recebido em 15/11/2026: api_code 0 em 26,842 s, sem recomendação nesse retorno e sem aplicação enviada. Comparação posterior com o app oficial concordou nesta rodada (Perfeito, canais 9/36, cerca de 27 s); aplicação permanece não testada.


## 0.5.0-dev.2

- Falhas conhecidas de transporte preservam sessão e repetem consulta no intervalo configurado, sem novo login.
- Erros de consulta classificados sem texto bruto; 401/403 e erros desconhecidos mantêm cooldown conservador.
- ACK de scan distingue recusa numérica de resposta desconhecida e inclui apenas metadados seguros.
- Prazo de cooldown já persistido por versões anteriores continua respeitado.

## 0.5.0-dev.1

- Varredura TMP manual autorizada, com consulta de estado antes/depois e prazo configurável.
- Identificador persistente impede repetir a mesma solicitação após reinício ou resultado incerto.
- Opcode de aplicação de canais continua bloqueado, inclusive com scan habilitado.
- Relatório persistido e distinção entre scan observado e idle imediatamente após aceite.
- 16 testes isolados; varredura real no M9 pendente.

## 0.4.0-dev.2

- Inclui seletor network_quality na consulta TMP de resultados, sem iniciar scan.
- Filtro de logs no handler bloqueia também loggers SSH criados após importação.
- Piloto confirmou autenticação TMP e resposta com api_code 1 na consulta anterior.

## 0.4.0-dev.1

- Módulo próprio TMP somente leitura via SSH 20001 e túnel loopback 20002.
- Descoberta de impressão digital sem senha e verificação antes de autenticar.
- Uma tentativa por execução, cooldown separado após falha, sem fallback interativo.
- ALLOC e leitura de qualidade apenas; validação de frames, CRC e fragmentos com limites.
- Dependência Paramiko 5.1.0; testes locais isolados; build e M9 pendentes.

## 0.3.0-dev.2

- Classifica erros opcionais por categoria fixa e códigos numéricos, sem texto de exceções.
- Pesquisa TMP registrada; sem implementar ou executar autenticação SSH, scan ou otimização.
- Atualização no HAOS pode aguardar consolidação dos próximos testes.

## 0.3.0-dev.1

- Consolida diagnósticos opcionais ACS, capacidades, canais e desempenho.
- Configuração das consultas e frequência sem alterar código ou reconstruir.
- Cache dos diagnósticos com data e erros isolados; 404 sem repetição.
- Projeções estritas para evitar exportação de segredos da resposta WLAN.
- Pesquisa comparativa de integrações e testes isolados sem acesso ao hardware.
- Otimização de escrita permanece não implementada.

## 0.2.0-dev.6

- Uma consulta ACS read por execução, no caminho admin/network_optimize.
- Nenhum scan, apply ou write. HTTP 404 não repete nem invalida a sessão.
- Logs ACS limitados a quatro campos numéricos/booleanos.

## 0.2.0-dev.5

- Zera o bloqueio de nova autenticação após login confirmado.
- Mantém cooldown após falha, sessão inválida ou tentativa interrompida.
- Preserva conservadoramente o prazo herdado de versões anteriores.

## 0.2.0-dev.4

- Resumo da Mesh nos logs na primeira consulta e quando mudar: modelos, firmwares, modo reportado e consultas não suportadas.
- Sem nomes de clientes, IPs, MACs, credenciais ou novas chamadas de rede.

## 0.2.0-dev.3

- Diagnóstico com horário, ciclo, intervalo carregado, consulta em andamento e espera de cooldown.
- Sem alteração dos endpoints ou da frequência de autenticação.

## 0.2.0-dev.2

- Intervalo de consulta configurável a partir de 30 segundos para desenvolvimento.
- Cooldown de autenticação preservado em 15 minutos.

## 0.2.0-dev.1

- Nova base de empacotamento Home Assistant com SDK 2.1.1 e Python 3.14.
- Diagnósticos somente leitura, sessão em memória e cooldown persistido de 15 minutos.
- Logs mínimos sem payloads ou exceções; nenhum endpoint ACS ou de escrita.
- Homologação de build, X50 e M9 Plus AP pendente.
- Não representa continuação verificada do código 0.1.10/0.1.11, indisponível nesta sessão.
