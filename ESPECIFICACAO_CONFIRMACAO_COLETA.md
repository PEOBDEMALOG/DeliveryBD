# Especificação — Confirmação de Coleta por E-mail com Realocação Automática

Status: **rascunho para revisão** — nenhum código funcional desta feature foi implementado.
Escopo desta fase: diagnóstico do que já existe + especificação do fluxo novo, para aprovação
linha por linha antes de qualquer implementação.

---

## FASE 1 — Diagnóstico

### 1. Como `confirmado_em`/`status_envio` são atualizados hoje

**Achado central: o mecanismo de confirmação de hoje não está em uso — é código morto.**

`agents/agente_monitor.py:181-232` (`processar_email_confirmacao`) é a única função no
código-fonte inteiro que escreve em `ProgramacaoColeta.confirmado_em`
(confirmei via busca por `confirmado_em\s*=` em todo o repositório — só essa ocorrência, além
da própria declaração da coluna em `core/models.py:249`). Essa função:

- Recebe o **corpo de um e-mail em texto livre** (`corpo_email: str`) e uma `transportadora: str`
  já como parâmetros — ou seja, não lê e-mail nenhum sozinha; espera que algo já tenha entregue
  o texto do e-mail a ela.
- Extrai por **regex**: protocolo (padrão `BD-[A-Z0-9]{8}`), horário (`HH:MM` ou `HHhMM`) e placa
  de veículo (padrão brasileiro).
- Se encontrar um protocolo que bate com uma `ProgramacaoColeta.protocolo` existente, seta
  incondicionalmente `confirmado_em = agora`, `status_envio = "confirmado"`,
  `veiculo_confirmado = placa`.
- **Não existe nenhum caminho de "recusa"** — a função só sabe processar aceite.

**Porém: essa função nunca é chamada.** Busquei `processar_email_confirmacao` em todo o
repositório e a única ocorrência é a própria definição — nenhum endpoint HTTP, nenhum
webhook de e-mail recebido, nenhum cron, nada a invoca. Não há integração de caixa de entrada
(IMAP, webhook do provedor de e-mail, etc.) em lugar nenhum do código.

**Conclusão para o design:** não estamos substituindo um fluxo funcionando — estamos
**construindo o primeiro fluxo de confirmação que realmente funciona em produção**. O parsing
por regex de `processar_email_confirmacao` pode ser descartado ou mantido como
fallback secundário (ex.: se o operador colar manualmente a resposta de um e-mail que veio fora
do fluxo de link), mas não é a base do novo mecanismo — o novo fluxo (item 4) é
**baseado em link + token assinado**, não em parsing de texto de e-mail.

**Infra de envio de e-mail hoje** (`agents/agente_comunicador.py:341-363`):
- Envio real via `smtplib` (`SMTP_HOST`/`PORT`/`USER`/`PASSWORD`, `core/config.py:80-84`),
  mas `SMTP_USER`/`SMTP_PASSWORD` default para string vazia — sem credenciais configuradas em
  origin, o envio real falharia (por isso o `dry_run` do `AgenteComunicador` existe: em modo
  demo, marca `status_envio="simulado"` e nunca chama `_enviar_email`).
- O e-mail hoje é **só texto plano** (`MIMEText(corpo, "plain", "utf-8")`) — não há parte HTML.
  O novo fluxo (item 4) exige e-mail com link clicável, então isso precisa mudar para
  `MIMEMultipart("alternative")` com uma parte HTML.
- **Não existe hoje nenhuma variável de configuração de URL pública da aplicação**
  (`BASE_URL`/`APP_URL`/`PUBLIC_URL` — busquei em `core/config.py`, não existe). Precisa ser
  adicionada para montar o link do e-mail (ex.: `https://<host>/confirmar-coleta?token=...`).

---

### 2. Transportadora por região — já existe e já funciona

**Já existe uma implementação completa e testada disso**, em
`agents/agente_montador.py:367-453` (`_selecionar_melhor_transportadora`). Ela já faz
exatamente "listar transportadoras candidatas de uma região, ordenadas por preço":

1. Determina UF e classificação (`Capital`/`Interior`, via `_classificar_cliente()`,
   linha 37) a partir do(s) cliente(s) da remessa/lote.
2. Consulta `tabela_preco_transportadoras` (`core/models.py:294-333`) filtrando por
   `transportadora_id IN (candidatas do CD)`, `uf`, `classificacao`, `cobertura=True`,
   `ativo=True`.
3. Calcula o **custo real** por linha via `_custo()` (linha 427-432):
   - FTL: `preco_ftl_fixo` (valor fixo).
   - Fracionado: `preco_por_kg × peso_kg`, mais `(ad_valorem_pct + gris_pct) × valor_nf`,
     com piso em `preco_minimo`.
4. Ordena por `(custo, prazo)` crescente — mais barato primeiro, empate desempatado pelo prazo
   mais curto (linha 437: `sorted(precos, key=lambda p: (_custo(p), _prazo(p)))`).
5. Hoje retorna só a **primeira** (mais barata) — mas a lista ordenada inteira
   (`cotacoes`, linha 437) já está computada internamente antes de pegar `cotacoes[0]`.
6. Tem fallback (`REGRAS_REGIAO`, linha 52) para quando não há linha de preço cadastrada
   para aquela UF/classificação.

**Estrutura de dado usada:** `TabelaPrecoTransportadora` também tem uma coluna
`sla_confirmacao_h` (linha 321) — um SLA de confirmação **por rota/tabela de preço**, mais
granular que o `Transportadora.sla_resposta_h` (por transportadora, usado no card de
Turnaround de Coleta que fizemos antes). Vale decidir explicitamente qual dos dois usar como
SLA de referência nesta feature — provavelmente `sla_confirmacao_h` da linha de preço
correspondente, por ser mais específico, com fallback pro `sla_resposta_h` da transportadora
se a linha de preço não tiver o campo preenchido.

**Para a nova feature:** não precisa reimplementar a lógica de cotação — precisa de uma
função irmã que, em vez de retornar só a mais barata, **retorna a lista ordenada inteira**
(reaproveitando exatamente a mesma query e o mesmo `_custo()`), para permitir "tentar a
próxima" quando a atual recusa. A reconstrução do "lote" de remessas a partir de uma `Onda`
já planejada é possível via `Onda.remessas` (relationship many-to-many já existente,
`core/models.py:224-226`).

---

### 3. Condição de "risco de prazo/ATA" — proposta exata

**Alertas já existentes gerados pelo Classificador** (`agents/agente_classificador.py:192-272`):

| Tipo do alerta | Condição de disparo | Severidade |
|---|---|---|
| `ata_prazo` | `is_ata=True` e `prazo_empenho` definido, `dias_restantes <= settings.ALERTA_ATA_DIAS` (=5) | `critica` se `dias_restantes <= 2`, senão `alta` |
| `nf_pendente` | `nf_emitida=False` e (`is_ata=True` ou `dias_restantes <= 1`) | sempre `critica` |
| `janela_vencida` | `janela_fim` já passou hoje | sempre `alta` (nunca `critica`) |
| `hospital_sem_armazenagem` | cliente hospitalar sem armazenagem consolidado em FTL | (não li a severidade exata — não é um risco de prazo, é operacional; não deveria contar pra essa condição) |

**Campo `janela_critica` da própria Remessa** (`agente_ingestor.py:363-393,508-524`): é
populado de verdade na ingestão (não é campo morto), mas hoje só alimenta o cálculo de
`Remessa.prioridade` (`"alta"` se `janela_critica=True`) — **não gera Alerta nenhum** hoje.
É um sinal real, mas desconectado do sistema de alertas.

**Proposta de condição exata** (a confirmar com você antes de implementar):

> Escalar para o operador do CD (NÃO reagendar automaticamente) se, para a remessa em questão,
> **qualquer uma** das condições abaixo for verdadeira:
>
> 1. Existe `Alerta` ativo (`resolvido=False`) com `severidade == "critica"` para essa remessa
>    (cobre `ata_prazo` grave e `nf_pendente`, sem precisar reimplementar a regra — só consultar
>    `remessa.alertas` já existente, `core/models.py:166,290`).
> 2. `remessa.is_ata == True` e `remessa.dias_restantes is not None` e `dias_restantes <= 2`
>    (mesmo limiar que já dispara severidade crítica em `ata_prazo` — redundante com a condição
>    1 quando o alerta já foi gerado, mas serve de rede de segurança caso o alerta ainda não
>    tenha rodado nesse ciclo).
> 3. `remessa.janela_critica == True` (incluído mesmo não gerando alerta hoje, porque é um sinal
>    real e específico de "esse cliente não tolera atraso" — a critério seu, pode ser removido
>    da condição se você achar que é sinal fraco demais).
>
> Caso nenhuma seja verdadeira → reagendamento automático permitido (item 5d).

Isso é uma proposta — pode ser ajustada (ex.: incluir `alta` também, não só `critica`) antes da
implementação.

---

## FASE 2 — Especificação do fluxo

### 4. Fluxo de e-mail com link + token

- E-mail passa a ter parte HTML (além do texto plano, por compatibilidade) com um botão/link
  único: `GET https://<BASE_URL>/confirmar-coleta?token=<token>` — a página é só leitura
  (mostra os dados da programação: onda, transportadora, veículo esperado, prazo) com **dois
  formulários POST** (`Aceitar` / `Recusar`), cada um enviando o mesmo token de volta.
  Isso evita o problema real de scanners corporativos de segurança (Microsoft Defender for
  Office 365, Proofpoint, etc.) que pré-visitam todo link de e-mail via GET — se aceitar/recusar
  fosse uma ação GET direta, o scanner "confirmaria" ou "recusaria" a coleta sozinho, sem
  ninguém na transportadora ter clicado.
- Token: assinado (mesma biblioteca JWT já usada em `core/auth.py` para os tokens de sessão, ou
  um HMAC simples — a definir), carregando `programacao_coleta_id` e uma data de expiração.
  Expira em **48h após emissão** OU imediatamente após ser usado (aceito ou recusado) —
  o que vier primeiro.
- Checagem de expiração e de uso prévio acontece **no momento do clique** (lazy), não via cron
  — evita depender de um job periódico (relevante porque o projeto já usa o único cron do
  plano Hobby da Vercel para o Resolvedor, `vercel.json` + `api/main.py:1868-1887`, comentário
  explícito: *"Vercel Cron, 1x/dia — limite do plano Hobby"*).

### 5. Fluxo de recusa (cascata de realocação)

a) Busca as transportadoras candidatas da mesma região (UF + classificação), ordenadas por
   menor preço — reaproveitando a lógica de `_selecionar_melhor_transportadora` descrita no
   item 2, exposta como lista completa em vez de só a primeira.

b) Envia o mesmo fluxo de e-mail (item 4) para a próxima transportadora da lista, criando uma
   **nova `ProgramacaoColeta`** (não sobrescrevendo a anterior — preserva histórico de quem já
   recusou, seguindo o padrão do resto do projeto de nunca apagar/sobrescrever histórico:
   `historico_eventos`, `EventoRastreio` funcionam da mesma forma).

c) Se recusar de novo, repete com a próxima, até esgotar a lista de candidatas ativas e com
   cobertura para aquela região.

d) Se todas recusarem:
   - Risco de prazo/ATA (condição do item 3) → gera `Alerta` de severidade alta/crítica para
     o operador do CD decidir manualmente (reaproveitando `_criar_alerta`/`_criar_alerta_raw`
     já existentes em `agente_monitor.py`/`agente_classificador.py`).
   - Sem risco → reagenda automaticamente pro próximo dia útil, sem alerta nem intervenção.

**Pergunta em aberto (favor decidir antes da implementação):** o fluxo acima só é acionado por
**recusa explícita** (clique em "Recusar"). E se a transportadora simplesmente **não responder**
dentro do SLA (nem aceita nem recusa)? Isso é exatamente o estado que o card "Turnaround de
Coleta por Transportadora" (já em produção) já detecta — `excedeu SLA`. Faz sentido a cascata
de realocação também disparar automaticamente quando o SLA estoura, não só na recusa explícita?
Se sim, isso implica algum mecanismo de verificação periódica (cron, ou checagem lazy no
próximo carregamento do dashboard) — precisa ser decidido e endereçado numa iteração futura ou
já nesta, dado o limite de cron do Vercel Hobby mencionado acima.

### 6. Idempotência

- Ao clicar em Aceitar ou Recusar, o token é imediatamente marcado como usado (campo novo, ver
  item 7) **antes** de qualquer efeito colateral (mesmo padrão transacional que o resto do
  projeto usa: `db.add()` + `db.flush()` + `db.commit()` só no fim).
- Uma segunda tentativa de uso do mesmo token (ex.: alguém reenvia o e-mail, ou o scanner de
  segurança da transportadora clica de novo) deve mostrar uma página informando
  "esta programação já foi respondida em `<data/hora>` como `<aceita/recusada>`", **sem alterar
  nada** — nem repetir o efeito de aceite/recusa, nem dar erro genérico.

### 7. Modelo de dado necessário (proposta — não implementar ainda)

Novos campos em `ProgramacaoColeta` (`core/models.py:236-256`):
- `token_confirmacao` (String, único, indexado) — o token assinado emitido para esta
  programação específica.
- `token_expira_em` (DateTime) — momento de expiração (emissão + 48h).
- `token_usado_em` (DateTime, nullable) — quando o token foi consumido (aceito ou recusado);
  distinto de `confirmado_em` porque uma recusa também "usa" o token sem confirmar.
- `recusado_em` (DateTime, nullable) — espelha `confirmado_em`, mas pro caminho de recusa.
- `tentativa_numero` (Integer, default 1) — qual tentativa de realocação esta linha representa
  (1ª transportadora, 2ª após 1ª recusa, etc.) — dá pra derivar via
  `programacao_coleta_anterior_id` também, mas um contador simples é mais fácil de consultar.
- `programacao_coleta_anterior_id` (FK para `programacoes_coleta.id`, nullable) — encadeia a
  cascata: cada nova tentativa aponta pra qual recusa a originou, formando uma cadeia
  auditável por `onda_id`.

Novo campo em `Onda` (`core/models.py:198-226`) — opcional, a avaliar:
- Nenhum campo novo necessariamente necessário aqui, já que `Onda.transportadora_id` pode ser
  atualizado para refletir a transportadora finalmente confirmada, e o histórico completo de
  tentativas fica em `programacoes_coleta` via o encadeamento acima.

Sem tabela nova dedicada a "histórico de tentativas de realocação" — o encadeamento de
`ProgramacaoColeta` via `programacao_coleta_anterior_id` já serve esse propósito sem duplicar
dado que já existe em `historico_eventos` (que também deve registrar cada tentativa/recusa,
seguindo o padrão já usado em todo o resto do projeto).

Config nova necessária em `core/config.py`:
- `BASE_URL` (ou `APP_URL`) — URL pública da aplicação, para montar o link do e-mail. Hoje não
  existe nenhuma variável assim no projeto.

---

## Resumo do que precisa da sua decisão antes de qualquer código

1. Confirmar a condição exata do item 3 (incluir `janela_critica`? incluir severidade `alta`
   além de `critica`?).
2. Confirmar se usa `TabelaPrecoTransportadora.sla_confirmacao_h` ou
   `Transportadora.sla_resposta_h` como SLA de referência (ou os dois, com fallback).
3. Decidir a pergunta em aberto do item 5 (SLA estourado sem resposta também dispara cascata,
   ou só recusa explícita nesta primeira versão?).
4. Aprovar (ou ajustar) o modelo de dado do item 7.
5. Confirmar mecanismo de assinatura do token (JWT vs HMAC simples).

Nenhum código funcional foi escrito para esta feature. Este documento está commitado
**apenas em `origin`**, para sua revisão.
