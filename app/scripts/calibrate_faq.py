"""Avalia limiares de FAQ com perguntas rotuladas locais; não altera coleções."""
import argparse
import asyncio
import json
from pathlib import Path

from app.infrastructure.vectorstore.faq import FaqVectors


def metrics(rows, threshold):
    tp = fp = fn = 0
    for expected, snippets in rows:
        found = {s["fonte"] for s in snippets if s["relevancia"] >= threshold}
        tp += len(found & expected)
        fp += len(found - expected)
        fn += len(expected - found)
    return {"threshold": threshold, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None, "false_positives": fp,
            "false_negatives": fn}


async def evaluate(cases):
    import app.infrastructure.vectorstore.faq as faq
    # Só este processo recebe o limiar zero, para comparar todos os candidatos.
    faq.FAQ_SCORE_THRESHOLD = 0.0
    store = FaqVectors()
    try:
        rows = []
        for case in cases:
            rows.append((set(case["expected_sources"]), await store.search(case["query"])))
        return [metrics(rows, value) for value in (0.25, 0.30, 0.35, 0.40, 0.45, 0.50)]
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSONL privado: query e expected_sources (nomes dos PDFs)")
    parser.add_argument("--max-cases", type=int, default=20)
    args = parser.parse_args()
    try:
        cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not 1 <= len(cases) <= min(100, args.max_cases):
            raise ValueError()
        if any(not isinstance(c.get("query"), str) or not c["query"].strip()
               or len(c["query"]) > 1000 or not isinstance(c.get("expected_sources"), list)
               or any(not isinstance(x, str) for x in c["expected_sources"]) for c in cases):
            raise ValueError()
        print(json.dumps(asyncio.run(evaluate(cases))))
    except Exception as error:
        parser.exit(1, "Calibracao interrompida: " + type(error).__name__ + ". Nenhuma colecao alterada.\n")


if __name__ == "__main__":
    main()
