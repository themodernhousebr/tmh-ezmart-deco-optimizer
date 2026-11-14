# Piloto residencial — 0.3.0-dev.1

Aplicativo somente leitura. Não executa scan, apply, otimização automática ou mudanças de canal. O nome Optimizer descreve o objetivo do projeto; o recurso de escrita ainda não foi homologado.

## Opções

| Opção | Padrão | Função |
|---|---|---|
| host | vazio | IP do Deco principal; no piloto 192.168.68.4 |
| username | admin | Usuário administrativo |
| password | vazio | Preencher apenas no Supervisor |
| poll_seconds | 30 | Pausa entre ciclos de inventário; mínimo 30, máximo 86400 |
| diagnose_acs | true | Lê o estado ACS em admin/network_optimize |
| diagnose_wifi_support | true | Consulta get_support em admin/wireless |
| diagnose_channels | false | Lê WLAN e retém apenas canais e larguras |
| diagnose_performance | false | Lê CPU e memória reportadas |
| diagnostic_seconds | 0 | Zero: diagnósticos uma vez por execução. Outro valor: intervalo mínimo entre diagnósticos |

Após alterar opções, salvar e reiniciar somente o app; não precisa reconstruir. Reiniciar gera nova autenticação, enquanto os ciclos reutilizam a sessão. Diagnósticos habilitados rodam serialmente após o inventário. Para repetição, prefira diagnostic_seconds >= 300. Para operação prolongada, aumente poll_seconds para 900; a cadência de 30 é para desenvolvimento.

HTTP 404 desabilita a consulta opcional até reiniciar. Outros erros opcionais são resumidos sem payloads nem texto da exceção. 401/403 ou AuthenticationError levam ao cooldown. Erros desconhecidos no inventário também levam a cooldown conservador. Não há promessa de reconhecimento de todos os códigos proprietários de sessão inválida.

## Relatório

Os logs incluem horários, ciclo, modelos, firmwares, modo reportado e diagnósticos permitidos. /data/status.json contém o último ciclo e os diagnósticos em cache com sua própria data: um resultado anterior não deve ser confundido com nova medição. Falha do inventário marca o resumo indisponível.

ACS read pode retornar indicação de necessidade de otimizar, contadores, data e recomendações de canais. Um resultado vazio significa resposta aceita sem campos reconhecidos; não comprova suporte completo. Nenhum scan novo é solicitado. Resultado ACS pode ser antigo. Não inferimos interferência, ganho de velocidade ou qualidade de conexão desses dados.

## Segurança e sessão

A sessão permanece só em memória. O cooldown de 900 segundos é salvo antes da autenticação, zerado após sucesso e renovado após falha. Reiniciar após sucesso não gera espera; interromper login preserva a proteção. Cooldown legado é respeitado. Não apagar o estado para contornar falhas.

O SDK usa HTTPS sem validar o certificado; usar somente em rede local confiável. Configuração e backups do Supervisor contêm a senha. Logs do SDK estão desabilitados; não exportamos SSID, senha, tokens, cookies, nomes de clientes, IPs ou MACs. A consulta WLAN opcional recebe configuração com segredos em memória, mas sua projeção descarta tudo exceto canais e larguras. Não habilitar debug do SDK.

## Instalação e atualização local

Copiar a pasta inteira ezmart_deco_optimizer para /local_apps. Atualizar a loja, instalar/atualizar ou reconstruir uma única vez e aguardar conclusão. Não é necessário desinstalar. Depois, mudanças das opções acima só exigem salvar e reiniciar o app.

O Dockerfile mantém dependências antes do código para permitir cache de camadas. O Supervisor pode remover imagens durante rebuild, e há downloads de imagem e builder; não garantimos redução dos sete minutos. Imagens pré-construídas por arquitetura no GHCR são a etapa futura para acelerar instalações, após autorização de publicação.

## Evidências do piloto

HAOS 18.4, amd64, Supervisor 2026.11.0. M9 principal AP: três M9Plus 1.9.1, M5 1.9.5 e X50 1.11.1 reportados; modo FAP. Na dev.5, três ciclos e reinício após sucesso validados pelo usuário. ACS e diagnósticos adicionais desta versão pendentes de execução no equipamento. Falha real e cooldown ainda não homologados.

## Roteiro desta versão

1. Executar com ACS e capacidades habilitados, diagnostic_seconds 0 e poll_seconds 30.
2. Confirmar dois ciclos e guardar as linhas de Diagnóstico.
3. Se necessário, habilitar canais e desempenho pela configuração e reiniciar, sem rebuild.
4. Comparar canal atual e indicação ACS, lembrando que ACS read pode ser histórico.
5. Se ACS retornar 404, investigar diferenças de API local/app antes de propor escrita. Nenhuma tentativa em caminhos arbitrários ou no X50 secundário é automática.
