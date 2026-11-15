# EZmart Deco Optimizer

<img src="icon.png" alt="The Modern House" width="128">

**0.9.0-dev.1** — monitoramento Deco e otimização sob demanda pelo Home Assistant.

Cria um dispositivo MQTT com 22 entidades: botões para solicitar otimização ou verificar a última aplicação, disponibilidade, resultado, recomendação, canais, horários, inventário e CPU/memória. Cada pedido faz uma análise; aplica somente quando recomendado. Não há otimização por intervalo. Pedidos simultâneos, antigos ou dentro do intervalo mínimo são bloqueados, sem fila para executar depois.

[Configurar entidades e automações](AUTOMATION.md) inclui atualização única, blueprint de câmera indisponível e cartão de painel. Após instalar, os botões não exigem reinício do app nem mudança manual de ID. O modo manual anterior continua disponível com MQTT desabilitado; veja [DOCS.md](DOCS.md).

O piloto M9 Plus V2 em AP validou uma aplicação na 0.7, com canal 5_2 de 149 para 157 e monitoramento posterior funcionando. A 0.8.0-dev.1 também criou as entidades no HA do piloto e recebeu/concluiu um pedido de verificação por MQTT; a revisão dev.2 ainda não foi instalada nesse HAOS. Uma mudança de canal não comprova ganho de desempenho. A aplicação pode interromper temporariamente o Wi-Fi. O X50 1.30 / 1.8.4 em Router teve aplicação física confirmada (backhaul 2,4 GHz 10→4, três leituras estáveis). A 0.9 incorpora esse transporte aos mesmos controles MQTT; o fluxo completo no HA do cliente ainda precisa ser validado. M5 e outros perfis X50 como principal não estão validados.

Segredos não são publicados. O Mosquitto do Supervisor é descoberto automaticamente. Instalação pela loja via [repositório personalizado](https://github.com/themodernhousebr/tmh-ezmart-deco-optimizer). Distribuição experimental; o Supervisor constrói a imagem localmente.

A configuração nova mostra 10 campos principais; opções manuais e tempos são avançados e opcionais. Consulte [DOCS.md](DOCS.md) para preencher apenas os campos do seu modelo. Configurações M9 existentes são preservadas.
