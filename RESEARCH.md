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
