# EZmart Deco Optimizer

<img src="icon.png" alt="The Modern House" width="128">

**0.12.1 — estável.** Monitoramento local e otimização sob demanda pelo Home Assistant para os perfis exatos M9 Plus V2, X50 e M5 descritos na [documentação](DOCS.md).

O dispositivo MQTT principal reúne os botões de otimização/verificação, o controle opcional de visitantes e sensores de canais, inventário, CPU/memória, disponibilidade e resultados. Cada pedido de otimização analisa e aplica somente quando recomendado. Não há otimização periódica interna.

Cada Deco identificado recebe seu próprio dispositivo MQTT com nome sincronizado com o app Deco e entidades fixas para conectividade da malha, endereço IP e resposta ICMP. Nomes padrão de cômodos são traduzidos para português; nomes personalizados são preservados. CPU/memória referem-se ao principal. Monitoramento de nós, IP e desempenho fica ativo por padrão.

O controle de visitantes preserva as configurações existentes e confirma o estado por leitura. Pedidos durante seu intervalo de um minuto são agendados, mantendo apenas o último estado solicitado. Otimizações têm intervalo mínimo de cinco minutos e não entram nessa fila. Falhas de escrita não geram repetição automática.

[Configurar entidades e automações](AUTOMATION.md). [Opções, perfis e limites](DOCS.md). [Histórico de versões](CHANGELOG.md).

Instalação e atualização pela loja via [repositório personalizado](https://github.com/themodernhousebr/tmh-ezmart-deco-optimizer). O Supervisor constrói a imagem localmente. Atualize sem desinstalar para preservar opções, identidade MQTT e histórico.

CPU/memória e descoberta dos nós foram observadas nos modelos M9, M5 e X50. O acompanhamento de renomeação e queda/recuperação de satélites continua. Aplicação positiva de canais M5 hardware 3.0 aguarda recomendação de otimizar; uma alteração de canal não comprova ganho de desempenho.
