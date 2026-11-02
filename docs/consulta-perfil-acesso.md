# Consulta do próprio perfil de acesso

Perguntas completas como “Qual é meu nível de acesso?”, “Qual é meu perfil de
acesso?”, “Qual é meu perfil?” e “Qual é meu tipo de usuário?” usam o mesmo fluxo
de campos individuais de `buscar_meus_dados`.

O trace da homologação de 02/11/2026 às 11:01:51 em São Paulo confirmou que a
consulta retornava dados, mas a candidata incluía a ficha completa. O Juiz pediu
revisão porque a pergunta precisava somente do perfil. A correção restringe a
evidência e a resposta ao campo `tipo`; não força aprovação.

A ferramenta continua sem seletor de pessoa: o UID é fornecido pelo contexto
autenticado. Nome, e-mail, cargo e outros campos não entram no prompt do Juiz
nessa consulta. O perfil confirmado no cadastro é apresentado como:

| Valor interno | Nome público |
| --- | --- |
| GESTOR_WORKSPACE | Gestor do Workspace |
| GESTOR | Gestor |
| COLABORADOR | Colaborador |
| ADMIN | Administrador |

Exemplo de bloco público:

```json
{"tipo":"texto","conteudo":"Seu perfil de acesso é Gestor do Workspace."}
```

O texto não concede permissões nem enumera capacidades inferidas pelo modelo.
Cada operação continua validando autorização e escopo no backend. Pedidos para
alterar perfil ou consultar terceiros não correspondem ao reconhecedor de campo
pessoal. Cadastro ausente, falha ou tipo desconhecido não viram uma afirmação de
acesso. Juiz e guardrail de saída continuam capazes de recusar a resposta.

Não há ficha completa nesse pedido específico. “Meus dados” continua entregando
`ficha_usuario`, com os códigos internos preservados para compatibilidade com os
clientes. A tradução acima aplica-se à resposta textual de perfil; não altera
contratos de tabela, filtros ou regras de autorização.

POST e histórico mantêm o mesmo bloco de texto, sem reescrever respostas antigas.
Web e mobile não precisam de um novo componente. A mudança não acrescenta
chamadas de modelo e reutiliza a consulta e revisão existentes.

Também foram alinhadas as apresentações prontas da aplicação para “Astrinho,
o assistente virtual do Astro”, além do prompt e exemplos já ajustados. Isso
inclui saudações, explicação de capacidades e proteção de instruções internas.
Os limites de privacidade continuam os mesmos.

Validação local: **357 testes aprovados**, incluindo os quatro perfis, minimização
dos dados enviados ao Juiz, consulta pelo UID autenticado, dados ausentes, recusa,
histórico, saudações e contratos dos prompts. Modelos e ferramentas são simulados;
os dois avisos são de dependências locais. Não houve chamada adicional de IA real,
merge ou deploy nesta etapa.

## O que perguntar com cada perfil

“Que tipo de perguntas o meu perfil pode fazer?” e “O que posso fazer com meu
nível de acesso?” passam a consultar o catálogo local de orientação
`app/modules/chat/capabilities.py`, selecionado pelo papel autenticado da requisição.

| Perfil | Alcance explicado | Exemplos de perguntas |
| --- | --- | --- |
| Colaborador | Próprios dados e atividades | “Tenho pendências de NR?”; “Quais treinamentos eu preciso realizar?”; “Mostre meus treinamentos concluídos em um gráfico de linhas.” |
| Gestor | Unidade autorizada | “Quem da minha equipe tem pendências de NR?”; “Quais conformidades vencem nos próximos 30 dias?”; “Mostre a distribuição da minha equipe por cargo.” |
| Gestor do Workspace | Unidades autorizadas do mesmo workspace | Exemplos de equipe e “Compare a conformidade entre as unidades.” |
| Administrador | Cadastro administrativo e informações gerais | “Meus dados.”; “Como funciona o Astro?”; “O que é uma NR?” |

Os exemplos são uma seleção de perguntas úteis, não uma lista exaustiva de
permissões. Gestores também podem consultar os próprios dados e treinamentos.
A comparação por unidade continua limitada pelas regras reais da consulta; não
se alterou a ferramenta para restringir ou ampliar papéis. Administrador não é
apresentado como um Gestor do Workspace e não recebe exemplos de consultas
funcionais de NRs/treinamentos pessoais.

As regras foram conferidas em `rh/tools.py`, `sst/tools.py`, `agenda/tools.py`
e `operational/tools.py`/`insights.py`. Equipe exige papel de gestor; o SQL
revalida usuário, workspace e unidade. A simulação de exigência de NR é reservada
a Gestor do Workspace; exemplos de orientação não mudam essas verificações.

O catálogo explica também a consulta de informações gerais e a geração de PDF
de conteúdo consultado/aprovado. Não promete registros existentes, inscrições,
criação de eventos ou alteração de perfil. Os dados continuam dependendo de
cadastro, vínculos e disponibilidade das fontes.

### Fluxo e continuidade

A pergunta completa é reconhecida após o guardrail de entrada. A aplicação monta
uma candidata em linguagem simples e sua evidência de contexto; o Juiz avalia
o perfil, o alcance e os exemplos antes do guardrail de saída. Uma recusa permanece
recusa. Nenhuma consulta operacional é executada apenas para oferecer sugestões.
Pedidos com instruções extras, terceiros, mudanças de perfil ou exportação não
são absorvidos por esse reconhecedor.

Os agentes que roteiam, respondem e revisam recebem `capacidades_perfil` no
contexto confiável, para também orientar formulações livres. A seleção vem sempre
de `CurrentUser.role`, não do histórico ou de um papel declarado na mensagem.
Trocar de papel entre requisições recalcula as orientações; não há cache de
permissões nem novos dados pessoais persistidos.

Somente blocos `texto` são produzidos nesse fluxo, iguais no POST e no histórico.
Não existe componente novo para web/mobile nem reescrita do histórico anterior.

### Verificação e custo

**547 testes aprovados**, incluindo 20 regressões de orientação por perfil, além
dos testes de roteamento, contexto, Juiz/guardrails, permissões, PDF, privacidade
e histórico. Os modelos e as consultas externas são simulados; os dois avisos
são de dependências locais. As rodadas anteriores se sobrepõem e não devem ser
somadas a esse total.

Uma única revisão real do Juiz, com perfil e conteúdo inteiramente sintéticos,
aprovou a orientação de Gestor do Workspace: 3.354 tokens e US$ 0,000433425.
Nenhuma conta real ou dado privado foi consultado nessa verificação.

O fluxo reconhecido usa guardrail de entrada e Juiz existentes, sem classificação
por Jev/Luna ou reescrita adicional para montar os exemplos. O catálogo acrescenta
texto ao contexto das chamadas existentes dos agentes; há aumento proporcional
de tokens de entrada, sem uma chamada adicional de IA para buscar capacidades.
Nenhum merge ou deploy foi realizado.

## Apresentação dos recursos visuais e arquivos

Perguntas gerais como “O que o chatbot pode fazer?”, “O que você pode fazer?”
e “Quais são suas funcionalidades?” explicam também:

- gráficos de barras, linhas, setores e rosca, quando os dados/consulta forem compatíveis;
- tabelas e fichas de perfil, com foto quando disponível e autorizada;
- geração de PDF para baixar, a partir de conteúdo consultado e aprovado.

O texto inclui exemplos de pedidos e indica como perguntar pelas opções do
próprio perfil. A orientação por perfil usa as mesmas descrições de recursos;
Administrador recebe a ressalva de que consultas funcionais e gráficos de equipe
exigem um perfil autorizado. Os exemplos de gestores incluem gráfico por cargo
e listagem de colaboradores em tabela; exemplos pessoais preservam a série de
treinamentos concluídos.

Explicar capacidades não executa consulta, não gera arquivo ou gráfico nem afirma
que existem dados. Somente blocos de texto são enviados nessa apresentação,
iguais no POST e histórico. Os demais contratos e as autorizações de cada tool
permanecem intactos. Os clientes não precisam de componente adicional.

A apresentação pública pronta e o exemplo de identidade usam essa orientação;
os agentes também recebem o catálogo por perfil no contexto existente. Não foi
acrescentada chamada de IA ou reescrita para montar a apresentação. O catálogo
maior aumenta proporcionalmente os tokens de contexto nas chamadas que já o usam.

Validação desta etapa: **74 testes aprovados** de capacidades, exemplos, identidade,
proteção dos atalhos públicos e histórico, com modelos/consultas simulados. Dois
avisos de dependências locais. Nenhuma chamada real de IA, merge ou deploy.
