"""Integrity of the synthetic AcmePay benchmark: ground truth must point at real sections."""

import yaml

from tests.conftest import CORPUS, DOC_NAMES, VERSIONS, md_path

GROUND_TRUTH = yaml.safe_load((CORPUS / "ground_truth.yaml").read_text())
SYNTHETIC_LABEL = "SYNTHETIC BENCHMARK DOCUMENT"


def section(md_docs, version, doc_name, rel_path):
    doc = md_docs[(version, doc_name)]
    return doc.section_by_path([doc.title, *rel_path])


def test_every_document_is_labelled_synthetic():
    for v in VERSIONS:
        for name in DOC_NAMES:
            assert SYNTHETIC_LABEL in md_path(v, name).read_text(), (v, name)


def test_change_paths_exist(md_docs):
    old, new = GROUND_TRUTH["from_release"], GROUND_TRUTH["to_release"]
    for change in GROUND_TRUTH["changes"]:
        if change["from_path"]:
            assert section(md_docs, old, change["doc"], change["from_path"]), change["id"]
        if change["to_path"]:
            assert section(md_docs, new, change["doc"], change["to_path"]), change["id"]
        if change["kind"] == "added":
            assert not section(md_docs, old, change["doc"], change["to_path"]), change["id"]
        if change["kind"] == "removed":
            assert not section(md_docs, new, change["doc"], change["from_path"]), change["id"]


def test_modified_sections_actually_differ(md_docs):
    old, new = GROUND_TRUTH["from_release"], GROUND_TRUTH["to_release"]
    for change in GROUND_TRUTH["changes"]:
        if change["kind"] == "modified":
            a = section(md_docs, old, change["doc"], change["from_path"])
            b = section(md_docs, new, change["doc"], change["to_path"])
            assert a.text != b.text, change["id"]


def test_unchanged_sections_are_identical(md_docs):
    old, new = GROUND_TRUTH["from_release"], GROUND_TRUTH["to_release"]
    for entry in GROUND_TRUTH["unchanged"]:
        a = section(md_docs, old, entry["doc"], entry["path"])
        b = section(md_docs, new, entry["doc"], entry["path"])
        assert a.text == b.text, entry


def test_contradictions_and_injections_are_present(md_docs):
    for k in GROUND_TRUTH["contradictions"]:
        for st in k["statements"]:
            assert st["says"] in section(md_docs, k["release"], st["doc"], st["path"]).text, k["id"]
    for inj in GROUND_TRUTH["prompt_injections"]:
        text = section(md_docs, inj["release"], inj["doc"], inj["path"]).text
        assert inj["text_contains"] in text, inj["id"]
