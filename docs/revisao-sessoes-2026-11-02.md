# Revisão das sessões de 02/11/2026

Fonte: exportação local `astro.sessoes.json`, examinada sem reenviar conversas a
modelos. Todas as 21 sessões têm atividade em 02/11 no fuso America/Sao_Paulo:
170 perguntas e 170 respostas. Não há revisão de build nem timestamp individual
em cada mensagem. As datas da sessão permitem ordenar atividade, mas não provar
qual commit produziu cada resposta. Falhas sem turno salvo também podem faltar
no arquivo: a recusa de treinamentos da captura web não está nesta exportação;
a sessão termina na pergunta de esclarecimento anterior.
Há 6 respostas com gráfico, 7 com tabela e nenhuma com bloco de arquivo.
Não há a mensagem padrão de recusa do Juiz nas respostas salvas; isso não comprova
ausência de recusas, pois turnos bloqueados podem não ser persistidos.

## Achados e situação

| Caso observado | Avaliação na revisão local |
| --- | --- |
| Menu de gráficos → `1` → `s`, com promessas sem execução | PR #45 mantém menu estruturado na sessão e executa a seleção; Juiz permanece obrigatório. |
| `vencimento` → `30 d` | Esclarecimento de prazo persistido, período validado e ferramenta acionada. Faixas fixas de gráfico não viram série histórica. |
| Gráfico de pendências → quem tem pendência na NR-6 → `isso` | Consulta detalhada reaproveita filtros e revalida acesso. Proposta de leitura é distinta de confirmação de escrita. |
| “quantas pessoas tem no workspace?” e concordância com oferta de unidade | Contagem RH existente reutilizada. Workspace para gestor de workspace; gestor recebe somente unidade, com limitação explícita. Exclui o solicitante conforme contrato preexistente. |
| “de colaboradores por cargo” após pedir um gráfico | Continuação curta seleciona agregação de colaboradores por cargo, sem afirmar que o recurso inexiste. |
| “qualquer um” após menu | Seleciona a primeira consulta visível daquele menu. Sem menu ativo não supõe uma operação; apenas leituras documentadas são elegíveis. |
| Escolha “os próximos treinamentos atribuídos a você” | Encaminha à consulta pessoal autenticada. Não interpreta o pronome como identidade de terceiros. |
| “Me fala as pendências de NR da minha equipe” | Encaminha à ferramenta existente em vez de produzir resposta sem consulta. |
| “Faz em um gráfico de setores” após barras | Alteração de formato reconsulta o conjunto autorizado, preservando filtros. |
| Exercícios, receitas, humor, avaliações genéricas de empresas e repetição em massa | Controles de escopo da PR e revisões anteriores cobrem os exemplos principais; não constituem garantia universal contra paráfrases/adversários. |
| “Quanto custa 1 + ...” / “Se somar 1+1 dá qual NR” | Reconhecidos como soma genérica; acrescentar NR não cria indicador de SST nem justifica associar o resultado a uma norma. |
| Usuário declara ser colaborador após autenticar como gestor | Correto preservar perfil autenticado; declaração na conversa não muda permissões. |
| Pedido de SQL/todas as tabelas | Correto não expor esquema/dados arbitrários; repetir menus por muitos turnos prejudica a conversa. Prompt orienta aproveitar esclarecimentos e apresentar opções executáveis. |
| Objeto com peso/altura de pessoa | Exportação mostra esclarecimento, sem instruções de ocultação; não confirma a alegação original de ajuda perigosa. |

## Limitações e pendências para homologação

- Recusas explícitas do Juiz precisam do trace do turno para identificar qual
  afirmação ou evidência motivou a recusa. Não liberar resultados automaticamente
  para melhorar fluidez. O caso da captura de treinamentos tem correção de rota
  e testes simulados; aprovação real do Juiz ainda depende de homologação.
- A sessão sobre vencimentos nos últimos 12 meses pede uma análise histórica
  que o gráfico de faixas atuais não fornece. É necessário explicar a limitação,
  sem inventar uma série nem tratar a ausência de recurso como queda do banco.
- A primeira sessão contém falha de PDF após vencimentos. A exportação não
  contém erro do provedor, armazenamento nem revisão; isoladamente não prova
  defeito novo no fluxo atual de reconsulta/exportação. Repetir após publicação.
- Persistem oportunidades de concisão: listas de treinamentos longas e indicadores
  com termos técnicos. Simplificar a linguagem preservando fonte, referência,
  significado e avisos de dados parciais; não ocultar limites dos números.
- O arquivo contém respostas fora de escopo anteriores às correções. Não são
  evidência de recorrência após publicação. O reteste mais recente do arquivo
  já recusou exercícios/receitas e respondeu as próprias NRs.

## Contrato de contagem

`buscar_outros_usuarios` mantém `consulta: contagem` e acrescenta o filtro interno
`escopo: autorizado | unidade` (padrão `autorizado`). `unidade` só é aceito para
contagem, restringe à unidade do usuário autenticado e não recebe ID de empresa
nem de unidade. Para gestor de workspace, a restrição é adicional ao workspace.
Nenhuma listagem existente muda seu alcance.

O resultado interno de contagem informa `escopo_consultado` e
`inclui_solicitante: false`. Conta todos os registros autorizados pelos filtros
com `COUNT(*)`, independente de limite/paginação, sem buscar linhas individuais.
“Pessoas” mantém os perfis permitidos pelo papel; “colaboradores” aplica
`tipos: [COLABORADOR]`. Sem status solicitado, não presume apenas ativos.
Não somar o próprio usuário artificialmente: ele é excluído pela tool existente.
Não usar o número da tabela como total do workspace.

Contagem de unidade não é salva como filtro de listagem para uma agregação
posterior de workspace. Isso evita perder o recorte ao converter consultas.
Clientes recebem bloco `texto`, sem novos componentes; histórico mantém o mesmo
bloco aprovado. Consultas funcionais novas por este atalho exigem Gestor ou
Gestor do Workspace; o caminho administrativo existente não foi ampliado.

## Perguntas para repetir após publicação

1. “Quantas pessoas tem no workspace?”; verificar escopo exibido e exclusão do solicitante.
2. “Quantos colaboradores há na minha unidade?”; conferir filtro de perfil.
3. “Me faça um gráfico” → “de colaboradores por cargo”.
4. “Quais gráficos você pode fazer?” → “o primeiro”.
5. “Liste os próximos” → escolher treinamentos ou eventos; não repetir a pergunta após escolha.
6. “Me fala as pendências de NR da minha equipe” → “Quem tem pendência na NR-6?”.
7. Vencimentos → PDF; confirmar arquivo real ou erro preciso, nunca promessa de sucesso.

Executar com os perfis autorizados e comparar POST/histórico. Testes automatizados
usam respostas simuladas; não substituem reteste publicado com dados reais.
