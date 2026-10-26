import pytest
from langchain_core.messages import AIMessage

from sales_copilot.db import CRM, seed_database
from sales_copilot.demo import create_demo_database
from sales_copilot.graph import Copilot
from sales_copilot.tracing import Trace
from sales_copilot.retrieval import PolicyRetriever, NOTICE
from sales_copilot.corpus import PolicyError


class StubPolicyRetriever(PolicyRetriever):
    """Deterministic semantic backend for graph tests; canonical validation is real.

    Semantic quality is measured separately using the actual local models.
    """
    def __init__(self, corpus_dir=None):
        options = {"corpus_dir": corpus_dir} if corpus_dir is not None else {}
        super().__init__(trace=Trace(None), **options)
        self.queries = []
        self.fail_code = None
        self.transform = None

    def search(self, query, top_k=3):
        self.queries.append(query)
        if self.fail_code:
            raise PolicyError(self.fail_code)
        chunks = [c for c in self.corpus.chunks if c.policy_id == "CRM-OWN-010"][:top_k]
        refs = [{**c.reference(), "dense_score": 0.8, "bm25_score": 3.0,
                 "rrf_score": 0.03, "cross_score": 5.0} for c in chunks]
        result = {"ok": True, "corpus_fingerprint": self.corpus.fingerprint, "policy_refs": refs,
                  "passages": [{**r, "text": c.text} for r, c in zip(refs, chunks)], "notice": NOTICE}
        return self.transform(result) if self.transform else result


class ScriptedModel:
    """Offline native-tool-call-shaped model double; production uses a real adapter."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tools = []
        self.invocations = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def with_structured_output(self, schema, **kwargs):
        from sales_copilot.agent_eval.fixtures import ScriptedIntentResponse
        return ScriptedIntentResponse(schema)

    def invoke(self, messages):
        self.invocations.append(list(messages))
        if not self.responses:
            raise AssertionError("Unexpected model invocation")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


@pytest.fixture
def crm(tmp_path):
    path = tmp_path / "crm.sqlite3"
    create_demo_database(path)
    return CRM(path)


@pytest.fixture
def policy_retriever():
    return StubPolicyRetriever()


@pytest.fixture
def make_copilot(crm, policy_retriever):
    def make(*responses, max_steps=8, trace=None, retriever=None, actor_id="demo-dispatcher", service=None):
        model = ScriptedModel(responses)
        bot = Copilot(crm, model, thread_id="test-thread", max_steps=max_steps,
                      trace=trace if trace is not None else Trace(None),
                      policy_retriever=retriever if retriever is not None else policy_retriever,
                      actor_id=actor_id, assignment_service=service)
        return bot, model
    return make
