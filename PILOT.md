# Um pacote, vários testes — 0.4.0-dev.1

## Atualização única

1. Parar o EZmart.
2. Copiar toda a pasta ezmart_deco_optimizer para /local_apps pelo SFTP, incluindo diagnostics.py e tmp_readonly.py.
3. Verificar atualizações na loja e atualizar para 0.4.0-dev.1. Atualização já constrói a imagem; não fazer rebuild adicional. Se a versão já aparece instalada mas apenas arquivos foram substituídos, reconstruir uma vez.
4. Aguardar a conclusão e iniciar.

## Primeira rodada sem credenciais TMP

Manter credenciais HTTPS existentes, poll_seconds 30, diagnostic_seconds 0.
Habilitar diagnose_channels, diagnose_performance e tmp_enabled. Manter tmp_email, tmp_password e tmp_host_key_sha256 vazios. tmp_timeout_seconds 10.

O app mantém diagnóstico HTTPS e faz um único handshake TMP. Resultado host_key_required mostra impressão digital SHA-256 hexadecimal. Se failed/handshake, nenhuma senha foi enviada. A chave pode ser incompatível com a biblioteca e isso exige análise, não tentativas de senha.

## Segunda rodada sem rebuild

Após conferir que o IP e a identidade correspondem ao M9 residencial, preencher tmp_host_key_sha256 com a impressão observada, tmp_email com e-mail da conta proprietária e tmp_password com senha TP-Link. Não usar admin como e-mail nem enviar os campos no chat. Salvar e reiniciar somente o app.

Resultado esperado: Diagnóstico tmp com state e details. fresh_scan_requested false indica explicitamente que o teste não iniciou scan. Estado vazio/desconhecido não será tratado como optimal. Não haverá repetição automática de senha no mesmo processo.

Se failed/authentication, corrigir as opções e respeitar cooldown de 15 min; não reiniciar em sequência. Se failed/channel, allocate ou quality_read, enviar somente a linha de diagnóstico; as credenciais podem ter funcionado, mas o protocolo ainda não foi confirmado. A variante não troca automaticamente de framing/handshake.

## Testes que não precisam de outra imagem

- Comparar inventário e canais: diagnose_channels e poll_seconds.
- Ativar/desativar CPU/memória: diagnose_performance.
- Diagnósticos HTTP somente na inicialização: diagnostic_seconds 0.
- Diagnósticos HTTP repetidos: diagnostic_seconds 300 ou mais; 404 permanece bloqueado até reiniciar.
- Descobrir identidade SSH: tmp_enabled true com pin vazio.
- Testar login e qualidade: tmp_enabled true, pin conferido e conta preenchida.
- Ajustar timeout inicial SSH: tmp_timeout_seconds de 5 a 30.
- Desligar todo acesso TMP: tmp_enabled false.

Salvar e reiniciar carrega opções. A imagem incorpora o código e dependências; alterações de código ainda exigem atualização/rebuild. Nenhuma senha é salva no repositório.

## Limites desta entrega

Sem scan, optimize, canais automáticos, agendamento ou promessa de compatibilidade TMP no M9. As referências públicas divergem em detalhes de protocolo, e só teste real pode validar a variante. A chave SSH é verificada antes de enviar credenciais, mas a primeira impressão observada não constitui comprovação independente de identidade. Bibliotecas SSH não têm controles criptográficos reduzidos automaticamente.

Uma futura etapa de scan/aplicação deverá ser preparada após confirmar leitura, parâmetros e autorização para alterar a rede. Não usar o report do script de terceiros como leitura passiva: ele inicia varredura.
