"""Local lexical similarity. No external API, model download or message collection."""
import math
import re
from collections import Counter

STOP = set("будет будут был была были ли когда как какой какая какое какие кто что где сколько до после через этот эта это эти году года сегодня завтра yes no will the a an is are be by in on at of to and or".split())


def features(title):
    words = [w for w in re.findall(r"[^\W\d_]+", title.lower().replace("ё", "е"))
             if len(w) >= 3 and w not in STOP]
    # Prefixes and character fragments tolerate Russian inflections, not synonyms.
    return {part for w in words for part in ([w[:4]] if len(w) < 5 else
            [w[i:i+4] for i in range(len(w)-3)])}


def title_scores(candidates, positive, negative):
    documents = {pk: features(title) for pk, title in candidates}
    pos = [features(title) for title in positive]
    neg = [features(title) for title in negative]
    corpus = list(documents.values()) + pos + neg
    frequency = Counter(token for doc in corpus for token in doc)
    weights = {token: 1 + math.log((1+len(corpus))/(1+count)) for token, count in frequency.items()}

    def similarity(left, right):
        norm = math.sqrt(sum(weights[t]**2 for t in left) * sum(weights[t]**2 for t in right))
        return sum(weights[t]**2 for t in left & right)/norm if norm else 0

    return {pk: round(100*(max((similarity(doc, p) for p in pos), default=0)
                          - max((similarity(doc, n) for n in neg), default=0)))
            for pk, doc in documents.items()}
