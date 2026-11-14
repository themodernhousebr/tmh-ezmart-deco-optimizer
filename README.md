# EZmart Home Assistant Apps

<img src="ezmart_deco_optimizer/icon.png" alt="The Modern House" width="128">

Versão 0.4.0-dev.1: piloto residencial somente leitura para uma Mesh Deco mista com M9 principal em AP.

Inventário, sessão reutilizada, cooldown após falha, resumo de modelos/firmwares e diagnósticos opcionais de ACS, capacidades Wi-Fi, canais e desempenho. Opções permitem novos testes sem rebuild; salvar e reiniciar o app é suficiente.

Consulte [configuração e instalação](ezmart_deco_optimizer/DOCS.md), [pesquisa de integrações](RESEARCH.md) e [changelog](ezmart_deco_optimizer/CHANGELOG.md).

Não há otimização de canais implementada, sensores automáticos, MQTT ou interface web nesta versão. ACS é apenas leitura de estado possivelmente histórico, ainda pendente de homologação no piloto.

## Desenvolvimento

Runtime no contêiner: Python 3.14, SDK tplink-deco-api 2.1.1. Não é necessário instalar o SDK no Mac. Testes isolados de projeção e contrato de leitura: `python3 -m unittest discover -s tests`. Não acessam o Deco.

O repositório Git é local. Publicação, remoto GitHub e imagens GHCR dependem de autorização. Antes de distribuir, definir licença e URLs reais de suporte. O código original 0.1.10/0.1.11 não estava disponível; esta base é nova.

Adicionar repository.yaml e a pasta do app a um GitHub futuro permitirá instalar por repositório personalizado; não implica inclusão no catálogo oficial. Instalação atual: copiar pasta do app para /local_apps e construir pelo Supervisor.

TMP experimental em duas etapas: descobrir identidade SSH sem credenciais e, após configurar a chave, autenticar uma vez e ler o estado. Sem scan ou aplicação. Consulte DOCS.md antes do piloto.
