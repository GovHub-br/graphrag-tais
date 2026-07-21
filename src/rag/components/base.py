"""
Base classes for component factories.

Todo componente Haystack usado nas pipelines é construído por uma Factory que
lê os defaults do settings (.env) e permite override via __init__. Componentes
custom (decorados com @component) ficam nas subpastas de `components/`.
"""
from abc import ABC, abstractmethod


class ComponentFactory(ABC):
    """Base para todas as factories de componentes."""

    @abstractmethod
    def build(self):
        """Build and return the component instance."""
        ...
