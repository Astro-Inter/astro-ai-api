# Homologação das consultas de gestão

Leituras feitas em 31/10/2026 com as identidades autorizadas de gestor e gestor de
workspace. Autenticação temporária por custom token Firebase; as senhas fornecidas
não foram exercitadas. Não foram criados usuários, inscrições, evidências ou
conformidades. Não houve migração, alteração de registros de negócio ou deploy.
Os scripts locais não são versionados e não imprimem tokens, nomes, e-mails,
identificadores de seleção, URLs ou arquivos. Apenas contagens/status constam aqui.

## Leituras reais

| Consulta | Gestor workspace | Gestor |
| --- | --- | --- |
| Matriz de conformidade | OK, 272 pares pessoa/NR; amostra de 3 linhas validada | OK, 236 pares; amostra de 3 linhas validada |
| Cobertura sem agendados/análise | OK, 157 pares; amostra de 3 | OK, 169 pares; amostra de 3 |
| Fila de validação do criador | Sem dados, total 0 | Sem dados, total 0 |
| Resultados de evento autorizado com participações | OK, 4 categorias; tabela/gráfico validados | OK, 4 categorias; tabela/gráfico validados |
| Evolução de conclusões | OK, 5 meses; tabela/gráfico validados | OK, 5 meses; tabela/gráfico validados |
| Conflitos na equipe autorizada | OK, 1 par | Sem dados, total 0 |
| Simulação de NR em cargo selecionado | OK, 1 categoria | Não autorizado, conforme regra |
| Origem de referência autorizada | OK, 1 registro | OK, 1 registro |

Contagens são retratos da leitura, não constantes de teste nem totais comparáveis
entre períodos/cargos diferentes. Filtros/referências foram obtidos dentro do
escopo autorizado. Resultados de evento passaram pela autorização Java com o token
do solicitante; respostas de participantes foram agregadas antes de retornar ao
modelo. Todas as saídas positivas foram validadas pelo contrato de tabela/gráfico.

Não havia conclusões pendentes em eventos ativos de modo colaborador criados pelos
gestores testados. Portanto não houve validação real de uma fila não vazia; os casos
positivos, criador diferente, status e metadados de arquivos são cobertos por mocks
e inspeção das regras Java. Não foram criadas evidências apenas para fabricar dados.

## Testes automatizados

Respostas simuladas cobrem as oito tools pelo registro LangChain e pelo endpoint
de chat, incluindo seleção de ação, contexto autenticado injetado, permissões,
filtros, contratos, Juiz/guardrail, gráficos e igualdade POST/histórico. Cobrem
negação de ADMIN/colaborador, exclusividade da simulação, restrição de consultas
próprias, datas e parâmetros inválidos, erros do banco/HTTP, paginação vazia com
total preservado, resultados sem nome/foto de participante, status desconhecido,
agregação antes da página, inscrição versus envio em análise e limites.

Um defeito encontrado na primeira homologação foi corrigido: as assinaturas das
tools precisam declarar RunnableConfig para o LangChain injetar o contexto seguro.
Sem isso, todas retornavam negação por falta de identidade. Há regressão específica
para garantir que UID/perfil vêm do contexto e não aparecem como argumentos.

O fluxo multiagente foi testado com modelos simulados: não houve chamadas reais de
LLM/embeddings nesta implementação, portanto consumo observado desses provedores foi
zero. Isso não mede custo real de uso das ferramentas em produção. As leituras
usaram PostgreSQL e o único GET Java previsto por evento, sem fan-out por pessoa.
A expansão do catálogo e do schema aumenta o prompt de roteamento; não foram
adicionadas revisões ou gerações por linha.

## Ainda necessário

- Homologação visual no mobile/web com as novas consultas após publicação da API.
- Fila real não vazia quando houver envios elegíveis do criador, sem inventar dados.
- Revisão funcional de critérios de simulação/cobertura pelo responsável de SST;
  ferramentas expõem hipótese cadastral e não certificam aptidão ou conformidade legal.
- Configuração runtime de Postgres/Java e permissão de leitura já usada pelas tools
  existentes; nenhuma nova chave de LLM e nenhuma alteração de schema é necessária.
- Merge e deploy não realizados. Referências de evidência/formulário não autorizam
  leitura de seu conteúdo; não foram adicionados downloads ou ações de escrita.

Validação final: **939 testes passaram**, com 15 avisos de dependências; compileall e diff --check passaram. A leitura real validou contratos positivos das sete consultas com dados disponíveis; a fila de validação ficou sem dados no escopo testado.
