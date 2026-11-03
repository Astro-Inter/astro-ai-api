# Pergunta: quais NRs preciso cumprir?

## Causa confirmada

O trace autorizado de 02/11/2026 às 15:53:49 UTC (12:53 em São Paulo) confirmou
`consultar_nrs_obrigatorias` com status `ok` e duas NRs vinculadas. O Juiz pediu
revisão porque a candidata dizia “vigentes” sem esse campo explícito na evidência
e apresentava o intervalo de reciclagem cadastrado como obrigação do colaborador.
Outra execução às 12:51 teve a mesma recusa. Não foi falta de resultados nem
bloqueio pelo Jev. Traces brutos e dados pessoais ficaram fora do Git.

A consulta SQL filtra normas não revogadas vinculadas ao cargo do UID
autenticado, conforme a tela de perfil. A unidade continua como contexto de
cadastro/autorização, sem eliminar vínculos de cargo por falta de `unidade_nr`.
Isso não comprova cumprimento, validade pessoal, periodicidade
legal aplicável a cada atividade ou todas as obrigações legais da pessoa.

## Alteração

A candidata informa somente as NRs vinculadas ao cargo cadastrado no Astro,
seguida de “Essa lista mostra os vínculos do cadastro; não confirma conclusão
ou validade das suas atividades”. O prazo genérico de reciclagem não é exposto
como obrigação pessoal nessa resposta. O valor original permanece intacto no
resultado da ferramenta e na evidência do Juiz, sem alteração de cadastro.

## Atualização: cargo com NRs e unidade sem vínculos

O caso das capturas de 02/11/2026 às 20h27 mostrou um segundo problema. O cargo
possuía NR-1, NR-5 e NR-32, mas a unidade não tinha esses vínculos. A consulta
anterior da IA fazia a interseção entre `cargo_nr` e `unidade_nr` e retornava
zero. A tela Java usa os vínculos de `cargo_nr` para “Minhas NRs”. O trace
confirmou resultado vazio da ferramenta, aprovado pelo Juiz; não era perda de
contexto ou bloqueio pelo Jev.

`consultar_nrs_obrigatorias` e `_consultar_situacao_nrs_filtrada` agora selecionam
as NRs não revogadas diretamente de `cargo_nr`. Os contratos de resposta
permanecem; somente a lista de tabelas da fonte deixa de citar `unidade_nr`.
O UID continua vindo exclusivamente da autenticação. Consultas de terceiros
mantêm resolução inequívoca e revalidação do escopo de unidade/workspace antes
de ler a situação, usando o mesmo critério de NRs do cargo da pessoa.

Ficha própria, ficha de terceiro e indicador pessoal de pendências herdam essa
correção. Três NRs vinculadas não são automaticamente três pendências: a
contagem continua distinguindo registro vigente, atividade pendente e necessidade
de realização/renovação. Não altera validade, conclusão ou cadastro.

As consultas de NRs da organização e as agregações de equipe continuam com seus
critérios próprios documentados. Não se altera o denominador das análises
coletivas nesta correção pessoal, nem a prioridade de seleção de registros de
validade. A ficha não certifica conformidade legal.

Testes executam a SQL de listagem sobre dados sintéticos com vínculos de unidade
ausentes, parciais ou completos, incluindo cargo sem NRs, outra pessoa e NR
revogada. Também cobrem situação, autenticação, restrições de terceiros,
pendências, apresentação e histórico. A verificação real limitada é somente
leitura, sem chamadas adicionais de IA.

Nesta atualização, 145 testes locais passaram em dois grupos: 74 para SQL de
vínculos, ferramentas SST e fichas/pendências; 71 para intenção pessoal,
apresentação, evidências, respostas específicas e histórico. Na leitura real
limitada do caso investigado, a listagem corrigida retornou NR-1, NR-5 e NR-32;
a situação retornou respectivamente pendente, vigente e realização necessária,
com duas pendências. Não houve alteração de dados ou chamada de LLM nessa leitura.

Resultado vazio informa que não foram encontrados vínculos nessa consulta;
não afirma ausência de obrigações legais. Consultas de situação pessoal,
vencimentos e detalhes de norma continuam nas suas ferramentas existentes.

Identidade continua exclusivamente no contexto autenticado, sem seletores de
UID/cargo fornecidos pelo usuário. Juiz e guardrail permanecem ativos; recusa
continua impedindo a entrega. Não foram adicionadas chamadas de IA, permissões,
blocos ou formatos novos. POST e histórico mantêm os mesmos blocos de texto.
Web/mobile não precisam alterar o parser para esta correção.

A PR #43 já estava mesclada e a produção consultada retornava
`01a8873af704357e4632c5f72f91696b66c70ac2`. Esta correção parte dessa main em
uma nova branch. Não houve merge ou deploy.

## Homologação

Validação: **324 testes aprovados**, incluindo quatro regressões novas, com
modelos e consultas simulados. Dois avisos de dependências locais, sem falhas.
Uma única revisão real do Juiz com dados inteiramente sintéticos aprovou a nova
candidata: 3.219 tokens e US$ 0,000418425 observados. Sem consulta de dados reais
ou alteração de produção nessa verificação.

Após publicação autorizada, perguntar “Quais NRs preciso cumprir?” e verificar
os vínculos do cadastro, sem afirmações de conformidade ou prazo de reciclagem
pessoal. Reabrir o histórico e conferir a mesma resposta. Perguntar depois
“Tenho pendências de NR?” para consultar a situação individual separadamente.
