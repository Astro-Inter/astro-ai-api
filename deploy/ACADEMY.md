# CD no AWS Academy — SCRUM-393

Os manifestos Academy e o bootstrap do Argo CD foram centralizados no
[Astro-Inter/astro-gitops](https://github.com/Astro-Inter/astro-gitops).

Siga [o guia central](https://github.com/Astro-Inter/astro-gitops/blob/main/docs/ACADEMY.md).

Neste repositório permanece o workflow de testes/publicação GHCR e promoção
da tag no GitOps. Configure o Secret Actions ASTRO_GITOPS_TOKEN antes do merge:
token fine-grained, owner Astro-Inter, somente repo astro-gitops, Contents
Read and write, expiração curta. Não envie seu valor no chat ou em commits.
Ele não é o PAT classic read:packages usado pelo Kubernetes.

O job update-gitops muda somente a tag em
apps/astro-ai-api/overlays/academy/kustomization.yaml na main do repo central.
Não cria branches GitOps aqui, não acessa a AWS e não gerencia Secrets.
PRs apenas validam; publicação/promoção ocorrem na main após testes bem-sucedidos.
Os manifestos AWS de produção preexistentes em deploy/k8s não foram alterados.
