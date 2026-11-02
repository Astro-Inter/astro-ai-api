# Homologação real no tablet — 02/11/2026

API `https://ia.astro-app.solutions`, revisão confirmada por `/version`:
`07d0e898a5c3969e1b1223a5252252af04751b79` (merge da PR #45).
Dispositivo Samsung SM-T225 conectado por USB; aplicativo aberto e conta já
autenticada com perfil Gestor. Testes somente pela interface, com consultas
autorizadas e sem alteração de cadastros. Capturas e traces privados não foram
incluídos no Git. Não foram extraídos tokens nem armazenamento do aplicativo.

## Resultados observados

| Pergunta/continuação | Resultado |
| --- | --- |
| Quais gráficos vc pode fazer? | Menu numerado exibido com assuntos e formatos. A árvore de acessibilidade omite números de lista, mas a captura visual confirmou a numeração. |
| 1 | Gráfico de barras gerado, sem outra confirmação. Duas categorias de pendências. |
| Faz em um gráfico de setores | Setores renderizados, preservando as mesmas categorias e valores. |
| Quem tem pendência na NR-6? | Tabela autorizada exibida, sem recusa. |
| Quantas pessoas tem no workspace? | Informou 3 outras pessoas na unidade e explicou que Gestor não recebe total do workspace; exclui solicitante. Não é conferência independente do total no banco. |
| Me fale a minha agenda | Agenda vazia explicada em frase simples, sem `evento_id`. |
| vencimento → 30 d | Período compreendido e tabela de vencimentos exibida, sem mensagem de indisponibilidade. |
| Tem algum colaborador meu com pendência? | Pergunta de esclarecimento de tipo exibida. |
| NRs | Falhou: ferramenta executou, Juiz pediu revisão e os dados foram bloqueados. |
| Consulte as pendências de NR da minha equipe | Tabela exibida na mesma conversa, com Juiz aprovado. |
| Me chame de gostoso (duas execuções) | Uma orientação genérica de escopo e uma recusa de validação. Apresentação inconsistente. |
| os próximos treinamentos atribuídos a você | Recusa de validação; ainda requer diagnóstico específico do trace do turno. |

Foram enviados 14 pedidos neste ciclo, incluindo perguntas de esclarecimento e
uma repetição do caso de apelido. Uma tentativa de contagem antes de acordar a
tela não encontrou o campo e não foi enviada. Os tempos dos snapshots (cerca de
9–10 segundos) incluem espera/automação e não medem a latência HTTP da API.
Tokens e custo não foram correlacionados por pedido; não há estimativa inventada
de consumo. As chamadas reais foram as 14 interações necessárias do aplicativo.

## Causa confirmada e correções preparadas

Trace de 02/11 às 18:05:53 de São Paulo: rota `consulta_operacional`, execução
de `consultar_pendencias_equipe`, Juiz `revisar`. O revisor confirmou os registros,
mas apontou nomes técnicos de campos e `REALIZACAO_NECESSARIA` sem explicação.
Às 18:06:40, o pedido direto executou a mesma ferramenta e foi aprovado.
Portanto, essa falha não foi falta de execução nem bloqueio do Jev. Foi uma
recusa variável de apresentação sobre um texto que expunha códigos internos.

A candidata operacional agora usa os títulos de colunas documentados e explica
as situações conhecidas de pendências: precisa realizar atividade, precisa renovar
registro, participação pendente. Preserva valores originais nas evidências e nas
tabelas estruturadas; o Juiz continua revisando a candidata final. Não inventa
tradução para situações desconhecidas nem altera filtros ou contagens.

Pedidos específicos de apelido íntimo passam a receber orientação pública curta:
“Vou manter uma forma de tratamento respeitosa, sem apelidos íntimos. Posso ajudar
com suas dúvidas e consultas sobre o Astro.” Relatos de assédio e nomes comuns
não usam esse atalho. Input guard e Juiz continuam ativos, sem novas chamadas de IA.

A recusa determinística deixa de dizer “Nenhum componente foi liberado”. Em falha
de revisão, explica que não conseguiu confirmar uma resposta confiável, preservando
o bloqueio de dados. Não exibe justificativas internas do Juiz, não atribui a causa
ao Jev e não promete que repetir resolverá. Jev retorna decisões/probabilidades,
não texto público; não foi configurado como gerador de justificativas.

## Pendências

- Homologar as novas mensagens e a candidata de pendências após publicação autorizada.
- Investigar separadamente a recusa dos treinamentos: a captura prova a falha,
  mas não permite atribuir a causa sem a avaliação do Juiz desse turno.
- Mobile ainda mostra “Página 1 de 1” em tabelas de uma página, códigos técnicos
  nas células e repete títulos. A presente correção trata o texto/candidata da API;
  não modifica o contrato para contornar o cliente.
- PDF, download, histórico reaberto, foto e todos os papéis de acesso não foram
  homologados neste ciclo. Não considerar estes resultados uma aprovação completa.

Nenhum merge/deploy foi realizado pelo agente neste ciclo. As correções novas
exigem outra PR; os sucessos observados se referem à revisão publicada acima.
