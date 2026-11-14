# Configuração e homologação

Informe host como IP do Deco principal, username (normalmente admin), password e poll_seconds (30 a 86400). Durante o desenvolvimento, use 30; para operação contínua, recomenda-se 900. O cooldown de autenticação permanece em 900 segundos. A inicialização automática está desabilitada nesta versão de desenvolvimento.

O mesmo cliente e sessão permanecem em memória entre consultas. Tokens e cookies não são gravados. O instante mínimo da próxima autenticação é persistido em /data/state.json durante cada tentativa e após falhas; o prazo é zerado após autenticação confirmada: reiniciar preserva o cooldown de 15 minutos. Restaurar backup ou alterar o relógio pode afetar esse prazo. Sessões entre reinícios exigem nova autenticação após o prazo; serializar chaves não faz parte desta versão.

Uma falha interrompe o ciclo sem reconexão imediata. HTTP 404 desabilita apenas a consulta afetada até reiniciar; não significa senha incorreta. acs_optimize não é consultado, e não há endpoints de escrita. Autenticação cria uma sessão administrativa e pode interferir em outra sessão do app Deco.

Logs não incluem respostas, exceções, senha, cookies, tokens, IP, SSID ou nomes de clientes. O resumo local contém somente modelo, firmware e modo reportados, além dos estados de consulta. As opções e backups do Supervisor contêm a senha: trate-os como dados privados. O SDK utiliza HTTPS com validação de certificado desabilitada; operar apenas na rede local confiável.

## Matriz de compatibilidade

| Equipamento | Evidência anterior | Pendente |
|---|---|---|
| X50 | Usuário relata autenticação admin funcional; acs_optimize read HTTP 404 | Build, consultas desta versão, estabilidade e sessão concorrente |
| M9 Plus AP | HTTPS acessível na conversa anterior | Autenticação e todas as consultas, firmware e comportamento em AP |

Modo AP não é inferido pelo modelo. Não são consultados endpoints WAN/DHCP. Modelos e firmwares podem variar na API; 404 é registrado como unsupported.

## Teste no HAOS

1. Construir e instalar em ambiente de desenvolvimento.
2. Configurar credenciais pela interface do Supervisor, nunca no Git.
3. Iniciar uma vez e conferir logs resumidos.
4. Aguardar dois ciclos e confirmar reaproveitamento de sessão.
5. Invalidar a sessão manualmente em teste controlado e verificar pausa de 15 minutos, inclusive após reinício.
6. Repetir para X50 e M9 Plus AP e registrar firmware, modo e resultado sem segredos.

Não há integração automática de entidades nesta fase. /data/status.json é um artefato interno do app.

A partir da dev.4, o resumo de modelos, firmwares e modo também aparece nos logs na primeira consulta e quando mudar.

Na dev.5, login bem-sucedido libera nova autenticação após reinício. Falhas e tentativas interrompidas preservam cooldown de 15 minutos. Um prazo herdado da dev.4 é respeitado, pois não há registro que permita distinguir sucesso de falha.
