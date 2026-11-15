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

