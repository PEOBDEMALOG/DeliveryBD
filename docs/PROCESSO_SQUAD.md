# Processo de Squad e Sprint — PEO-BD

Formaliza por escrito o processo que já vem sendo seguido na prática neste
repositório (confirmado no histórico de commits e em `WORKFLOW.md`), não
um processo novo importado de outro contexto. Onde a prática real diverge
do que seria "ideal" em um projeto maior, este documento descreve a
prática real — ajustar deliberadamente, não por omissão.

---

## 1. Como abrir uma frente de trabalho nova

Hoje o projeto **não usa branch por feature** — todo trabalho vai direto
para `main` em `origin` (confirmado no histórico de commits: nenhuma
branch além de `main` foi usada até aqui). Uma frente de trabalho nova
("squad" no sentido deste projeto = uma pessoa ou par focado num escopo
específico por um período) abre assim:

1. **Defina o escopo em uma frase** — o que muda e por quê. Se não cabe
   em uma frase, é grande demais para uma frente só; quebre em partes.
2. **Registre no backlog** antes de começar a codar, usando o
   [template da seção 4](#4-template-de-sprintbacklog). Se for dívida
   técnica (algo que já existe e está errado/incompleto), registra direto
   em `DIVIDA_TECNICA.md`. Se for feature nova, registra onde a squad
   estiver rastreando sprint (backlog compartilhado da equipe — este
   projeto não tem um issue tracker próprio hoje).
3. **Trabalhe em `origin`.** Nunca em `producao` diretamente — ver DoD
   abaixo.
4. **Trabalho exploratório/temporário vai marcado.** Convenção já em uso
   no histórico: commits e rotas temporárias de debug levam o prefixo
   `[TEMPORARIO]` na mensagem (ex: `[TEMPORARIO] debug read-only: ...`) e
   são removidos num commit seguinte explícito (`Remove rota temporaria
   de debug ...`) assim que servem seu propósito. Nunca deixe uma rota ou
   endpoint `[TEMPORARIO]` sobreviver até a promoção pra `producao`.

Se o escopo crescer o suficiente para justificar isolamento (ex: mudança
grande e arriscada que pode quebrar `origin` por dias), considere uma
branch — não é proibido, só não é o padrão atual. Documente a decisão no
registro do backlog se fugir do padrão.

### 1.1 Proteção de branch em `origin`: decisão registrada (16/07/2026)

`origin` (`marciobarbarulo10-oss/Projeto-BD`) é um repositório **privado**.
O GitHub Free não oferece branch protection (nem clássica, nem rulesets)
em repositórios privados — só em públicos, ou em privados com plano
Pro/Team/Enterprise. Testado nesta data: com o repo temporariamente
público, uma configuração de proteção (PR obrigatório, CI obrigatório,
sem force-push, `enforce_admins`) bloqueava push direto de verdade
(rejeitado pelo GitHub, `GH006: Protected branch update failed`); assim
que o repo voltou a ser privado, a mesma proteção foi **desativada
automaticamente** — não fica pausada, deixa de existir, e um push direto
sem PR voltou a ser aceito sem nenhum bloqueio (confirmado na prática).

**Decisão consciente:** manter o repositório **privado** e aceitar que a
disciplina de branch + PR + CI antes de merge em `main` é seguida **por
convenção** (revisão manual), não por trava técnica do GitHub.
Privacidade do código (não é open source, contém lógica de negócio da
Emalog) pesou mais que ter enforcement automático — não há hoje uma
segunda pessoa fixa na squad para justificar o custo do GitHub Pro só
para habilitar a trava.

Na prática, a partir de agora:
- Toda mudança não-trivial passa por branch e PR por hábito da squad, mas
  nada no GitHub impede tecnicamente um push direto que pule esse fluxo.
- O CI (`.github/workflows/ci.yml`) roda em todo push/PR independente de
  branch protection — mas nada bloqueia um merge com CI vermelho.
- A revisão por uma segunda pessoa antes do push (seção 3, Definição de
  Pronto) continua sendo, por natureza, uma convenção informal.

**Reavaliar se:** o repositório precisar ficar público por outro motivo,
uma segunda pessoa entrar fixa na squad, ou o custo do GitHub Pro deixar
de ser um problema.

---

## 2. Convenção de mensagem de commit

A partir de agora (não retroativo — ver nota no fim desta seção), commits
seguem [Conventional Commits](https://www.conventionalcommits.org/pt-br/):

```
<tipo>(<escopo opcional>): <descrição no imperativo, minúsculo>
```

**Tipos usados neste projeto:**

| Tipo | Quando usar |
|---|---|
| `feat` | Feature nova ou comportamento novo visível pro usuário |
| `fix` | Correção de bug — comportamento estava errado, agora está certo |
| `docs` | Só documentação (`docs/`, `README.md`, `DIVIDA_TECNICA.md`, comentário) — nenhum código de produto muda |
| `chore` | Manutenção sem efeito funcional — dependência, config, limpeza de arquivo, reorganização de pasta |
| `refactor` | Reestrutura código existente sem mudar comportamento observável |
| `security` | Correção especificamente de vulnerabilidade/exposição de dado — **não é tipo oficial do Conventional Commits**, é extensão deste projeto pra deixar achado de segurança grepável separado de `fix` genérico |
| `test` | Mudança em `tests/` (scripts de validação) sem tocar código de produto |

Escopo (entre parênteses, opcional) indica a área afetada quando ajuda a
quem lê o log — ex: `fix(upload):`, `security(auth):`. Não é obrigatório;
use quando a descrição sozinha não deixa claro onde a mudança acontece.

**Exemplos reais adaptados do histórico deste repositório** (mensagem
original → como ficaria no padrão novo):

- `Remove card duplicado de Alertas Críticos, ajusta grid KPIs 7→6`
  → `fix: remove card duplicado de Alertas Críticos, ajusta grid KPIs 7→6`
- `Remove segredos hardcoded: JWT_SECRET falha explícito, credenciais de demo rotacionadas`
  → `security(auth): remove segredos hardcoded — JWT_SECRET falha explícito, credenciais de demo rotacionadas`
- `Card Turnaround de Coleta por Transportadora, substitui Status das Remessas`
  → `feat(dashboard): card Turnaround de Coleta por Transportadora, substitui Status das Remessas`
- `Reorganiza scripts de validacao para tests/ e limpa residuos de raiz`
  → `chore: reorganiza scripts de validação para tests/ e limpa resíduos de raiz`

**Isto vale só daqui pra frente.** Não reescrevemos o histórico de commits
já existente pra encaixar no padrão — reescrever histórico (`rebase -i`,
`filter-branch`) é arriscado (reescreve hash de tudo que vem depois, quebra
qualquer referência externa ao commit) e não traz benefício real
proporcional ao risco. O `CHANGELOG.md` na raiz já cobre o histórico
anterior de forma legível, sem precisar mexer nos commits originais.

---

## 3. Definição de Pronto (Definition of Done)

Uma frente de trabalho só está pronta quando **todos** os itens abaixo
são verdade:

- [ ] **Validado em `origin`** — funcionalmente correto, testado contra a
  API/frontend de pé (local ou `origin`'s deploy). Para qualquer mudança
  que toque autenticação, filtro por CD, ou dado exibido por tela:
  validado logado como os **dois** perfis de operador (Timóteo/Osasco e
  Carlos/Itajaí), não só como admin — ver `ONBOARDING.md` → "Validação
  manual multi-perfil".
- [ ] **`DIVIDA_TECNICA.md` atualizada quando aplicável** — se a mudança
  introduz uma limitação conhecida, resolve uma pendência já registrada,
  ou revela uma dívida nova, o arquivo reflete isso antes de considerar a
  frente concluída. Não deixe descoberta relevante só na cabeça de quem
  fez a mudança.
- [ ] **Nenhum código/rota `[TEMPORARIO]` sobrevivendo** — removido antes
  da promoção (ver seção 1).
- [ ] **Regras de segurança obrigatórias respeitadas** — ver seção 5.
  Isso é bloqueante, não "nice to have".
- [ ] **Mensagem de commit no padrão Conventional Commits** — ver seção 2.
- [ ] **`CHANGELOG.md` atualizado quando a mudança é relevante pra quem
  usa ou revisa o projeto** — feature nova, correção de bug visível,
  mudança de segurança, ou reorganização estrutural ganham uma linha
  nova em `CHANGELOG.md` no mesmo commit (ou num commit `docs` logo em
  seguida). Ajuste interno sem efeito observável (typo em comentário,
  formatação) não precisa.
- [ ] **Revisado por uma segunda pessoa antes do push para `origin`** —
  mesmo que informalmente (compartilhar o diff, pedir uma leitura rápida),
  toda mudança que toque autenticação, filtro por CD, ou dado sensível
  passa por pelo menos um outro par de olhos antes de ir para `origin`.
  Trabalho solo temporário (squad de 1 pessoa) é exceção documentada, não
  o padrão esperado quando houver mais de uma pessoa na squad.
- [ ] **`config/requirements.lock.txt` regenerado no mesmo commit, se
  `config/requirements.txt` mudou** — `pip install pip-tools && pip-compile
  --output-file=config/requirements.lock.txt config/requirements.txt`. Esquecer esse
  passo é silencioso: o CI continua instalando a partir do lockfile (ver
  `docs/CI.md`), então passaria verde testando a versão **antiga**,
  escondendo exatamente o cenário que o lockfile existe pra prevenir —
  ex: o upgrade de `python-jose`/`python-multipart` por CVE (ver
  `DIVIDA_TECNICA.md`) só vale alguma coisa se o lockfile refletir a
  versão nova.
- [ ] **`git push origin main` feito e validado** antes de sequer
  considerar `producao`.
- [ ] **CI passou (verde)** antes de considerar pronto pra promover pra
  `producao` — ver `docs/CI.md` pro que o workflow cobre (import de
  sanidade, smoke test, checklist informativo) e pro que **não** cobre
  (não substitui `tests/testar_offline.py` nem a validação manual
  multi-perfil acima — CI verde não dispensa nenhum dos itens deste
  checklist).
- [ ] **Promoção pra `producao` é um passo separado e explícito** —
  `git push producao main` só acontece depois de tudo acima, seguindo o
  processo em `WORKFLOW.md`. **Nunca commit ou push direto em `producao`
  sem o commit já ter passado por `origin` primeiro** — `producao` só
  recebe fast-forward do que já foi validado em `origin`, nunca um
  histórico divergente.

---

## 4. Template de sprint/backlog

Estrutura inspirada no padrão já usado em `DIVIDA_TECNICA.md` (título,
contexto do "por quê", e o que fazer), com os campos de priorização
explícitos no topo. Use isso tanto para dívida técnica nova (cole direto
em `DIVIDA_TECNICA.md`, seção "Pendências") quanto para item de sprint
em qualquer lugar que a squad esteja rastreando backlog:

```markdown
### <Nome curto e específico da pendência/feature>

**Prioridade:** Alta | Média | Baixa
**Esforço estimado:** P | M | G  (ou horas/dias, se preferir granularidade maior)
**Risco:** Alto | Médio | Baixo — risco de NÃO fazer, ou risco de fazer errado

<Uma ou duas frases descrevendo o problema/necessidade concretamente —
sem jargão, alguém que não é da squad deve entender do que se trata.>

**Por quê:** <motivação — incidente, pedido de cliente, achado de
auditoria, decisão de arquitetura. O que torna isso relevante agora.>

**O que fazer:** <ação concreta esperada, ou opções (a)/(b) se houver mais
de um caminho razoável.>
```

Exemplo real já registrado nesse formato:
`DIVIDA_TECNICA.md` → "Vazamento de dados entre CDs (13/07/2026)".

---

## 5. Regras de segurança obrigatórias

Estas três regras são **decisões de arquitetura**, não descrições de bugs
já corrigidos — valem para todo código novo, sempre, mesmo que o
incidente que as originou já esteja resolvido.

### Regra 1 — Nunca commitar credencial em texto puro

Toda credencial (senha, chave de API, secret de assinatura) é variável de
ambiente, nunca literal no código. `core/auth.py` já seguiu esse padrão
antes de ser corrigido — as senhas dos 3 usuários demo ficaram em texto
puro no código-fonte por um tempo, inclusive sobrevivendo a uma rotação
anterior que só trocou o valor sem resolver o problema real. Corrigido em
16/07/2026 (senhas movidas para `AUTH_SENHA_TIMOTEO`/`CARLOS`/`ERICK`,
fail-fast igual ao `JWT_SECRET`) — ver `DIVIDA_TECNICA.md` para o
histórico completo do incidente.

### Regra 2 — Variável de ambiente sensível é fail-fast, nunca tem default silencioso

Padrão de referência: `JWT_SECRET` em `core/config.py` — a aplicação
**recusa subir** (`RuntimeError` explícito) se a variável não estiver
definida, em vez de cair silenciosamente num valor default hardcoded.
Um default aqui ficaria exposto no código-fonte e serviria pra assinar
tokens válidos se a env var real nunca fosse configurada — falha explícita
é sempre melhor que a aplicação rodando com um segredo público conhecido.
Aplique o mesmo padrão a qualquer secret/credencial nova.

### Regra 3 — Nunca aceitar parâmetro de escopo vindo do cliente sem derivar do token

Todo endpoint que retorna dado particionado por CD (remessa, alerta,
histórico, onda, upload) **deve** derivar o CD do usuário autenticado a
partir do JWT — nunca confiar em `cd_id`/`cd_codigo` (ou qualquer
parâmetro de escopo equivalente que venha a existir no futuro, ex: um
`tenant_id` numa expansão multi-cliente) vindo de query param, body, ou
header controlado pelo cliente. Use a dependency `usuario_autenticado` +
`cd_codigo_forcado`/`cd_id_forcado` (`api/main.py`) — não reimplemente
essa lógica numa rota nova.

Contexto: esta regra existe porque sua ausência causou um vazamento real
de dados entre CDs (13/07/2026, ver `DIVIDA_TECNICA.md` → "Vazamento de
dados entre CDs"), onde 11+ endpoints confiavam em parâmetro do cliente e
um operador conseguia ver dado de outro CD só editando a URL. A correção
foi pontual; esta regra é o que impede a mesma classe de bug de voltar em
qualquer endpoint futuro.

---

## Referências

- [`WORKFLOW.md`](WORKFLOW.md) — os dois ambientes (`origin`/`producao`),
  comandos de promoção, e o checklist de teste de vazamento de CD antes do
  primeiro upload real em produção.
- [`DIVIDA_TECNICA.md`](DIVIDA_TECNICA.md) — todas as pendências e
  registros de processo (incluindo o incidente que originou a Regra 3
  acima, em detalhe).
- [`ONBOARDING.md`](ONBOARDING.md) — setup local, convenções de código,
  como validar uma mudança antes de considerar pronta.
- [`ARQUITETURA.md`](ARQUITETURA.md) — visão geral do sistema, stack e os
  7 agentes.
- [`CHANGELOG.md`](../CHANGELOG.md) — histórico de mudanças relevantes por
  data/marco, retroativo ao primeiro commit do repositório; alimentado a
  partir de agora seguindo a Definição de Pronto (seção 3).
- [`CI.md`](CI.md) — o que `.github/workflows/ci.yml` cobre e o que não
  cobre (não substitui `tests/testar_offline.py` nem a validação manual
  multi-perfil).
