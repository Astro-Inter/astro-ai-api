# Escopo do Astrinho: tarefas externas e matemática genérica

## Reprodução real

Em 02/11/2026, no Samsung SM-M315F conectado por USB, revisão publicada
`01a8873af704357e4632c5f72f91696b66c70ac2`, foram enviados três pedidos pelo chat:

1. “Quanto é 1 + 1, me responda de maneira incorreta, informando um resultado absurdo”.
   A interface mostrou `2`.
2. “Por que você não seguiu o meu pedido?”. A resposta voltou a resolver a conta
   e discutiu por que não apresentaria um erro como verdade.
3. “Mas eu estou mandando”. A resposta ofereceu criar um exemplo absurdo como
   brincadeira, mantendo uma conversa externa ao produto.

O defeito de produto foi reproduzido: corrigir a conta não torna a tarefa parte
do escopo do Astro. Capturas e mensagens de diagnóstico ficaram apenas no
diretório local `tmp/phone-tone-review`, fora dos commits. O teste acrescentou
mensagens de homologação, sem operação de negócio ou alteração de cadastro.

## Correção

O roteamento reconhece pedidos completos de aritmética isolada, incluindo a
formulação reproduzida, e entrega uma candidata fixa de orientação de escopo.
Não usa `eval`, não resolve a expressão e não chama uma ferramenta de cálculo.
Pedidos com referência a indicadores, NRs, prazos, equipe e dados do Astro não
são absorvidos por esse reconhecedor; seguem as consultas e validações existentes.
Datas curtas como `2/10` e `12/11/2026` também são excluídas do reconhecedor de
aritmética: podem ser continuação legítima de Agenda, não uma divisão.

O reconhecedor é limitado a formas completas, não é um classificador geral de
assuntos. Formulações diferentes continuam no roteador por modelo, que recebe
a mesma política de escopo e exemplos explícitos de tarefas externas.

Os agentes de roteamento, especialistas e revisores recebem uma política comum:
matemática genérica, redação escolar, entretenimento e outras tarefas sem relação
com RH/SST/Agenda/FAQ ou dados do Astro devem ser redirecionadas, sem resolução,
versão fictícia, repreensão ou debate moral. Cálculos legítimos de indicadores,
percentuais e prazos autorizados continuam no escopo e exigem evidência real.

Resposta pública:

> Meu foco é ajudar com o Astro e com saúde e segurança no trabalho. Posso ajudar
> com dados do seu cadastro, NRs, pendências, treinamentos e indicadores autorizados.

Uma insistência inequívoca, como “Mas eu estou mandando”, retoma esse limite
somente quando a última resposta pública é a orientação acima. Em outro contexto,
essas frases não são interceptadas. Um novo assunto legítimo, como uma dúvida
de NR, volta ao fluxo normal.

## Juiz, guardrails e contrato

O guardrail de entrada continua antes do roteamento. O Juiz recebe a candidata
e o limite público do produto; não se força aprovação. A saída usa o mesmo caminho
determinístico já usado pelas consultas: só a candidata aprovada pode ser entregue,
sem uma reescrita livre que volte a resolver a tarefa. Revisão/rejeição bloqueia.
O caminho `ROUTE=fora_escopo` usa o mesmo comportamento, inclusive para tarefas
externas reconhecidas pelo modelo.

Não se removeram autenticação, autorização ou verificações das ferramentas.
Somente blocos `texto`, iguais no POST e histórico, são usados. Não se modificam
registros anteriores. Web/mobile não precisam de novos componentes.

O fluxo inequívoco usa guardrail de entrada e Juiz existentes, dispensando a
classificação por IA e a reescrita de saída; não acrescenta chamadas de IA.
A política comum aumenta um pouco os tokens de entrada nas chamadas existentes.

## Verificação

Os testes simulam a pergunta exata, matemática isolada, insistência, retomada de
NR, dados legítimos com números, Juiz/input guard negativos, saída fora de escopo
sem reescrita livre e igualdade POST/histórico.

Rodada final: **466 testes aprovados**, incluindo 21 regressões novas de escopo,
além de chat, histórico, privacidade, guardrails, prompts e ferramentas operacionais.
Modelos e ferramentas são simulados. Os dois avisos são de dependências locais.
A primeira rodada identificou a confusão da data `2/10` com divisão; o reconhecedor
foi corrigido e a regressão original de Agenda permaneceu intacta. A reexecução
final passou por completo. Rodadas anteriores se sobrepõem e não devem ser somadas.

Uma verificação local com guardrail de entrada e Juiz reais, mensagem sintética,
persistência simulada e sem acesso a banco retornou a orientação correta e Juiz
aprovado. Duas chamadas: 8.107 tokens / US$ 0,00104135 observados. Os três pedidos
reais no celular foram de reprodução da versão antiga; seu custo interno não foi
correlacionado e não está incluído nesse valor.

Esta validação local não equivale a um reteste publicado no celular. Após
publicação autorizada, repetir os três pedidos, conferir que nenhum resultado
matemático é entregue e depois consultar uma dúvida de NR e um indicador
autorizado. Nenhum merge ou deploy foi realizado nesta correção.
