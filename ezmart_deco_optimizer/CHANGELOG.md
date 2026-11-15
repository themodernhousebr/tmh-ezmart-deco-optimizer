# Changelog

## 0.9.0-dev.1

- Incorpora ao app o transporte X50 TPAP/TLS com certificado fixado e autenticação mútua, reutilizando o cliente do diagnóstico físico. Seleção explícita por modelo; transporte e histórico M9 preservados.
- X50 validado fisicamente no perfil 1.30 / 1.8.4 Build 26051413 Rel. 27460 / Router: aplicação aceita, backhaul 2,4 GHz 10→4, três leituras estáveis, sem falhas, 85,999 s. Fluxo integrado X50/MQTT ainda requer teste no HA do cliente.
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
