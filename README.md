# EZmart Home Assistant Apps

<img src="ezmart_deco_optimizer/icon.png" alt="The Modern House" width="128">

Base de desenvolvimento local do EZmart Deco Optimizer, versão 0.2.0-dev.1.

## Estado

Somente leitura: lista de nós (modelo e firmware) e modo de operação. Sem otimização, alterações de Wi-Fi, varreduras ACS ou exportação de configuração WLAN. Ainda sem sensores no Home Assistant ou interface web. Ícone oficial da The Modern House incluído no app.

Os arquivos run.py 0.1.10 e 0.1.11 não estavam disponíveis na conversa recuperada; esta implementação é nova e precisa ser comparada aos originais quando forem fornecidos.

## Desenvolvimento

O SDK 2.1.1 exige Python 3.14 ou superior. A execução está prevista no contêiner; não é necessário instalar o SDK no Mac. Dockerfile declara amd64 e aarch64; builds e instalação no Supervisor ainda precisam ser homologados.

Estrutura: repository.yaml na raiz e ezmart_deco_optimizer/ com config.yaml, Dockerfile, run.py, requirements.txt, README.md, DOCS.md e CHANGELOG.md.

Para inicializar o histórico local, execute nesta pasta:

```sh
git init -b main
git add .
git commit -m "Initial read-only development package"
```

Nenhum remoto deve ser adicionado até definir e autorizar o destino no GitHub. Antes da distribuição, escolher licença do projeto e acrescentar URL e suporte reais aos metadados. Não há licença de distribuição concedida por este pacote.

## Instalação futura

Depois de autorizar e criar um repositório GitHub, enviar esta estrutura com repository.yaml na raiz. No Home Assistant OS: Configurações → Apps (ou Complementos) → instalar → menu → Repositórios; adicionar a URL e instalar EZmart Deco Optimizer. O Supervisor construirá o Dockerfile localmente, pois não há imagem publicada. Isso permite instalação por repositório personalizado; não representa aprovação ou inclusão no catálogo oficial.

## Fontes verificadas

- https://contributors.home-assistant.io/apps/guides/repository/
- https://pypi.org/project/tplink-deco-api/2.1.1/
- SDK 2.1.1: cliente, transporte e modelos inspecionados no wheel público, sem instalação.
