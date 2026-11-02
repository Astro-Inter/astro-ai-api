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
