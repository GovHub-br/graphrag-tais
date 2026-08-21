r"""Prompts do sistema — um arquivo por prompt, fora do código Python.

Por que arquivo e não constante no módulo: prompt é **texto de produto**, lido e
ajustado com muito mais frequência que o componente que o usa. Como literal de
triplo-aspas ele ficava escondido no meio da pipeline, com `\` de continuação em
toda linha (o texto real não batia com o que se lia) e sem realce de sintaxe.
Em `.md`, cada prompt abre sozinho, renderiza no editor e o diff de uma mudança
de instrução não se mistura com o diff do código.

⚠️ **O texto é o contrato medido.** Os prompts da Persona 2 (`query_*`) estão em
território calibrado — mudar uma regra muda o SQL gerado e invalida o baseline do
`eval/`. Editar aqui é mudar comportamento, não formatação.

Uso:

    from rag import prompts

    SYSTEM_PROMPT = prompts.load("query_system")              # texto cru
    user_msg = prompts.render("chart_spec_user", query=q, …)  # str.format

`load` devolve o arquivo cru (sem o `\n` final), então serve tanto para prompt de
sistema quanto para **template Jinja** do `ChatPromptBuilder` (`*_user.md` das
pipelines — quem renderiza é o Haystack). `render` é para os prompts montados em
Python, com `{placeholder}` do `str.format`; **não** use em texto que tenha
chave literal (`{`/`}`), como os exemplos de JSON dos prompts de sistema.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_SUFFIX = ".md"


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Conteúdo de `prompts/<name>.md`, sem quebra de linha final.

    Em cache: o arquivo é lido uma vez por processo (os prompts são carregados no
    build da pipeline, não a cada pergunta). Reiniciar o hayhooks recarrega.
    """
    path = _DIR / f"{name}{_SUFFIX}"
    if not path.is_file():
        disponiveis = ", ".join(sorted(p.stem for p in _DIR.glob(f"*{_SUFFIX}")))
        raise FileNotFoundError(
            f"prompt '{name}' não existe em {_DIR} (disponíveis: {disponiveis})"
        )
    return path.read_text(encoding="utf-8").strip("\n")


def render(name: str, **values) -> str:
    """`load(name)` com os `{placeholder}` substituídos via `str.format`."""
    return load(name).format(**values)
