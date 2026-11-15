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
