"""خوشه‌بندی و انتخاب رکورد کانونیکال (Union-Find)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..dedup.fingerprint import blocking_keys
from ..dedup.similarity import BusinessView, compare


@dataclass
class Cluster:
    canonical_index: int
    members: list[int]
    score: float
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


def canonical_choice(views: list[BusinessView], members: list[int], scores: dict[int, int] | None = None) -> int:
    """انتخاب رکورد کانونیکال: بیشترین امتیاز اعتبار، سپس کامل‌ترین رکورد، سپس کوچک‌ترین شناسه."""
    scores = scores or {}

    def sort_key(idx: int):
        v = views[idx]
        completeness = sum(
            1
            for x in (v.name, v.address, v.domain, v.city_name)
            if x
        ) + (1 if v.phones else 0)
        return (-scores.get(idx, 0), -completeness, v.id if v.id is not None else idx)

    return sorted(members, key=sort_key)[0]


def find_duplicate_clusters(
    views: list[BusinessView],
    *,
    scores: dict[int, int] | None = None,
    settings: Settings | None = None,
) -> tuple[list[Cluster], list[tuple[int, int, float, dict[str, float]]]]:
    """یافتن خوشه‌های تکراری با انسداد کلیدها.

    بازگشت: (خوشه‌ها، جفت‌های «نیازمند بازبینی انسانی»)
    """
    s = settings or get_settings()
    uf = UnionFind(len(views))
    pair_info: dict[tuple[int, int], dict] = {}

    blocks: dict[str, list[int]] = {}
    for i, v in enumerate(views):
        for key in blocking_keys(
            name=v.name, city_name=v.city_name, domain=v.domain, phones=v.phones, address=v.address
        ):
            blocks.setdefault(key, []).append(i)

    review_pairs: list[tuple[int, int, float, dict[str, float]]] = []
    compared: set[tuple[int, int]] = set()
    for members in blocks.values():
        if len(members) < 2 or len(members) > 400:  # انسدادهای بسیار بزرگ بی‌فایده‌اند
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                key = (min(a, b), max(a, b))
                if key in compared:
                    continue
                compared.add(key)
                ps = compare(views[a], views[b])
                if ps.score >= s.dedup_merge_threshold:
                    uf.union(a, b)
                    pair_info[key] = {"score": ps.score, "reasons": ps.reasons, "signals": ps.signals}
                elif ps.score >= s.dedup_review_threshold:
                    review_pairs.append((a, b, ps.score, ps.signals))

    groups: dict[int, list[int]] = {}
    for i in range(len(views)):
        groups.setdefault(uf.find(i), []).append(i)

    clusters: list[Cluster] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canon = canonical_choice(views, members, scores)
        best = 0.0
        reasons: list[str] = []
        signals: dict[str, float] = {}
        for k, info in pair_info.items():
            if k[0] in members and k[1] in members:
                if info["score"] > best:
                    best = info["score"]
                    reasons = info["reasons"]
                    signals = info["signals"]
        clusters.append(
            Cluster(canonical_index=canon, members=members, score=best, reasons=reasons, signals=signals)
        )

    return clusters, review_pairs
