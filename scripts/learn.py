#!/usr/bin/env python3
"""ユーザー端末から投函された学習データ（写真は含まない特徴ベクトル＋読み取り文字）を検証し、
全ユーザー向けの参照ベクトル references.json と「みんなの登録」項目を更新する。

投函ファイル: inbox/<inbox>/**/*.json（アプリの LearningService.Submission）
検証（自動で正しさを採点し、しきい値以上だけ採用）:
  +1.0  バーコードがラベルのJANと一致（ほぼ確実）
  -1.0  バーコードが別のカタログ商品と一致（ラベル違い→不採用）
  +0.6  読み取り文字に「No.<番号>」がある
  +0.4  読み取り文字に車名の特徴的な語がある
  +0.4/+0.25/+0.1  端末での自己診断で正解が1位/3位以内/10位以内
  +0.2  自己診断の距離が近い
  +0.5  他の端末が同じ正解で登録したベクトルと近い（合意）
  -0.6  別の正解で採用済みのベクトルと非常に近い（矛盾）
  採用 >= 0.6、保留 0.3〜0.6（後の合意で昇格）、不採用 < 0.3
カタログにない名前（custom）は、カタログ全体（過去分含む）から名前検索して一致すれば JAN に付け替える。
一致しなければ community 項目として扱い、2端末以上が登録したらカタログへ公開する。
"""
import json, os, re, sys, glob, math, unicodedata, difflib, datetime, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "inbox")
CATALOG = os.path.join(ROOT, "catalog.json")
STATE_DIR = os.path.join(ROOT, "learning")
ACCEPTED = os.path.join(STATE_DIR, "accepted.json")
PENDING = os.path.join(STATE_DIR, "pending.json")
REJECTED = os.path.join(STATE_DIR, "rejected.json")
COMMUNITY = os.path.join(STATE_DIR, "community_items.json")
REFERENCES = os.path.join(ROOT, "references.json")
ACCEPT = 0.6
HOLD = 0.3
MAX_PER_CODE = 16
MAX_PER_DEVICE = 6

os.makedirs(STATE_DIR, exist_ok=True)


def load(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def save(path, obj):
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def normalize(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s・\-‐－/／()（）]", "", s).upper()


def tokens(name):
    n = unicodedata.normalize("NFKC", name or "")
    parts = re.split(r"[\s・\-‐－/／()（）]+", n)
    return [normalize(p) for p in parts if len(normalize(p)) >= 3]


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-6
    nb = math.sqrt(sum(x * x for x in b)) or 1e-6
    return dot / (na * nb)


def main():
    catalog = load(CATALOG, {"items": []})["items"]
    by_code = {it["productCode"]: it for it in catalog if it.get("productCode")}
    names = {it["productCode"]: it.get("name", "") for it in catalog if it.get("productCode")}
    accepted = load(ACCEPTED, {})     # id -> record
    pending = load(PENDING, {})       # id -> submission
    rejected = load(REJECTED, {})     # id -> reason
    community = load(COMMUNITY, {})   # code -> {name, series, devices:[...], count}

    files = sorted(glob.glob(os.path.join(INBOX, "inbox", "**", "*.json"), recursive=True))
    new = 0
    for f in files:
        try:
            sub = json.load(open(f, encoding="utf-8"))
        except Exception:
            rejected[os.path.basename(f)] = "unreadable"
            continue
        sid = sub.get("id") or os.path.basename(f)[:-5]
        if sid in accepted or sid in rejected:
            continue
        if not isinstance(sub.get("vectors"), list) or not sub["vectors"] or \
           any(len(v) != 768 for v in sub["vectors"]) or len(sub["vectors"]) > 6:
            rejected[sid] = "bad vectors"
            continue
        pending[sid] = sub
        new += 1

    # ── 名前だけの登録（custom）はカタログ全体から検索して JAN に付け替える
    all_names = [(code, normalize(n)) for code, n in names.items() if n]
    for sid, sub in pending.items():
        label = sub.get("label") or {}
        if label.get("productCode"):
            continue
        q = normalize(label.get("name", ""))
        if len(q) < 3:
            continue
        best, best_ratio = None, 0
        for code, n in all_names:
            if q == n or (len(q) >= 5 and (q in n or n in q)):
                best, best_ratio = code, 1.0
                break
            r = difflib.SequenceMatcher(None, q, n).ratio()
            if r > best_ratio:
                best, best_ratio = code, r
        if best and best_ratio >= 0.86:
            label["productCode"] = best
            label["resolvedFrom"] = "name-search"
            label["number"] = by_code[best].get("number")
            sub["label"] = label

    # ── 採点
    def code_of(sub):
        label = sub.get("label") or {}
        if label.get("productCode"):
            return label["productCode"]
        return "custom:" + normalize(label.get("name", ""))

    def accepted_vectors(exclude_device=None):
        out = {}
        for rec in accepted.values():
            if exclude_device and rec["device"] == exclude_device:
                continue
            out.setdefault(rec["code"], []).extend(rec["vectors"])
        return out

    changed = True
    rounds = 0
    while changed and rounds < 3:
        changed = False
        rounds += 1
        for sid in list(pending.keys()):
            sub = pending[sid]
            label = sub.get("label") or {}
            code = code_of(sub)
            device = sub.get("device", "")
            score = 0.0
            reasons = []
            barcode = sub.get("barcode")
            if barcode:
                if barcode == label.get("productCode"):
                    score += 1.0; reasons.append("barcode")
                elif barcode in by_code:
                    rejected[sid] = f"barcode mismatch ({barcode})"
                    del pending[sid]
                    continue
            ocr = normalize(" ".join(sub.get("ocr") or []))
            number = label.get("number") or by_code.get(label.get("productCode", ""), {}).get("number")
            if number and re.search(r"NO\.?0*%d(?!\d)" % number, ocr):
                score += 0.6; reasons.append("ocr-number")
            name = names.get(label.get("productCode", ""), label.get("name", ""))
            if any(t in ocr for t in tokens(name)):
                score += 0.4; reasons.append("ocr-name")
            rank = sub.get("selfRank")
            if rank == 1:
                score += 0.4
            elif rank and rank <= 3:
                score += 0.25
            elif rank and rank <= 10:
                score += 0.1
            if (sub.get("selfDistance") or 9) < 0.45:
                score += 0.2
            others = accepted_vectors(exclude_device=device)
            same = others.get(code, [])
            best_same = max((cos(v, w) for v in sub["vectors"] for w in same), default=0)
            if best_same >= 0.80:
                score += 0.5; reasons.append("consensus")
            conflict = 0
            for other_code, vecs in others.items():
                if other_code == code:
                    continue
                m = max((cos(v, w) for v in sub["vectors"] for w in vecs), default=0)
                conflict = max(conflict, m)
            if conflict >= 0.85 and best_same < conflict:
                score -= 0.6; reasons.append("conflict")
            # 同一端末は同じ正解につき最大 MAX_PER_DEVICE 本まで
            own = sum(len(r["vectors"]) for r in accepted.values() if r["device"] == device and r["code"] == code)
            if own >= MAX_PER_DEVICE:
                rejected[sid] = "device quota"
                del pending[sid]
                continue
            if score >= ACCEPT:
                accepted[sid] = {
                    "code": code, "device": device, "score": round(score, 2), "reasons": reasons,
                    "vectors": [[round(x, 4) for x in v] for v in sub["vectors"]],
                    "createdAt": sub.get("createdAt"), "name": label.get("name"),
                    "series": label.get("series"), "isCustom": not label.get("productCode"),
                }
                del pending[sid]
                changed = True
            elif score < HOLD:
                rejected[sid] = f"low score {score:.2f} {reasons}"
                del pending[sid]

    # ── みんなの登録（カタログに無い名前）: 2端末以上で公開
    for rec in accepted.values():
        if not rec["code"].startswith("custom:"):
            continue
        c = community.setdefault(rec["code"], {"productCode": rec["code"], "name": rec.get("name") or rec["code"][7:],
                                                "series": "みんなの登録", "devices": [], "isCurrent": True})
        if rec["device"] not in c["devices"]:
            c["devices"].append(rec["device"])
        c["count"] = len(c["devices"])
    published = [
        {"productCode": c["productCode"], "name": c["name"], "series": c["series"], "isCurrent": True,
         "number": None, "source": "community", "detail": f"{c['count']}人のユーザーが登録した、カタログにないトミカです。"}
        for c in community.values() if c.get("count", 0) >= 2
    ]
    save(os.path.join(STATE_DIR, "community_published.json"), published)

    # ── references.json（1商品あたり最大 MAX_PER_CODE 本。互いに離れたものを優先）
    per_code = {}
    for rec in accepted.values():
        per_code.setdefault(rec["code"], []).extend(rec["vectors"])
    items = []
    for code, vecs in per_code.items():
        chosen = []
        for v in vecs:
            if len(chosen) >= MAX_PER_CODE:
                break
            if all(cos(v, w) < 0.97 for w in chosen):
                chosen.append(v)
        items.append({"productCode": code, "vectors": chosen, "count": len(vecs)})
    refs = {
        "version": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M"),
        "items": sorted(items, key=lambda x: x["productCode"]),
    }
    json.dump(refs, open(REFERENCES, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    save(ACCEPTED, accepted)
    save(PENDING, pending)
    save(REJECTED, rejected)
    save(COMMUNITY, community)

    # 処理済み投函を processed/ へ移す（inbox リポジトリ側）
    moved = 0
    for f in files:
        rel = os.path.relpath(f, os.path.join(INBOX, "inbox"))
        dest = os.path.join(INBOX, "processed", rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(f, dest)
        moved += 1
    print(f"new={new} accepted={len(accepted)} pending={len(pending)} rejected={len(rejected)} "
          f"refs={len(items)} community_published={len(published)} moved={moved}")


if __name__ == "__main__":
    main()
