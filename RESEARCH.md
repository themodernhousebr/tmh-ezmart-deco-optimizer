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
