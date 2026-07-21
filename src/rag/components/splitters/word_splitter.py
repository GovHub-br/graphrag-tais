from haystack.components.preprocessors import DocumentSplitter

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class WordSplitterFactory(ComponentFactory):
    """Factory para o DocumentSplitter. Defaults vêm do settings (.env)."""

    def __init__(
        self,
        split_by: str | None = None,
        split_length: int | None = None,
        split_overlap: int | None = None,
    ):
        self.split_by = split_by or settings.SPLIT_BY
        self.split_length = split_length or settings.SPLIT_LENGTH
        self.split_overlap = split_overlap if split_overlap is not None else settings.SPLIT_OVERLAP

    def build(self) -> DocumentSplitter:
        return DocumentSplitter(
            split_by=self.split_by,
            split_length=self.split_length,
            split_overlap=self.split_overlap,
        )
