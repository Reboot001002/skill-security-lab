import math
import re
from collections import Counter
from .common import digest


def tokens(text):
    # Stable ASCII words plus individual CJK characters; fixtures use English.
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def retrieve(manifest, scenario, config):
    skills = [s for s in manifest["skills"] if s["split"] == scenario["split"]]
    documents = [tokens(s["name"] + " " + s["description"]) for s in skills]
    counts = [Counter(d) for d in documents]
    n = len(documents)
    mean_len = sum(map(len, documents)) / max(n, 1)
    query = set(tokens(scenario["query"]))
    df = {term: sum(term in d for d in counts) for term in query}
    result = []
    for skill, doc, count in zip(skills, documents, counts):
        score = 0.0
        for term in query:
            freq = count[term]
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * freq * 2.5 / (freq + 1.5 * (0.25 + 0.75 * len(doc) / max(mean_len, 1)))
        result.append({"id": skill["id"], "score": score, "hash": skill["versions"][scenario["version"]]["hash"]})
    result.sort(key=lambda item: (-item["score"], item["id"]))
    maximum = result[0]["score"] if result else 0
    filtered = []
    if maximum > 0:
        for item in result:
            if item["score"] > 0 and item["score"] / maximum >= config["relevance_floor"]:
                filtered.append({**item, "normalized_score": item["score"] / maximum})
    pool = filtered[:config["recall_k"]]
    return {"query": scenario["query"], "retriever": "bm25-k1=1.5-b=0.75-tokenizer-v1",
            "pool": pool, "fingerprint": digest(pool), "relevance_floor": config["relevance_floor"]}


def admit(check, threshold):
    return not check["unknown"] and not check["explicit_violation"] and check["score"] >= threshold


def policy(group, pool, context, static, config):
    top_k = config["top_k"]
    if group == "A":
        return pool[:top_k]
    checks = static if group == "E" else context
    threshold = config["static_threshold"] if group == "E" else config["threshold"]
    source = pool[:top_k] if group == "B" else pool
    candidates = [item for item in source if admit(checks[item["id"]]["decision"], threshold)]
    if group in ("D", "E"):
        candidates.sort(key=lambda item: (-checks[item["id"]]["decision"]["score"], -item["score"], item["id"]))
    return candidates[:top_k]
