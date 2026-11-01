# Revisão de bloqueios e roteamento Jev — SCRUM-167

## Evidências e limites

Revisão em 01/11/2026. `/version` e main retornaram `0d50610b4ed2a36772e7419bd357d0ebcb81b561` durante a investigação. As correções pertencem à PR #40; não houve merge ou deploy.

Foram examinados 99 runs raiz de chat no projeto LangSmith autorizado (mais um run de outro fluxo, excluído): 60 resolvidos, 20 respostas bloqueadas, 5 indisponíveis, 1 fora de escopo, 1 não autorizado e 12 erros sem resultado. Entre os 20 bloqueados, 19 chegaram ao Juiz e 1 parou na entrada. Isso não demonstra 20 falsos positivos nem 20 vetos do Jev.

A amostra informa `environment=development`; dez runs têm revisão antiga `b31e337` (um com alterações locais) e os demais não identificam revisão. Não é possível atribuí-los integralmente à produção atual. Alguns spans antigos ainda fornecem estados pseudonimizados e vereditos. O export autorizado contém 245 sessões e 1.066 mensagens, mas turnos recusados frequentemente não são persistidos: sozinho, ele não comprova a decisão do Jev. Traces e export não foram versionados.

## Causas confirmadas e correções

| Caso | Evidência sanitizada | Correção |
| --- | --- | --- |
| Definição de NR, inclusive em PDF | Consulta ao catálogo `consultar_nrs` retornou zero registros; não havia definição para fundamentar a resposta. | Formulações gerais reconhecidas usam `consultar_orientacoes_sst` e a fonte fixa `mte_normas` do MTE. NRs numeradas e obrigações pessoais continuam em seus fluxos específicos. |
| Gráfico de pizza de pendências de colaboradores | Foi consultada situação cadastral, medida diferente de pendências de NR. O Juiz apontou corretamente a inconsistência. | Seleção da tool de pendências e instruções explícitas sobre unidade de contagem; autorização e agregação integral preservadas. |
| Consulta cadastral com seletor omitido | Listagem genérica não comprovava a identidade pedida; a redação original está parcialmente ocultada. | Quando e-mail/UID não puder ser aplicado pelo contrato atual, solicitar nome completo em vez de executar listagem sem filtro. Não foi introduzida busca por e-mail/UID. |
| Eventos | `situacao=todos` coexistia com `proximos=true`; o Juiz confundiu todos os estados de conclusão com todos os períodos. | Explicar a independência dos dois filtros. Pedidos de todos os períodos continuam exigindo `proximos=false`. |
| Exemplos no classificador | Os few-shots do roteador eram serializados como turnos da conversa enviada ao Jev. | Marcação interna e exclusão somente da entrada Jev; Luna mantém os exemplos. Instruções de reparo seguem pelo roteador textual. |
| Contrato alpha e falhas | Formatos inválidos podiam escapar como erro de atributo; 403 alpha era tratado como indisponibilidade global. | Validação de tipos/distribuições, fallback Luna e circuito de 60 s. Chave inválida e saldo insuficiente continuam interrompendo a chamada. |
| Verificador opcional | Número negativo do Jev podia encerrar a resposta sem revisão textual final. | Negativo/incerteza exige novo veredito do Juiz contra a mesma evidência; revisão/rejeição continua bloqueando, sem PDF ou persistência do turno. |

Pedidos ambíguos como “tudo” e tentativa explícita de acessar outro workspace não receberam aprovação forçada. Nenhuma alteração concede permissões ou dispensa validação das tools.

A fonte adicional é uma URL oficial fixa na allowlist, não uma URL livre fornecida pelo modelo. Em um A2A com catálogo anterior, somente esse novo identificador pode recorrer ao MCP local. O catálogo foi acrescentado ao final da lista; as quatro fontes padrão não mudaram. A resposta não é inventada se a fonte estiver ausente. O Juiz também foi orientado a avaliar limitações honestas sem exigir que a IA preencha fatos ausentes com conhecimento próprio.

## Contratos, privacidade e custo

Não há novo campo público de Jev para web/mobile. Respostas de texto novas persistem o mesmo bloco enviado no POST, corrigindo a divergência anterior com o histórico; mensagens antigas permanecem legíveis. Ordem e tipos dos demais blocos são mantidos.

Telemetria registra somente rótulos fechados `decision_route_engine`, `decision_jev_route`, `decision_jev_evidence`, `decision_input_guard`, `decision_judge`, `decision_output_guard` e `decision_evidence_review`. Não registra motivos em texto, perguntas, identidades ou URLs temporárias. Ausência de um sinal pode indicar caminho determinístico ou etapa não executada.

O limiar conservador de roteamento permanece 0,90. Probabilidade/confiança não garante correção. Intenções complexas reconhecidas evitam a chamada alpha e seguem para Luna. A checagem adicional de evidências permanece desativada por padrão; quando habilitada, negativo/incerteza pode acrescentar uma revisão Luna. Não foi acrescentada chamada de IA ao caminho determinístico de consulta de NR: fonte oficial, Juiz e guardrails permanecem obrigatórios.

## Validação

- Testes simulados cobrem exclusão de exemplos, limites de contexto, reparo textual, 403/401/402, contrato alpha inválido, divergência de sinais, autorização, fonte ausente, rejeição do Juiz, PDF e igualdade POST/histórico.
- A primeira suíte completa teve 1.201 aprovações e quatro expectativas antigas de histórico sem blocos. Essas expectativas foram atualizadas para exigir igualdade com o POST; **suíte final completa: 1.205 testes aprovados**, com 15 avisos de dependências.
- Uma consulta real MCP confirmou conteúdo da fonte do MTE, sem chamada de IA.
- Duas tentativas locais com Luna encontraram a fonte indisponível e receberam revisão do Juiz: isso não foi um bloqueio Jev. A terceira verificou primeiro a disponibilidade da fonte; o fluxo respondeu com definição e referência oficial, aprovado pelo Juiz e persistido em dois registros sintéticos.
- Três consultas locais pequenas: 22.965 tokens totais e US$ 0,002959425 reportados pelo OpenRouter. O teste bem-sucedido utilizou 8.010 tokens / US$ 0,001026225. Nenhuma conta real ou banco privado foi necessário; autenticação e armazenamento foram simulados. Isso não substitui homologação em produção.

Não houve nova carga Qdrant, alteração Redis, merge ou deploy nesta revisão. A carga de FAQ anterior já autorizada está descrita separadamente em `atualizacao-faq-funcional.md`.

## Homologação após publicação autorizada

1. “O que é uma NR?” e “Me explica uma norma regulamentadora”. Deve haver consulta oficial e referência; indisponibilidade deve ser declarada honestamente.
2. “Gera um PDF falando o que é uma NR”. Somente conteúdo aprovado pode gerar arquivo.
3. “Gráfico de pizza de pendências de colaboradores”. Deve medir pendências por NR, não situação cadastral.
4. Listar eventos com referências; selecionar evento autorizado. Ambiguidade deve solicitar esclarecimento.
5. Consultar cadastro por e-mail/UID sem nome. Deve esclarecer a limitação; nunca retornar listagem geral como se fosse ficha.
6. Repetir tentativa de acesso a outro workspace. Deve continuar bloqueada.

## Referências oficiais

- [MTE — Normas Regulamentadoras](https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/seguranca-e-saude-no-trabalho/ctpp-nrs/normas-regulamentadoras-nrs).
- [OpenRouter — Jev](https://openrouter.ai/docs/guides/community/jev) e [contrato alpha](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).
- [Typesafe — confiança](https://docs.typesafe.ai/confidence).
- [LangSmith — consulta de traces](https://docs.langchain.com/langsmith/trace-query-syntax).
