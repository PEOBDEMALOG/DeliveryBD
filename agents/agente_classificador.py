# peo_bd/agents/agente_classificador.py
# Agente 2 — Classificador
# Responsabilidade: analisa remessas ingeridas e dispara alertas:
# ATA com prazo crítico, janela de entrega impossível, NF pendente,
# oportunidades de consolidação FTL, clientes sem armazenagem.

import logging
from datetime import date, datetime, time
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

    # ── Entrada principal ──────────────────────────────────────────────────────

    async def classificar_upload(self, upload_id: int) -> dict[str, Any]:
        logger.info(f"[Classificador] Iniciando para upload_id={upload_id}")

        remessas = await self._carregar_remessas(upload_id)
        if not remessas:
            return {"upload_id": upload_id, "remessas": 0, "alertas": 0}

        alertas_criados = 0
        oportunidades   = 0

        for remessa in remessas:
            novos_alertas = await self._classificar_remessa(remessa)
            alertas_criados += novos_alertas

        # Análise de consolidação por região
        oportunidades = await self._analisar_consolidacao(upload_id)

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

    async def _analisar_consolidacao(self, upload_id: int) -> int:
        """
        Identifica grupos de clientes em mesma região comprando
        fracionado mas com volume agregado suficiente para FTL.
        Cria OportunidadeConsolidacao para cada grupo.
        """

        # Agrupa remessas do upload por região
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
                    Remessa.upload_id == upload_id,
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

            oportunidade = OportunidadeConsolidacao(
                cd_id             = row.cd_id,
                regiao            = row.regiao or "não mapeada",
                data_analise      = date.today(),
                qtd_clientes      = row.qtd,
                volume_atual_m3   = round(vol, 2),
                tipo_atual        = "fracionado",
                tipo_possivel     = "ftl",
                economia_estimada = round(economia, 2),
                acao_sugerida     = (
                    f"Consolidar {row.qtd} cliente(s) da região '{row.regiao}' "
                    f"em uma única carga FTL de {vol:.1f}m³. "
                    f"Economia estimada de frete: R$ {economia:,.2f}."
                ),
                status            = "aberta",
            )
            self.db.add(oportunidade)
            oportunidades += 1

        return oportunidades

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _criar_alerta(self, **kwargs) -> None:
        # Evita duplicatas de alertas não resolvidos para mesma remessa+tipo
        remessa_id = kwargs.get("remessa_id")
        tipo       = kwargs.get("tipo")
        if remessa_id and tipo:
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

    async def _carregar_remessas(self, upload_id: int) -> list[Remessa]:
        res = await self.db.execute(
            select(Remessa)
            .options(selectinload(Remessa.cliente))
            .where(Remessa.upload_id == upload_id)
        )
        return list(res.scalars().all())
