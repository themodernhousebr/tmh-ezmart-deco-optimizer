# Changelog

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
