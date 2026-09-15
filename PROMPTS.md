# Engenharia de prompts do Astro

Revisão baseada no material didático IA_2.pdf (Engenharia de Prompt).

## Estrutura e técnicas

Os prompts usam papel, tarefa, contexto, procedimento, limites e formato de saída.
Isso combina PTF (papel/tarefa/formato), TAO (tarefa/ação/objeto) e CARE
(contexto/ação/resultado/exemplo) conforme a responsabilidade de cada agente.
A persona comum define comunicação PT-BR para funcionários e gestores, sem
deduzir direitos ou conhecimento por estereótipos.

As instruções são mensagens de sistema. Contexto autenticado é separado da
entrada e identidade da conversa não concede privilégios. O backend continua
responsável por RBAC, escopo, confirmação de escritas e validação Pydantic.
O ChatPromptTemplate monta sistema, exemplos e conversa sem interpolar o
conteúdo de documentos ou JSON como instruções de template.

Os few-shots ficam em `app/modules/chat/prompts/examples.py`, com mensagens
human/ai marcadas EXEMPLO FICTÍCIO. São separados das mensagens reais e nunca
persistidos no histórico da sessão. RH e SST recebem exemplos de decisão de
tool somente quando o schema atual corresponde a esse contrato. O resumo
também recebe um exemplo separado de entrada/saída.

Há decomposição da tarefa e verificação de evidência, escopo e formato antes
da resposta. Para ambiguidades, o roteador compara alternativas de domínio.
Isso adapta a avaliação passo a passo e de alternativas discutida em CoT/ToT
sem exigir exposição de raciocínio interno ou contaminar rota/JSON. Não há
implementação de busca ToT com rollouts: isso exigiria outro fluxo e mais chamadas.
Zero-shot, one-shot e few-shot são alternativas, não requisitos cumulativos.

O roteador mantém quatro rotas e cinco comandos exclusivos: MEMORY,
CONVERSATION, NOTIFICATIONS, ACCESSES e MESSAGE. Exemplos redundantes e regras
repetidas foram retirados do sistema. Redução de linhas não é contagem de tokens:
exemplos e histórico também consomem contexto. Temperatura/top-p e provedores
não foram alterados nesta revisão.

## Avaliação contínua

Os testes verificam papéis dos exemplos, compatibilidade com schemas, limites
de contexto, comandos e integração do chat, além dos controles de segurança
existentes. Modelos simulados não demonstram ganho de qualidade de um LLM real.

Após deploy, compare conversas reais no LangSmith: acerto de rota, pedidos de
esclarecimento desnecessários, fidelidade às fontes, taxa de resolução, erros,
latência e consumo/custo de tokens. Use os mesmos casos e modelo ao comparar.
Inclua listagem de funcionários e "só um", próximo evento interno versus Google,
NRs da empresa versus catálogo, cartilha da Fundacentro versus política interna,
mensagem com prévia/confirmação, paginação e consultas de memória de outras sessões.
Só traces/evaluations reais permitem concluir se a mudança melhorou esses índices.
