# Varredura manual — 0.5.0-dev.1

Esta versão amplia o piloto autorizado: permite iniciar uma varredura de qualidade, além das leituras. Não envia aplicação de canais (0x4266), nem oferece otimização automática. As seções antigas descrevem o histórico de desenvolvimento; este roteiro prevalece para a varredura.

Após copiar a pasta inteira e atualizar uma vez, mantenha credenciais e chave TMP já validadas. Opções novas:

```yaml
tmp_enabled: true
tmp_action: scan
tmp_scan_request_id: piloto-001
tmp_scan_timeout_seconds: 90
```

Salvar e iniciar/reiniciar somente o app. Isso autoriza uma única varredura para o ID informado. Não mudar o ID para repetir enquanto houver resultado incerto ou operação ainda ativa. A varredura é uma ação ativa no rádio, embora não aplique canais explicitamente.

Para leitura passiva: tmp_action read. Para outra varredura deliberada, após a anterior terminar, escolher outro ID (letras, números, hífen ou underscore, máximo 48 caracteres), salvar e reiniciar; não precisa rebuild. Nenhum agendamento ou repetição automática está habilitado.

O identificador é salvo ANTES de enviar a ação. Reutilizar o ID retorna already_requested com o relatório anterior. Falha ou interrupção após envio não gera reenvio automático. Não apagar tmp_scan_journal.json para contornar isso. O histórico retém até 1000 IDs e depois bloqueia novas ações.

O teste lê o estado antes e só inicia se idle. Observa respostas por até tmp_scan_timeout_seconds (30–180) mais eventual requisição em andamento, de até 20 segundos. scan_finished significa que scanning/checking foi observado antes de idle. idle_after_ack significa que o comando foi aceito e a consulta seguinte já retornou idle: pode ter sido rápido ou o resultado pode ser antigo; não comprova uma medição nova. scan_timeout e scan_outcome_uncertain exigem conferir o app oficial e consultar estado, sem repetir automaticamente.

Resultado contém before/after de qualidade, horários, aceite da ação e channels_applied false (nenhum comando de aplicação foi enviado; isso não é medição independente de canais). Relatório local /data/tmp_scan_report.json. Estado não equivale a melhora real de desempenho. Enviar apenas linhas Diagnóstico tmp para análise.

Atualizar a imagem é necessário uma vez. As rodadas read/scan, ID e timeout passam a ser controladas pela configuração. Não desinstalar, pois isso pode remover o histórico de ações.


---

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

## TMP experimental — 0.4.0-dev.1

No piloto, TCP 22 recusou conexão, mas TCP 20001 respondeu SSH-2.0-dropbear. Isso confirma o serviço SSH, não o protocolo interno ou a autenticação. O transporte implementado segue a variante descrita em mthsg/pydeco-tmp, com ALLOC e leitura 0x4265. O módulo próprio só aceita esses dois códigos; não oferece scan, optimize, shell ou comando arbitrário. Não há fallback automático para outras variantes.

Opções novas, todas disponíveis após um único rebuild:
- tmp_enabled: false por padrão. Ativar para um teste por execução.
- tmp_email: e-mail da conta proprietária TP-Link, diferente do usuário admin HTTPS.
- tmp_password: senha dessa conta, inserida somente na interface do Supervisor.
- tmp_host_key_sha256: 64 caracteres hexadecimais da chave pública do M9.

Etapa 1: tmp_enabled true e os três campos restantes vazios. Salvar e iniciar/reiniciar. O resultado esperado é host_key_required com host_key_sha256; nenhuma senha é transmitida. Conferir identidade do IP na rede confiável. Esse primeiro contato não é verificação independente da identidade e pode ser comparado a uma leitura da chave por outra ferramenta na mesma rede. Não aceitar troca inesperada de chave.

Etapa 2: preencher a impressão digital observada e conferida, e-mail e senha. Salvar e reiniciar, sem rebuild. O módulo verifica a chave antes de autenticar; divergência bloqueia credenciais. Uma única tentativa de senha por execução, sem fallback para autenticação interativa. Falha de autenticação mantém cooldown separado de 15 minutos em /data/tmp_state.json. Não há tentativas automáticas no mesmo processo. Login confirmado remove cooldown de autenticação; falha posterior de protocolo não implica senha inválida.

Consulta de qualidade não solicita nova varredura: estado pode ser antigo ou inexistente. Não tratar idle ou optimal como prova de medição nova. Chaves SSH antigas/curtas ou algoritmos incompatíveis podem impedir handshake; a versão não reduz controles criptográficos automaticamente. Nesse caso registrar failed/handshake e investigar antes de mudar dependências.

Paramiko é dependência adicional no contêiner, sem instalação no Mac. Logs da biblioteca são suprimidos. Token ALLOC não é registrado nem persistido. Resposta TMP é filtrada para estados fixos e códigos numéricos. Relatórios incluem fase de falha, nunca exceções brutas, e-mail, hash de usuário ou senha.

Testes locais cobrem bloqueio de opcodes de ação, resposta fragmentada, checksum inválido, descarte de campos secretos, ausência de autenticação sem pin e respeito ao cooldown. Teste no M9 e build com as novas dependências ainda pendentes.

Timeout inicial SSH configurável: tmp_timeout_seconds 5–30 (padrão 10). Consultas TMP têm prazo total de 20 segundos por requisição. Roteiro consolidado na raiz do repositório: PILOT.md.
