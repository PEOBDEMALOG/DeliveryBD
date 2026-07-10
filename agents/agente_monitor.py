# peo_bd/agents/agente_monitor.py
# Agente 5 — Monitor de Rastreio Consolidado (Módulo 6)
# Responsabilidade: centraliza status de entregas de DHL, UPS e frota própria
# num único painel, sem que Timóteo ou Carlos precisem acessar portais individuais.
# Restrição da BD: sem API externa — consulta portais via scraping estruturado
# ou recebe updates via e-mail/EDI e os normaliza.

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, exists
from sqlalchemy.orm import selectinload

from core.config import settings
from core.db import AsyncSessionLocal
from core.historico import HistoricoService
from core.models import Alerta, EventoRastreio, Remessa, ProgramacaoColeta, Onda, OndaRemessa

logger = logging.getLogger(__name__)

# ── Status normalizados (mapa de cada transportadora → status interno) ────────

STATUS_MAP_DHL = {
    "shipment picked up":       "coletado",
    "in transit":               "em_transito",
    "out for delivery":         "em_rota_entrega",
    "delivered":                "entregue",
    "delivery attempt failed":  "tentativa",
    "returned to sender":       "devolvido",
    "customs clearance":        "em_transito",
}

STATUS_MAP_UPS = {
    "package picked up":        "coletado",
    "in transit":               "em_transito",
    "out for delivery":         "em_rota_entrega",
    "delivered":                "entregue",
    "delivery attempt":         "tentativa",
    "returned":                 "devolvido",
}

STATUS_MAP_FROTA = {
    "saiu":         "em_rota_entrega",
    "entregue":     "entregue",
    "tentativa":    "tentativa",
    "retornou":     "devolvido",
    "aguardando":   "aguardando",
}

# MOTIVOS GENÉRICOS PARA DEMONSTRAÇÃO — atribuição determinística sem base em
# regra de negócio real, pendente de definição com dado real pós-apresentação
# (13/07). Só se aplica a pendentes com nf_emitida == True (a regra real "Sem
# NF" para nf_emitida == False nunca é sobreposta por isto). Escolha é
# remessa.id % len(MOTIVOS_DEMO_PENDENCIA) — determinístico entre reloads.
# Única fonte de verdade — motivo_pendencia() abaixo é reaproveitada tanto
# por _pendentes_detalhe() (dashboard) quanto por GET /api/remessas
# (api/main.py). O frontend só lê o campo "motivo" já calculado — não tem
# lógica própria de cálculo.
MOTIVOS_DEMO_PENDENCIA = [
    "Peso/cubagem excede capacidade do veículo",
    "Fora da janela de recebimento do destinatário",
    "Divergência de endereço/CEP",
    "Aguardando consolidação de carga fracionada",
    "Bloqueio financeiro do cliente",
    "Pendência de agenda de coleta da transportadora",
]


def motivo_pendencia(remessa_id: int, nf_emitida: bool) -> str:
    """Motivo de bloqueio de uma remessa pendente (nunca planejada em onda).
    "Sem NF" é regra de negócio real; o restante são motivos genéricos de
    demonstração (ver comentário acima de MOTIVOS_DEMO_PENDENCIA)."""
    if not nf_emitida:
        return "Sem NF"
    return MOTIVOS_DEMO_PENDENCIA[remessa_id % len(MOTIVOS_DEMO_PENDENCIA)]


class AgenteMonitor:
    """
    Agente 5 — Monitor de Rastreio Consolidado.

    Como funciona sem API direta:
    - DHL: scraping da página de rastreio pública (código de rastreio por remessa)
    - UPS:  idem
    - Frota própria: motorista registra via formulário web simples (endpoint /rastreio/update)

    O método `processar_update_manual` é chamado quando:
      - Um motorista submete update via formulário
      - Um e-mail de confirmação da transportadora é recebido e parseado
      - Timóteo/Carlos inserem manualmente no painel

    O método `verificar_sla` roda a cada ciclo e gera alertas automáticos
    para remessas em atraso ou sem atualização há X horas.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.historico = HistoricoService(db)

    # ── Update de rastreio ────────────────────────────────────────────────────

    async def processar_update_manual(
        self,
        numero_remessa: str,
        status_raw: str,
        transportadora: str,
        localizacao: str | None = None,
        detalhe: str | None     = None,
        evento_em: datetime | None = None,
        fonte: str = "manual",
    ) -> dict[str, Any]:
        """
        Recebe um update de rastreio (manual, e-mail parseado ou scraping)
        e normaliza para o schema interno.
        """
        remessa = await self._get_remessa(numero_remessa)
        if not remessa:
            raise ValueError(f"Remessa não encontrada: {numero_remessa}")

        status_norm = self._normalizar_status(status_raw, transportadora)

        evento = EventoRastreio(
            remessa_id      = remessa.id,
            transportadora  = transportadora.upper(),
            codigo_rastreio = numero_remessa,
            status          = status_norm,
            localizacao     = localizacao,
            detalhe         = detalhe,
            evento_em       = evento_em or datetime.utcnow(),
            fonte           = fonte,
        )
        self.db.add(evento)

        # Captura status anterior antes de atualizar
        status_anterior = remessa.status

        # Atualiza status da remessa
        remessa.status = status_norm
        if status_norm == "entregue":
            await self._resolver_alertas_remessa(remessa.id)

        # Gera alerta se tentativa de entrega falhou
        if status_norm == "tentativa":
            await self._criar_alerta_tentativa(remessa)

        await self.historico.registrar(
            tipo_evento="mudanca_status",
            origem="monitor",
            ator_tipo="agente_ia",
            ator_nome="Agente Monitor",
            remessa_id=remessa.id,
            cd_id=remessa.cd_id,
            descricao=(
                f"Remessa {numero_remessa} — status atualizado: "
                f"{status_anterior} → {status_norm} "
                f"[{transportadora.upper()}, fonte: {fonte}]"
            ),
            resultado="sucesso",
            gravidade="alerta" if status_norm == "tentativa" else ("info" if status_norm == "entregue" else None),
            dados_extra={
                "status_anterior": status_anterior,
                "status_novo": status_norm,
                "transportadora": transportadora,
                "localizacao": localizacao,
                "fonte": fonte,
            },
        )

        await self.db.commit()

        return {
            "remessa": numero_remessa,
            "status":  status_norm,
            "fonte":   fonte,
        }

    async def processar_email_confirmacao(
        self, corpo_email: str, transportadora: str
    ) -> list[dict]:
        """
        Parseia e-mail de confirmação da transportadora e extrai:
        - Protocolo
        - Veículo confirmado
        - Horário real de coleta
        """
        resultados = []

        # Extrai protocolo (padrão: BD-XXXXXXXX)
        protocolo_match = re.search(r"BD-[A-Z0-9]{8}", corpo_email)
        protocolo = protocolo_match.group(0) if protocolo_match else None

        # Extrai horário (padrão: HH:MM ou HHhMM)
        horario_match = re.search(r"(\d{1,2})[h:](\d{2})", corpo_email)
        horario = None
        if horario_match:
            try:
                horario = datetime.now().replace(
                    hour=int(horario_match.group(1)),
                    minute=int(horario_match.group(2))
                ).time()
            except ValueError:
                pass

        # Extrai placa (padrão brasileiro AAA-0000 ou AAA0A00)
        placa_match = re.search(r"[A-Z]{3}[-]?\d{4}|[A-Z]{3}\d[A-Z]\d{2}", corpo_email)
        placa = placa_match.group(0) if placa_match else None

        if protocolo:
            # Atualiza programação de coleta
            prog_res = await self.db.execute(
                select(ProgramacaoColeta).where(
                    ProgramacaoColeta.protocolo == protocolo
                )
            )
            prog = prog_res.scalar_one_or_none()
            if prog:
                prog.confirmado_em       = datetime.utcnow()
                prog.status_envio        = "confirmado"
                prog.veiculo_confirmado  = placa
                await self.db.commit()
                resultados.append({
                    "protocolo": protocolo,
                    "placa":     placa,
                    "horario":   str(horario) if horario else None,
                    "status":    "confirmado",
                })

        return resultados

    # ── Verificação de SLA ────────────────────────────────────────────────────

    async def verificar_sla(self) -> dict[str, Any]:
        """
        Roda periodicamente (ex: a cada 2h via scheduler).
        Gera alertas para:
        - Remessas em rota sem update há mais de 4h
        - Coletas não confirmadas em mais de SLA da transportadora
        - Remessas com janela de entrega vencida e status != entregue
        """
        alertas_gerados = 0
        agora = datetime.utcnow()

        # 1. Coletas não confirmadas dentro do SLA
        resultado = await self.db.execute(
            select(ProgramacaoColeta)
            .options(
                selectinload(ProgramacaoColeta.transportadora),
                selectinload(ProgramacaoColeta.onda)
                    .selectinload(Onda.plano),
            )
            .where(
                and_(
                    ProgramacaoColeta.status_envio == "enviado",
                    ProgramacaoColeta.confirmado_em.is_(None),
                )
            )
        )
        programacoes = resultado.scalars().all()

        for prog in programacoes:
            if not prog.enviado_em:
                continue
            horas_sem_resposta = (agora - prog.enviado_em).total_seconds() / 3600
            sla_h = prog.transportadora.sla_resposta_h if prog.transportadora else 2

            if horas_sem_resposta > sla_h:
                await self._criar_alerta_raw(
                    tipo       = "coleta_sem_confirmacao",
                    severidade = "alta",
                    titulo     = f"Coleta sem confirmação — {prog.onda.nome if prog.onda else prog.id}",
                    descricao  = (
                        f"Programação enviada há {horas_sem_resposta:.1f}h para "
                        f"{prog.transportadora.nome if prog.transportadora else 'transportadora'} "
                        f"sem confirmação de veículo. SLA: {sla_h}h."
                    ),
                    cd_id      = prog.onda.plano.cd_id if prog.onda and prog.onda.plano else None,
                )
                alertas_gerados += 1

        # 2. Remessas em rota sem update há mais de 4h
        resultado2 = await self.db.execute(
            select(Remessa).where(
                Remessa.status.in_(["em_transito", "em_rota_entrega", "coletado"])
            )
        )
        em_rota = resultado2.scalars().all()

        for remessa in em_rota:
            ultimo_evento = await self._ultimo_evento(remessa.id)
            if ultimo_evento:
                horas = (agora - ultimo_evento.capturado_em).total_seconds() / 3600
                if horas > 4:
                    await self._criar_alerta_remessa(
                        tipo       = "sem_atualizacao_rota",
                        severidade = "media",
                        titulo     = f"Sem atualização há {horas:.0f}h — {remessa.numero_remessa}",
                        descricao  = (
                            f"Remessa {remessa.numero_remessa} está com status "
                            f"'{remessa.status}' mas não tem eventos de rastreio "
                            f"há {horas:.0f}h. Verifique com a transportadora."
                        ),
                        remessa    = remessa,
                    )
                    alertas_gerados += 1

        await self.db.commit()
        return {"alertas_gerados": alertas_gerados, "em_rota": len(em_rota)}

    # ── Dashboard consolidado ─────────────────────────────────────────────────

    async def dashboard(self, cd_id: int | None = None) -> dict[str, Any]:
        """
        Retorna visão consolidada do dia para o painel de Timóteo e Carlos.

        As 4 agregações abaixo são independentes entre si (nenhuma depende do
        resultado de outra) e só leem dados — por isso rodam em sessões próprias,
        concorrentes via asyncio.gather, em vez de usar self.db sequencialmente.
        Contra um Postgres remoto (Supabase), cada round-trip de rede paga a
        mesma latência fixa; paralelizar transforma 4x essa latência em 1x.
        """
        filtro = [True]
        if cd_id:
            filtro.append(Remessa.cd_id == cd_id)

        filtro_alertas = [Alerta.resolvido == False]
        if cd_id:
            filtro_alertas.append(Alerta.cd_id == cd_id)

        # Pendentes: status 'novo' que nunca foi agrupado em nenhuma onda
        # (mesma definição usada em /api/admin/reprocessar-historico).
        tem_onda = exists().where(OndaRemessa.remessa_id == Remessa.id)

        async def _contagem_status() -> dict[str, int]:
            async with AsyncSessionLocal() as s:
                res = await s.execute(
                    select(Remessa.status, func.count(Remessa.id))
                    .where(and_(*filtro))
                    .group_by(Remessa.status)
                )
                return dict(res.all())

        async def _contagem_alertas() -> dict[str, int]:
            async with AsyncSessionLocal() as s:
                res = await s.execute(
                    select(Alerta.severidade, func.count(Alerta.id))
                    .where(and_(*filtro_alertas))
                    .group_by(Alerta.severidade)
                )
                return dict(res.all())

        async def _pendentes_detalhe() -> dict[str, int]:
            # Breakdown do motivo de bloqueio de cada pendente. "sem_nf" e o
            # total de "motivos_demo" vêm de motivo_pendencia() — a mesma
            # função usada por GET /api/remessas, sem duplicar a regra aqui.
            # sem_empenho/aguardando continuam calculados pela regra real de
            # ATA (independente do motivo de demonstração exibido por linha)
            # para não quebrar o contrato numérico já existente do card.
            async with AsyncSessionLocal() as s:
                res = await s.execute(
                    select(Remessa.id, Remessa.nf_emitida, Remessa.is_ata, Remessa.numero_empenho)
                    .where(and_(Remessa.status == "novo", ~tem_onda, *filtro))
                )
                linhas = res.all()

            sem_nf = sem_empenho = aguardando = 0
            motivos_demo = {m: 0 for m in MOTIVOS_DEMO_PENDENCIA}
            for remessa_id, nf_emitida, is_ata, numero_empenho in linhas:
                motivo = motivo_pendencia(remessa_id, nf_emitida)
                if motivo == "Sem NF":
                    sem_nf += 1
                    continue
                motivos_demo[motivo] += 1
                if is_ata and not numero_empenho:
                    sem_empenho += 1
                else:
                    aguardando += 1

            return {
                "sem_nf":       sem_nf,
                "sem_empenho":  sem_empenho,
                "aguardando":   aguardando,
                "motivos_demo": motivos_demo,
            }

        async def _sem_nf() -> int:
            # Sem NF: nf_emitida=False e ainda ativa (mesma definição do
            # Painel de Diagnóstico — só exclui os estados finais).
            async with AsyncSessionLocal() as s:
                res = await s.execute(
                    select(func.count(Remessa.id)).where(
                        and_(
                            Remessa.nf_emitida == False,
                            Remessa.status.not_in(["entregue", "devolvido"]),
                            *filtro,
                        )
                    )
                )
                return res.scalar() or 0

        contagens_raw, alertas_raw, pendentes_detalhe, sem_nf = await asyncio.gather(
            _contagem_status(), _contagem_alertas(), _pendentes_detalhe(), _sem_nf()
        )
        pendentes = pendentes_detalhe["sem_nf"] + pendentes_detalhe["sem_empenho"] + pendentes_detalhe["aguardando"]

        contagens = {
            status: contagens_raw.get(status, 0)
            for status in ["novo", "planejado", "coletado", "em_transito",
                           "em_rota_entrega", "entregue", "tentativa", "devolvido"]
        }
        alertas = {
            sev: alertas_raw.get(sev, 0)
            for sev in ["critica", "alta", "media", "baixa"]
        }

        # OTIF do dia (entregues / (entregues + tentativa + devolvido))
        total_fin = contagens["entregue"] + contagens["tentativa"] + contagens["devolvido"]
        otif = (contagens["entregue"] / total_fin * 100) if total_fin > 0 else 0.0

        return {
            "remessas":      contagens,
            "alertas":       alertas,
            "otif_pct":      round(otif, 1),
            "meta_otif_pct": settings.META_OTIF_PCT * 100,
            "pendentes":          pendentes,
            "pendentes_detalhe":  pendentes_detalhe,
            "sem_nf":             sem_nf,
            "gerado_em":          datetime.utcnow().isoformat(),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _normalizar_status(self, status_raw: str, transportadora: str) -> str:
        raw_lower = status_raw.lower().strip()
        mapa = {
            "DHL":      STATUS_MAP_DHL,
            "UPS":      STATUS_MAP_UPS,
            "FROTA_BD": STATUS_MAP_FROTA,
        }.get(transportadora.upper(), {})

        for chave, valor in mapa.items():
            if chave in raw_lower:
                return valor
        return raw_lower  # retorna o raw se não mapear

    async def _criar_alerta_tentativa(self, remessa: Remessa) -> None:
        await self._criar_alerta_raw(
            tipo       = "tentativa_entrega",
            severidade = "alta",
            titulo     = f"Tentativa de entrega falhou — {remessa.numero_remessa}",
            descricao  = (
                f"A transportadora registrou tentativa sem sucesso para "
                f"{remessa.cliente.razao_social if remessa.cliente else 'N/D'}. "
                f"Acione o cliente e reagende."
            ),
            remessa_id = remessa.id,
            cliente_id = remessa.cliente_id,
            cd_id      = remessa.cd_id,
        )

    async def _criar_alerta_remessa(
        self, tipo: str, severidade: str, titulo: str, descricao: str,
        remessa: Remessa
    ) -> None:
        await self._criar_alerta_raw(
            tipo       = tipo,
            severidade = severidade,
            titulo     = titulo,
            descricao  = descricao,
            remessa_id = remessa.id,
            cliente_id = remessa.cliente_id,
            cd_id      = remessa.cd_id,
        )

    async def _criar_alerta_raw(self, **kwargs) -> None:
        alerta = Alerta(**kwargs)
        self.db.add(alerta)

    async def _resolver_alertas_remessa(self, remessa_id: int) -> None:
        res = await self.db.execute(
            select(Alerta).where(
                and_(
                    Alerta.remessa_id == remessa_id,
                    Alerta.resolvido  == False,
                )
            )
        )
        for alerta in res.scalars():
            alerta.resolvido    = True
            alerta.resolvido_em = datetime.utcnow()

    async def _ultimo_evento(self, remessa_id: int) -> EventoRastreio | None:
        res = await self.db.execute(
            select(EventoRastreio)
            .where(EventoRastreio.remessa_id == remessa_id)
            .order_by(EventoRastreio.capturado_em.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def _get_remessa(self, numero: str) -> Remessa | None:
        res = await self.db.execute(
            select(Remessa)
            .options(selectinload(Remessa.cliente))
            .where(Remessa.numero_remessa == numero)
        )
        return res.scalar_one_or_none()
