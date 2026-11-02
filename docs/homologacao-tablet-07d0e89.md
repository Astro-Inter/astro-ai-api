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

## Recusa da definição de NR-2

Na captura posterior, o trace de 02/11 às 17:51:46 de São Paulo confirmou conteúdo
fundamentado; o Juiz pediu revisão por excesso de detalhes e campos técnicos,
contrariando o pedido anterior de respostas simples. Não houve decisão do Jev
que justificasse apresentar essa pergunta como proibida. A mensagem genérica
também não distinguia consulta concluída de revisão da explicação.

Perguntas breves de definição (“O que é a NR 2?”, “Sobre o que é a NR-2?”) agora
recebem título, situação e fonte; para norma não revogada, acrescentam um trecho
curto disponível do objetivo/resumo. Quando revogada, não repetem dados internos
de reciclagem/aplicabilidade como obrigações atuais. Pedidos de detalhes mantêm
o formato detalhado e a evidência completa. “NR2” também chega à consulta sem
exigir espaço. Não há conhecimento regulatório inventado no formatador: situação
e conteúdo vêm da ferramenta, e o Juiz continua avaliando a candidata final.

Se a consulta de NR concluir mas a revisão ainda não aprovar, o texto explica:
“Consultei as informações da norma, mas a explicação ainda precisa de revisão.”
Não expõe o motivo bruto do Juiz, não exibe dados reprovados nem força aprovação.

Verificação externa: a página oficial do MTE identifica a NR-2 como Inspeção
Prévia, revogada. A consulta do aplicativo continua utilizando seu fluxo oficial
de fontes, e não uma resposta estática específica para a NR-2.

Fonte: [MTE — NR-2](https://www.gov.br/trabalho-e-emprego/pt-br/acesso-a-informacao/participacao-social/conselhos-e-orgaos-colegiados/comissao-tripartite-partitaria-permanente/normas-regulamentadora/normas-regulamentadoras-vigentes/norma-regulamentadora-no-2-nr-2).

## Entrada sem sentido e indisponibilidade temporária

A captura de 17:58 mostrou uma entrada sem sentido e o erro genérico do mobile.
Os traces da janela confirmaram `rate_limited` às 17:57:49 no roteador e às
17:57:54 no guardrail de entrada. Isso registra limitação temporária do provedor,
não uma proibição do texto nem falha ao interpretar aquelas letras. O trace
sanitizado não inclui a mensagem original, portanto a correlação é pela janela
e não por identificação inequívoca do request da captura.

Uma nova tentativa real no mesmo tablet com `Hshshehehebdbdne` recebeu:
“Não entendi sua mensagem. Pode explicar o que você gostaria de consultar ou
fazer no Astro?” A primeira captura ainda não mostrava a resposta; a tela foi
reobservada antes de concluir. Essa chamada adicional não reproduziu a queda.

Para `ChatError.reason=rate_limited`, a API continua retornando HTTP 503 e
`Retry-After` quando fornecido. O limite vem do serviço externo, não demonstra
excesso causado pela pessoa e não deve virar uma recusa de segurança. O `detail`
agora explica o limite temporário de solicitações e que a mensagem não foi concluída.
Mantém o contrato `{detail: string}`, as tentativas limitadas e o tratamento dos
demais erros. Não salva uma resposta de sucesso nem perde a conversa existente.

Web/mobile devem usar esse detalhe seguro da API para o erro de envio e respeitar
`Retry-After` antes de tentar novamente; não repetir automaticamente em laço.
Se o cliente substitui qualquer 503 por “assistente indisponível”, a frase nova
não aparecerá até esse ajuste no cliente. Não exibir corpo bruto do provedor,
stack trace, tokens ou mensagens internas. O fato de a API explicar o erro não
remove limites de capacidade do serviço externo nem comprova solução da quota.

Testes simulados cobrem limite nas duas etapas, ausência de dados privados,
preservação da sessão, sucesso após nova tentativa e esclarecimento de texto
incompreensível. Não foi adicionada heurística de letras aleatórias que possa
bloquear nomes, siglas ou códigos legítimos.

## Continuação: situação geral após gráfico de pendências

Na janela das 18h03 de 02/11/2026, o trace registra `consultar_nrs_organizacao`
e revisão pelo Juiz: os vínculos de NRs não respondem à situação geral de
conformidade. O problema confirmado é a escolha da consulta; a recusa não deve
ser contornada. Os textos dos traces são ocultados por privacidade, portanto
a correlação com a captura usa horário e sequência, sem afirmar identidade de request.

“Tá tudo nos conformes na empresa?” e “empresa inteira e quero saber se tá tudo
nos conformes” agora executam `consultar_indicadores_cobertura`. Consultam novamente
o cadastro autorizado completo, sem contar barras históricas nem herdar a seleção
de uma NR do gráfico anterior. A consulta continua excluindo o solicitante e usando
cadastros ativos, vínculos cargo/NR e unidade/NR e as restrições SQL já existentes.
Isso é uma visão da equipe autorizada, não de todos os cadastros da empresa.

Para Gestor, o texto explica o limite à própria unidade. Para Gestor do Workspace,
explica que a equipe consultada pertence ao workspace autorizado. Não amplia
permissões nem promete uma auditoria da empresa inteira. Colaborador continua
sem acesso a esses indicadores de equipe.

A introdução explica exigências com registro vigente, em análise, agendadas e sem
encaminhamento, com próximo passo. Sem exigências ou mesmo com todos os registros
vigentes, não certifica conformidade legal ou aptidão. O gráfico continua medindo
pares pessoa/NR sem encaminhamento, não colaboradores únicos. Fonte, referência,
critérios e limites permanecem nos blocos analíticos existentes.

O texto passa pelo mesmo Juiz antes da liberação e é preservado como bloco `texto`
antes dos indicadores neste fluxo. POST e histórico guardam a mesma ordem e conteúdo.
Sem dados confiáveis, falha de consulta ou recusa, não cria indicadores de sucesso.
Não há tipo novo de bloco nem chamada extra de IA. Web/mobile devem renderizar os
blocos na ordem recebida; a paginação visual segue local.

198 testes passaram no grupo de regressões de situação geral, gráficos, ferramentas
analíticas, esclarecimentos e contratos dos prompts, com respostas simuladas.
Repetir no tablet após publicar a revisão: gráfico de pendências → “tá tudo nos
conformes na empresa?” → “empresa inteira e quero saber se tá tudo nos conformes”.
Esta correção não foi publicada em produção durante o desenvolvimento.
