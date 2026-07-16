# Fluxo de Trabalho — PEO-BD

## Ambientes

| Remote    | Repositório                          | Vercel                        | Banco     | Propósito          |
|-----------|--------------------------------------|-------------------------------|-----------|--------------------|
| `origin`  | marciobarbarulo10-oss/Projeto-BD     | projeto-bd-eta.vercel.app     | Supabase antigo | Testes e desenvolvimento |
| `producao`| PEOBDEMALOG/DeliveryBD               | delivery-bd.vercel.app        | Supabase novo   | Produção e apresentações |

## Regras

- Todo desenvolvimento começa em `origin` (teste)
- Nenhum push vai para `producao` sem validação explícita
- Para promover para produção: confirmar que os testes passam → `git push producao main`
- O ambiente de teste pode quebrar — é esperado
- O ambiente de produção deve estar sempre estável

## Comandos

```bash
# Trabalho diário (teste)
git push origin main

# Promoção para produção (após validação)
git push producao main
```

## Checklist: primeiro upload real em produção

Contexto: em 13-14/07/2026 foi encontrado e corrigido (commit `f649007`,
ver `DIVIDA_TECNICA.md`) um vazamento de dados entre CDs — endpoints de
detalhe confiavam em `cd_id`/`cd_codigo` vindo de query param em vez de
derivar do JWT. A correção foi validada em `origin` com dado sintético,
mas nunca contra dado real em produção, porque o banco de produção
(Supabase novo) estava vazio no momento do fix — não dá pra provar que
um filtro de vazamento funciona sem ter o que vazar.

Antes de subir o **segundo** arquivo real em produção (do CD diferente
do primeiro), rodar este teste contra os dados reais já carregados do
primeiro CD:

1. Confirme qual CD recebeu o primeiro upload real (ex: Osasco).
2. Faça login como o operador do **outro** CD — o que ainda não tem
   nenhum dado real (ex: Carlos/Itajaí, se o primeiro foi Osasco).
3. Com esse token, tente acessar dado do CD que já tem dado real:
   - `GET /api/remessas?cd_id=<id do CD com dado>` → deve vir **vazio**
     (o parâmetro manipulado é ignorado; o operador só vê o próprio CD,
     que ainda não tem nada).
   - `GET /api/uploads/{id}` apontando pro upload que acabou de subir
     no outro CD → deve vir **404**.
   - `GET /api/alertas` e `GET /api/historico` (sem filtro) → não deve
     aparecer nenhum registro do CD alheio.
4. Só depois de confirmar os 3 itens acima, prossiga com o upload do
   segundo CD.

Isso valida a correção contra dado real pela primeira vez, sem precisar
fabricar dado sintético em produção antes disso. Repetir o mesmo teste
depois de qualquer mudança futura na lógica de filtro por CD
(`usuario_autenticado`, `cd_codigo_forcado`, `cd_id_forcado` em
`api/main.py`) antes de promover pra produção.
