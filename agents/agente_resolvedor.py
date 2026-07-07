# peo_bd/agents/agente_resolvedor.py
# Agente 6 — Resolvedor Automático
# Responsabilidade: ao receber um código de erro canônico, consulta a tabela
# erro_acoes para decidir entre ignorar_log, retry_automatico ou escalar_humano.
# Nunca bloqueia o fluxo principal — retorna um dict com "escalado" para que o
# chamador decida se deve continuar ou interromper o pipeline.

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.historico import HistoricoService
from core.models import ErroAcao, HistoricoEventos

logger = logging.getLogger(__name__)


class AgenteResolvedor:
    """
    Agente 6 — Resolvedor Automático.

    Fluxo por ação:
      ignorar_log      → registra acao_resolvedor/info, retorna escalado=False
      retry_automatico → tenta retry_callback até max_tentativas vezes,
                         dorme intervalo_retry_segundos entre tentativas;
                         se resolver: acao_resolvedor/sucesso, escalado=False;
                         se esgotar: erro_sistema/critico → auto-email, escalado=True
      escalar_humano   → erro_sistema/critico → auto-email, escalado=True
      sem regra        → trata como escalar_humano por segurança
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.historico = HistoricoService(db)

    async def tratar_erro(
        self,
        tipo_erro_codigo: str,
        contexto: dict[str, Any],
        retry_callback: Callable[[], Awaitable[Any]] | None = None,
        sleep_override_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Consulta a regra de ação para o tipo de erro e tenta resolver
        automaticamente antes de escalar.

        Parâmetros:
          tipo_erro_codigo      — código canônico (ex: "TIMEOUT_SAP")
          contexto              — dict com metadados do erro (arquivo, cd, etc.)
          retry_callback        — coroutine a reexecutar nos casos retry_automatico
          sleep_override_seconds — substitui intervalo_retry_segundos (útil em testes/demo)

        Retorno:
          {
            "status":             "ignorado" | "resolvido" | "falhou" | "escalado" | "sem_regra",
            "escalado":           bool,
            "tentativa":          int | None,        # presente em "resolvido"
            "resultado_callback": Any | None,        # presente em "resolvido"
          }
        """
        res = await self.db.execute(
            select(ErroAcao).where(
                ErroAcao.tipo_erro_codigo == tipo_erro_codigo,
                ErroAcao.ativo == True,
            )
        )
        regra = res.scalar_one_or_none()

        if not regra:
            logger.warning("[Resolvedor] Sem regra para '%s' — escalando por segurança.", tipo_erro_codigo)
            await self.historico.registrar(
                tipo_evento="erro_sistema",
                origem="resolvedor",
                ator_tipo="agente_ia",
                ator_nome="Agente Resolvedor",
                descricao=(
                    f"Erro {tipo_erro_codigo} sem regra de ação definida — "
                    f"escalado por segurança | contexto: {contexto}"
                ),
                resultado="escalado_humano",
                gravidade="critico",
                dados_extra={"codigo_erro": tipo_erro_codigo, "contexto": contexto},
            )
            return {"status": "sem_regra", "escalado": True, "tentativa": None, "resultado_callback": None}

        # ── ignorar_log ──────────────────────────────────────────────────────────
        if regra.acao == "ignorar_log":
            logger.info("[Resolvedor] '%s' → ignorar_log (sem bloqueio, sem e-mail).", tipo_erro_codigo)
            await self.historico.registrar(
                tipo_evento="acao_resolvedor",
                origem="resolvedor",
                ator_tipo="agente_ia",
                ator_nome="Agente Resolvedor",
                descricao=(
                    f"Erro {tipo_erro_codigo} ignorado por regra — "
                    f"não bloqueia operação | contexto: {contexto}"
                ),
                resultado="acao_automatica",
                gravidade="info",
                dados_extra={"codigo_erro": tipo_erro_codigo, "acao": "ignorar_log", "contexto": contexto},
            )
            return {"status": "ignorado", "escalado": False, "tentativa": None, "resultado_callback": None}

        # ── retry_automatico ─────────────────────────────────────────────────────
        if regra.acao == "retry_automatico" and retry_callback:
            sleep_s = sleep_override_seconds if sleep_override_seconds is not None else regra.intervalo_retry_segundos
            logger.info(
                "[Resolvedor] '%s' → retry_automatico (%d tentativas, %ds entre cada).",
                tipo_erro_codigo, regra.max_tentativas, sleep_s,
            )

            for tentativa in range(1, regra.max_tentativas + 1):
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)
                try:
                    resultado_callback = await retry_callback()
                    logger.info(
                        "[Resolvedor] '%s' resolvido na tentativa %d/%d.",
                        tipo_erro_codigo, tentativa, regra.max_tentativas,
                    )
                    await self.historico.registrar(
                        tipo_evento="acao_resolvedor",
                        origem="resolvedor",
                        ator_tipo="agente_ia",
                        ator_nome="Agente Resolvedor",
                        descricao=(
                            f"Erro {tipo_erro_codigo} resolvido automaticamente "
                            f"na tentativa {tentativa}/{regra.max_tentativas}"
                        ),
                        resultado="sucesso",
                        gravidade="info",
                        dados_extra={
                            "codigo_erro": tipo_erro_codigo,
                            "tentativa": tentativa,
                            "max_tentativas": regra.max_tentativas,
                        },
                    )
                    return {
                        "status": "resolvido",
                        "escalado": False,
                        "tentativa": tentativa,
                        "resultado_callback": resultado_callback,
                    }
                except Exception as exc:
                    logger.warning(
                        "[Resolvedor] '%s' tentativa %d/%d falhou: %s",
                        tipo_erro_codigo, tentativa, regra.max_tentativas, exc,
                    )
                    continue

            # Esgotou todas as tentativas → escalação
            logger.error(
                "[Resolvedor] '%s' não resolvido após %d tentativas — escalando.",
                tipo_erro_codigo, regra.max_tentativas,
            )
            await self.historico.registrar(
                tipo_evento="erro_sistema",
                origem="resolvedor",
                ator_tipo="agente_ia",
                ator_nome="Agente Resolvedor",
                descricao=(
                    f"Erro {tipo_erro_codigo} não resolvido após "
                    f"{regra.max_tentativas} tentativa(s) — escalado para equipe humana"
                ),
                resultado="escalado_humano",
                gravidade="critico",
                dados_extra={
                    "codigo_erro": tipo_erro_codigo,
                    "max_tentativas": regra.max_tentativas,
                    "contexto": contexto,
                },
            )
            return {"status": "falhou", "escalado": True, "tentativa": regra.max_tentativas, "resultado_callback": None}

        # ── escalar_humano (e fallback de retry sem callback) ────────────────────
        motivo = (
            f"requer intervenção humana — escalado imediatamente"
            if regra.acao == "escalar_humano"
            else f"retry_automatico sem callback disponível — escalado"
        )
        logger.warning("[Resolvedor] '%s' → %s.", tipo_erro_codigo, motivo)
        await self.historico.registrar(
            tipo_evento="erro_sistema",
            origem="resolvedor",
            ator_tipo="agente_ia",
            ator_nome="Agente Resolvedor",
            descricao=f"Erro {tipo_erro_codigo} {motivo} | contexto: {contexto}",
            resultado="escalado_humano",
            gravidade="critico",
            dados_extra={
                "codigo_erro": tipo_erro_codigo,
                "acao_regra": regra.acao,
                "contexto": contexto,
            },
        )
        return {"status": "escalado", "escalado": True, "tentativa": None, "resultado_callback": None}

    async def varrer_erros_pendentes(self) -> dict[str, int]:
        """
        Executado periodicamente pelo Vercel Cron (1x/dia — limite de
        frequência do plano Hobby; ver vercel.json).

        Reavalia eventos "erro_sistema" das últimas 2 horas que ainda não
        foram marcados como "resolvido" nem "ignorado", reenviando cada um
        para tratar_erro() — sem loop interno, sem bloquear a função serverless.
        """
        limite = datetime.utcnow() - timedelta(hours=2)
        res = await self.db.execute(
            select(HistoricoEventos).where(
                HistoricoEventos.tipo_evento == "erro_sistema",
                HistoricoEventos.resultado.notin_(["resolvido", "ignorado"]),
                HistoricoEventos.timestamp >= limite,
            )
        )
        eventos = res.scalars().all()

        varridos = 0
        resolvidos = 0
        escalados = 0

        for evento in eventos:
            varridos += 1
            dados_extra = evento.dados_extra or {}
            tipo_erro_codigo = dados_extra.get("codigo_erro")
            contexto = dados_extra.get("contexto", {})
            if not tipo_erro_codigo:
                continue

            resultado = await self.tratar_erro(tipo_erro_codigo, contexto)
            if resultado["status"] == "resolvido":
                resolvidos += 1
            elif resultado["escalado"]:
                escalados += 1

        logger.info(
            "[Resolvedor] Varredura periódica: %d varrido(s), %d resolvido(s), %d escalado(s).",
            varridos, resolvidos, escalados,
        )
        return {"varridos": varridos, "resolvidos": resolvidos, "escalados": escalados}
