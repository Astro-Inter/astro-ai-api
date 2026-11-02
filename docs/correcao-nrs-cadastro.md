# Pergunta: quais NRs preciso cumprir?

## Causa confirmada

O trace autorizado de 02/11/2026 às 15:53:49 UTC (12:53 em São Paulo) confirmou
`consultar_nrs_obrigatorias` com status `ok` e duas NRs vinculadas. O Juiz pediu
revisão porque a candidata dizia “vigentes” sem esse campo explícito na evidência
e apresentava o intervalo de reciclagem cadastrado como obrigação do colaborador.
Outra execução às 12:51 teve a mesma recusa. Não foi falta de resultados nem
bloqueio pelo Jev. Traces brutos e dados pessoais ficaram fora do Git.

A consulta SQL filtra normas não revogadas e cruza cargo e unidade do UID
autenticado. Isso não comprova cumprimento, validade pessoal, periodicidade
legal aplicável a cada atividade ou todas as obrigações legais da pessoa.

## Alteração

A candidata informa somente as NRs vinculadas ao cargo e à unidade no Astro,
seguida de “Essa lista mostra os vínculos do cadastro; não confirma conclusão
ou validade das suas atividades”. O prazo genérico de reciclagem não é exposto
como obrigação pessoal nessa resposta. O valor original permanece intacto no
resultado da ferramenta e na evidência do Juiz, sem alteração de cadastro ou SQL.

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
