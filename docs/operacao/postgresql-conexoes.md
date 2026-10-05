# PostgreSQL recusando conexões

`motivo=limite_conexoes` significa que o servidor recusou uma nova conexão por
esgotamento das vagas disponíveis ao usuário. Não é um erro de prompt, CORS,
Firebase ou MongoDB. `sqlstate=indisponivel` significa que o driver não recebeu
esse código durante a abertura da conexão, não que o diagnóstico seja ausente.

## Identificar o consumo no banco

Use uma sessão administrativa já disponível ou os recursos de diagnóstico do
provedor. Se nenhuma sessão puder ser aberta, consulte as métricas no painel da
Aiven ou contate o suporte. Não tente solucionar concedendo privilégios de
superusuário ao usuário da API.

Consultas somente leitura, sem revelar os textos das consultas ou dados pessoais:

```sql
SELECT name, setting
FROM pg_settings
WHERE name IN ('max_connections', 'reserved_connections',
               'superuser_reserved_connections');

SELECT application_name, state, count(*) AS conexoes,
       max(now() - backend_start) AS conexao_mais_antiga
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY application_name, state
ORDER BY conexoes DESC;
```

As novas conexões desta API usam `application_name=astro-ai-api`. A visibilidade
de sessões de outros usuários depende das permissões da sessão administrativa.
Não use a idade de uma conexão como prova de vazamento: pools podem manter sessões
ociosas legítimas. Analise também a configuração de cada serviço consumidor.

## Reduzir o consumo

- Configure `POSTGRES_MAX_CONCURRENCY=2` na API. Esse é o orçamento **total** de
  conexões por processo para autorização, tools e endpoints de suporte.
- `POSTGRES_AUTH_MAX_CONCURRENCY=2` continua limitando a autorização dentro desse
  orçamento; não soma duas novas vagas ao limite total.
- Some todos os processos/replicas da API, pools dos demais backends, ferramentas
  de administração e conexões reservadas ao dimensionar a capacidade.
- Reduza pools superdimensionados dos serviços identificados. Feche clientes de
  administração que não precisam manter sessões abertas.
- Quando disponível, use pooling do provedor ou ajuste o plano/capacidade. Na
  Aiven, PgBouncer exige plano Startup ou superior e uma URI própria; não basta
  alterar a porta da URI manualmente. Verifique compatibilidade dos parâmetros
  de sessão e do modo do pool antes de migrar.
- Não encerre sessões de outros sistemas nem reinicie o banco sem avaliar o
  impacto e obter autorização operacional.

As conexões da API são fechadas após cada consulta, sem pool ocioso ou cache de
perfis. Um limite local não cria vagas no servidor quando outros sistemas já
consumiram a capacidade. Não aumente concorrência/retries para esse erro.

Referências: [limites PostgreSQL](https://www.postgresql.org/docs/current/runtime-config-connection.html)
e [pooling Aiven](https://aiven.io/docs/products/postgresql/howto/manage-pool).
