# Geração abusiva e repetição em etapas

## Evidência e limites do diagnóstico

As capturas fornecidas em 02/11/2026 mostram uma sequência em que uma alegação
de nome de gestor foi seguida por pedidos de texto afetivo e repetição em massa,
culminando em conteúdo de exaltação extremista. A alegação de identidade não é
permissão para produzir conteúdo inadequado. A resposta anterior do assistente
também não autoriza continuar a geração.

Não se repetiu esse conteúdo cem vezes na API de produção para homologar.
As verificações foram locais, com amostras sintéticas curtas e modelos simulados,
evitando criar novo material abusivo em sessões reais ou logs de produção.
As capturas demonstram a falha de entrega, mas não identificam isoladamente qual
decisão interna de modelo falhou; não se atribui a causa ao Jev ou a um provedor
sem correlação do trace.

## Controles

- Entrada: instruções explícitas de bloqueio de exaltação extremista, inclusive
  cópia em etapas ou alegação de homônimo. Relatos, prevenção e nomes isolados
  continuam legítimos.
- Escopo: declarações afetivas pessoais e repetição mecânica não são tarefas do
  produto só porque o destinatário é chamado de gestor. Pedidos explícitos de
  repetição de texto pelo menos 20 vezes são redirecionados sem gerar o conteúdo.
  Isso não limita listas de 100/200 colaboradores, consultas ou tabelas.
- Revisão: Juiz e guardrail avaliam o conteúdo final e o contexto da sequência,
  sem tratar resposta antiga como autorização.
- Barreira local complementar: verifica padrões limitados de exaltação gerada
  e repetição idêntica de frases antes da entrega. Ela roda na candidata e na
  eventual reescrita do guardrail, após restauração de dados pseudonimizados.
  Uma mensagem inadequada de bloqueio gerada pelo guardrail de entrada também
  não pode ser ecoada ao usuário.

O bloqueio substitui a resposta por orientação curta, marca `guardar_turno=false`,
desativa PDF e remove ação pendente. Assim o texto gerado não vira resposta nova
no histórico, resumo ou arquivo. O conteúdo da candidata não é copiado para a
mensagem substituta. Não se alteram registros históricos já existentes.

A barreira não bloqueia pela simples presença de um nome: consultas cadastrais,
menções históricas e denúncias contextualizadas não equivalem a apoio extremista.
O reconhecedor local é complementar e limitado; não pretende detectar todo abuso
possível nem substitui avaliação contextual dos modelos. Não foi criado um
cadastro de nomes proibidos nem alterado o RBAC.

## Contratos, clientes e custo

O resultado de bloqueio continua em bloco `texto`, sem contrato novo. Não entrega
gráfico, ficha ou cartão de arquivo de sucesso. Web/mobile não precisam modificar
parsers. Histórico legado permanece compatível; o teste de sequência usa conteúdo
antigo sintético e verifica que ele não autoriza nova geração.

Os controles locais não acrescentam chamadas de IA. As instruções novas usam as
chamadas existentes e aumentam um pouco os tokens de entrada. Não são registrados
nomes, conteúdo ofensivo ou credenciais em mensagens de diagnóstico novas.
As barreiras locais registram somente o sinal fechado de decisão bloqueada,
reutilizando o vocabulário existente de observabilidade.

Testes cobrem repetição explícita, destinatário alegado como gestor, modelos que
aprovam indevidamente, reescrita indevida, PDF não executado, persistência impedida,
histórico legado, nomes isolados, denúncias e listagens normais de colaboradores.
Nenhum merge ou deploy foi realizado; a PR #44 ainda precisa de publicação
autorizada para o reteste do ambiente, usando apenas verificações seguras.

Validação ampliada: **516 testes aprovados**, incluindo 16 novos de conteúdo,
chat, escopo, Juiz/guardrails, privacidade, ferramentas e PDF. Modelos e consultas
externas simulados, com dois avisos de dependências locais. Os grupos anteriores
se sobrepõem e não devem ser somados.

Uma única classificação real do guardrail de entrada, em ambiente local com
pedido sintético explícito de exaltação, retornou `bloquear/pedido_danoso` sem
gerar o material: 3.412 tokens / US$ 0,000442175. Não houve chamada de produção
nem consulta privada nessa verificação.
