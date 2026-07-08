# peo_bd/agents/agente_classificador.py
# Agente 2 — Classificador
# Responsabilidade: analisa remessas ingeridas e dispara alertas:
# ATA com prazo crítico, janela de entrega impossível, NF pendente,
# oportunidades de consolidação FTL, clientes sem armazenagem.

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload

from core.config import settings
from core.historico import HistoricoService
from core.models import (
    Alerta, CentroDistribuicao, Cliente, OportunidadeConsolidacao,
    Remessa, Upload
)

logger = logging.getLogger(__name__)


class AgenteClassificador:
    """
    Agente 2 — Classificador.

    Fluxo:
      1. Recebe upload_id (após Agente 1 concluir)
      2. Carrega remessas do upload
      3. Aplica todas as regras de classificação e alerta
      4. Persiste alertas no banco
      5. Detecta oportunidades de consolidação FTL
      6. Retorna resumo de classificação
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.historico = HistoricoService(db)
        # Cache de (remessa_id, tipo) de alertas não resolvidos já existentes,
        # recarregado a cada classificar_upload/classificar_periodo — evita
        # 1 SELECT por regra por remessa (N+1 caro sob o Supabase Transaction
        # Pooler, onde cada round-trip pesa bem mais que localmente).
        self._alertas_existentes: set[tuple[int, str]] | None = None

    # ── Entrada principal ──────────────────────────────────────────────────────

    async def classificar_upload(self, upload_id: int) -> dict[str, Any]:
        logger.info(f"[Classificador] Iniciando para upload_id={upload_id}")

        remessas = await self._carregar_remessas(upload_id)
        if not remessas:
            return {"upload_id": upload_id, "remessas": 0, "alertas": 0}

        await self._carregar_cache_alertas([r.id for r in remessas])

        alertas_criados = 0
        oportunidades   = 0

        for remessa in remessas:
            novos_alertas = await self._classificar_remessa(remessa)
            alertas_criados += novos_alertas

        # Análise de consolidação por região
        oportunidades = await self._analisar_consolidacao(Remessa.upload_id == upload_id)

        criticas = sum(1 for r in remessas if r.prioridade == "critica")
        altas    = sum(1 for r in remessas if r.prioridade == "alta")
        atas     = sum(1 for r in remessas if r.is_ata)

        await self.historico.registrar(
            tipo_evento="decisao_agente",
            origem="classificador",
            ator_tipo="agente_ia",
            ator_nome="Agente Classificador",
            descricao=(
                f"Upload {upload_id} classificado — {len(remessas)} remessas: "
                f"{criticas} críticas, {altas} altas, {atas} ATAs — "
                f"{alertas_criados} alertas gerados, {oportunidades} oportunidades FTL"
            ),
            resultado="sucesso",
            gravidade="critico" if criticas > 0 else ("alerta" if altas > 0 else None),
            dados_extra={
                "upload_id": upload_id,
                "remessas": len(remessas),
                "alertas_criados": alertas_criados,
                "oportunidades": oportunidades,
                "criticas": criticas,
                "altas": altas,
                "atas": atas,
            },
        )

        await self.db.commit()

        resumo = {
            "upload_id":        upload_id,
            "remessas":         len(remessas),
            "alertas_criados":  alertas_criados,
            "oportunidades":    oportunidades,
            "criticas":         criticas,
            "altas":            altas,
            "atas":             atas,
        }

        logger.info(f"[Classificador] {alertas_criados} alertas, {oportunidades} oportunidades")
        return resumo

    async def classificar_periodo(self, cd_id: int, data: date) -> dict[str, Any]:
        """
        Mesmo fluxo de classificar_upload(), mas para remessas identificadas
        por (cd_id, data_extracao) em vez de upload_id — usado para
        reprocessar remessas que entraram no banco sem passar por um upload
        real (ex.: seed de teste de stress, correções de histórico).
        """
        logger.info(f"[Classificador] Iniciando para cd_id={cd_id} data={data}")

        remessas = await self._carregar_remessas_periodo(cd_id, data)
        if not remessas:
            return {"cd_id": cd_id, "data": str(data), "remessas": 0, "alertas_criados": 0}

        await self._carregar_cache_alertas([r.id for r in remessas])

        alertas_criados = 0
        for remessa in remessas:
            alertas_criados += await self._classificar_remessa(remessa)

        oportunidades = await self._analisar_consolidacao(
            and_(Remessa.cd_id == cd_id, Remessa.data_extracao == data)
        )

        criticas = sum(1 for r in remessas if r.prioridade == "critica")
        altas    = sum(1 for r in remessas if r.prioridade == "alta")
        atas     = sum(1 for r in remessas if r.is_ata)

        await self.historico.registrar(
            tipo_evento="decisao_agente",
            origem="classificador",
            ator_tipo="agente_ia",
            ator_nome="Agente Classificador",
            cd_id=cd_id,
            descricao=(
                f"Reprocessamento {data} classificado — {len(remessas)} remessas: "
                f"{criticas} críticas, {altas} altas, {atas} ATAs — "
                f"{alertas_criados} alertas gerados, {oportunidades} oportunidades FTL"
            ),
            resultado="sucesso",
            gravidade="critico" if criticas > 0 else ("alerta" if altas > 0 else None),
            dados_extra={
                "cd_id": cd_id,
                "data": str(data),
                "remessas": len(remessas),
                "alertas_criados": alertas_criados,
                "oportunidades": oportunidades,
                "criticas": criticas,
                "altas": altas,
                "atas": atas,
            },
        )

        await self.db.commit()

        resumo = {
            "cd_id":            cd_id,
            "data":             str(data),
            "remessas":         len(remessas),
            "alertas_criados":  alertas_criados,
            "oportunidades":    oportunidades,
            "criticas":         criticas,
            "altas":            altas,
            "atas":             atas,
        }
        logger.info(f"[Classificador] {alertas_criados} alertas, {oportunidades} oportunidades")
        return resumo

    # ── Classificação por remessa ─────────────────────────────────────────────

    async def _classificar_remessa(self, remessa: Remessa) -> int:
        alertas = 0
        regras = [
            self._checar_ata_prazo_critico,
            self._checar_nf_pendente_d1,
            self._checar_janela_impossivel,
            self._checar_hospital_sem_armazenagem,
        ]
        for regra in regras:
            criou = await regra(remessa)
            alertas += 1 if criou else 0
        return alertas

    async def _checar_ata_prazo_critico(self, r: Remessa) -> bool:
        if not r.is_ata or not r.prazo_empenho:
            return False
        dias = r.dias_restantes
        if dias is None or dias > settings.ALERTA_ATA_DIAS:
            return False

        severidade = "critica" if dias <= 2 else "alta"
        await self._criar_alerta(
            tipo        = "ata_prazo",
            severidade  = severidade,
            titulo      = f"ATA com prazo crítico — {dias} dia(s) restantes",
            descricao   = (
                f"Remessa {r.numero_remessa} ({r.cliente.razao_social if r.cliente else 'N/D'}) "
                f"é um empenho de ATA com vencimento em {r.prazo_empenho.strftime('%d/%m/%Y')}. "
                f"Restam {dias} dia(s). SLA legal exige entrega em até {settings.ALERTA_ATA_DIAS} dias."
            ),
            remessa_id  = r.id,
            cliente_id  = r.cliente_id,
            cd_id       = r.cd_id,
        )
        await self.historico.registrar(
            tipo_evento="decisao_agente",
            origem="classificador",
            ator_tipo="agente_ia",
            ator_nome="Agente Classificador",
            remessa_id=r.id,
            cd_id=r.cd_id,
            descricao=(
                f"Remessa {r.numero_remessa} — ATA vencendo em {dias} dia(s) "
                f"({r.prazo_empenho.strftime('%d/%m/%Y')}) — marcada como {severidade.upper()}"
            ),
            resultado="alerta_gerado",
            gravidade="critico" if severidade == "critica" else "alerta",
        )
        return True

    async def _checar_nf_pendente_d1(self, r: Remessa) -> bool:
        """NF pendente em remessa D+1 ou ATA crítica — bloqueia despacho."""
        if r.nf_emitida:
            return False
        if not r.is_ata and (r.dias_restantes is None or r.dias_restantes > 1):
            return False

        await self._criar_alerta(
            tipo        = "nf_pendente",
            severidade  = "critica",
            titulo      = f"NF pendente — despacho bloqueado ({r.numero_remessa})",
            descricao   = (
                f"Remessa {r.numero_remessa} para {r.cliente.razao_social if r.cliente else 'N/D'} "
                f"está sem NF emitida mas possui prazo crítico. O despacho não pode ser feito "
                f"sem a NF — acione Osasco para priorizar o faturamento no SAP."
            ),
            remessa_id  = r.id,
            cliente_id  = r.cliente_id,
            cd_id       = r.cd_id,
        )
        return True

    async def _checar_janela_impossivel(self, r: Remessa) -> bool:
        """Janela de entrega no passado ou em menos de 1h."""
        if not r.janela_fim:
            return False
        agora = datetime.now().time()
        # Janela já passou hoje
        if r.janela_fim < agora:
            await self._criar_alerta(
                tipo        = "janela_vencida",
                severidade  = "alta",
                titulo      = f"Janela vencida — {r.numero_remessa}",
                descricao   = (
                    f"A janela de entrega do cliente {r.cliente.razao_social if r.cliente else 'N/D'} "
                    f"era até {r.janela_fim.strftime('%Hh%M')} e já passou. "
                    f"Reagende ou acione o cliente."
                ),
                remessa_id  = r.id,
                cliente_id  = r.cliente_id,
                cd_id       = r.cd_id,
            )
            return True
        return False

    async def _checar_hospital_sem_armazenagem(self, r: Remessa) -> bool:
        """
        Hospital sem armazenagem própria consolidado em onda FTL
        — consolidação impossível, precisa ser fracionado.
        """
        if not r.cliente:
            return False
        if r.cliente.tipo != "hospital":
            return False
        if r.cliente.tem_armazenagem:
            return False
        if r.tipo_entrega == "fracionado":
            return False  # já correto

        await self._criar_alerta(
            tipo        = "hospital_sem_armazenagem",
            severidade  = "media",
            titulo      = f"Hospital sem armazenagem — consolidação inviável ({r.numero_remessa})",
            descricao   = (
                f"{r.cliente.razao_social} não possui área de armazenagem própria. "
                f"A remessa precisa ir fracionada — não pode ser consolidada em FTL. "
                f"Ajuste a onda antes de fechar o planejamento."
            ),
            remessa_id  = r.id,
            cliente_id  = r.cliente_id,
            cd_id       = r.cd_id,
        )
        return True

    # ── Análise de consolidação FTL ───────────────────────────────────────────

    async def _analisar_consolidacao(self, filtro_remessas) -> int:
        """
        Identifica grupos de clientes em mesma região comprando
        fracionado mas com volume agregado suficiente para FTL.
        Cria OportunidadeConsolidacao para cada grupo.

        `filtro_remessas` é a condição SQLAlchemy que escopa quais remessas
        entram na análise (por upload_id, ou por cd_id+data_extracao).
        """

        # Agrupa remessas do escopo por região
        resultado = await self.db.execute(
            select(
                Cliente.regiao,
                Remessa.cd_id,
                func.count(Remessa.id).label("qtd"),
                func.sum(Remessa.volume_m3).label("vol_total"),
                func.sum(Remessa.valor_nf).label("val_total"),
            )
            .join(Cliente, Remessa.cliente_id == Cliente.id)
            .where(
                and_(
                    filtro_remessas,
                    Remessa.tipo_entrega == "fracionado",
                    Cliente.tem_armazenagem == True,   # só consolida quem aceita armazenagem
                )
            )
            .group_by(Cliente.regiao, Remessa.cd_id)
        )

        rows = resultado.all()
        oportunidades = 0

        for row in rows:
            if not row.vol_total:
                continue
            vol = float(row.vol_total)

            if vol < settings.LIMIAR_FTL_M3:
                continue  # volume insuficiente para FTL

            # Estima economia: frete FTL ~40% mais barato que fracionado
            economia = float(row.val_total or 0) * 0.40 * 0.10  # 10% do valor da NF em frete
            regiao = row.regiao or "não mapeada"
            acao_sugerida = (
                f"Consolidar {row.qtd} cliente(s) da região '{regiao}' "
                f"em uma única carga FTL de {vol:.1f}m³. "
                f"Economia estimada de frete: R$ {economia:,.2f}."
            )

            # Evita duplicar a mesma oportunidade (cd+região) a cada
            # chamada — atualiza os números de uma já aberta em vez de
            # criar outra linha. Sem isso, rodar a classificação repetida
            # (um upload por dia, ou a varredura de 30 dias) empilha
            # dezenas de linhas redundantes para a mesma região. Podem já
            # existir várias duplicatas de antes desse dedup existir — usa
            # a mais recente e fecha o resto como "descartada".
            existente = await self.db.execute(
                select(OportunidadeConsolidacao)
                .where(
                    and_(
                        OportunidadeConsolidacao.cd_id  == row.cd_id,
                        OportunidadeConsolidacao.regiao == regiao,
                        OportunidadeConsolidacao.status == "aberta",
                    )
                )
                .order_by(OportunidadeConsolidacao.criado_em.desc())
            )
            duplicatas = existente.scalars().all()
            oportunidade = duplicatas[0] if duplicatas else None
            for extra in duplicatas[1:]:
                extra.status = "descartada"

            if oportunidade:
                oportunidade.data_analise      = date.today()
                oportunidade.qtd_clientes      = row.qtd
                oportunidade.volume_atual_m3   = round(vol, 2)
                oportunidade.economia_estimada = round(economia, 2)
                oportunidade.acao_sugerida     = acao_sugerida
            else:
                oportunidade = OportunidadeConsolidacao(
                    cd_id             = row.cd_id,
                    regiao            = regiao,
                    data_analise      = date.today(),
                    qtd_clientes      = row.qtd,
                    volume_atual_m3   = round(vol, 2),
                    tipo_atual        = "fracionado",
                    tipo_possivel     = "ftl",
                    economia_estimada = round(economia, 2),
                    acao_sugerida     = acao_sugerida,
                    status            = "aberta",
                )
                self.db.add(oportunidade)

            oportunidades += 1

        return oportunidades

    async def analisar_oportunidades_periodo(self, dias: int = 30) -> dict[str, Any]:
        """
        Varredura sob demanda de oportunidades de consolidação FTL — analisa
        as remessas recentes (últimos `dias` dias, não só o upload do dia)
        agrupando por região/rota. Complementa a análise em tempo real de
        classificar_upload()/classificar_periodo() (que só olha o upload ou
        dia corrente) com uma visão agregada do histórico recente, onde
        volume de FTL costuma se acumular ao longo de vários dias.
        """
        data_inicio = date.today() - timedelta(days=dias)
        logger.info(f"[Classificador] Varredura de oportunidades — últimos {dias} dia(s)")

        oportunidades = await self._analisar_consolidacao(
            and_(
                Remessa.data_extracao >= data_inicio,
                Remessa.status.in_(["novo", "planejado", "coletado"]),
            )
        )

        await self.historico.registrar(
            tipo_evento="decisao_agente",
            origem="classificador",
            ator_tipo="agente_ia",
            ator_nome="Agente Classificador",
            descricao=(
                f"Varredura de oportunidades FTL — últimos {dias} dias: "
                f"{oportunidades} oportunidade(s) identificada(s)"
            ),
            resultado="sucesso",
            dados_extra={"dias": dias, "oportunidades": oportunidades},
        )
        await self.db.commit()

        logger.info(f"[Classificador] {oportunidades} oportunidade(s) de consolidação FTL")
        return {"dias": dias, "oportunidades": oportunidades}

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _criar_alerta(self, **kwargs) -> None:
        # Evita duplicatas de alertas não resolvidos para mesma remessa+tipo.
        # Usa o cache pré-carregado (_carregar_cache_alertas) quando
        # disponível — só cai para a query individual se chamado fora do
        # fluxo normal (cache não inicializado).
        remessa_id = kwargs.get("remessa_id")
        tipo       = kwargs.get("tipo")
        if remessa_id and tipo:
            if self._alertas_existentes is not None:
                if (remessa_id, tipo) in self._alertas_existentes:
                    return  # alerta já existe
            else:
                existente = await self.db.execute(
                    select(Alerta).where(
                        and_(
                            Alerta.remessa_id == remessa_id,
                            Alerta.tipo       == tipo,
                            Alerta.resolvido  == False,
                        )
                    )
                )
                if existente.scalar_one_or_none():
                    return  # alerta já existe

        alerta = Alerta(**kwargs)
        self.db.add(alerta)
        if remessa_id and tipo and self._alertas_existentes is not None:
            self._alertas_existentes.add((remessa_id, tipo))

    async def _carregar_cache_alertas(self, remessa_ids: list[int]) -> None:
        if not remessa_ids:
            self._alertas_existentes = set()
            return
        res = await self.db.execute(
            select(Alerta.remessa_id, Alerta.tipo).where(
                and_(Alerta.remessa_id.in_(remessa_ids), Alerta.resolvido == False)
            )
        )
        self._alertas_existentes = {(rid, tipo) for rid, tipo in res.all()}

    async def _carregar_remessas(self, upload_id: int) -> list[Remessa]:
        res = await self.db.execute(
            select(Remessa)
            .options(selectinload(Remessa.cliente))
            .where(Remessa.upload_id == upload_id)
        )
        return list(res.scalars().all())

    async def _carregar_remessas_periodo(self, cd_id: int, data: date) -> list[Remessa]:
        res = await self.db.execute(
            select(Remessa)
            .options(selectinload(Remessa.cliente))
            .where(and_(Remessa.cd_id == cd_id, Remessa.data_extracao == data))
        )
        return list(res.scalars().all())
