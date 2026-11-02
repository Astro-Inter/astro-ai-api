# Linguagem simples nas respostas do chatbot

O chatbot deve ser compreensível para gestores e colaboradores que não conhecem
termos de SST ou detalhes dos sistemas. A resposta começa pelo resultado e pelo
próximo passo possível. Siglas ou termos técnicos necessários devem ser explicados.

No texto próprio, o Astrinho usa “colaborador” e “colaboradores” em vez de
“funcionário” e “funcionários”. A regra está no prompt comum e a apresentação
pronta também segue esse vocabulário. Nomes e citações literais são preservados;
mensagens do usuário com o termo antigo continuam sendo compreendidas.

## NRs pessoais e de terceiros

A consulta continua usando as tools existentes e as mesmas regras de autorização.
O formatador transforma situação e ação em frases curtas, separadas por parágrafos,
sem linhas de campos concatenados por `|` ou tabela textual.

Exemplo com atividade pendente:

> Estas são as NRs do cargo Técnico, na unidade Matriz:
>
> NR-1 — Disposições gerais: tem uma atividade pendente. Conclua a atividade pendente.
>
> NR-5 — CIPA: precisa ser renovada. Providencie a renovação.
>
> Algumas NRs estão sem data de validade. Não é possível confirmar se estão em dia.

Os nomes e situações do exemplo são ilustrativos. Os dados reais sempre vêm da
consulta autorizada. Não se inventa atividade, inscrição, prazo ou link de acesso.

- A data de atividade prevista deixa de aparecer no resumo padrão.
- Campos técnicos como `data_inicio_pendencia` continuam na evidência interna,
  sem serem expostos como nomes de campo no texto.
- Uma validade disponível continua relevante para o usuário: `Validade: 12/09/2027.`
- Falta de validade é explicada uma única vez no fim, sem repetir a ressalva longa
  em cada NR. Não permite declarar que está em dia ou sem vencimentos.
- Se uma situação vier como vigente sem data utilizável, o texto não garante
  validade: informa que ela não está confirmada. A evidência original não é alterada.
- A situação de cada NR continua visível; simplificar não significa omitir uma
  renovação ou tratar pendência como concluída.

## Outros formatadores revisados

As listas textuais de usuários, catálogo de NRs, NRs obrigatórias, treinamentos e
referências de eventos também deixam de concatenar campos por `|`.

Treinamentos passam a usar frases como “Sua participação está pendente” e
“É necessário enviar uma evidência para comprovar a realização”. Turma, período,
cancelamento, rejeição e instruções recebidas continuam disponíveis quando
pertinentes. Links continuam sujeitos à validação já existente. As referências
numéricas para selecionar um evento são preservadas, inclusive `evento_id`, para
compatibilidade com os fluxos existentes; a explicação informa como usá-las.

Datas ISO válidas são apresentadas como `dd/mm/aaaa` e, quando pertinente ao horário
do treinamento, `dd/mm/aaaa às HH:MM`. Essa alteração não converte fusos nem altera
a data/hora recebida: não há hipótese nova sobre timestamps sem fuso. Valores
inválidos não são inventados ou impressos como se fossem datas confiáveis.

## Contratos, histórico e aprovação

Não há novo tipo ou versão de bloco. As respostas textuais continuam em `texto`;
fichas, tabelas e gráficos explícitos mantêm seus contratos e ordem. A mudança de
redação não transforma os blocos visuais em parágrafos nem impede pedidos de tabela.

POST e histórico de mensagens novas devem manter o mesmo texto e blocos. Mensagens
antigas não são reescritas. Os dados retornados pelas tools, as permissões, os filtros,
a identidade autenticada e as evidências enviadas ao Juiz não são substituídos por
texto simplificado. Juiz e guardrails continuam avaliando o conteúdo antes da entrega.

A regra de linguagem é compartilhada pelos agentes que redigem ou revisam a resposta.
O roteador recebe uma instrução curta; resumos internos mantêm seu próprio contrato. Os formatadores
determinísticos fazem a alteração diretamente; não foi acrescentada chamada de IA
para reescrever uma resposta já pronta. Há apenas o pequeno acréscimo de instruções
ao prompt existente. Não se exige código novo no web/mobile para renderizar o texto;
deve-se respeitar as quebras de parágrafo. A publicação da API continua necessária
para observar a nova redação em produção.


## Verificações locais

A rodada ampla aprovou 1.250 testes e identificou uma expectativa textual do prompt
compactado. A instrução de não recusar por mera menção a cargo foi mantida com a
redação esperada, sem mudar o teste ou o limite de tamanho. Na reexecução final,
53 testes de prompts, apresentação, validade e entrega/privacidade passaram.
Os grupos se sobrepõem. Também passaram 24 verificações focadas anteriores;
não somar esses números como casos independentes. Restam apenas avisos de dependências.

As regressões verificam situação/ação/validade, ausência de separadores e datas de
atividade prevista, aviso único para validade ausente, dados da tool sem mutação,
texto de terceiros, datas sem horário inventado, cancelamento/rejeição, evidência
obrigatória, referência de seleção e preservação dos contratos de memória.
Modelos e ferramentas foram simulados; não houve consumo real de IA nesta mudança.
