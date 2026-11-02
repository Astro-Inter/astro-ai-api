# Continuação de PDF e treinamentos pendentes com período passado

## Causas confirmadas

O reteste da revisão `e811ea8` no tablet mostrou falha no PDF do resumo de vencimentos
e recusa na consulta “Quais treinamentos eu preciso realizar?”. Os traces autorizados
confirmaram causas diferentes; não se atribuiu a falha ao Jev, ao storage ou ao mobile
sem evidência.

**PDF:** após a consulta de vencimentos, “Gere um PDF com esse resumo de vencimentos”
seguiu para SST. Não houve reconsulta operacional de vencimentos nem chamada a
`gerar_pdf`: o resultado estava indisponível. O Juiz aprovou a limitação honesta.
O reconhecedor determinístico anterior cobria apenas uma formulação específica
de PDF de distribuição por cargo, não o resumo de vencimentos.

**Treinamentos:** o resultado continha atividades atribuídas pendentes com período
anterior à data da requisição. O formatador dizia “Começa”/“Termina”, sem explicar
o período passado. O Juiz pediu revisão por esse motivo. O guardrail preservou
a recusa, como exige o caminho de resposta determinística; nenhuma aprovação
foi forçada. Pendência de participação e data do evento são informações distintas.

Os traces brutos, perguntas com dados privados e identificadores ficaram apenas
no diretório local de diagnóstico, fora do Git. Este documento contém somente
evidências sanitizadas.

Uma verificação local real com dados sintéticos mostrou ainda que a candidata de
vencimentos era uma sequência de campos (`categoria`, `quantidade`) sem fonte no
texto a exportar. O Juiz pediu revisão por falta de preparação completa do resumo.
O formatador passou a descrever cada faixa e quantidade em texto simples, com fonte
e data quando disponíveis. O mesmo texto é revisado e usado no PDF; não se depende
do gráfico visual para fundamentar um documento textual.

Na sequência, o Juiz recusou uma candidata completa apenas por não existir ainda
um comprovante de geração. A revisão ocorre antes da geração: a aplicação passou
a indicar explicitamente essa fase aos revisores, com metadados internos fixos
`fase=revisao_conteudo_antes_geracao`, `geracao_executada=false` e geração condicionada
à aprovação. O texto ao usuário nunca afirma que o arquivo já foi criado nessa etapa.
Falta de evidência factual, contradições ou acesso indevido continuam exigindo recusa;
não há código convertendo uma decisão negativa em aprovação.

## Implementação

Pedidos completos e limitados de exportação de “esse resumo/gráfico/resultado”
podem selecionar a última consulta operacional da mesma sessão para:

- distribuição da equipe;
- pendências por NR;
- vencimentos por faixa;
- indicadores de cobertura.

O assunto explícito deve coincidir com a ação salva. Uma consulta incompatível,
contexto inválido ou mudança recente para outro assunto exige consultar o resumo
novamente. A seleção não interpreta instruções extras ou pedidos de outra empresa.
Pedidos de PDF com outros assuntos continuam nos fluxos existentes.

Os filtros são validados pelo contrato original e preservados, inclusive a referência
à listagem RH quando presente. O gráfico volta à primeira página de categorias,
com limite interno máximo já existente; o limite de linhas da tabela não se torna
base de cálculo. Não se usa texto histórico ou blocos antigos como evidência atual.
Cada consulta repete autorização e SQL com o usuário autenticado. A ação salva
não concede acesso nem altera o papel do usuário.

O fluxo passa novamente por Juiz/guardrail antes da geração. Consulta indisponível,
sem dados, não autorizada ou recusada não produz cartão de sucesso. Falha de upload
continua sem cartão. A entrega existente mantém id, prazo e URL no backend;
“Cadê o PDF?” reutiliza a entrega válida, sem nova consulta ou renovação de assinatura.
POST e histórico preservam `arquivo` v1; URLs temporárias não entram em prompts.

Treinamentos usam “Início”/“Término”, sem afirmar que um evento passado ainda vai
começar. Quando o término é de um dia anterior ao dia da requisição, o texto informa
“O período informado já terminou”. Se a participação continua pendente, isso é
explicitado, com orientação para conferir a atividade no aplicativo. Não se presume
presença, conclusão, vaga, reagendamento ou inscrição. A data da requisição vem
do contexto confiável e não de exemplos ou mensagens do usuário.

Datas sem fuso seguem a data de calendário fornecida pela consulta; não foi criada
uma hipótese de conversão de horário. Na falta de data utilizável, não se inventa
um período. Juiz recebe os mesmos dados reais; as regras passam a explicar que
pendências podem persistir após o período do evento, sem dispensar sua revisão.

## Apresentação

Avisos de gráficos deixam de expor “UID cadastrado” e explicam que o gráfico
considera todas as pessoas que correspondem aos critérios da lista. Em vencimentos,
a distinção entre registros e pessoas é explicada em uma frase; fonte e data de
referência continuam disponíveis. A data de referência é apresentada como
`dd/mm/aaaa`, sem alterar seu valor ou regra de cálculo.

Tabelas, gráficos, fichas e suas versões não foram removidos ou alterados. Os textos
novos e os blocos enviados no POST devem ser os mesmos no histórico. Respostas antigas
não são reescritas. Nenhuma chamada adicional de IA foi acrescentada para parafrasear
respostas: os formatadores montam o texto. A exportação contextual acrescenta a
consulta necessária para obter dados atuais; isso tem custo de banco/latência,
não uma nova chamada de classificação. A geração/revisão mantém seus custos existentes.

## Testes e limites

Regressões simuladas verificam seleção do resumo, assunto incompatível, filtros
preservados, contexto inválido, autorização autenticada, rejeição do Juiz, consulta
indisponível, upload com falha, reuso de id/prazo, ausência de URL em prompts e
igualdade POST/histórico. Treinamentos com datas passadas, atuais, futuras,
ausentes ou inválidas mantêm situação e evidência obrigatória, sem alterar a tool.

Uma verificação local real com Luna e dados inteiramente sintéticos aprovou a
resposta de treinamento com período passado e participação pendente. A execução
usou guardrail de entrada e Juiz reais, repositório/tool simulados e não acessou
dados privados. Consumo observado: 8.055 tokens / US$ 0,001035225. Isso não foi um
reteste da produção nem uma validação do armazenamento de PDFs.

Uma segunda verificação sintética real completou o caminho do PDF: consulta nova,
Juiz aprovado e bloco arquivo, com geração/upload simulados. A fixture foi corrigida
para que as colunas correspondessem às linhas; a validação de tabela permaneceu
ativa e não foi flexibilizada. Total de seis verificações pequenas, incluindo as
tentativas diagnósticas: 49.846 tokens / US$ 0,006431225 observados. Foram doze
chamadas de modelo. Nenhuma consulta privada ou upload real foi necessário.

O reteste no tablet após publicação autorizada deve repetir:

1. Consultar vencimentos → pedir “Gere um PDF com esse resumo de vencimentos”.
2. Repetir “Cadê o PDF?”, conferir o mesmo id/prazo e o arquivo v1 no histórico.
3. Consultar treinamentos pessoais, incluindo pendências com período já encerrado.
4. Conferir pendências atuais/futuras e limites de autorização com conta permitida.
5. Repetir gráfico da listagem e conferir critérios, total e texto simples.

Nenhum merge ou deploy é realizado nesta correção. A validação local de pipeline
e dos contratos não comprova upload real do ambiente de produção; essa verificação
continua necessária após publicação autorizada. Não se afirma que outros incidentes
de evidências, seleção de eventos ou materiais estejam resolvidos por este ajuste.


Validação final: **1.271 testes aprovados**, incluindo 20 regressões novas e os
cenários existentes de agentes, PDF, histórico, autorização e blocos. Os 15 avisos
são de dependências/recursos de teste, sem falhas. A etapa focada final teve 56
aprovações; os grupos se sobrepõem e não devem ser somados. Uma fixture sintética
com coluna ausente foi corrigida, preservando a validação das tabelas. Expectativas
de redação foram atualizadas para data legível e aviso sem jargão; verificações
de dados, permissões e igualdade POST/histórico permaneceram ativas.
