# Validação de CI

O workflow `CI` roda nas PRs, nos pushes da `main` e manualmente. Pushes em branches
de PR não geram uma segunda execução igual. O job de testes usa Python 3.12,
instala o projeto com dependências de desenvolvimento, executa `pip check` e toda
a suíte `pytest -q`. Depois do sucesso, constrói a imagem Docker e verifica a
importação da API. Tracing remoto e exportação OpenTelemetry ficam desabilitados
nos testes; nenhum segredo de produção é necessário.

O workflow de publicação mantém sua própria validação. Em PRs, os jobs de
publicação e atualização GitOps continuam bloqueados pela condição de evento e
branch. Corrigir/reexecutar o CI de uma PR não promove a aplicação para produção.

## Falhas corrigidas na PR #45

- O prompt completo do roteador tinha 11.521 caracteres e excedia o limite de
  10.000 do teste de engenharia de prompts. Foi compactado para 9.649, preservando
  comandos, regras de autorização, rotas, contexto e exportação PDF. O limite
  continua sendo testado, sem aumento nem exclusão do teste.
- O teste de privacidade do resumo buscava a palavra `blocos` em todas as
  mensagens, inclusive nas instruções de sistema, e acusava vazamento por uma
  menção legítima aos blocos estruturados. Agora verifica o payload JSON de dados:
  somente data inicial, resumo parcial e mensagens públicas de papel/conteúdo.
  A URL privada e os metadados privados continuam proibidos. O caminho de produção
  da memória não precisou ser alterado.

Essas duas falhas também derrubavam a validação do workflow de publicação e
impediam o build Docker de rodar. Não eram falhas de instalação de dependências
nem justificavam remover validações ou acrescentar credenciais ao CI.
