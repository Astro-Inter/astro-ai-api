# Contexto da sessão — diagnóstico e correção (SCRUM-167)

## Evidências do export e limites da análise

O export local foi lido integralmente, sem ser copiado para o Git. Foram
examinadas **245 sessões e 1.066 mensagens**: 533 perguntas e 533 respostas.
Há 93 mensagens com blocos, incluindo 59 tabelas, 24 fichas e 8 arquivos.
Três sessões ultrapassam 20 mensagens; a maior tem 28. Duas respostas textuais
ultrapassam 6.000 caracteres. Estes são totais do arquivo analisado, não métricas
atuais de produção. Nenhum identificador ou conteúdo pessoal foi reproduzido
nas fixtures, nesta documentação ou na PR.

Foram encontrados pedidos de esclarecimento após listagens, fichas e explicações.
Esses marcadores não comprovam isoladamente uma falha: há perguntas realmente
ambíguas e mudanças legítimas de assunto. As causas abaixo foram confirmadas
pela inspeção do código e por regressões com dados fictícios. O export não
contém traces, decisões completas dos agentes ou a revisão de cada resposta;
não permite atribuir cada recusa a uma causa específica nem medir a taxa real
de falhas do provedor.

## Causas confirmadas

| Causa no backend | Consequência | Correção |
| --- | --- | --- |
| `_history_for_agent` não incluía FAQ, Orquestrador, Juiz e guardrail de saída | Agentes que dependiam de uma referência anterior recebiam apenas o pedido atual e a evidência do turno | Histórico limitado também para esses agentes |
| A projeção de histórico usava somente `content` | O resumo “100 de 141” ou “confira a ficha” chegava ao modelo sem os dados presentes nos blocos | Projeção privada de tabelas, fichas, gráficos e metadados; contratos públicos preservados |
| Histórico global cortado nas últimas 20 mensagens e nos 24.000 caracteres | Turnos anteriores ficavam invisíveis; uma resposta muito longa podia remover o próprio último turno | Último par preservado com omissão explícita; índice e recuperação determinística de trechos anteriores da mesma sessão |
| `buscar_meus_dados` substituía `consulta_rh` por `None` | A listagem perdia seus filtros após consultar a própria ficha | Preservação da última consulta de listagem; nova listagem atualiza os filtros |
| Referência de gráfico dependia da última rota ser RH e de uma ordem restrita das palavras | “Com base nessa listagem, mostre a equipe por cargo em um gráfico de barras” falhava após outro assunto | Reconhecimento completo dessa referência e reaplicação validada dos filtros, sem depender da última rota |
| Referência “dessa pessoa” dependia de uma frase textual antiga | Uma ficha estruturada não era reconhecida como referente inequívoco | Metadado privado da ficha única de terceiro; tool revalida o acesso |
| “PDF com esses dados” não relacionava a ficha própria ao usuário autenticado | Roteamento podia pedir novamente os dados ou escolher outro domínio | Após ficha própria, consulta novamente `buscar_meus_dados` usando o UID autenticado, seguida das revisões habituais |
| Uma consulta não operacional apagava `consulta_operacional` | PDF explicitamente baseado na distribuição anterior perdia os filtros | Preservação da referência operacional entre assuntos; nova consulta operacional substitui a anterior |
| A busca FAQ recebia só a pergunta referencial, como “detalhe essas orientações” | Mesmo com histórico no agente, a busca podia não recuperar o tema original | Acrescenta o assunto anterior em referências FAQ reconhecidas; continua sendo uma única busca |

## Mecânica e limites

`app/modules/chat/session_context.py` prepara contexto **por requisição**, depois
de carregar a sessão do UID autenticado no Mongo e adquirir o lock. O Mongo
continua sendo a fonte canônica; checkpoints de outro worker ou de um processo
reiniciado são reconciliados a partir dele. A memória vetorial de outras sessões
não é necessária para lembrar a sessão em andamento.

1. Projeta até 20 mensagens recentes, limitadas a 24.000 caracteres. Cada mensagem
   fica limitada a 10.000 caracteres; o último par não é descartado. Cortes mantêm
   início/fim e são marcados explicitamente.
2. Acrescenta blocos reconhecidos às respostas anteriores para uso interno.
   Tabelas entram como **amostra de até 8 linhas**, mais até 4 linhas cujo nome
   completo ou e-mail seja citado no pedido atual. Mantém metadados de total e
   sinaliza linhas omitidas. Não altera as linhas salvas nem entregues ao cliente.
   O texto-base usa até 3.000 caracteres e os blocos até 6.000.
3. Para turnos anteriores ao último par, produz um índice de perguntas e trechos
   de até quatro turnos (primeiro e três selecionados por termos do pedido e
   recência), com orçamento total de 6.000 caracteres. Inclui os turnos que podem
   ficar fora da janela específica de um agente. Não há busca em outra sessão.
4. Aplica também as janelas de cada agente: entrada/roteador/Juiz/saída até
   6 mensagens e 6.000 caracteres; RH/SST/FAQ/Orquestrador até 10 e 12.000;
   Agenda até 20 e 12.000. O contexto anterior de até 6.000 caracteres é adicional.
5. Dados históricos são explicitamente não confiáveis: não autorizam operações,
   não substituem consultas atuais nem comprovam cadastros, contagens ou normas.
   Juiz e guardrails permanecem responsáveis pela validação. Referentes múltiplos
   ou filtros não recuperáveis pedem esclarecimento.

Esta estratégia não envia a transcrição inteira sem limite e não garante recordar
literalmente qualquer detalhe arbitrário de uma conversa longa. Mantém contexto
recente, índice e trechos relevantes, com omissões visíveis. Permanecem os limites
existentes de 200 mensagens e 240.000 caracteres textuais por sessão. Se for
necessária recuperação exata de um detalhe omitido, o bot deve pedir esclarecimento;
não deve inventar nem afirmar que consultou um dado sem ferramenta.

### Filtros e agregações

Sessões legadas podem recuperar a consulta a partir de uma tabela de usuários e
da pergunta original **inteiramente reconhecida**. Não se infere autorização ou
filtros a partir dos nomes ou das linhas. Uma pergunta não suportada ou uma nova
listagem que falhou impede reutilizar silenciosamente filtros mais antigos.
Filtros inválidos pedem esclarecimento. As ferramentas sempre revalidam o papel,
a empresa e o escopo do UID atual, inclusive em retomadas de sessão.

O gráfico referenciado refaz a agregação no conjunto completo autorizado pelos
filtros. `limite` e `pagina` da tabela não são copiados para os cálculos. A paginação
visual continua local no web/mobile; nenhuma nova página é buscada pelo chatbot
para renderizar os controles do cliente.

### Privacidade e custo

Arquivos e galerias não entram na projeção de blocos. Fotos, URLs, identificadores
privados e campos de segredo são removidos; URLs presentes no texto também são
omitidas. Nomes de pessoas nos blocos são reconhecidos antes da pseudonimização
por chamada, inclusive em JSON abreviado. Os tokens reversíveis não são gravados
no histórico público. Nenhuma URL de download é renovada por este mecanismo.

Não há novas chamadas de IA, embeddings adicionais, novas chaves Redis ou
reindexação. A busca FAQ já existente recebe assunto + pergunta somente em
continuações reconhecidas, até aproximadamente 2.600 caracteres. Novos assuntos
e mudança de domínio preservam a consulta original. O custo de entrada e a
latência do modelo podem aumentar porque mais contexto útil passa a ser enviado;
os orçamentos acima limitam esse aumento. Consumo adicional real de API nesta
validação: **zero** (modelos, armazenamento e consultas simulados).

## Compatibilidade e homologação

Não há alteração do JSON do POST, dos blocos públicos ou do histórico. Metadados
como `pessoa_em_foco` são internos e não são retornados ao cliente. O backend não
reescreve mensagens antigas nem atualiza fichas já exibidas. Web e mobile devem
continuar enviando o mesmo `session_id` ao prosseguir e separando sessões por conta.
Criar outro identificador inicia outra conversa; este ajuste não tenta uni-las.

Testes de regressão em `tests/test_session_context.py` e
`tests/test_session_context_chat.py`, junto aos testes existentes de checkpoints,
chat, permissões, PDF, agentes e guardrails. As fixtures são fictícias. Há cobertura
para reinício de worker, igualdade POST/histórico, rejeição do Juiz/entrada,
isolamento entre sessões e acesso de outra conta.

Após uma publicação autorizada, homologar na mesma sessão:

- “Liste 100 usuários da minha equipe em uma tabela.” → “Meus dados” →
  “Com base nessa listagem, mostre a equipe por cargo em um gráfico de barras.”
  Confirmar filtros e agregação completa, mesmo com paginação visual local.
- “Meus dados” → “Gere um PDF com esses dados.” Confirmar consulta pelo próprio
  UID, arquivo somente após validação e mesma representação no histórico.
- Consultar a ficha de **uma** pessoa autorizada → “Como está a conformidade
  dessa pessoa?” Confirmar nova consulta autorizada. Com mais de uma pessoa,
  solicitar identificação em vez de escolher arbitrariamente.
- Pergunta sobre política interna disponível no FAQ → “Pode detalhar essas
  orientações?” Confirmar fonte atual, sem transformar a resposta antiga em prova.
- Retomar o assunto inicial depois de várias trocas e após reiniciar o backend.
  Abrir outra sessão e conferir que o assunto não é herdado automaticamente.

A aprovação em testes simulados não substitui homologação com respostas reais de
modelos, que podem variar. Esta entrega não faz merge nem deploy e não modifica
Qdrant, Redis, usuários ou dados de produção.

## Resultado da validação local

Foram executados 1.130 casos: os 1.109 testes preexistentes passaram, assim como
20 dos 21 novos na rodada completa. A única falha foi de uma fixture nova:
a pergunta usada para iniciar FAQ pedia uma publicação de ergonomia e acionava
corretamente SST. A fixture passou a pedir explicitamente a política interna,
sem alterar o roteamento da aplicação. Os **21 novos testes foram reexecutados
e passaram**. Nenhum código da aplicação mudou após a rodada completa.

As chamadas de modelos, ferramentas de negócio e armazenamento dessas regressões
foram simuladas. Continuam pendentes publicação autorizada e homologação real.
