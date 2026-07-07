# peo_bd/agents/agente_montador.py
# Agente 3 — Montador de Ondas
# Responsabilidade: a partir das remessas classificadas, monta automaticamente
# as ondas de separação respeitando: janelas de entrega, capacidade de veículo,
# regra FTL/fracionado, prioridade ATA, restrição de armazenagem do cliente.

import logging
from datetime import date, time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload, joinedload

from core.config import settings
from core.historico import HistoricoService
from core.models import (
    CentroDistribuicao, Onda, OndaRemessa, PlanoDia,
    Remessa, Transportadora, TabelaPrecoTransportadora, Veiculo
)

logger = logging.getLogger(__name__)

# ── Mapeamento UF → capital (para classificar clientes como Capital/Interior) ──
CAPITAL_POR_UF: dict[str, str] = {
    "AC": "rio branco",  "AL": "maceio",      "AP": "macapa",
    "AM": "manaus",      "BA": "salvador",     "CE": "fortaleza",
    "DF": "brasilia",    "ES": "vitoria",      "GO": "goiania",
    "MA": "sao luis",    "MT": "cuiaba",       "MS": "campo grande",
    "MG": "belo horizonte", "PA": "belem",     "PB": "joao pessoa",
    "PR": "curitiba",    "PE": "recife",       "PI": "teresina",
    "RJ": "rio de janeiro", "RN": "natal",     "RS": "porto alegre",
    "RO": "porto velho", "RR": "boa vista",    "SC": "florianopolis",
    "SP": "sao paulo",   "SE": "aracaju",      "TO": "palmas",
}

def _classificar_cliente(cidade: str | None, uf: str | None) -> str:
    """Retorna 'Capital' se a cidade for capital do estado, senão 'Interior'."""
    if not uf or not cidade:
        return "Interior"
    capital = CAPITAL_POR_UF.get(uf.upper(), "")
    cidade_norm = (cidade.lower()
                   .replace("á","a").replace("é","e").replace("ê","e")
                   .replace("í","i").replace("ó","o").replace("ô","o")
                   .replace("ú","u").replace("ã","a").replace("ç","c"))
    return "Capital" if capital and capital in cidade_norm else "Interior"


# ── Regras de agrupamento por região ─────────────────────────────────────────
# Fallback usado quando não há tabela de preços cadastrada no banco.

REGRAS_REGIAO = {
    "capital_sp": {
        "transportadora": "DHL",
        "tipo_veiculo_preferido": ["van", "vuc_eletrico"],
        "tipo_entrega": "fracionado",   # hospitais SP = sem armazenagem
    },
    "interior_sp": {
        "transportadora": "DHL",
        "tipo_veiculo_preferido": ["truck", "vuc_combustao"],
        "tipo_entrega": "misto",
    },
    "sul": {
        "transportadora": "UPS",
        "tipo_veiculo_preferido": ["truck"],
        "tipo_entrega": "misto",
    },
    "default": {
        "transportadora": "DHL",
        "tipo_veiculo_preferido": ["truck"],
        "tipo_entrega": "fracionado",
    }
}


class AgenteMontador:
    """
    Agente 3 — Montador de Ondas.

    Algoritmo de montagem:
      1. Carrega remessas prontas (status='novo', sem onda) do CD e data
      2. Ordena por prioridade: critica > alta > normal
      3. Ordena secundária: janela_inicio ASC (entregas mais cedo primeiro)
      4. Agrupa por região
      5. Para cada grupo: decide FTL ou fracionado
      6. Aloca veículo disponível respeitando capacidade
      7. Se volume > capacidade do maior veículo: quebra em múltiplas ondas
      8. Persiste ondas e associações onda_remessas
    """

    ORDEM_PRIORIDADE = {"critica": 0, "alta": 1, "normal": 2}

    def __init__(self, db: AsyncSession):
        self.db = db
        self.historico = HistoricoService(db)

    # ── Entrada principal ──────────────────────────────────────────────────────

    async def montar_plano(
        self,
        cd_codigo: str,
        data_plano: date | None = None,
        ciclo: int = 1,
        usuario: str = "sistema",
    ) -> dict[str, Any]:

        if not data_plano:
            data_plano = date.today()

        logger.info(f"[Montador] Montando plano {cd_codigo} / {data_plano} / ciclo {ciclo}")

        cd = await self._get_cd(cd_codigo)
        if not cd:
            raise ValueError(f"CD não encontrado: {cd_codigo}")

        # Reaproveita plano do dia se já existir (idempotente para múltiplos uploads)
        res_existing = await self.db.execute(
            select(PlanoDia).where(
                and_(
                    PlanoDia.cd_id      == cd.id,
                    PlanoDia.data_plano == data_plano,
                    PlanoDia.ciclo      == ciclo,
                )
            )
        )
        plano = res_existing.scalar_one_or_none()
        plano_reaproveitado = plano is not None
        if not plano:
            plano = PlanoDia(
                cd_id      = cd.id,
                data_plano = data_plano,
                ciclo      = ciclo,
                criado_por = usuario,
                status     = "rascunho",
            )
            self.db.add(plano)
            await self.db.flush()

        await self.historico.registrar(
            tipo_evento="decisao_agente",
            origem="montador",
            ator_tipo="agente_ia",
            ator_nome="Agente Montador",
            cd_id=cd.id,
            descricao=(
                f"Plano {cd_codigo} {data_plano} ciclo {ciclo} "
                + ("reaproveitado" if plano_reaproveitado else "criado")
            ),
            resultado="sucesso",
            dados_extra={"plano_id": plano.id, "ciclo": ciclo, "reaproveitado": plano_reaproveitado},
        )

        # Carrega remessas disponíveis (ainda não planejadas)
        remessas = await self._carregar_remessas_disponiveis(cd.id, data_plano)
        if not remessas:
            logger.info("[Montador] Nenhuma remessa disponível para planejar")
            return {"plano_id": plano.id, "ondas": 0, "remessas": 0}

        # Ordena por prioridade e janela
        remessas.sort(key=lambda r: (
            self.ORDEM_PRIORIDADE.get(r.prioridade, 2),
            r.janela_inicio or time(23, 59),
        ))

        # Agrupa por região
        grupos = self._agrupar_por_regiao(remessas)

        # Monta ondas por grupo
        veiculos   = await self._carregar_veiculos(cd.id)
        transportadoras = await self._carregar_transportadoras(cd.id)
        # Continua a numeração a partir do maior número já existente neste plano
        res_max = await self.db.execute(
            select(func.max(Onda.numero_onda)).where(Onda.plano_id == plano.id)
        )
        num_onda   = res_max.scalar() or 0
        total_ondas = 0
        ids_planejados = []

        for regiao, grupo in grupos.items():
            regra = REGRAS_REGIAO.get(regiao, REGRAS_REGIAO["default"])
            ondas_grupo = self._dividir_em_ondas(grupo, regra, veiculos)

            for lote in ondas_grupo:
                num_onda += 1
                vol   = sum(float(r.volume_m3 or 0) for r in lote)
                peso  = sum(float(r.peso_kg   or 0) for r in lote)
                valor = sum(float(r.valor_nf  or 0) for r in lote)

                veiculo               = self._selecionar_veiculo(regra, veiculos, vol, peso)
                tipo_entrega, motivo_tipo = self._determinar_tipo(lote, vol, regra)

                transportadora, cotacao = await self._selecionar_melhor_transportadora(
                    regiao, tipo_entrega, peso, valor, transportadoras, lote
                )
                if cotacao["metodo"] == "cotacao_banco":
                    logger.info(
                        f"[Montador] Onda {num_onda} | {regiao} {tipo_entrega} | "
                        f"Escolhida: {transportadora.nome if transportadora else '?'} "
                        f"(R$ {cotacao['custo']:.2f}) | "
                        f"{cotacao['cotacoes_comparadas']} cotacoes comparadas | "
                        f"economia R$ {cotacao['economia_vs_pior']:.2f}"
                    )
                ocupacao       = (vol / float(veiculo.capacidade_m3) * 100) if veiculo else 0
                nome_onda      = self._nome_onda(num_onda, regiao, tipo_entrega)
                horario_coleta = self._horario_coleta(lote)

                # Alerta de subutilização — calculado antes da justificativa para poder
                # ser mencionado nela também.
                subutilizada = bool(veiculo) and ocupacao < (settings.OCUPACAO_MIN_FTL_PCT * 100) and tipo_entrega == "ftl"

                motivo_transp   = self._motivo_transportadora(transportadora, cotacao)
                motivo_veiculo  = self._motivo_veiculo(veiculo, regra, vol, peso)
                justificativa   = self._montar_justificativa(
                    motivo_tipo, motivo_transp, motivo_veiculo,
                    ocupacao, subutilizada,
                )

                onda = Onda(
                    plano_id          = plano.id,
                    numero_onda       = num_onda,
                    nome              = nome_onda,
                    regiao            = regiao,
                    tipo              = tipo_entrega,
                    veiculo_id        = veiculo.id if veiculo else None,
                    transportadora_id = transportadora.id if transportadora else None,
                    volume_total_m3   = round(vol, 3),
                    peso_total_kg     = round(peso, 2),
                    valor_total_nf    = round(valor, 2),
                    ocupacao_pct      = round(ocupacao, 1),
                    horario_coleta    = horario_coleta,
                    status            = "planejada",
                    justificativa     = justificativa,
                )
                self.db.add(onda)
                await self.db.flush()

                # Associa remessas à onda
                for seq, remessa in enumerate(lote, start=1):
                    assoc = OndaRemessa(
                        onda_id    = onda.id,
                        remessa_id = remessa.id,
                        sequencia  = seq,
                    )
                    self.db.add(assoc)
                    # Atualiza status da remessa
                    remessa.status = "planejado"
                    ids_planejados.append(remessa.id)

                if subutilizada:
                    logger.warning(
                        f"[Montador] Onda {num_onda} subutilizada: {ocupacao:.1f}% "
                        f"(veículo {veiculo.tipo} {veiculo.capacidade_m3}m³)"
                    )

                await self.historico.registrar(
                    tipo_evento="decisao_agente",
                    origem="montador",
                    ator_tipo="agente_ia",
                    ator_nome="Agente Montador",
                    cd_id=cd.id,
                    descricao=(
                        f"Onda {num_onda:02d} criada — {nome_onda} — "
                        f"{len(lote)} remessas, {vol:.2f}m³, ocupação {ocupacao:.1f}%"
                        + (f" [SUBUTILIZADA]" if subutilizada else "")
                    ),
                    resultado="sucesso" if not subutilizada else "alerta_gerado",
                    gravidade="alerta" if subutilizada else None,
                    dados_extra={
                        "plano_id": plano.id,
                        "onda_id": onda.id,
                        "numero_onda": num_onda,
                        "regiao": regiao,
                        "tipo_entrega": tipo_entrega,
                        "remessas": len(lote),
                        "volume_m3": round(vol, 2),
                        "ocupacao_pct": round(ocupacao, 1),
                        "transportadora": transportadora.nome if transportadora else None,
                        "justificativa": justificativa,
                    },
                )

                total_ondas += 1

        # Totaliza plano
        ids_set = set(ids_planejados)
        plano.total_remessas  = len(ids_planejados)
        plano.total_volume_m3 = round(sum(float(r.volume_m3 or 0) for r in remessas if r.id in ids_set), 2)
        plano.total_peso_kg   = round(sum(float(r.peso_kg   or 0) for r in remessas if r.id in ids_set), 2)
        plano.total_valor_nf  = round(sum(float(r.valor_nf  or 0) for r in remessas if r.id in ids_set), 2)

        await self.db.commit()

        resultado = {
            "plano_id":         plano.id,
            "cd":               cd_codigo,
            "data":             str(data_plano),
            "ciclo":            ciclo,
            "ondas":            total_ondas,
            "remessas":         len(ids_planejados),
            "volume_total_m3":  plano.total_volume_m3,
            "valor_total_nf":   plano.total_valor_nf,
        }
        logger.info(f"[Montador] Plano montado: {total_ondas} ondas, {len(ids_planejados)} remessas")
        return resultado

    # ── Lógica de montagem ────────────────────────────────────────────────────

    def _agrupar_por_regiao(self, remessas: list[Remessa]) -> dict[str, list[Remessa]]:
        grupos: dict[str, list[Remessa]] = {}
        for r in remessas:
            regiao = (r.cliente.regiao if r.cliente and r.cliente.regiao else "default")
            grupos.setdefault(regiao, []).append(r)
        return grupos

    def _dividir_em_ondas(
        self,
        remessas: list[Remessa],
        regra: dict,
        veiculos: list[Veiculo],
    ) -> list[list[Remessa]]:
        """Divide o grupo em lotes que cabem no maior veículo disponível."""
        cap_max = max(
            (float(v.capacidade_m3) for v in veiculos
             if v.tipo in regra["tipo_veiculo_preferido"]),
            default=30.0,
        )

        ondas, lote_atual, vol_atual = [], [], 0.0
        for r in remessas:
            vol = float(r.volume_m3 or 0)
            if vol_atual + vol > cap_max and lote_atual:
                ondas.append(lote_atual)
                lote_atual, vol_atual = [], 0.0
            lote_atual.append(r)
            vol_atual += vol

        if lote_atual:
            ondas.append(lote_atual)
        return ondas

    def _selecionar_veiculo(
        self,
        regra: dict,
        veiculos: list[Veiculo],
        vol: float,
        peso: float,
    ) -> Veiculo | None:
        preferidos = regra["tipo_veiculo_preferido"]
        candidatos = [
            v for v in veiculos
            if v.tipo in preferidos
            and float(v.capacidade_m3) >= vol
            and float(v.capacidade_kg) >= peso
        ]
        if not candidatos:
            # fallback: qualquer veículo que caiba
            candidatos = [
                v for v in veiculos
                if float(v.capacidade_m3) >= vol
                and float(v.capacidade_kg) >= peso
            ]
        if not candidatos:
            return None
        # Prefere o menor que caiba (minimiza subutilização)
        return min(candidatos, key=lambda v: float(v.capacidade_m3))

    async def _selecionar_melhor_transportadora(
        self,
        regiao:          str,
        tipo_servico:    str,
        peso_kg:         float,
        valor_nf:        float,
        transportadoras: list[Transportadora],
        lote:            "list[Remessa] | None" = None,
    ) -> tuple["Transportadora | None", dict]:
        """
        Consulta tabela_preco_transportadoras por (uf, classificacao) do lote.
        Calcula custo real para cada transportadora e escolhe a mais barata.
        Fallback: regras fixas por região se não houver tabela cadastrada.
        """
        ids_cd = {t.id for t in transportadoras}
        if not ids_cd:
            return None, {"metodo": "sem_transportadoras", "custo": 0,
                          "cotacoes_comparadas": 0, "economia_vs_pior": 0}

        # Determina UF e classificação do lote (usa a maioria dos clientes)
        uf_counts: dict[str, int] = {}
        for r in (lote or []):
            if r.cliente and r.cliente.uf:
                uf_counts[r.cliente.uf] = uf_counts.get(r.cliente.uf, 0) + 1
        uf_lote = max(uf_counts, key=uf_counts.get) if uf_counts else None

        classif_lote = "Interior"
        if lote and uf_lote:
            # Usa a cidade do primeiro cliente com UF majoritário
            for r in lote:
                if r.cliente and r.cliente.uf == uf_lote:
                    classif_lote = _classificar_cliente(r.cliente.cidade, uf_lote)
                    break

        eh_ftl = tipo_servico == "ftl"

        if uf_lote:
            res = await self.db.execute(
                select(TabelaPrecoTransportadora)
                .options(selectinload(TabelaPrecoTransportadora.transportadora))
                .where(
                    TabelaPrecoTransportadora.transportadora_id.in_(ids_cd),
                    TabelaPrecoTransportadora.uf            == uf_lote,
                    TabelaPrecoTransportadora.classificacao == classif_lote,
                    TabelaPrecoTransportadora.cobertura     == True,
                    TabelaPrecoTransportadora.ativo         == True,
                )
            )
            precos = res.scalars().all()
        else:
            precos = []

        if not precos:
            regra  = REGRAS_REGIAO.get(regiao, REGRAS_REGIAO["default"])
            codigo = regra.get("transportadora", "DHL")
            transp = next((t for t in transportadoras if t.codigo == codigo),
                          transportadoras[0] if transportadoras else None)
            return transp, {"metodo": "regra_fixa", "custo": 0,
                            "cotacoes_comparadas": 0, "economia_vs_pior": 0}

        def _custo(p: TabelaPrecoTransportadora) -> float:
            if eh_ftl:
                return float(p.preco_ftl_fixo or 0)
            base = float(p.preco_por_kg or 0) * peso_kg
            enc  = (float(p.ad_valorem_pct or 0) + float(p.gris_pct or 0)) * valor_nf
            return round(max(base + enc, float(p.preco_minimo or 0)), 2)

        def _prazo(p: TabelaPrecoTransportadora) -> int:
            return (p.prazo_ftl_dias if eh_ftl else p.prazo_frac_dias) or 999

        cotacoes = sorted(precos, key=lambda p: (_custo(p), _prazo(p)))
        melhor   = cotacoes[0]
        pior     = cotacoes[-1]

        transp_obj = next(
            (t for t in transportadoras if t.id == melhor.transportadora_id),
            melhor.transportadora,
        )
        return transp_obj, {
            "metodo":              "cotacao_banco",
            "uf":                  uf_lote,
            "classificacao":       classif_lote,
            "custo":               _custo(melhor),
            "prazo_dias":          _prazo(melhor),
            "cotacoes_comparadas": len(cotacoes),
            "economia_vs_pior":    round(_custo(pior) - _custo(melhor), 2),
        }

    def _selecionar_transportadora_regra(
        self, regra: dict, transportadoras: list[Transportadora]
    ) -> "Transportadora | None":
        codigo = regra.get("transportadora", "DHL")
        return next((t for t in transportadoras if t.codigo == codigo),
                    transportadoras[0] if transportadoras else None)

    def _determinar_tipo(
        self, lote: list[Remessa], vol: float, regra: dict
    ) -> tuple[str, str]:
        """Retorna (tipo_entrega, motivo) — o motivo explica a regra que decidiu o tipo."""
        # Hospital sem armazenagem → sempre fracionado
        sem_armazenagem = [
            r.cliente.razao_social for r in lote
            if r.cliente and r.cliente.tipo == "hospital" and not r.cliente.tem_armazenagem
        ]
        if sem_armazenagem:
            exemplo = sem_armazenagem[0]
            sufixo  = f" (ex.: {exemplo})" if len(sem_armazenagem) == 1 else f" ({len(sem_armazenagem)} hospitais)"
            return "fracionado", (
                f"fracionado porque o lote inclui cliente(s) hospitalar(es) sem "
                f"armazenagem própria{sufixo}, que não podem receber consolidação FTL."
            )
        # Volume ≥ limiar FTL → FTL
        if vol >= settings.LIMIAR_FTL_M3:
            return "ftl", (
                f"FTL porque o volume do lote ({vol:.2f} m³) atingiu o limiar de "
                f"consolidação de {settings.LIMIAR_FTL_M3:.0f} m³."
            )
        # Regra da região
        tipo_regra = regra.get("tipo_entrega", "fracionado")
        if tipo_regra == "misto":
            limiar_misto = settings.LIMIAR_FTL_M3 * 0.5
            if vol >= limiar_misto:
                return "ftl", (
                    f"FTL porque a região aceita regra mista e o volume do lote "
                    f"({vol:.2f} m³) já ultrapassa metade do limiar de consolidação "
                    f"({limiar_misto:.1f} m³)."
                )
            return "fracionado", (
                f"fracionado porque a região aceita regra mista, mas o volume do lote "
                f"({vol:.2f} m³) ainda não atinge metade do limiar de consolidação "
                f"({limiar_misto:.1f} m³)."
            )
        return tipo_regra, (
            f"{tipo_regra} por regra padrão da região — volume do lote ({vol:.2f} m³) "
            f"abaixo do limiar de consolidação FTL ({settings.LIMIAR_FTL_M3:.0f} m³)."
        )

    def _motivo_transportadora(
        self, transportadora: "Transportadora | None", cotacao: dict
    ) -> str:
        nome = transportadora.nome if transportadora else None
        metodo = cotacao.get("metodo")
        if metodo == "cotacao_banco":
            return (
                f"{nome} selecionada por menor custo entre {cotacao['cotacoes_comparadas']} "
                f"cotação(ões) cadastrada(s) para {cotacao.get('uf', '?')}/"
                f"{cotacao.get('classificacao', '?')} — R$ {cotacao['custo']:.2f}, "
                f"economia de R$ {cotacao['economia_vs_pior']:.2f} vs. a opção mais cara "
                f"(prazo estimado: {cotacao['prazo_dias']} dia(s))."
            )
        if metodo == "regra_fixa":
            return (
                f"{nome} aplicada por regra padrão da região — nenhuma tabela de preço "
                f"cadastrada para esta rota ainda."
            )
        return "nenhuma transportadora ativa cadastrada para este CD; onda ficará sem transportadora até cadastro manual."

    def _motivo_veiculo(
        self, veiculo: "Veiculo | None", regra: dict, vol: float, peso: float
    ) -> str:
        if not veiculo:
            return (
                f"nenhum veículo com capacidade suficiente para {vol:.2f} m³ / "
                f"{peso:.0f} kg estava disponível neste CD."
            )
        preferido = veiculo.tipo in regra.get("tipo_veiculo_preferido", [])
        base = (
            f"{veiculo.tipo} ({veiculo.capacidade_m3:.0f} m³ / {veiculo.capacidade_kg:.0f} kg) "
            f"— menor veículo disponível que comporta o volume/peso do lote, minimizando ociosidade."
        )
        if not preferido:
            base += " Nenhum veículo do tipo preferido para esta região estava disponível; usado fallback."
        return base

    def _montar_justificativa(
        self,
        motivo_tipo: str,
        motivo_transp: str,
        motivo_veiculo: str,
        ocupacao: float,
        subutilizada: bool,
    ) -> str:
        partes = [
            f"Tipo de entrega: {motivo_tipo}",
            f"Transportadora: {motivo_transp}",
            f"Veículo: {motivo_veiculo}",
        ]
        if subutilizada:
            partes.append(
                f"Atenção: ocupação de {ocupacao:.1f}% abaixo do mínimo recomendado para "
                f"FTL ({settings.OCUPACAO_MIN_FTL_PCT * 100:.0f}%) — considere consolidar "
                f"com outra onda antes de fechar o plano."
            )
        return "\n".join(partes)

    def _horario_coleta(self, lote: list[Remessa]) -> time | None:
        """Horário de coleta = 1h antes da janela mais cedo do lote."""
        janelas = [r.janela_inicio for r in lote if r.janela_inicio]
        if not janelas:
            return None
        mais_cedo = min(janelas)
        hora = mais_cedo.hour - 1
        if hora < 0:
            hora = 0
        return time(hora, mais_cedo.minute)

    def _nome_onda(self, num: int, regiao: str, tipo: str) -> str:
        regiao_fmt = regiao.replace("_", " ").title()
        tipo_fmt   = "FTL" if tipo == "ftl" else "Fracionado"
        return f"Onda {num:02d} — {regiao_fmt} ({tipo_fmt})"

    # ── Queries ───────────────────────────────────────────────────────────────

    async def _carregar_remessas_disponiveis(
        self, cd_id: int, data: date
    ) -> list[Remessa]:
        # Remessa sem NF emitida não pode ser despachada — fica de fora da
        # montagem e permanece com status "novo" (aberta/pendente) até que
        # a NF seja emitida e uma futura montagem a recolha.
        res = await self.db.execute(
            select(Remessa)
            .options(selectinload(Remessa.cliente))
            .where(
                and_(
                    Remessa.cd_id         == cd_id,
                    Remessa.data_extracao == data,
                    Remessa.status        == "novo",
                    Remessa.nf_emitida    == True,
                )
            )
        )
        return list(res.scalars().all())

    async def _carregar_veiculos(self, cd_id: int) -> list[Veiculo]:
        res = await self.db.execute(
            select(Veiculo).where(
                and_(Veiculo.cd_id == cd_id, Veiculo.ativo == True)
            )
        )
        return list(res.scalars().all())

    async def _carregar_transportadoras(self, cd_id: int) -> list[Transportadora]:
        res = await self.db.execute(
            select(Transportadora).where(
                and_(Transportadora.cd_id == cd_id, Transportadora.ativo == True)
            )
        )
        return list(res.scalars().all())

    async def _get_cd(self, codigo: str) -> CentroDistribuicao | None:
        res = await self.db.execute(
            select(CentroDistribuicao).where(CentroDistribuicao.codigo == codigo)
        )
        return res.scalar_one_or_none()
