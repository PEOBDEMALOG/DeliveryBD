# Dívida Técnica — PEO-BD

## Pendências

### Senhas de usuários demo em texto puro no código
`core/auth.py` guarda usuário/senha dos 3 usuários de demonstração
(`timoteo`, `carlos`, `erick`) hardcoded em texto puro no dicionário
`USUARIOS`. Não há cadastro em banco nem hashing.

**Por quê:** decisão original do projeto para simplificar a demo (comentário
no próprio arquivo). Depois da rotação de segurança (10/07/2026), os valores
atuais não estão mais expostos em nenhum lugar do histórico anterior a essa
rotação, mas o padrão em si continua sendo credencial em texto puro
versionada no repositório — qualquer rotação futura exige o mesmo processo
manual, e o valor fica visível a qualquer um com acesso de leitura ao repo.

**O que fazer:** mover para variável de ambiente (mesmo padrão já aplicado a
`JWT_SECRET`) ou, melhor, cadastro real de usuário com senha hasheada no
banco. Não bloqueante para a apresentação de 13/07 — item de próximo ciclo.

### JWT_SECRET de produção (delivery-bd) pendente de rotação manual
O `JWT_SECRET` do ambiente de teste (`origin`/`projeto-bd`) já foi
rotacionado e configurado no Vercel em 10/07/2026 (valor novo, gerado com
`openssl rand -hex 32`, sem reaproveitar o antigo exposto no código).

**Pendente:** o ambiente de produção (`producao`/`delivery-bd`) está numa
conta Vercel à qual o agente não tem acesso. Se o valor de `JWT_SECRET`
configurado lá foi copiado das instruções originais (que incluíam o valor
antigo `peo-bd-demo-secret-2026`, hoje já removido do código), ele precisa
ser trocado manualmente por quem tem acesso a essa conta — gerar novo valor
com `openssl rand -hex 32` e não reaproveitar nenhum valor já usado em outro
ambiente. Ação do responsável do projeto, não do agente.

## Concluído (registro, não pendência)

### Cálculo de motivo de pendência unificado no backend
Antes: `motivoBloqueio(r)` duplicava no frontend (`index.html`) a mesma
lógica de `_pendentes_detalhe()` no backend (`agents/agente_monitor.py`).
Refatorado (10/07/2026): `motivo_pendencia()` em `agents/agente_monitor.py`
é a única fonte de verdade, reaproveitada tanto pelo dashboard quanto por
`GET /api/remessas` (campo `motivo`). Frontend só lê o campo já calculado.
Validado remessa por remessa contra o comportamento anterior — zero
divergências.
