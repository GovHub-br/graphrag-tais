"""Orquestração da jornada (ADR-0020): pergunta → População geral → trilhas.

    pergunta ─► guardrail ─► número ou assunto?
                              │ número                      │ assunto
                              ▼                             ▼
                motor (SQL) ─► executor ─► Facts      modo Explorar
                              │                             │
                              ▼                             ▼
                resposta População geral          panorama do catálogo
                + estado guardado (ref)           + perguntas de número
                              │                     como botões
                  clique ◄────┘
                    ▼
       técnica · política · gestor · história · baixar   (sem novo SQL,
                                                          exceto a série
                                                          histórica)

O motor, o executor e o explorador são **injetados** (o wrapper do Hayhooks
passa as pipelines reais; os testes passam dublês). Nada aqui altera o caminho
medido da Persona 2: o motor é chamado como caixa-preta, com a pergunta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from rag.journey import facts as facts_mod
from rag.journey import fmt, ibge, render, routing, tracks
from rag.journey.catalog_probe import (CatalogIndex, Scope, complementary, policy_stages,
                                       products_of, scope_of)
from rag.journey.human_scale import scale_for
from rag.journey.marker import Marker
from rag.journey.state import JourneyStore, new_ref
from rag.journey.text import strip_emoji

# Contratos dos colaboradores injetados.
MotorFn = Callable[[str], Tuple[Dict[str, Any], List[Any]]]           # pergunta → (answer, docs)
ExecFn = Callable[[str, str, str], Dict[str, Any]]                      # sql, pergunta, contexto → exec
ExploreFn = Callable[[str], Tuple[str, Dict[str, Any]]]                 # pergunta → (markdown, answer)
PhraseFn = Callable[[str, str, str, str], str]                          # pergunta, número, legenda, digest → JSON
CatalogFn = Callable[[], Optional[CatalogIndex]]

ABOUT = """\
Sou o assistente de dados do GovHub. Pergunte em linguagem simples — por exemplo, \
*"quanto foi contratado por estado?"* — e eu respondo com o número, o que ele \
significa e onde ele está no mapa.

Depois da resposta aparecem botões para ir além, conforme o que você precisa:

- **Como esse dado foi calculado?** — o SQL, as tabelas, as colunas e o CSV;
- **Contexto da política** — a série histórica, as regiões e o que falta no dado;
- **Números para decisão** — indicadores-chave, ranking e o relatório `.docx`;
- **Contar a história** — o dado em quatro passos.

Se quiser saber o que existe sobre um assunto, pergunte *"o que vocês têm sobre …?"*."""

_SEM_RESULTADO = (
    "Não consegui chegar a um número para essa pergunta com as bases disponíveis. "
    "Abaixo está o que o sistema tentou — dá para reformular a pergunta com outro recorte "
    "(por UF, por ano) ou perguntar *\"o que vocês têm sobre …?\"* para ver as bases."
)


@dataclass
class Reply:
    markdown: str
    marker: Marker
    follow_ups: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # O Assistente não usa emoji (pedido de produto, 01/11/2026). Os textos
        # próprios já saem sem; isto limpa o que vem das peças reaproveitadas —
        # o dicionário do Indicador ("🔑 PK"), o panorama do Explorar ("📥").
        self.markdown = strip_emoji(self.markdown)
        self.follow_ups = [strip_emoji(b).strip() for b in self.follow_ups]

    def text(self) -> str:
        return f"{self.markdown}\n\n{self.marker.render()}"


class JourneyService:
    def __init__(self, *, motor: MotorFn, executor: ExecFn, explorer: ExploreFn,
                 store: JourneyStore, phrase: PhraseFn, catalog: CatalogFn,
                 publisher: Any, charts: Any = None, line_renderer: Any = None,
                 settings: Any = None) -> None:
        if charts is None:
            from rag.components.charts import infographics as charts
        if settings is None:
            from rag.settings.configs import settings
        self.motor, self.executor, self.explorer = motor, executor, explorer
        self.store, self.phrase, self.catalog = store, phrase, catalog
        self.publisher, self.charts, self.settings = publisher, charts, settings
        self.line_renderer = line_renderer or _line_renderer

    # ------------------------------------------------------------------ #
    # Pergunta nova
    # ------------------------------------------------------------------ #
    def answer(self, question: str) -> Reply:
        rota = routing.classify(question)
        logger.info("[jornada] rota={} '{}…'", rota, question[:60])
        if rota == routing.ASSUNTO:
            return self._assunto(question)
        state = self._compute(question)
        return self._populacao_reply(state)

    def _assunto(self, question: str) -> Reply:
        markdown, answer = self.explorer(question)
        perguntas = []
        for tabela in (answer or {}).get("tables", []) or []:
            perguntas.extend(q for q in tabela.get("questions", []) if q)
        ref = new_ref()
        self.store.save(ref, {"route": routing.ASSUNTO, "question": question,
                              "questions": perguntas[:3]})
        return Reply(markdown, Marker(ref=ref, rota=routing.ASSUNTO, trilha="panorama"),
                     follow_ups=perguntas[:3])

    def _compute(self, question: str) -> Dict[str, Any]:
        """Motor + execução. Devolve o estado que os cliques vão reusar."""
        answer, docs = self.motor(question)
        state: Dict[str, Any] = {"route": routing.NUMERO, "question": question,
                                 "answer": _lean_answer(answer), "exec": {}, "status": "ok"}
        if (answer.get("refusal") or "").strip():
            state["status"] = "recusa"
            return state
        sql = (answer.get("sql") or "").strip()
        if not sql:
            state["status"] = "sem_resultado"
            return state
        contexto = "\n\n".join(getattr(d, "content", "") for d in docs or [])
        execu = self.executor(sql, question, contexto) or {}
        state["exec"] = {
            "rows": execu.get("rows") or [], "columns": execu.get("columns") or [],
            "sql_final": execu.get("sql_final") or sql, "exec_ok": bool(execu.get("exec_ok")),
            "error": execu.get("error") or "", "masked_columns": execu.get("masked_columns") or [],
            "truncated": bool(execu.get("truncated")),
        }
        if not state["exec"]["exec_ok"]:
            logger.warning("[jornada] SQL não executado ({})", state["exec"]["error"] or "?")
        f = self._facts(state)
        if not f.ok:
            state["status"] = "sem_resultado"
        return state

    def _facts(self, state: Dict[str, Any], prefer_time: bool = False) -> facts_mod.Facts:
        execu = state.get("exec") or {}
        f = facts_mod.build(execu.get("rows") or [], execu.get("columns") or [],
                            sql=execu.get("sql_final", ""), truncated=bool(execu.get("truncated")),
                            prefer_time=prefer_time,
                            references=(state.get("answer") or {}).get("references") or [])
        f.label = state.get("label", "")
        return f

    def _populacao_reply(self, state: Dict[str, Any]) -> Reply:
        ref = new_ref()
        question = state["question"]
        marker = Marker(ref=ref, rota=routing.NUMERO, trilha=tracks.POPULACAO)
        if state["status"] == "recusa":
            self.store.save(ref, state)
            return Reply(render_motor(state), marker)
        if state["status"] != "ok":
            self.store.save(ref, state)
            return Reply(_SEM_RESULTADO + "\n\n" + render_motor(state), marker)

        f = self._facts(state)
        escala = scale_for(f)
        frase, legenda = self._phrase(question, f)
        index, usadas = self.catalog(), _used_tables(state)
        frase = with_scope(frase, question, scope_of(index, usadas), products_of(index, usadas),
                           getattr(self.settings, "SEED_PRODUCT_LABEL_STOPWORDS", ""))
        state["phrase"], state["label"] = frase, legenda
        f.label = legenda
        state["scale"] = escala.text if escala else ""
        markdown = render.populacao(
            f, frase, escala, charts=self.charts, tables=_used_tables(state),
            service=self.settings.OM_SERVICE, open_data_url=self.settings.JOURNEY_OPEN_DATA_URL,
            notes=_notes(state, f), line_renderer=self.line_renderer,
        )
        _keep_section(state, tracks.POPULACAO, markdown)
        self.store.save(ref, state)
        buttons = tracks.buttons(tracks.suggest_order(question))
        return Reply(markdown, marker, follow_ups=buttons)

    def _phrase(self, question: str, f: facts_mod.Facts) -> Tuple[str, str]:
        """(frase, legenda) da População geral, com guarda nos dois campos."""
        numero = render.headline_text(f)
        try:
            bruto = self.phrase(question, numero, render.caption(f), render.digest(f)) or ""
            texto, legenda = parse_phrase(bruto)
        except Exception as exc:  # noqa: BLE001 — a frase tem fallback determinístico
            logger.warning("[jornada] frase do LLM falhou ({}); usando a determinística", exc)
            texto, legenda = "", ""
        # A legenda vai embaixo do número no cartão: sem dígito (número nela
        # seria número inventado) e curta. Fora disso, fica o nome da coluna.
        if re.search(r"\d", legenda) or not 2 <= len(legenda.split()) <= 10:
            legenda = ""
        f.label = legenda
        # Guardrail da frase: o número principal tem de estar nela, como veio.
        # Se o modelo arredondou ou trocou a unidade, a frase determinística
        # entra — número na tela que não bate com o cartão é pior que estilo.
        if not texto or numero not in texto:
            if texto:
                logger.warning("[jornada] frase sem o número '{}'; usando a determinística", numero)
            return render.fallback_phrase(f), legenda
        # O número certo no lugar errado: o total atribuído a quem lidera.
        if render.misreads_total(f, texto, numero):
            logger.warning("[jornada] frase atribui o total a um item; usando a determinística")
            return render.fallback_phrase(f), legenda
        return texto, legenda

    # ------------------------------------------------------------------ #
    # Clique num botão
    # ------------------------------------------------------------------ #
    def track(self, track: tracks.Track, marker: Optional[Marker], fallback_question: str) -> Reply:
        state = self.store.load(marker.ref) if marker and marker.ref else None
        aviso = ""
        if state is None or state.get("route") != routing.NUMERO:
            if not fallback_question:
                return Reply("Faça uma pergunta primeiro — por exemplo, *\"quanto foi contratado "
                             "por estado?\"* — e os botões aparecem logo abaixo da resposta.",
                             Marker(rota=routing.NUMERO, trilha=track.id))
            logger.info("[jornada] estado ausente para ref={}; recalculando",
                        marker.ref if marker else "-")
            state = self._compute(fallback_question)
            aviso = ("*O resultado anterior expirou; o número foi recalculado agora "
                     "e pode ter mudado.*")
            ref = new_ref()
            marker = Marker(ref=ref, rota=routing.NUMERO, trilha=tracks.POPULACAO)
            self.store.save(ref, state)

        vistas = list(dict.fromkeys((marker.vistas if marker else []) + [track.id]))
        sugestao = [t.id for t in tracks.suggest_order(state["question"], marker.vistas)]
        logger.info("[jornada/intencao] ref={} trilha={} sugestao={} posicao={}",
                    marker.ref, track.id, ",".join(sugestao),
                    sugestao.index(track.id) + 1 if track.id in sugestao else 0)

        novo = Marker(ref=marker.ref, rota=routing.NUMERO, trilha=track.id, vistas=vistas)
        if state.get("status") != "ok":
            return Reply("Essa pergunta não chegou a um número, então não há o que detalhar "
                         "nesta trilha. Tente reformular a pergunta.", novo)

        f = self._facts(state)
        corpo = getattr(self, f"_track_{track.id}")(state, f, marker.ref)
        if track.id != tracks.BAIXAR.id:
            # O relatório é o que a pessoa VIU: cada trilha aberta entra nele,
            # na ordem em que foi aberta (ADR-0020).
            _keep_section(state, track.id, corpo)
            self.store.save(marker.ref, state)
        if track.id == tracks.GESTOR.id:
            corpo += "\n\n" + render.report_link(self._ensure_report(state, marker.ref))
        if aviso:
            corpo = aviso + "\n\n" + corpo
        botoes = tracks.buttons(tracks.suggest_order(state["question"], vistas))
        return Reply(corpo, novo, follow_ups=botoes)

    # --- técnica -------------------------------------------------------- #
    def _track_tecnica(self, state, f, ref) -> str:
        execu = state["exec"]
        csv_url = self._ensure_csv(state, ref)
        return render.tecnica(
            f, render_motor(state), charts=self.charts, steps=lineage_steps(state, self.settings),
            preview_table=render.preview_table(execu["columns"], execu["rows"]),
            limitations=limitations(state, f), csv_url=csv_url,
            open_data_url=self.settings.JOURNEY_OPEN_DATA_URL,
        )

    # --- gestor --------------------------------------------------------- #
    def _track_gestor(self, state, f, ref) -> str:
        # Sem o link: ele é posto DEPOIS, porque o relatório inclui esta trilha.
        index = self.catalog()
        return render.gestor(f, charts=self.charts, kpis=kpis(f), region_table=region_table(f),
                             planned_text=planned_text(f, index, _used_tables(state)))

    # --- política ------------------------------------------------------- #
    def _track_politica(self, state, f, ref) -> str:
        index = self.catalog()
        stages = policy_stages(index, _used_tables(state)) if index else []
        comp = complementary(index) if index else []
        png, texto, sql = self._series(state, f)
        metodologia = limitations(state, f)
        if sql and png:
            metodologia = metodologia + ["A série histórica vem de uma consulta própria, gerada "
                                         "pelo motor a partir da sua pergunta (SQL no fim)."]
        return render.politica(f, charts=self.charts, stages=stages, complementary=comp,
                               series_png=png, series_text=texto, series_sql=sql,
                               methodology=metodologia)

    def _series(self, state, f) -> Tuple[Optional[bytes], str, str]:
        if f.dimension_kind == "tempo" and f.n >= 2:
            return (render.safe_chart(self.line_renderer, f), series_sentence(f), "")
        if not self.settings.JOURNEY_POLICY_SERIES:
            return None, "_Série histórica desligada neste ambiente._", ""
        tentado = ""
        for pergunta in series_questions(state["question"], f):
            try:
                derivado = self._compute(pergunta)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[jornada] série histórica falhou: {}", exc)
                continue
            execu = derivado.get("exec") or {}
            logger.info("[jornada/serie] '{}' → status={} sql={}", pergunta[:60],
                        derivado.get("status"), execu.get("sql_final", "")[:160])
            if execu.get("sql_final") and not execu.get("exec_ok"):
                tentado = execu["sql_final"]
            if derivado.get("status") != "ok":
                continue
            fs = self._facts(derivado, prefer_time=True)
            fs.label = f.label
            if fs.dimension_kind == "tempo" and fs.n >= 2:
                return (render.safe_chart(self.line_renderer, fs), series_sentence(fs),
                        execu.get("sql_final", ""))
        if tentado:
            # O catálogo apontou uma tabela com data, mas o banco não a executou:
            # dizer "não há coluna de tempo" seria falso. Diz o que houve e
            # mostra a consulta, que é o que alguém técnico precisa para seguir.
            return (None, "Tentei montar a série histórica, mas a consulta não rodou no banco — "
                          "a tabela com datas que o catálogo indica não respondeu. A consulta "
                          "tentada está no fim desta resposta.", tentado)
        return (None, "Não encontrei, nas tabelas do catálogo, uma coluna de tempo que permita "
                      "montar a série histórica deste número.", "")

    # --- história ------------------------------------------------------- #
    def _track_historia(self, state, f, ref) -> str:
        index = self.catalog()
        escala = scale_for(f)
        return render.historia(f, state.get("phrase") or render.fallback_phrase(f), escala,
                               charts=self.charts,
                               complementary=complementary(index) if index else [],
                               line_renderer=self.line_renderer)

    # --- baixar --------------------------------------------------------- #
    def _track_baixar(self, state, f, ref) -> str:
        report_url = self._ensure_report(state, ref)
        return render.baixar(rows=len(state["exec"]["rows"]), csv_url=self._ensure_csv(state, ref),
                             report_url=report_url,
                             open_data_url=self.settings.JOURNEY_OPEN_DATA_URL,
                             report_parts=_section_names(state))

    # ------------------------------------------------------------------ #
    def _ensure_csv(self, state, ref) -> str:
        if not state.get("csv_url"):
            execu = state["exec"]
            state["csv_url"] = self.publisher.csv(execu["columns"], execu["rows"], state["question"])
            self.store.save(ref, state)
        return state["csv_url"]

    def _ensure_report(self, state, ref) -> str:
        """Relatório com as seções que a pessoa abriu até agora.

        Refeito quando ela abre uma trilha nova: o link do botão "Baixar"
        depois de "Contexto da política" tem de trazer a política, e o do
        "Números para decisão" não pode trazer o SQL que ela nunca viu.
        """
        secoes = state.get("sections") or {}
        chave = "|".join(secoes)
        if not state.get("report_url") or state.get("report_key") != chave:
            markdown = render.report_markdown(list(secoes.items()))
            state["report_url"] = self.publisher.report(markdown, state["question"])
            state["report_key"] = chave
            self.store.save(ref, state)
        return state["report_url"]

    # ------------------------------------------------------------------ #
    # Botões (follow-ups do OpenWebUI)
    # ------------------------------------------------------------------ #
    def follow_ups(self, marker: Optional[Marker]) -> List[str]:
        if marker is None:
            return []
        state = self.store.load(marker.ref) or {}
        if marker.rota == routing.ASSUNTO:
            return [strip_emoji(q).strip() for q in (state.get("questions") or [])[:3]]
        if state and state.get("status") != "ok":
            return []
        pergunta = state.get("question", "")
        return tracks.buttons(tracks.suggest_order(pergunta, marker.vistas))


# --------------------------------------------------------------------------- #
# Peças determinísticas (funções de módulo: testáveis sem serviço)
# --------------------------------------------------------------------------- #
def parse_phrase(raw: str) -> Tuple[str, str]:
    """`{"frase", "legenda"}` do LLM. Texto que não é JSON vira só a frase."""
    import json

    texto = (raw or "").strip()
    try:
        dados = json.loads(texto[texto.find("{"): texto.rfind("}") + 1])
        return (str(dados.get("frase", "")).strip(),
                str(dados.get("legenda", "")).strip().rstrip("."))
    except (ValueError, AttributeError):
        return texto, ""

_ANSWER_KEYS = ("status", "explanation", "sql", "refusal", "ir", "references", "joins",
                "context_tables")


def _lean_answer(answer: Dict[str, Any]) -> Dict[str, Any]:
    return {k: answer.get(k) for k in _ANSWER_KEYS if k in answer}


def _section_names(state: Dict[str, Any]) -> List[str]:
    """"a resposta", "Números para decisão"… — o que o relatório traz, em palavras."""
    nomes = []
    for track_id in state.get("sections") or {}:
        track = tracks.get(track_id)
        nomes.append(track.button.rstrip("?") if track else "a resposta")
    return nomes


def _keep_section(state: Dict[str, Any], track_id: str, markdown: str) -> None:
    """Guarda o Markdown de uma trilha como foi mostrado (dict preserva a ordem)."""
    secoes = state.setdefault("sections", {})
    secoes.pop(track_id, None)
    secoes[track_id] = markdown


def _used_tables(state: Dict[str, Any]) -> List[str]:
    """Tabelas de onde o número saiu — as do SQL que RODOU.

    Antes vinham das tabelas que o LLM marcou como usadas (`references.used`),
    e o "De onde veio" podia citar uma tabela prata enquanto o SQL de "Como foi
    calculado" lia outra, ouro (a autocorreção troca a tabela; o LLM também
    marca uma e escreve outra). A marcação do LLM fica de reserva para quando
    não há SQL executado ou ele não parseia."""
    execu = state.get("exec") or {}
    if execu.get("exec_ok"):
        tabelas = _sql_tables(execu.get("sql_final") or "")
        if tabelas:
            return tabelas
    refs = (state.get("answer") or {}).get("references") or []
    usadas = [r.get("table", "") for r in refs if r.get("used")]
    return [t for t in (usadas or [r.get("table", "") for r in refs]) if t]


def _sql_tables(sql: str) -> List[str]:
    """`schema.tabela` lidas pelo SQL (sem CTE: CTE não tem schema)."""
    if not sql.strip():
        return []
    try:
        from rag.sensitivity.resolver import source_tables
        from rag.settings.configs import settings

        return [t for t in source_tables(sql, dialect=settings.SQL_DIALECT) if "." in t]
    except Exception as exc:  # noqa: BLE001 — sem parse, fica a marcação do LLM
        logger.warning("[jornada] não consegui ler as tabelas do SQL ({})", exc)
        return []


def with_scope(phrase: str, question: str, scope: Optional[Scope],
               products: Sequence[str] = (), stopwords: str = "") -> str:
    """Diz de onde é o número quando a pergunta não disse.

    "Qual o total contratado por UF?" não cita programa, e a busca escolhe a
    base de UM (no MCid, o Pró-Moradia): a frase dava "R$ 5,8 bi" como se fosse
    o total de tudo. O recorte é a primeira oração da descrição da tabela que
    rodou ("Grandes números do Pró-Moradia por UF") — ver `scope_of`.

    Nada muda quando a pergunta (ou a frase do LLM) já cita a base: pelas formas
    do roteamento por produto (rótulo, sigla, rótulo sem as palavras de
    `SEED_PRODUCT_LABEL_STOPWORDS`) ou pelas da própria tabela (pares de
    palavras do nome — "pro moradia" — e siglas da descrição — "FAR")."""
    if not phrase or scope is None:
        return phrase
    from rag.components.retrievers.product_router import (_stopwords, aliases_from_label,
                                                          products_in_question)

    sw = _stopwords(stopwords)
    formas = {p: aliases_from_label(p, sw) for p in products}
    formas["_tabela"] = set(scope.forms)
    if products_in_question(question, formas) or products_in_question(phrase, formas):
        return phrase
    return (f"{phrase.rstrip()}\n\n*Recorte: {scope.text} (`{scope.table}`). A pergunta "
            f"não disse de qual programa ou base, então a resposta usa esta.*")


def render_motor(state: Dict[str, Any]) -> str:
    from rag.components.answer.sql_answer_builder import render_markdown

    execu = dict(state.get("exec") or {})
    execu.setdefault("row_limit", 0)
    return render_markdown(state.get("answer") or {}, execu)


def _notes(state: Dict[str, Any], f: Optional[facts_mod.Facts] = None) -> List[str]:
    from rag.components.answer.sql_answer_builder import (render_masking_note,
                                                          render_truncation_note)
    from rag.settings.configs import settings

    execu = state.get("exec") or {}
    notas = [render_masking_note(execu.get("masked_columns") or []),
             render_truncation_note(bool(execu.get("truncated")), settings.MAX_EXEC_ROWS)]
    if f is not None and f.mixed_levels:
        notas.append("> A tabela traz também linhas de região ou total "
                     f"({', '.join(f.mixed_levels)}); elas ficaram fora da soma para não contar "
                     "o mesmo valor duas vezes. Detalhes em *Como esse dado foi calculado?*")
    return notas


_AGG = re.compile(r"\b(sum|count|avg|min|max)\s*\(\s*(distinct\s+)?([^()]*?)\s*\)", re.IGNORECASE)


def calculation_lines(sql: str) -> List[str]:
    """O cálculo, lido do SQL que rodou: agregação, agrupamento e filtro."""
    texto = " ".join((sql or "").split())
    linhas = [f"{m.group(1).upper()}({(m.group(2) or '').upper()}{m.group(3)})"
              for m in _AGG.finditer(texto)][:2]
    grupo = re.search(r"group\s+by\s+(.+?)(?:\s+order\s+by|\s+having|\s+limit|\)|$)", texto, re.I)
    if grupo:
        linhas.append(f"agrupado por {grupo.group(1).strip()}")
    filtro = re.search(r"\bwhere\s+(.+?)(?:\s+group\s+by|\s+order\s+by|\s+limit|\)|$)", texto, re.I)
    if filtro:
        linhas.append(f"filtro: {filtro.group(1).strip()}")
    return linhas or ["leitura direta das colunas (sem soma nem contagem)"]


def lineage_steps(state: Dict[str, Any], settings: Any) -> List[Tuple[str, List[str]]]:
    execu = state.get("exec") or {}
    sql = execu.get("sql_final") or (state.get("answer") or {}).get("sql", "")
    tabelas = _used_tables(state)
    citadas = set(re.findall(r"[a-z_][a-z0-9_]*", sql.lower()))
    colunas = []
    for ref in (state.get("answer") or {}).get("references") or []:
        for col in ref.get("columns") or []:
            nome = col.get("name", "")
            if nome.lower() in citadas and nome not in [c.split(" ")[0] for c in colunas]:
                colunas.append(f"{nome} ({col.get('type') or 'sem tipo'})")
    return [
        ("Fonte", [f"OpenMetadata · serviço {settings.OM_SERVICE}",
                   f"banco {settings.OM_DATABASE}"]),
        ("Tabela" if len(tabelas) <= 1 else "Tabelas", tabelas[:3] or ["(não identificada)"]),
        ("Colunas", colunas[:4] or ["(não identificadas)"]),
        ("Cálculo", calculation_lines(sql)),
    ]


def limitations(state: Dict[str, Any], f: facts_mod.Facts) -> List[str]:
    execu = state.get("exec") or {}
    itens = []
    if execu.get("truncated"):
        itens.append(f"O resultado passou do limite de linhas da execução; tudo o que está aqui "
                     f"(e o CSV) cobre só as primeiras {len(execu.get('rows') or [])}.")
    for col, frac in f.coverage.items():
        if frac < 0.95:
            itens.append(f"`{col}` está vazia em {fmt.percent(1 - frac)} das linhas.")
    if not f.additive:
        itens.append("A métrica é uma média, taxa ou índice: não faz sentido somá-la entre "
                     "categorias, por isso não há total.")
    if f.unit_note:
        itens.append(f.unit_note)
    if f.dimension_kind == "uf" and 0 < len(f.uf_values) < 27:
        faltam = sorted({u["sigla"] for u in ibge.ufs()} - set(f.uf_values))
        itens.append(f"O resultado tem {len(f.uf_values)} das 27 UFs (faltam: {', '.join(faltam)}).")
    if f.mixed_levels:
        aviso = (f"O resultado misturava UFs com linhas de região ou total "
                 f"({', '.join(f.mixed_levels)}); elas ficaram fora das contas para não somar o "
                 "mesmo valor duas vezes.")
        if "SE" in f.mixed_levels:
            aviso += (" “SE” também ficou de fora: nessa mistura ele pode ser Sergipe ou a região "
                      "Sudeste — confira no CSV.")
        itens.append(aviso)
    if execu.get("masked_columns"):
        itens.append("Há colunas com dado pessoal pseudonimizadas (ver nota acima).")
    escala = scale_for(f)
    if escala:
        itens.append(f"A comparação em escala humana usa: {escala.source}.")
    return itens or ["Nenhuma limitação encontrada no resultado."]


def kpis(f: facts_mod.Facts) -> List[Tuple[str, str, str]]:
    """Até 4 cartões (valor, rótulo, detalhe). Rótulos CURTOS e genéricos — o
    cartão tem ~2,8" de largura; o nome da métrica já está no título da trilha."""
    m = render.money_fmt(f)
    unidade = {"uf": "UF", "tempo": "período"}.get(f.dimension_kind, "categoria")
    plural = {"uf": "UFs", "tempo": "períodos"}.get(f.dimension_kind, "categorias")
    if f.dimension_kind == "tempo":
        serie = f.series()
        cartoes = [(render.headline_text(f), "Último período",
                    fmt.period(serie[-1][0]) if serie else "")]
        if len(serie) >= 2 and serie[-2][1]:
            var = serie[-1][1] / serie[-2][1] - 1
            cartoes.append((("+" if var >= 0 else "") + fmt.percent(var, 1),
                            "Variação no último período",
                            f"{fmt.period(serie[-2][0])} → {fmt.period(serie[-1][0])}"))
        pico = max(serie, key=lambda kv: kv[1])
        cartoes.append((m(pico[1]), "Pico da série", f"em {fmt.period(pico[0])}"))
        cartoes.append((m(f.mean()), "Média por período", f"{len(serie)} períodos"))
        return cartoes
    rotulo_total = {"total": "Total", "valor": "Valor", "maior": "Maior valor"}.get(
        f.headline_kind, "Valor")
    cartoes = [(render.headline_text(f), rotulo_total,
                f"{f.n} {plural}" if f.n > 1 and f.headline_kind == "total" else "")]
    if f.n >= 2:
        rotulo, valor = f.top(1)[0]
        share = f.share_of_top()
        cartoes.append((_short(rotulo), "Maior valor",
                        m(valor) + (f" · {fmt.percent(share[1])} do total" if share else "")))
        ordenados = sorted(v for _, v in f.values)
        mediana = ordenados[len(ordenados) // 2]
        cartoes.append((m(f.mean()), f"Média por {unidade}", f"mediana {m(mediana)}"))
        total = f.total()
        if total and f.n >= 4:
            top3 = sum(v for _, v in f.top(3)) / total
            cartoes.append((fmt.percent(top3), "Concentração", f"nas 3 maiores {plural}"))
        else:
            menor = f.top(1, reverse=False)[0]
            cartoes.append((_short(menor[0]), "Menor valor", m(menor[1])))
    return cartoes


def _short(texto: str, n: int = 14) -> str:
    texto = str(texto)
    return texto if len(texto) <= n else texto[: n - 1] + "…"


def region_table(f: facts_mod.Facts) -> str:
    if f.dimension_kind != "uf" or not f.additive or len(f.uf_values) < 5:
        return ""
    por_cem_mil = not f.money
    unidade = "por habitante" if f.money else "por 100 mil habitantes"
    linhas = [f"| Região | Média {unidade} | Maior | Menor |", "| :-- | --: | :-- | :-- |"]
    for regiao, ufs_ in sorted(f.region_values().items()):
        pops = {s: ibge.by_sigla(s)["populacao_2022"] for s in ufs_}
        if not sum(pops.values()):
            continue
        fator = 100_000 if por_cem_mil else 1

        def per(v, p):
            return v / p * fator

        media = per(sum(ufs_.values()), sum(pops.values()))
        taxas = {s: per(v, pops[s]) for s, v in ufs_.items() if pops[s]}
        melhor = max(taxas, key=taxas.get)
        pior = min(taxas, key=taxas.get)
        fmt_v = (lambda v: fmt.compact(v, money=True)) if f.money else (lambda v: fmt.compact(v))
        linhas.append(f"| {regiao} | {fmt_v(media)} | {melhor} ({fmt_v(taxas[melhor])}) | "
                      f"{pior} ({fmt_v(taxas[pior])}) |")
    nota = (f"\n\n*Cada UF comparada com as da mesma região, {unidade} "
            "(população do IBGE, Censo 2022).*")
    return "\n".join(linhas) + nota


_PREVISTO = re.compile(r"(previst|meta|planejad|dotacao|orcad|programad)")
_EXECUTADO = re.compile(r"(executad|pago|realizad|liquidad|desembols|contratad|entregue)")


def planned_text(f: facts_mod.Facts, index: Optional[CatalogIndex], used: Sequence[str]) -> str:
    nums = [c for c in f.columns if f.kinds.get(c) == "numeric"]
    previsto = next((c for c in nums if _PREVISTO.search(c.lower())), None)
    executado = next((c for c in nums if c != previsto and _EXECUTADO.search(c.lower())), None)
    if previsto and executado:
        tp = sum(facts_mod.as_float(r.get(previsto)) or 0 for r in f.rows)
        te = sum(facts_mod.as_float(r.get(executado)) or 0 for r in f.rows)
        m = render.money_fmt(f)
        if tp:
            return (f"**{fmt.percent(te / tp)}** do previsto foi executado: {m(te)} de {m(tp)} "
                    f"(`{executado}` sobre `{previsto}`).")
    texto = ("As tabelas desta resposta não trazem um valor **previsto** (meta, dotação, "
             "orçamento), então não dá para comparar executado com previsto.")
    if index:
        etapa = next((s for s in policy_stages(index, used) if s.name == "Orçamento"), None)
        if etapa and etapa.tables:
            texto += (" No catálogo há tabelas de orçamento que podem ajudar: "
                      + ", ".join(f"`{t}`" for t in etapa.tables[:2])
                      + " — pergunte, por exemplo, pelo valor previsto do mesmo recorte.")
    return texto


def series_questions(question: str, f: facts_mod.Facts) -> List[str]:
    """Perguntas que pedem ao motor a série histórica do mesmo número.

    Primeiro pela legenda ("Qual a evolução por ano de empreendimentos do FAR em
    todo o país?"): sem o recorte da pergunta original (por UF, por município),
    a recuperação puxa a tabela com data em vez da tabela do recorte. Depois a
    pergunta original + "ano a ano", que é a segunda tentativa."""
    perguntas = []
    if f.label:
        perguntas.append(f"Qual a evolução por ano de {f.label}?")
    perguntas.append(question.rstrip(" ?.") + ". Mostre a evolução ano a ano.")
    return perguntas


def series_sentence(f: facts_mod.Facts) -> str:
    serie = f.series()
    m = render.money_fmt(f)
    if len(serie) < 2:
        return ""
    (p0, v0), (p1, v1) = serie[0], serie[-1]
    frase = f"De {m(v0)} em {fmt.period(p0)} para {m(v1)} em {fmt.period(p1)}"
    if v0:
        var = v1 / v0 - 1
        frase += f" ({'+' if var >= 0 else ''}{fmt.percent(var)} no período)"
    return frase + "."


def _line_renderer(f: facts_mod.Facts) -> Optional[bytes]:
    """Série no tempo desenhada para o celular, com os pontos fora da curva
    marcados (o renderer de linha da Persona 2 tem 11" e letra de desktop)."""
    from rag.components.charts import infographics

    serie = f.series()
    if len(serie) < 2:
        return None
    fora = [r for r, _ in f.outliers()]
    return infographics.alerts(
        serie, fora, render.money_fmt(f), temporal=True,
        title=f"{render.cap(f.metric_label)} ao longo do tempo",
        subtitle="Pontos fora da curva em laranja" if fora else
        f"{fmt.period(serie[0][0])} a {fmt.period(serie[-1][0])}",
    )
