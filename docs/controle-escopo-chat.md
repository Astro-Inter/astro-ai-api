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

## Entretenimento e continuação por assunto

O relato adicional mostrou uma piada de futebol depois de humor sobre segurança.
Mesmo uma piada que menciona SST continua sendo entretenimento, não uma consulta
de segurança do trabalho. A política comum, os critérios do Juiz e as regras do
guardrail de saída passam a explicitar essa distinção.

Pedidos explícitos e limitados de piada, charada, poema ou ficção recebem a mesma
orientação de escopo. “Me fale uma sobre futebol também” retoma esse limite quando
os turnos imediatamente anteriores identificam o pedido como humor. O histórico
não dá autorização para continuar conteúdo externo. Uma mudança legítima de
assunto quebra essa inferência; ausência de referente segue a interpretação normal.

Não se bloqueia pela simples presença da palavra “piada”: “Sofri assédio com
piadas ofensivas” e pedidos para relatar discriminação continuam no fluxo de SST.
Explicações de normas, prazos e indicadores também não são entretenimento.

O reconhecedor não é uma lista exaustiva de todos os assuntos externos. O limite
geral também está nos prompts do roteador e revisores; formulações não cobertas
pelas formas explícitas continuam exigindo classificação e revisão. Não se
promete ausência absoluta de falhas de modelo. Não há classificador extra nem
chamadas adicionais de IA para os pedidos reconhecidos.

Uma verificação real pequena apresentou ao Juiz, com conversa sintética, a
candidata da piada de futebol da imagem. O Juiz pediu **revisão**, em vez de
aprovar o entretenimento: 3.301 tokens / US$ 0,000430925. Isso não forçou aprovação
de uma resposta nem acessou contas ou dados reais. A rodada focada inicial teve
55 aprovações, incluindo piadas, elipse por assunto, limites da inferência e
relatos legítimos de assédio. Os grupos de testes se sobrepõem às rodadas anteriores.

A produção consultada durante esta ampliação ainda era `01a8873`, com a PR #44
aberta. As capturas antigas não são tratadas como reteste do código ainda não
publicado. Nenhum merge ou deploy foi realizado.

Validação ampliada final: **481 testes aprovados**, com 15 casos adicionais de
entretenimento/continuação e preservação de relatos legítimos. Modelos e consultas
externas simulados; dois avisos de dependências locais. Esse grupo inclui testes
da rodada anterior de 466 e não deve ser somado a ela.

## Exercícios escolares sem gabarito

O relato adicional mostra o pedido “Faça um lista para eu treinar multiplicação,
ela tem que ter 10 exercícios e deve ser simples” sendo atendido após uma resposta
anterior de limite de escopo. Isso continua sendo material escolar, mesmo sem
resultados preenchidos. Não equivale a consultar treinamentos de SST.

O roteamento passou a reconhecer pedidos explícitos de listas/atividades para
praticar matemática, multiplicação, divisão, tabuada e temas equivalentes. A frase
da captura, incluindo a variação gramatical, e pedidos sem gabarito retornam a
orientação de escopo, submetida ao Juiz e ao controle de saída existentes.
A política comum e os exemplos do roteador explicitam esse limite.

“Quais treinamentos preciso realizar?”, exercícios de prevenção de SST,
listagens de colaboradores, pendências por NR e explicações do cálculo de
indicadores não são classificados por esse reconhecedor como material escolar.
O controle não bloqueia a palavra “treinar” isoladamente. Formas não reconhecidas
continuam no roteador por modelo e na revisão geral de escopo.

Não há chamadas adicionais de IA nem novos contratos para os clientes. Explicar
o limite é enviado como bloco texto, igual no POST e no histórico, sem gerar lista
de exercícios ou PDF de sucesso. A produção consultada ainda retornava `01a8873`,
sem a PR #44, que permanecia aberta. Não houve merge ou deploy.

## Continuação após o merge da PR #44: culinária e opiniões de empresas

O trabalho local de exercícios escolares foi preservado em uma nova branch
baseada na main `1dc9927023e58b2e938f6e4bb27c02e11ed7d4d8`, pois a PR #44 já
foi mesclada. As referências anteriores à publicação são registros das respectivas
rodadas, não uma afirmação de que a PR #44 continua aberta.

“Ok, me ajuda com receita de bolo” e variações explícitas de receitas culinárias
recebem a orientação de foco do Astro. Pedidos em PDF não geram sequer um arquivo
com a mensagem de limite: o fluxo desativa `pdf_solicitado` e entrega só texto.
A mesma proteção vale para exercícios e tarefas classificadas `fora_escopo`.

Rankings genéricos de melhor/pior empresa e pedidos para redigir críticas ou
elogios são externos ao produto. Uma frase isolada como “eu odeio a Empresa
Exemplo” não confirma um problema trabalhista e não deve levar à concordância,
endosso ou oferta de crítica. A resposta faz uma pergunta neutra sobre eventual
situação de RH/SST. Não há lista de marcas proibidas nem preferência por empresa.

Relatos de assédio, discriminação, risco, acidente, condições de trabalho e
consultas de indicadores continuam na interpretação de RH/SST. Segurança em
cozinha industrial não equivale a receita culinária. Receita médica e exame
ocupacional também não são classificados como culinária; isso não autoriza
prescrição ou diagnóstico, que continuam proibidos pelas regras existentes.

O roteador e os revisores recebem a política comum atualizada. Os atalhos locais
cobrem formulações explícitas; não pretendem classificar todo assunto possível.
Juiz e guardrails continuam podendo recusar, e as tools repetem autorização.
Não há nova chamada de IA para classificar esses pedidos nem novo contrato:
respostas em blocos `texto`, iguais no POST/histórico, sem receitas, exercícios
ou avaliações da reputação das empresas. Nenhum cadastro ou dado foi alterado.

Validação local: **545 testes aprovados** no grupo ampliado; após impedir a geração
de PDF do limite de escopo, **82 testes finais aprovados** de escopo/PDF. Os grupos
se sobrepõem e não devem ser somados. Há 31 regressões adicionais em relação à
base, incluindo as duas de exportação externa. Modelos e ferramentas simulados;
dois avisos de dependências locais.

Uma revisão real pequena do Juiz, com empresa fictícia e dados sintéticos,
aprovou a pergunta neutra de contexto: 3.625 tokens / US$ 0,000467675. Sem consulta
privada ou operação de negócio. Não houve merge ou deploy deste complemento.

Reteste autorizado no Samsung SM-T225 após o merge da PR #44: quatro pedidos
reais na revisão `1dc9927` — exercícios de multiplicação, receita de bolo,
insatisfação com empresa fictícia e consulta das próprias NRs. Os três primeiros
ficaram no limite do produto sem conteúdo externo; o quarto retornou os vínculos
do cadastro. Portanto, esses vazamentos não foram reproduzidos nessa execução.
Isso não é teste publicado dos ajustes locais deste complemento. Custos dos
quatro pedidos de produção não foram correlacionados e não são estimados como
consumo observado. Nenhum dado de negócio ou configuração foi alterado.
