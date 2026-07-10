#!/usr/bin/env python3
"""
Infra de teste de stress — PEO-BD (Etapa 2)
Gera histórico realista de uso diário (Timóteo/Osasco e Carlos/Itajaí):
remessas, planos, ondas, eventos de histórico e alertas, em volume alto,
via bulk insert (nunca linha por linha).

Uso:
    python scripts/seed_stress_test.py --periodo semana --modo adicionar
    python scripts/seed_stress_test.py --periodo ano     --modo limpo

--periodo: semana | mes | trimestre | semestre | nove_meses | ano
--modo:    limpo (apaga dados operacionais antes) | adicionar (mantém o que existe)
"""

import argparse
import asyncio
import random
import sys
import time as time_module
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import insert, select, text, update, func, bindparam
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from core.config import settings, DB_CONNECT_ARGS
from core.models import (
    CentroDistribuicao, Cliente, Transportadora, Veiculo, Upload,
    Remessa, PlanoDia, Onda, OndaRemessa, ProgramacaoColeta,
    HistoricoEventos, Alerta,
)
from agents.agente_classificador import AgenteClassificador
from agents.agente_montador import AgenteMontador

# ── Parâmetros do período ────────────────────────────────────────────────────

PERIODOS = {
    "semana":      5,
    "mes":        22,
    "trimestre":  66,
    "semestre":  132,
    "nove_meses":198,
    "ano":       264,
}

# Tabelas operacionais apagadas em --modo limpo, em ordem de dependência de FK
# (historico_eventos.remessa_id referencia remessas — precisa vir antes;
#  /api/demo/reset tem a mesma lista mas com essa ordem invertida, um bug
#  latente que só aparece quando historico_eventos.remessa_id está populado).
TABELAS_OPERACIONAIS = [
    "onda_remessas", "programacoes_coleta", "ondas",
    "planos_dia", "alertas", "oportunidades_consolidacao",
    "eventos_rastreio", "historico_eventos", "remessas", "uploads",
]

REGIOES = {
    "OSA": ["capital_sp", "interior_sp", "grande_sp", "litoral_sp", "vale_paraiba"],
    "ITJ": ["sul", "litoral_sc", "planalto_sc", "vale_itajai", "extremo_sul"],
}

# Cadeia de status por que uma remessa passa até chegar no status final —
# cada etapa vira um evento "mudanca_status" no histórico.
FSM_ATE = {
    "novo":            [],
    "planejado":       ["planejado"],
    "coletado":        ["planejado", "coletado"],
    "em_transito":     ["planejado", "coletado", "em_transito"],
    "em_rota_entrega": ["planejado", "coletado", "em_transito", "em_rota_entrega"],
    "entregue":        ["planejado", "coletado", "em_transito", "em_rota_entrega", "entregue"],
    "tentativa":       ["planejado", "coletado", "em_transito", "em_rota_entrega", "tentativa"],
    "devolvido":       ["planejado", "coletado", "em_transito", "em_rota_entrega", "tentativa", "devolvido"],
}
FSM_OFFSET = {
    "planejado":       timedelta(hours=2),
    "coletado":        timedelta(hours=4),
    "em_transito":     timedelta(days=1, hours=1),
    "em_rota_entrega": timedelta(days=1, hours=8),
    "entregue":        timedelta(days=1, hours=10),
    "tentativa":       timedelta(days=1, hours=10),
    "devolvido":       timedelta(days=1, hours=14),
}

ERROS_CODIGOS_TIMEOUT = ["TIMEOUT_SAP", "ARQUIVO_CORROMPIDO"]

MOTIVOS_TENTATIVA = [
    "Destinatário ausente no momento da entrega",
    "Endereço não localizado",
    "Recusado pelo destinatário",
    "Estabelecimento fechado",
    "Acesso bloqueado ao local",
    "Documento do destinatário não apresentado",
]

CHUNK_DIAS = 44  # dias úteis processados por lote de commit


def distribuir_clientes(clientes: list, n_remessas: int) -> list[tuple[int, int]]:
    """Garante distribuição mínima: todos os clientes recebem ao menos 1
    remessa antes de qualquer remessa extra ser sorteada por peso.

    Sem essa garantia, um sorteio uniforme puro (random.choice por remessa)
    pode — sobretudo em pools pequenos como o de Itajaí (5 clientes) — deixar
    algum cliente sem nenhum pedido no período por pura chance, mesmo ele
    estando corretamente no pool. Cliente.peso não existe no schema; usamos
    volume_medio_m3 como proxy real do volume esperado de cada cliente.
    """
    if not clientes:
        return []
    base = {c.id: 1 for c in clientes}  # mínimo 1 por cliente
    extras = n_remessas - len(clientes)
    if extras > 0:
        pesos = [float(c.volume_medio_m3 or 1) for c in clientes]
        for cliente in random.choices(clientes, weights=pesos, k=extras):
            base[cliente.id] += 1
    return list(base.items())


def dias_uteis(n_dias: int) -> list[date]:
    """Gera n_dias úteis ANTERIORES a hoje (simula histórico real)."""
    dias = []
    d = date.today() - timedelta(days=1)  # começa ontem
    while len(dias) < n_dias:
        if d.weekday() < 5:  # seg-sex
            dias.append(d)
        d -= timedelta(days=1)
    return list(reversed(dias))


def volume_do_dia(dia: date) -> tuple[int, int]:
    """Retorna (n_osa, n_itj) com variação realista."""
    fator = 1.15 if dia.weekday() == 0 else 1.0
    fator *= random.uniform(0.80, 1.20)
    return int(65 * fator), int(35 * fator)


def otif_mensal(mes: int, ano: int) -> float:
    """Simula variação de OTIF ao longo do ano — não linear, tem sazonalidade."""
    if mes in (3, 4, 10, 11):
        return random.uniform(88.0, 93.0)
    elif mes in (12, 1):
        return random.uniform(91.0, 95.0)
    else:
        return random.uniform(93.5, 97.5)


def distribuicao_status(dia: date, hoje: date, otif_alvo: float) -> str:
    """Simula status realista baseado na antiguidade da remessa. O split
    entregue/tentativa/devolvido de remessas já resolvidas é derivado do
    OTIF-alvo do mês (otif_mensal), não fixo — é o que dá sazonalidade real
    ao OTIF do período."""
    dias_passados = (hoje - dia).days
    if dias_passados == 0:
        return random.choices(["novo", "planejado"], weights=[30, 70])[0]
    elif dias_passados <= 2:
        return random.choices(
            ["em_transito", "em_rota_entrega", "entregue"], weights=[20, 30, 50]
        )[0]
    else:
        p_entregue = otif_alvo / 100
        p_falha = 1 - p_entregue
        return random.choices(
            ["entregue", "tentativa", "devolvido"],
            weights=[p_entregue, p_falha * 0.75, p_falha * 0.25],
        )[0]


def hora_aleatoria(inicio_h: int, fim_h: int) -> time:
    return time(random.randint(inicio_h, fim_h), random.choice([0, 15, 30, 45]))


class GeradorStress:
    def __init__(self, db, run_id: str):
        self.db = db
        self.run_id = run_id
        self.contador = 0
        # contadores para o resumo final
        self.total_remessas = 0
        self.total_ondas = 0
        self.total_eventos = 0
        self.total_alertas = 0
        self.erros = {"TIMEOUT_SAP": 0, "ATA_VENCIDA": 0, "ARQUIVO_CORROMPIDO": 0}
        self.otifs_amostrados: list[float] = []

    def _prox_id(self) -> int:
        self.contador += 1
        return self.contador

    # ── Infra base (carregada uma vez) ──────────────────────────────────────

    async def carregar_infra(self):
        res = await self.db.execute(select(CentroDistribuicao).where(CentroDistribuicao.ativo == True))
        self.cds = {cd.codigo: cd for cd in res.scalars().all()}

        res = await self.db.execute(select(Cliente))
        clientes = res.scalars().all()
        self.clientes = {
            "OSA": [c for c in clientes if c.codigo_sap],
            "ITJ": [c for c in clientes if c.codigo_ups],
        }
        if not self.clientes["OSA"] or not self.clientes["ITJ"]:
            raise RuntimeError(
                "Base de clientes incompleta — rode 'python scripts/seed_demo.py' "
                "primeiro para popular clientes OSA/ITJ."
            )

        res = await self.db.execute(select(Transportadora).where(Transportadora.ativo == True))
        transportadoras = res.scalars().all()
        self.transportadoras = {
            cd_codigo: [t for t in transportadoras if t.cd_id == cd.id]
            for cd_codigo, cd in self.cds.items()
        }
        self.transportadora_por_id = {t.id: t for t in transportadoras}

        res = await self.db.execute(select(Veiculo).where(Veiculo.ativo == True))
        veiculos = res.scalars().all()
        self.veiculos = {
            cd_codigo: [v for v in veiculos if v.cd_id == cd.id]
            for cd_codigo, cd in self.cds.items()
        }

    # ── Um lote de dias ──────────────────────────────────────────────────────

    async def processar_lote(self, dias: list[date], hoje: date):
        uploads_buf, uploads_keys = [], []
        for dia in dias:
            for cd_codigo in ("OSA", "ITJ"):
                uploads_keys.append((dia, cd_codigo))
                sistema = "SAP" if cd_codigo == "OSA" else "UPS_WMS"
                uploads_buf.append({
                    "cd_id":          self.cds[cd_codigo].id,
                    "usuario":        "timoteo" if cd_codigo == "OSA" else "carlos",
                    "arquivo_nome":   f"Backlog_{sistema}_{cd_codigo}_{dia:%d%m%Y}.xlsx",
                    "arquivo_path":   f"/stress/{cd_codigo}_{dia:%Y%m%d}.xlsx",
                    "formato":        "xlsx",
                    "status":         "concluido",
                    "criado_em":      datetime.combine(dia, hora_aleatoria(6, 8)),
                })
        upload_ids = await self._inserir_returning(Upload, uploads_buf)
        upload_id_map = dict(zip(uploads_keys, upload_ids))

        remessas_buf, remessas_keys = [], []
        remessas_meta = {}  # numero_remessa -> dict com metadados úteis pra depois
        for dia in dias:
            otif_alvo = self._otif_do_dia(dia)
            n_osa, n_itj = volume_do_dia(dia)
            for cd_codigo, n in (("OSA", n_osa), ("ITJ", n_itj)):
                cd = self.cds[cd_codigo]
                clientes_cd = self.clientes[cd_codigo]

                # Distribuição garantida (≥1 remessa por cliente/dia) em vez de
                # sorteio uniforme puro — ver distribuir_clientes().
                cliente_por_id = {c.id: c for c in clientes_cd}
                pool_do_dia = []
                for cliente_id, qtd in distribuir_clientes(clientes_cd, n):
                    pool_do_dia.extend([cliente_por_id[cliente_id]] * qtd)
                random.shuffle(pool_do_dia)

                for cliente in pool_do_dia:
                    numero = self._numero_remessa(cd_codigo, dia)

                    dias_passados = (hoje - dia).days
                    p_sem_nf = 0.12 if dias_passados == 0 else (0.04 if dias_passados <= 2 else 0.01)
                    nf_emitida = random.random() > p_sem_nf
                    # status "final" (aged) — só é gravado no banco depois que a
                    # remessa passar pelo Montador de verdade (ver bloco de
                    # "Planos + Ondas" abaixo). No insert, toda remessa nasce
                    # 'novo', como no fluxo real de ingestão.
                    status_final = distribuicao_status(dia, hoje, otif_alvo) if nf_emitida else "novo"

                    is_ata = bool(cliente.contrato_ata) and random.random() < 0.25
                    prazo_empenho = (dia + timedelta(days=random.randint(5, 60))) if is_ata else None

                    volume_m3 = round(random.uniform(0.3, 4.5), 3)
                    peso_kg   = round(random.uniform(15, 220), 2)
                    valor_nf  = round(random.uniform(3000, 50000), 2)

                    remessas_buf.append({
                        "numero_remessa": numero,
                        "origem":         "sap" if cd_codigo == "OSA" else "ups",
                        "cd_id":          cd.id,
                        "cliente_id":     cliente.id,
                        "data_extracao":  dia,
                        "data_upload":    datetime.combine(dia, hora_aleatoria(6, 8)),
                        "upload_id":      upload_id_map[(dia, cd_codigo)],
                        "volume_m3":      volume_m3,
                        "peso_kg":        peso_kg,
                        "valor_nf":       valor_nf,
                        "qtd_volumes":    random.randint(1, 8),
                        "status":         "novo",
                        "tipo_entrega":   "fracionado",
                        "prioridade":     "urgente" if is_ata else "normal",
                        "is_ata":         is_ata,
                        "numero_empenho": f"EMP-{self._prox_id():06d}" if is_ata else None,
                        "prazo_empenho":  prazo_empenho,
                        "nf_emitida":     nf_emitida,
                        "numero_nf":      f"NF-{self._prox_id():06d}" if nf_emitida else None,
                        "janela_inicio":  cliente.janela_inicio if not cliente.janela_flexivel else None,
                        "janela_fim":     cliente.janela_fim if not cliente.janela_flexivel else None,
                        "janela_critica": random.random() < 0.05,
                        "criado_em":      datetime.combine(dia, hora_aleatoria(6, 9)),
                    })
                    remessas_keys.append(numero)
                    remessas_meta[numero] = {
                        "dia": dia, "cd": cd_codigo, "status": status_final,
                        "nf_emitida": nf_emitida, "cliente_id": cliente.id,
                        "is_ata": is_ata, "prazo_empenho": prazo_empenho,
                        "volume_m3": volume_m3, "peso_kg": peso_kg, "valor_nf": valor_nf,
                    }

        remessa_ids = await self._inserir_returning(Remessa, remessas_buf)
        remessa_id_map = dict(zip(remessas_keys, remessa_ids))
        self.total_remessas += len(remessas_buf)

        # ── Planos + Ondas — via Classificador/Montador reais ───────────────
        # Antes esse bloco fabricava PlanoDia/Onda/OndaRemessa com dados
        # aleatórios, sem nunca chamar os agentes de verdade — o que deixava
        # remessas "novo"/"planejado" que nunca passaram pelo pipeline real
        # (ver /api/admin/reprocessar-historico). Agora cada (dia, cd) roda o
        # Classificador e o Montador de verdade, então a montagem respeita as
        # mesmas regras de negócio da operação real (NF emitida, capacidade
        # de veículo, FTL/fracionado etc.), e futuros seeds já nascem corretos.
        classificador = AgenteClassificador(self.db)
        montador      = AgenteMontador(self.db)

        plano_ids_lote: set[int] = set()
        ondas_lote: list[Onda] = []
        onda_dia_cd: dict[int, tuple] = {}

        for dia in dias:
            for cd_codigo in ("OSA", "ITJ"):
                cd = self.cds[cd_codigo]
                usuario = "timoteo" if cd_codigo == "OSA" else "carlos"

                rel_classif = await classificador.classificar_periodo(cd.id, dia)
                self.total_alertas += rel_classif.get("alertas_criados", 0)

                res_max = await self.db.execute(
                    select(func.max(Onda.numero_onda))
                    .join(PlanoDia, Onda.plano_id == PlanoDia.id)
                    .where(
                        PlanoDia.cd_id == cd.id,
                        PlanoDia.data_plano == dia,
                        PlanoDia.ciclo == 1,
                    )
                )
                numero_onda_antes = res_max.scalar() or 0

                rel_montagem = await montador.montar_plano(cd_codigo, dia, ciclo=1, usuario=usuario)
                plano_id = rel_montagem.get("plano_id")
                if not plano_id:
                    continue
                plano_ids_lote.add(plano_id)

                res_novas = await self.db.execute(
                    select(Onda).where(
                        Onda.plano_id == plano_id,
                        Onda.numero_onda > numero_onda_antes,
                    )
                )
                for onda in res_novas.scalars().all():
                    ondas_lote.append(onda)
                    onda_dia_cd[onda.id] = (dia, cd_codigo)

        self.total_ondas += len(ondas_lote)

        # O período gerado é sempre histórico (dias anteriores a hoje) —
        # fecha os planos e ondas recém-criados para refletir isso (o
        # Montador sempre deixa como "rascunho"/"planejada", que é o estado
        # real logo após a montagem).
        if plano_ids_lote:
            await self.db.execute(
                update(PlanoDia)
                .where(PlanoDia.id.in_(plano_ids_lote))
                .values(status="aprovado")
            )
        if ondas_lote:
            await self.db.execute(
                update(Onda)
                .where(Onda.id.in_([o.id for o in ondas_lote]))
                .values(status="fechada")
            )

        # Envelhece o status das remessas que entraram em onda — o Montador
        # sempre deixa 'planejado' (estado real logo após a montagem); aqui
        # simulamos a passagem do tempo até o status final do dia (calculado
        # antes, em remessas_meta[num]["status"]).
        aging_por_status: dict[str, list[int]] = {}
        for numero, meta in remessas_meta.items():
            if not meta["nf_emitida"] or meta["status"] == "novo":
                continue
            aging_por_status.setdefault(meta["status"], []).append(remessa_id_map[numero])
        for status_final, ids in aging_por_status.items():
            await self.db.execute(
                update(Remessa).where(Remessa.id.in_(ids)).values(status=status_final)
            )

        # Motivo da falha, só para quem parou em "tentativa" (bulk update por PK).
        tentativa_ids = aging_por_status.get("tentativa", [])
        if tentativa_ids:
            await self.db.execute(
                # Remessa.__table__ (Core, não a classe ORM) evita que o SQLAlchemy
                # trate isto como "ORM Bulk UPDATE by Primary Key" — que exige a
                # chave do dict batendo com o nome literal da coluna ("id"), não
                # com o nome do bindparam.
                update(Remessa.__table__).where(Remessa.__table__.c.id == bindparam("_id")),
                [{"_id": rid, "motivo_tentativa": random.choice(MOTIVOS_TENTATIVA)} for rid in tentativa_ids],
            )

        # ── Programações de coleta (simuladas a partir das ondas reais) ─────
        # O Comunicador de verdade não é chamado aqui (enviaria e-mails de
        # verdade); simulamos a confirmação de coleta em cima das ondas reais
        # criadas pelo Montador acima.
        programacoes_buf = []
        eventos_comunicador = []
        for onda in ondas_lote:
            dia, cd_codigo = onda_dia_cd[onda.id]
            transp = self.transportadora_por_id.get(onda.transportadora_id)
            if not transp:
                continue
            enviado_em = datetime.combine(dia, hora_aleatoria(13, 17))
            programacoes_buf.append({
                "onda_id":            onda.id,
                "transportadora_id":  transp.id,
                "canal":              transp.integracao or "email",
                "destinatario_email": transp.email_operacoes,
                "assunto":            f"Programação BD {cd_codigo} — {onda.nome} — {dia:%d/%m/%Y}",
                "corpo":              "(gerado pela infra de teste de stress)",
                "enviado_em":         enviado_em,
                "status_envio":       "confirmado",
                "confirmado_em":      enviado_em + timedelta(hours=random.uniform(1, 4)),
                "protocolo":          f"PROT-{self._prox_id():06d}",
                "criado_em":          enviado_em,
            })
            eventos_comunicador.append(self._evento(
                timestamp=enviado_em + timedelta(hours=random.uniform(0.1, 0.5)),
                tipo_evento="decisao_agente", origem="comunicador",
                ator_nome="Agente Comunicador", cd_id=self.cds[cd_codigo].id,
                transportadora_id=transp.id,
                descricao=f"Programação enviada à transportadora — {onda.nome} — {dia:%d/%m/%Y}",
                resultado="sucesso", dados_extra={"onda_id": onda.id},
            ))
        await self._inserir(ProgramacaoColeta, programacoes_buf)

        # ── Histórico de eventos ─────────────────────────────────────────────
        eventos_buf = []
        for (dia, cd_codigo), upload_id in upload_id_map.items():
            eventos_buf.append(self._evento(
                timestamp=datetime.combine(dia, hora_aleatoria(6, 8)),
                tipo_evento="upload_processado", origem="ingestor",
                ator_nome="Agente Ingestor", cd_id=self.cds[cd_codigo].id,
                descricao=f"Upload processado — CD {cd_codigo} — {dia:%d/%m/%Y}",
                resultado="sucesso",
                dados_extra={"upload_id": upload_id},
            ))

        # Eventos de "onda montada"/classificação já foram gerados pelo
        # próprio Classificador/Montador reais (chamados acima) — só falta
        # simular o envio da programação de coleta pelo Comunicador.
        eventos_buf.extend(eventos_comunicador)

        for numero, meta in remessas_meta.items():
            if not meta["nf_emitida"] or meta["status"] == "novo":
                continue
            base = datetime.combine(meta["dia"], hora_aleatoria(9, 11))
            for etapa in FSM_ATE.get(meta["status"], []):
                eventos_buf.append(self._evento(
                    timestamp=base + FSM_OFFSET[etapa],
                    tipo_evento="mudanca_status", origem="monitor",
                    ator_nome="Agente Monitor",
                    remessa_id=remessa_id_map[numero], cd_id=self.cds[meta["cd"]].id,
                    descricao=f"Remessa {numero} → {etapa}",
                    resultado="sucesso", dados_extra={"status": etapa},
                ))

        # erros injetados: 8% dos dias TIMEOUT_SAP/ARQUIVO_CORROMPIDO, 2% ATA_VENCIDA
        for dia in dias:
            if random.random() < 0.08:
                codigo = random.choice(ERROS_CODIGOS_TIMEOUT)
                cd_codigo = random.choice(["OSA", "ITJ"])
                self.erros[codigo] += 1
                eventos_buf.append(self._evento(
                    timestamp=datetime.combine(dia, hora_aleatoria(6, 9)),
                    tipo_evento="erro_sistema", origem="resolvedor",
                    ator_nome="Agente Resolvedor", cd_id=self.cds[cd_codigo].id,
                    descricao=f"Erro {codigo} durante ingestão — CD {cd_codigo} — {dia:%d/%m/%Y}",
                    resultado="escalado_humano", gravidade="critico",
                    dados_extra={"codigo_erro": codigo, "contexto": {"cd": cd_codigo, "data": dia.isoformat()}},
                ))
            if random.random() < 0.02:
                candidatas = [
                    n for n, m in remessas_meta.items()
                    if m["dia"] == dia and m["is_ata"] and m["prazo_empenho"]
                    and (m["prazo_empenho"] - dia).days <= 3
                ]
                if candidatas:
                    numero = random.choice(candidatas)
                    meta = remessas_meta[numero]
                    self.erros["ATA_VENCIDA"] += 1
                    eventos_buf.append(self._evento(
                        timestamp=datetime.combine(dia, hora_aleatoria(6, 9)),
                        tipo_evento="erro_sistema", origem="resolvedor",
                        ator_nome="Agente Resolvedor",
                        remessa_id=remessa_id_map[numero], cd_id=self.cds[meta["cd"]].id,
                        descricao=f"Erro ATA_VENCIDA — remessa {numero} com empenho vencendo",
                        resultado="escalado_humano", gravidade="critico",
                        dados_extra={"codigo_erro": "ATA_VENCIDA", "contexto": {"remessa": numero}},
                    ))

        await self._inserir(HistoricoEventos, eventos_buf)
        self.total_eventos += len(eventos_buf)

        # ── Alertas ──────────────────────────────────────────────────────────
        alertas_buf = []
        for numero, meta in remessas_meta.items():
            dias_passados = (hoje - meta["dia"]).days
            if meta["status"] in ("tentativa", "devolvido"):
                criado_em = datetime.combine(meta["dia"], hora_aleatoria(15, 18))
                resolvido = dias_passados > 3
                alertas_buf.append({
                    "tipo":         "falha_entrega",
                    "severidade":   "critica" if meta["status"] == "devolvido" else "alta",
                    "titulo":       f"Falha de entrega — {numero}",
                    "descricao":    f"Remessa {numero} com status {meta['status']}.",
                    "remessa_id":   remessa_id_map[numero],
                    "cliente_id":   meta["cliente_id"],
                    "cd_id":        self.cds[meta["cd"]].id,
                    "resolvido":    resolvido,
                    "resolvido_em": criado_em + timedelta(hours=random.uniform(2, 48)) if resolvido else None,
                    "criado_em":    criado_em,
                })
            # Alertas de ATA crítica não são mais fabricados aqui — o
            # Classificador real (chamado acima, no bloco de Planos + Ondas)
            # já gera o alerta tipo="ata_prazo" para remessas ATA dentro do
            # prazo (settings.ALERTA_ATA_DIAS), com a mesma lógica do sistema
            # de produção.
        await self._inserir(Alerta, alertas_buf)
        self.total_alertas += len(alertas_buf)

        await self.db.commit()

    # ── Modo complementar (--clientes-sem-pedidos) ──────────────────────────

    async def processar_lote_complementar(
        self, dia: date, hoje: date, pool_por_cd: dict[str, list[Cliente]]
    ) -> None:
        """
        Gera exatamente 1 remessa por cliente-alvo, num único dia, para
        clientes cadastrados que nunca receberam nenhum pedido — normalmente
        por não terem codigo_sap/codigo_ups e por isso ficarem fora do pool
        de sorteio do processar_lote() normal.

        Reaproveita o mesmo pipeline real (Classificador + Montador) de
        processar_lote(), mas com volume mínimo e controlado — não usa
        volume_do_dia(), que geraria volume desproporcional para pools tão
        pequenos.
        """
        cds_tocados = [cd_codigo for cd_codigo, pool in pool_por_cd.items() if pool]
        if not cds_tocados:
            return

        uploads_buf, uploads_keys = [], []
        for cd_codigo in cds_tocados:
            uploads_keys.append((dia, cd_codigo))
            sistema = "SAP" if cd_codigo == "OSA" else "UPS_WMS"
            uploads_buf.append({
                "cd_id":        self.cds[cd_codigo].id,
                "usuario":      "timoteo" if cd_codigo == "OSA" else "carlos",
                "arquivo_nome": f"Complementar_{sistema}_{cd_codigo}_{dia:%d%m%Y}.xlsx",
                "arquivo_path": f"/stress/complementar_{cd_codigo}_{dia:%Y%m%d}.xlsx",
                "formato":      "xlsx",
                "status":       "concluido",
                "criado_em":    datetime.combine(dia, hora_aleatoria(6, 8)),
            })
        upload_ids = await self._inserir_returning(Upload, uploads_buf)
        upload_id_map = dict(zip(uploads_keys, upload_ids))

        otif_alvo = self._otif_do_dia(dia)

        remessas_buf, remessas_keys = [], []
        remessas_meta = {}
        for cd_codigo in cds_tocados:
            cd   = self.cds[cd_codigo]
            pool = pool_por_cd[cd_codigo]
            cliente_por_id = {c.id: c for c in pool}

            for cliente_id, qtd in distribuir_clientes(pool, len(pool)):
                cliente = cliente_por_id[cliente_id]
                for _ in range(qtd):
                    numero = self._numero_remessa(cd_codigo, dia)

                    dias_passados = (hoje - dia).days
                    p_sem_nf = 0.04 if dias_passados <= 2 else 0.01
                    nf_emitida = random.random() > p_sem_nf
                    status_final = distribuicao_status(dia, hoje, otif_alvo) if nf_emitida else "novo"

                    is_ata = bool(cliente.contrato_ata) and random.random() < 0.25
                    prazo_empenho = (dia + timedelta(days=random.randint(5, 60))) if is_ata else None
                    volume_m3 = round(random.uniform(0.3, 4.5), 3)
                    peso_kg   = round(random.uniform(15, 220), 2)
                    valor_nf  = round(random.uniform(3000, 50000), 2)

                    remessas_buf.append({
                        "numero_remessa": numero,
                        "origem":         "sap" if cd_codigo == "OSA" else "ups",
                        "cd_id":          cd.id,
                        "cliente_id":     cliente.id,
                        "data_extracao":  dia,
                        "data_upload":    datetime.combine(dia, hora_aleatoria(6, 8)),
                        "upload_id":      upload_id_map[(dia, cd_codigo)],
                        "volume_m3":      volume_m3,
                        "peso_kg":        peso_kg,
                        "valor_nf":       valor_nf,
                        "qtd_volumes":    random.randint(1, 8),
                        "status":         "novo",
                        "tipo_entrega":   "fracionado",
                        "prioridade":     "urgente" if is_ata else "normal",
                        "is_ata":         is_ata,
                        "numero_empenho": f"EMP-{self._prox_id():06d}" if is_ata else None,
                        "prazo_empenho":  prazo_empenho,
                        "nf_emitida":     nf_emitida,
                        "numero_nf":      f"NF-{self._prox_id():06d}" if nf_emitida else None,
                        "janela_inicio":  cliente.janela_inicio if not cliente.janela_flexivel else None,
                        "janela_fim":     cliente.janela_fim if not cliente.janela_flexivel else None,
                        "janela_critica": random.random() < 0.05,
                        "criado_em":      datetime.combine(dia, hora_aleatoria(6, 9)),
                    })
                    remessas_keys.append(numero)
                    remessas_meta[numero] = {
                        "dia": dia, "cd": cd_codigo, "status": status_final,
                        "nf_emitida": nf_emitida, "cliente_id": cliente.id,
                        "is_ata": is_ata, "prazo_empenho": prazo_empenho,
                        "volume_m3": volume_m3, "peso_kg": peso_kg, "valor_nf": valor_nf,
                    }

        remessa_ids = await self._inserir_returning(Remessa, remessas_buf)
        remessa_id_map = dict(zip(remessas_keys, remessa_ids))
        self.total_remessas += len(remessas_buf)

        # ── Classificador + Montador reais (mesmo padrão de processar_lote) ─
        classificador = AgenteClassificador(self.db)
        montador      = AgenteMontador(self.db)

        plano_ids_lote: set[int] = set()
        ondas_lote: list[Onda] = []
        onda_dia_cd: dict[int, tuple] = {}

        for cd_codigo in cds_tocados:
            cd = self.cds[cd_codigo]
            usuario = "timoteo" if cd_codigo == "OSA" else "carlos"

            await classificador.classificar_periodo(cd.id, dia)

            res_max = await self.db.execute(
                select(func.max(Onda.numero_onda))
                .join(PlanoDia, Onda.plano_id == PlanoDia.id)
                .where(
                    PlanoDia.cd_id == cd.id,
                    PlanoDia.data_plano == dia,
                    PlanoDia.ciclo == 1,
                )
            )
            numero_onda_antes = res_max.scalar() or 0

            rel_montagem = await montador.montar_plano(cd_codigo, dia, ciclo=1, usuario=usuario)
            plano_id = rel_montagem.get("plano_id")
            if not plano_id:
                continue
            plano_ids_lote.add(plano_id)

            res_novas = await self.db.execute(
                select(Onda).where(
                    Onda.plano_id == plano_id,
                    Onda.numero_onda > numero_onda_antes,
                )
            )
            for onda in res_novas.scalars().all():
                ondas_lote.append(onda)
                onda_dia_cd[onda.id] = (dia, cd_codigo)

        self.total_ondas += len(ondas_lote)

        if plano_ids_lote:
            await self.db.execute(
                update(PlanoDia).where(PlanoDia.id.in_(plano_ids_lote)).values(status="aprovado")
            )
        if ondas_lote:
            await self.db.execute(
                update(Onda).where(Onda.id.in_([o.id for o in ondas_lote])).values(status="fechada")
            )

        aging_por_status: dict[str, list[int]] = {}
        for numero, meta in remessas_meta.items():
            if not meta["nf_emitida"] or meta["status"] == "novo":
                continue
            aging_por_status.setdefault(meta["status"], []).append(remessa_id_map[numero])
        for status_final, ids in aging_por_status.items():
            await self.db.execute(
                update(Remessa).where(Remessa.id.in_(ids)).values(status=status_final)
            )

        tentativa_ids = aging_por_status.get("tentativa", [])
        if tentativa_ids:
            await self.db.execute(
                # Remessa.__table__ (Core, não a classe ORM) evita que o SQLAlchemy
                # trate isto como "ORM Bulk UPDATE by Primary Key" — que exige a
                # chave do dict batendo com o nome literal da coluna ("id"), não
                # com o nome do bindparam.
                update(Remessa.__table__).where(Remessa.__table__.c.id == bindparam("_id")),
                [{"_id": rid, "motivo_tentativa": random.choice(MOTIVOS_TENTATIVA)} for rid in tentativa_ids],
            )

        # ── Programações + eventos mínimos ──────────────────────────────────
        programacoes_buf, eventos_buf = [], []
        for onda in ondas_lote:
            dia_onda, cd_codigo = onda_dia_cd[onda.id]
            transp = self.transportadora_por_id.get(onda.transportadora_id)
            if not transp:
                continue
            enviado_em = datetime.combine(dia_onda, hora_aleatoria(13, 17))
            programacoes_buf.append({
                "onda_id":            onda.id,
                "transportadora_id":  transp.id,
                "canal":              transp.integracao or "email",
                "destinatario_email": transp.email_operacoes,
                "assunto":            f"Programação BD {cd_codigo} — {onda.nome} — {dia_onda:%d/%m/%Y}",
                "corpo":              "(gerado pelo seed complementar de clientes sem pedidos)",
                "enviado_em":         enviado_em,
                "status_envio":       "confirmado",
                "confirmado_em":      enviado_em + timedelta(hours=random.uniform(1, 4)),
                "protocolo":          f"PROT-{self._prox_id():06d}",
                "criado_em":          enviado_em,
            })
            eventos_buf.append(self._evento(
                timestamp=enviado_em + timedelta(hours=random.uniform(0.1, 0.5)),
                tipo_evento="decisao_agente", origem="comunicador",
                ator_nome="Agente Comunicador", cd_id=self.cds[cd_codigo].id,
                transportadora_id=transp.id,
                descricao=f"Programação enviada à transportadora — {onda.nome} — {dia_onda:%d/%m/%Y}",
                resultado="sucesso", dados_extra={"onda_id": onda.id},
            ))
        await self._inserir(ProgramacaoColeta, programacoes_buf)

        for (dia_up, cd_codigo), upload_id in upload_id_map.items():
            eventos_buf.append(self._evento(
                timestamp=datetime.combine(dia_up, hora_aleatoria(6, 8)),
                tipo_evento="upload_processado", origem="ingestor",
                ator_nome="Agente Ingestor", cd_id=self.cds[cd_codigo].id,
                descricao=f"Upload complementar processado — CD {cd_codigo} — {dia_up:%d/%m/%Y}",
                resultado="sucesso", dados_extra={"upload_id": upload_id},
            ))
        for numero, meta in remessas_meta.items():
            if not meta["nf_emitida"] or meta["status"] == "novo":
                continue
            base = datetime.combine(meta["dia"], hora_aleatoria(9, 11))
            for etapa in FSM_ATE.get(meta["status"], []):
                eventos_buf.append(self._evento(
                    timestamp=base + FSM_OFFSET[etapa],
                    tipo_evento="mudanca_status", origem="monitor",
                    ator_nome="Agente Monitor",
                    remessa_id=remessa_id_map[numero], cd_id=self.cds[meta["cd"]].id,
                    descricao=f"Remessa {numero} → {etapa}",
                    resultado="sucesso", dados_extra={"status": etapa},
                ))
        await self._inserir(HistoricoEventos, eventos_buf)
        self.total_eventos += len(eventos_buf)

        await self.db.commit()

    def _otif_do_dia(self, dia: date) -> float:
        otif = otif_mensal(dia.month, dia.year)
        self.otifs_amostrados.append(otif)
        return otif

    def _numero_remessa(self, cd_codigo: str, dia: date) -> str:
        letra = "O" if cd_codigo == "OSA" else "I"
        return f"SX{letra}{dia:%y%m%d}{self._prox_id():05d}{self.run_id}"

    def _evento(self, **kwargs) -> dict:
        base = {
            "ator_tipo":    "agente_ia",
            "resultado":    "sucesso",
            "gravidade":    None,
            "visibilidade": "interno",
            "remessa_id":        None,
            "transportadora_id": None,
            "cd_id":             None,
            "dados_extra":       None,
        }
        base.update(kwargs)
        return base

    @staticmethod
    def _agrupar_por_assinatura_nula(registros: list[dict]) -> list[list[tuple[int, dict]]]:
        """Agrupa linhas por 'assinatura' de quais colunas são None.

        Misturar None e valor na MESMA coluna dentro de um único INSERT
        multi-linha faz o driver (asyncpg, via Supabase Transaction Pooler)
        cair num modo catastroficamente lento — 500 linhas heterogêneas
        chegam a levar 300-400s contra ~1s em lotes homogêneos. Agrupar por
        assinatura de nulidade antes de cada insert resolve, ao custo de
        alguns poucos INSERTs a mais por lote (não linha a linha).
        """
        grupos: dict[tuple, list[tuple[int, dict]]] = {}
        for idx, row in enumerate(registros):
            assinatura = tuple(sorted(k for k, v in row.items() if v is None))
            grupos.setdefault(assinatura, []).append((idx, row))
        return list(grupos.values())

    async def _inserir_returning(self, model, registros: list[dict]) -> list[int]:
        if not registros:
            return []
        ids_por_posicao: dict[int, int] = {}
        for grupo in self._agrupar_por_assinatura_nula(registros):
            indices = [idx for idx, _ in grupo]
            linhas  = [row for _, row in grupo]
            res = await self.db.execute(insert(model).returning(model.id), linhas)
            for idx, row in zip(indices, res.fetchall()):
                ids_por_posicao[idx] = row[0]
        return [ids_por_posicao[i] for i in range(len(registros))]

    async def _inserir(self, model, registros: list[dict]) -> None:
        if not registros:
            return
        for grupo in self._agrupar_por_assinatura_nula(registros):
            linhas = [row for _, row in grupo]
            await self.db.execute(insert(model), linhas)


async def limpar_dados_operacionais(db):
    print("  Limpando dados operacionais (modo=limpo)...")
    for tabela in TABELAS_OPERACIONAIS:
        res = await db.execute(text(f"DELETE FROM {tabela}"))
        print(f"    - {tabela}: {res.rowcount} removido(s)")
    await db.commit()


async def clientes_sem_pedidos(db) -> list[Cliente]:
    """Clientes cadastrados que nunca receberam nenhuma remessa."""
    res_todos = await db.execute(select(Cliente))
    todos = res_todos.scalars().all()
    res_com_pedido = await db.execute(select(Remessa.cliente_id).distinct())
    com_pedido = {cid for (cid,) in res_com_pedido.all() if cid is not None}
    return [c for c in todos if c.id not in com_pedido]


async def gerar_pedido_complementar(db, run_id: str) -> None:
    """
    Modo --clientes-sem-pedidos: gera um pedido mínimo (1 remessa cada) para
    clientes cadastrados que nunca receberam nenhuma remessa. Na prática são
    clientes sem codigo_sap nem codigo_ups, que por isso ficam fora do pool
    de sorteio do processamento normal (GeradorStress.carregar_infra filtra
    por esses campos). Nunca apaga dados existentes.
    """
    gerador = GeradorStress(db, run_id)
    await gerador.carregar_infra()

    alvo = await clientes_sem_pedidos(db)
    if not alvo:
        print("  Nenhum cliente sem pedidos — nada a fazer.")
        return

    print(f"  {len(alvo)} cliente(s) sem nenhum pedido encontrado(s).")

    # Sem codigo_sap/codigo_ups não há como saber o CD "oficial" do cliente —
    # usamos a UF cadastrada como heurística (mesmo critério regional do
    # Montador). Sem UF, assume OSA (os casos observados sem UF são de SP).
    pool_por_cd: dict[str, list[Cliente]] = {"OSA": [], "ITJ": []}
    for c in alvo:
        cd_codigo = "ITJ" if (c.uf or "").upper() in ("SC", "PR", "RS") else "OSA"
        if cd_codigo in gerador.cds:
            pool_por_cd[cd_codigo].append(c)

    for cd_codigo, pool in pool_por_cd.items():
        if pool:
            nomes = ", ".join(c.razao_social for c in pool)
            print(f"    {cd_codigo}: {len(pool)} cliente(s) — {nomes}")

    dia = dias_uteis(1)[0]
    await gerador.processar_lote_complementar(dia, date.today(), pool_por_cd)

    print(
        f"\n  Pedido complementar concluído — {gerador.total_remessas} remessa(s), "
        f"{gerador.total_ondas} onda(s), {gerador.total_alertas} alerta(s), "
        f"data_extracao={dia:%d/%m/%Y}."
    )


async def main():
    parser = argparse.ArgumentParser(description="Gera dados de stress test para o PEO-BD")
    parser.add_argument("--periodo", choices=PERIODOS.keys())
    parser.add_argument("--modo", choices=["limpo", "adicionar"], default="adicionar")
    parser.add_argument(
        "--clientes-sem-pedidos", action="store_true",
        help=(
            "Gera 1 pedido complementar para clientes cadastrados que nunca "
            "receberam nenhuma remessa. Nunca apaga dados (--modo é ignorado)."
        ),
    )
    args = parser.parse_args()

    if not args.clientes_sem_pedidos and not args.periodo:
        parser.error("--periodo é obrigatório (exceto quando --clientes-sem-pedidos é usado)")

    run_id = f"{random.randint(0, 9999):04d}"

    engine_kwargs: dict = {"echo": False}
    if "postgresql" in settings.DATABASE_URL:
        engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    inicio = time_module.perf_counter()

    if args.clientes_sem_pedidos:
        async with SessionLocal() as db:
            await gerar_pedido_complementar(db, run_id)
        await engine.dispose()
        print(f"Tempo total: {time_module.perf_counter() - inicio:.1f}s")
        return

    n_dias = PERIODOS[args.periodo]
    hoje = date.today()
    lista_dias = dias_uteis(n_dias)

    async with SessionLocal() as db:
        if args.modo == "limpo":
            await limpar_dados_operacionais(db)

        gerador = GeradorStress(db, run_id)
        await gerador.carregar_infra()

        print(f"  Gerando {len(lista_dias)} dia(s) útil(eis) — {lista_dias[0]:%d/%m/%Y} a {lista_dias[-1]:%d/%m/%Y}\n")

        for i in range(0, len(lista_dias), CHUNK_DIAS):
            lote = lista_dias[i:i + CHUNK_DIAS]
            await gerador.processar_lote(lote, hoje)
            print(f"    ... {min(i + CHUNK_DIAS, len(lista_dias))}/{len(lista_dias)} dias processados "
                  f"({gerador.total_remessas} remessas, {gerador.total_ondas} ondas, {gerador.total_eventos} eventos)")

    await engine.dispose()

    duracao = time_module.perf_counter() - inicio
    otif_medio = sum(gerador.otifs_amostrados) / len(gerador.otifs_amostrados) if gerador.otifs_amostrados else 0.0
    total_erros = sum(gerador.erros.values())

    print(f"\nPeríodo: {args.periodo} ({len(lista_dias)} dias úteis)")
    print(f"Remessas inseridas: {gerador.total_remessas}")
    print(f"Ondas criadas: {gerador.total_ondas}")
    print(f"Eventos no histórico: {gerador.total_eventos}")
    print(f"Alertas gerados: {gerador.total_alertas}")
    print(
        f"Erros simulados: {total_erros} "
        f"(TIMEOUT_SAP: {gerador.erros['TIMEOUT_SAP']}, "
        f"ATA_VENCIDA: {gerador.erros['ATA_VENCIDA']}, "
        f"ARQUIVO_CORROMPIDO: {gerador.erros['ARQUIVO_CORROMPIDO']})"
    )
    print(f"Tempo total: {duracao:.1f}s")
    print(f"OTIF médio do período: {otif_medio:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
