"""Keep arXiv transport and runtime identity consistent across entry points."""
import ssl
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import enrich_data


@pytest.mark.parametrize("workflow", ["update", "backfill", "features", "enrich"])
def test_arxiv_workflows_forward_runtime_identity(workflow):
    path = Path(__file__).parents[1] / ".github" / "workflows" / f"{workflow}.yml"
    env = yaml.safe_load(path.read_text())["env"]
    for name in ("ARXIV_USER_AGENT", "ARXIV_CONTACT"):
        assert env[name] == "${{ secrets." + name + " }}"


def test_enrichment_uses_verified_tls_and_runtime_identity(monkeypatch):
    monkeypatch.setenv("ARXIV_USER_AGENT", "enrichment-test/1.0")
    monkeypatch.delenv("ARXIV_CONTACT", raising=False)
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><summary>Test abstract.</summary></entry></feed>'

    def opener(request, timeout, *, context):
        calls.append(request.full_url)
        assert request.get_header("User-agent") == "enrichment-test/1.0"
        assert "application/atom+xml" in request.get_header("Accept")
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert context.post_handshake_auth is False
        return Response()

    monkeypatch.setattr(enrich_data.urllib.request, "urlopen", opener)
    assert enrich_data.fetch_arxiv_meta("2601.12345v1")["abstract"] == "Test abstract."
    assert len(calls) == 1
