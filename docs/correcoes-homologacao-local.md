# Correções da homologação local do chatbot — 02/11/2026

## Causas e alterações

### Cobertura em rosca

O contrato aceitava `grafico_rosca`, mas `build_analytic_blocks` não incluía a
classe correspondente no mapa de serialização. Uma consulta aprovada podia
terminar em erro em vez de retornar seus blocos. O mapa agora inclui
`BlocoGraficoRosca`, preservando ordem, título, categorias, valores, fonte e unidade.
Testes cobrem valores positivos e zero, POST e histórico. Nenhum valor é criado.

### Barras depois de uma listagem

A consulta já agregava os dados autorizados antes do limite de categorias, sem
calcular sobre as linhas da tabela anterior. A apresentação e a candidata não
explicitavam juntas todos os critérios: a fonte agora informa os filtros realmente
aplicados e, quando há referência à listagem, a exclusão da própria conta usada
pela listagem de outros usuários. A candidata identifica tabela e gráfico como
componentes da agregação completa; a evidência de apresentação informa completude.

As recusas anteriores do Juiz não tinham seus motivos completos coletados nessa
rodada; não se atribui a elas uma causa única comprovada. Após o ajuste, o fluxo
real de 100 usuários → barras por cargo foi aprovado para as duas contas. O Juiz
mantém o mesmo contrato, prompt e poder de recusar. Aprovação nessa rodada não
garante aprovação futura de qualquer conteúdo. Foi removido um fallback que
aprovava candidatas determinísticas quando a resposta do Juiz continuava inválida
após as tentativas. Esse caso agora é recusado e não persiste conteúdo como aprovado.

### Eventos e atividades pendentes

O parser transformava “meus eventos pendentes” em eventos futuros. Datas e
situação da participação são filtros independentes. O contrato interno
`ConsultarEventosArgs` ganha o campo opcional `situacao`, com valores `todos`
(padrão compatível), `a_realizar` e `concluidos`.

Exemplo de leitura de pendências pessoais:

```json
{"acao":"consultar_eventos","filtros":{"proximos":false,"situacao":"a_realizar","pagina":1,"limite":5}}
```

`a_realizar` mantém os critérios existentes: evento ativo e participação pendente,
rejeitada ou sem registro de conclusão. Só restringe a futuros quando o pedido
disser “próximos”. UID vem exclusivamente da autenticação. Não recebe identidade
de outra pessoa. Falha de consulta continua diferente de conjunto vazio.

O cabeçalho da consulta de participações usa “Atividades atribuídas a você em
eventos do Astro”: o banco pode devolver reuniões e outros eventos, sem classificação
inequívoca de treinamento. Não inventamos essa categoria pelo título. “E
treinamentos?” após contexto pessoal e a pergunta de atividades após o texto
pessoal de NRs consultam participações reais, em vez de repetir os títulos das NRs.
Referências de terceiros e contexto de equipe não ativam esses atalhos pessoais.

### Continuidade do catálogo de gráficos

Um teste real de “Quero um gráfico de rosca” recebeu esclarecimento no guardrail
probabilístico antes de chegar ao catálogo fechado. Pedidos exatamente reconhecidos
pelo catálogo e seleções validadas do menu da sessão agora têm seu esclarecimento
encaminhado ao próprio catálogo da aplicação. O guardrail continua executando;
`bloquear` sempre interrompe o fluxo. Texto livre e instruções adicionais não
entram nesse tratamento. O menu continua sendo aprovado pelo Juiz antes de ser salvo; cada consulta
verifica o perfil atual e os resultados também passam pelo Juiz e pela saída.
Não se guarda resultado recusado como dado autorizado para turnos seguintes.

## Verificação real limitada

Testes automatizados: 432 passaram na primeira suíte ampliada; após os ajustes
finais, 525 passaram cobrindo contratos, ferramentas, contexto, Juiz, catálogo,
escopo, conteúdo e histórico. Modelos simulados nesses testes, com dois avisos de
dependências no Python local. Os bloqueios do guardrail e a falha de contrato do
Juiz têm regressões explícitas.

Foram usados as duas contas autorizadas, ferramentas com leitura real e modelos
OpenRouter, com pseudonimização. Sessões de diagnóstico ficaram em memória,
sem escrever novas conversas no MongoDB, reindexar vetores ou alterar cadastros.
Esses testes exercitam o serviço, não substituem login HTTP ou homologação visual.

- Ambas as contas: tabela de 100 e barras por cargo na continuação aprovadas.
- Ambas as contas: seleção de cobertura em rosca entregou `indicadores`, `tabela`,
  `grafico_rosca`, `contexto_dados`; Workspace foi repetido após corrigir o catálogo.
- Gestor: eventos pendentes e “E treinamentos?” consultaram as ferramentas corretas;
  os resultados atuais foram vazios. Cenários positivos com participação passada
  pendente são cobertos com dados simulados, sem alterar os cadastros reais.
- Pedido adicional “Mostre o resto que falta”: após 100 de 141, retornou nova
  tabela completa com 141, repetindo os 100 anteriores. Não entregou somente os
  41 adicionais. Após essa rodada, a política foi alterada a pedido do usuário:
  a listagem inicial já busca o conjunto completo até 150. Não foi acrescentada
  paginação de backend ou entrega incremental. Ver detalhes no contrato de tabelas.
- Consumo reportado nas quatro rodadas: 32 respostas de chat, 167.980 tokens de
  entrada, 1.111 de saída e aproximadamente US$ 0,02094. São valores capturados
  dessas chamadas, não custo total do sistema ou de infraestrutura.

## Clientes, histórico e limites

### Ajuste posterior: conjunto completo até 150

A listagem agora ignora o campo legado `limite` como corte de quantidade e busca
todo o conjunto autorizado até 150, com o mesmo orçamento de 2 MiB e lotes internos
de 50. O bloco continua tabela v2, sem propriedades novas. `solicitada` passa a
representar o total pretendido pela política; metadados internos registram a
quantidade mencionada no filtro. Contagem e gráficos não são cortados por 150.
Histórico antigo mantém seus metadados originais. A mudança vale para novas consultas.

376 testes passaram na suíte de listagens, contratos, ferramentas, histórico e
regressões anteriores. O teste real final retornou 141 de 141 já no pedido de 100;
a continuação retornou os mesmos 141 com critérios preservados e informou que é
uma tabela substituta, não registros adicionais. Ambos foram aprovados pelo Juiz.
Essa última rodada reportou três chamadas, 15.878 tokens de entrada, 136 de saída
e US$ 0,002052525; rodadas intermediárias de diagnóstico também consumiram API.
Nenhum deploy ou merge foi executado.

Não há novo tipo público de bloco: web e mobile que já aceitam rosca v1 continuam
compatíveis. Tabelas seguem v2 e paginação local, com limites anteriores. A extensão
de filtros de Agenda é interna. O histórico mantém os blocos produzidos no turno;
não reconta pendências nem substitui respostas antigas retroativamente.

O relatório anterior de PDF continua válido: geração, recuperação do mesmo ID/prazo
e download passaram, sem renovação da assinatura. CORS para fetch pelo armazenamento
não foi alterado; a navegação em nova aba usada pelo web é independente desse CORS.
Não houve novo teste visual porque o controle do navegador estava desativado.
Não há merge nem deploy de produção autorizado por este trabalho.

## Perguntas para repetir no web e mobile

1. “Liste 100 usuários da minha equipe em uma tabela.”
2. Na mesma sessão: “Gere um gráfico de barras por cargo com base nesses dados considerando todo o escopo autorizado.”
3. Nova sessão: “Quero um gráfico de rosca” → “cobertura”.
4. “Quais são meus eventos pendentes?” → “E treinamentos?”
5. Após consultar as próprias NRs: “Quais são as atividades pendentes?”

Conferir os mesmos blocos ao reabrir o histórico e a paginação da tabela sem
chamadas ao chatbot. Uma consulta vazia não comprova ausência de outras categorias
de pendências nem conclusão das NRs do cargo.
