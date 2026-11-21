"""属性图对照实验（freight/parity_lab.py）的结构与诚实性测试。"""
from __future__ import annotations

import pytest

from freight.parity_lab import build_report


@pytest.fixture(scope="module")
def report():
    return build_report()


def test_six_questions_present(report):
    assert [q["id"] for q in report["questions"]] == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]


def test_q1_honestly_admits_property_graph_can(report):
    """Q1 必须如实承认属性图能做——这是对照实验可信的前提。"""
    q1 = report["questions"][0]
    assert "属性图也能做" in q1["dimension"] or "不构成差异" in q1["dimension"]


def test_q4_ontology_only(report):
    q4 = report["questions"][3]
    assert "本体独有" in q4["dimension"]
    assert q4["property_graph"]["answered"] is False


def test_drift_experiment_property_graph_inconsistent(report):
    d = report["drift_experiment"]["property_graph"]
    assert d["consistent"] is False and d["detectable"] is False
    assert d["diagnosis"] != d["preflight"]


def test_drift_experiment_ontology_single_source(report):
    d = report["drift_experiment"]["ontology"]
    assert d["same_source"] is True and d["detectable"] is True


def test_hardcoded_lines_positive(report):
    assert report["property_graph_hardcoded_business_lines"] > 0


def test_full_alignment_estimate_positive(report):
    est = report["full_alignment_estimate"]
    assert est["total_estimated_lines"] > 1000
    assert sum(est["breakdown"].values()) == est["total_estimated_lines"]


def test_fairness_declared(report):
    f = report["fairness"]
    assert "禁止读" in f["property_graph_reads"] or "禁止" in f["note"]
