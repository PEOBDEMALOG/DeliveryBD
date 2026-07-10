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
