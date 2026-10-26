"""Versioned local policy corpus; canonical passages are the citation authority."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from .schemas import StrictModel

DEFAULT_CORPUS = Path(__file__).parent / "policies"


class PolicyError(RuntimeError):
    """A fixed public error code; never contains a raw model/download error."""


class Section(StrictModel):
    section_id: str = Field(pattern=r"^S[0-9]+$")
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Document(StrictModel):
    policy_id: str = Field(pattern=r"^CRM-[A-Z]+-[0-9]{3}$")
    title: str = Field(min_length=1)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    sections: list[Section] = Field(min_length=1)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Chunk:
    policy_id: str
    title: str
    version: str
    section_id: str
    section_title: str
    chunk_id: str
    text: str

    @property
    def citation(self) -> str:
        return f"[{self.chunk_id}]"

    @property
    def search_text(self) -> str:
        return f"{self.title}. {self.section_title}. {self.text}"

    def reference(self) -> dict:
        return {"policy_id": self.policy_id, "title": self.title, "version": self.version,
                "section_id": self.section_id, "section_title": self.section_title,
                "chunk_id": self.chunk_id, "citation": self.citation, "text_sha256": digest(self.text)}


def split_section(text: str, max_words: int = 170) -> list[str]:
    """Bounded chunks inside a section; never mix section/citation boundaries."""
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


class Corpus:
    def __init__(self, path: str | Path = DEFAULT_CORPUS):
        self.path = Path(path)
        try:
            manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
            required = set(manifest["required_policy_ids"])
            documents = [Document.model_validate_json(p.read_text(encoding="utf-8"))
                         for p in sorted(self.path.glob("*.json")) if p.name != "manifest.json"]
            ids = [d.policy_id for d in documents]
            if not required or set(ids) != required or len(ids) != len(set(ids)):
                raise ValueError("Missing or duplicate policy")
            chunks = []
            for doc in documents:
                if len({s.section_id for s in doc.sections}) != len(doc.sections):
                    raise ValueError("Duplicate section")
                for section in doc.sections:
                    for i, text in enumerate(split_section(section.text), 1):
                        chunk_id = f"{doc.policy_id}@{doc.version}/{section.section_id}/C{i}-{digest(text)[:10]}"
                        chunks.append(Chunk(doc.policy_id, doc.title, doc.version, section.section_id,
                                            section.title, chunk_id, text))
            if not chunks:
                raise ValueError("Empty corpus")
        except Exception as exc:
            raise PolicyError("policy_corpus_invalid_or_missing") from exc
        self.documents = documents
        self.chunks = tuple(chunks)
        self.by_id = {c.chunk_id: c for c in chunks}
        self.fingerprint = digest(json.dumps([c.reference() for c in chunks], sort_keys=True))

    def resolve(self, reference: dict) -> Chunk:
        chunk = self.by_id.get(reference.get("chunk_id"))
        if chunk is None or any(reference.get(k) != v for k, v in chunk.reference().items()):
            raise PolicyError("policy_citation_invalid")
        return chunk
