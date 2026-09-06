#!/usr/bin/env python3
"""タカラトミー公式サイトからトミカのカタログを収集し、catalog.json と画像を更新する。

収集元:
  - レギュラー/ロングタイプ現行ラインナップ: lineup/regular/ , 021-040.htm … 141-150.htm
  - ドリームトミカ現行ラインナップ:          lineup/dream/
  - 月別新製品ページ（2020年1月〜）:           new/YYMM.htm  （全シリーズ・入れ替え前商品も含む）

方針:
  - JAN(productCode) をキーに「一度取得した商品は決して消さない」（過去分・廃盤も残す）
  - seed/catalog_seed.json（アプリ同梱の解説付きカタログ）の内容を優先し、空欄だけを埋める
  - レギュラーの isCurrent は現行ラインナップと入れ替え情報から自動判定
"""
import json, re, sys, os, time, html, datetime, hashlib, io
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://www.takaratomy.co.jp/products/tomica/"
UA = "Mozilla/5.0 (Macintosh) TomiColleCatalogSync/1.0"
CACHE = os.path.join(ROOT, ".cache")
OUT = os.path.join(ROOT, "catalog.json")
IMG_DIR = os.path.join(ROOT, "images")
SEED = os.path.join(ROOT, "seed", "catalog_seed.json")
TODAY = datetime.date.today()

os.makedirs(CACHE, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)


def fetch(url, binary=False, cache_days=0):
    key = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(CACHE, key)
    if os.path.exists(path) and cache_days:
        age = time.time() - os.path.getmtime(path)
        if age < cache_days * 86400:
            data = open(path, "rb").read()
            return data if binary else data.decode("utf-8", "ignore")
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=30) as r:
                data = r.read()
            open(path, "wb").write(data)
            time.sleep(0.4)
            return data if binary else data.decode("utf-8", "ignore")
        except HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (attempt + 1))
        except URLError:
            time.sleep(2 * (attempt + 1))
    return None


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = s.replace("　", " ")
    return re.sub(r"\s+", " ", s).strip()


def resolve(url, page_url):
    if url.startswith("http"):
        return url
    base = page_url.rsplit("/", 1)[0] + "/"
    while url.startswith("../"):
        url = url[3:]
        base = base.rstrip("/").rsplit("/", 1)[0] + "/"
    return base + url


def series_for_number(n):
    return "ロングタイプ" if n and n >= 121 else "レギュラー"


NUMBER_RE = re.compile(r"No\.\s*(\d{1,3})", re.I)
JAN_RE = re.compile(r"takaratomymall\.jp/shop/g/g(\d{13})")


def parse_blocks(page_html, page_url, block_class):
    """block_class（lineup-box / category_tomica）ごとに分割し、直前の h2 見出しをシリーズ名として付ける。"""
    items = []
    pattern = re.compile(r'<div class="%s[^"]*">' % block_class)
    positions = [m.start() for m in pattern.finditer(page_html)]
    headings = [(m.start(), clean(m.group(1))) for m in
                re.finditer(r'<h2 class="series-titles[^"]*"[^>]*>(.*?)</h2>', page_html, re.S)]
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(page_html)
        block = page_html[start:end]
        series = ""
        for pos, title in headings:
            if pos < start:
                series = title
        name_m = re.search(r'class="CarName[^"]*"[^>]*>(.*?)</(?:p|h3|h4)>', block, re.S)
        if not name_m:
            continue
        name = clean(name_m.group(1))
        num_m = NUMBER_RE.search(name)
        # 入れ替え前商品（mark-irekae）のリンクは除いてから本商品のJANを拾う
        irekae = None
        ire_m = re.search(r'mark-irekae[^>]*>\s*<a[^>]*href="[^"]*g(\d{13})[^"]*"[^>]*>(.*?)</a>', block, re.S)
        if ire_m:
            irekae = {"productCode": ire_m.group(1), "name": clean(ire_m.group(2))}
            block_wo = block[:ire_m.start()] + block[ire_m.end():]
        else:
            block_wo = block
        jan_m = JAN_RE.search(block_wo)
        jan = jan_m.group(1) if jan_m else None
        imgs = [resolve(u, page_url) for u in re.findall(r'<img[^>]+src="([^"]+\.(?:jpg|jpeg|png|webp))"', block)
                if "/mark/" not in u and "btn_" not in u and "/img/" not in u]
        action_m = re.search(r'class="mark-action"[^>]*>(.*?)</p>', block, re.S)
        price_m = re.search(r'class="CarPrice"[^>]*>(.*?)</(?:p|div)>', block, re.S)
        price = clean(price_m.group(1)) if price_m else ""
        rel_m = re.search(r"(\d{4})年(\d{1,2})月", price)
        release = f"{rel_m.group(1)}年{int(rel_m.group(2))}月" if rel_m else ""
        # 「No.6 マツダ CX-5」→「マツダ CX-5」（アプリ側が No. を付けて表示する）
        display_name = re.sub(r"^\s*No\.\s*\d{1,3}\s*", "", name, flags=re.I).strip() or name
        items.append({
            "name": display_name,
            "productCode": jan,
            "number": int(num_m.group(1)) if num_m else None,
            "series": series,
            "images": imgs,
            "action": clean(action_m.group(1)) if action_m else "",
            "releaseDate": release,
            "replaces": irekae,
        })
    return items


def normalize_series(title, number):
    t = title.replace(" ", "")
    if t in ("トミカシリーズ", "トミカ", ""):
        return series_for_number(number)
    return title.strip()


def load_store():
    store = {}
    if os.path.exists(OUT):
        prev = json.load(open(OUT, encoding="utf-8"))
        for it in prev.get("items", []):
            if it.get("productCode"):
                store[it["productCode"]] = it
    seed = json.load(open(SEED, encoding="utf-8"))
    for it in seed:
        jan = it.get("productCode")
        if not jan:
            continue
        cur = store.get(jan, {})
        merged = dict(it)
        merged.update({k: v for k, v in cur.items() if v not in (None, "", [])})
        # seed の解説・仕様など「人が書いた情報」は seed を優先
        for k in ("name", "detail", "action", "color", "releaseDate", "packageSize", "productCode"):
            if it.get(k):
                merged[k] = it[k]
        merged.setdefault("source", "seed")
        store[jan] = merged
    return store


def fill(store, jan, **fields):
    it = store.setdefault(jan, {"productCode": jan, "source": "official"})
    for k, v in fields.items():
        if v in (None, "", []):
            continue
        if k == "images":
            it["imageSources"] = list(dict.fromkeys((it.get("imageSources") or []) + v))
        elif not it.get(k):
            it[k] = v
    return it


def month_range():
    y, m = 2020, 1
    end = (TODAY.year, TODAY.month)
    # 3か月先まで（予告ページが先行公開される）
    ey, em = end
    em += 3
    while em > 12:
        em -= 12
        ey += 1
    while (y, m) <= (ey, em):
        yield f"{y % 100:02d}{m:02d}", (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1


def merge_community(store, log):
    """learn.py が公開した「みんなの登録」（カタログに無い名前を2端末以上が登録）をカタログへ合流"""
    path = os.path.join(ROOT, "learning", "community_published.json")
    if not os.path.exists(path):
        return
    published = json.load(open(path, encoding="utf-8"))
    for it in published:
        cur = store.setdefault(it["productCode"], {})
        for k, v in it.items():
            if k == "name" and cur.get("name"):
                continue
            cur[k] = v
        cur.setdefault("isCurrent", True)
        cur.setdefault("status", "current")
    log.append(f"community items: {len(published)}")


def main():
    store = load_store()
    log = []
    merge_only = "--merge-only" in sys.argv
    if merge_only:
        # 公式サイトへは行かず、蓄積済みデータ＋みんなの登録だけで catalog.json を書き直す
        merge_community(store, log)
        for it in store.values():
            it.setdefault("isCurrent", True)
            it.setdefault("status", "current" if it.get("isCurrent") else "discontinued")
        write_output(store, log)
        return

    # ── 1. 現行レギュラー/ロングタイプ
    current_regular = {}   # jan -> number
    pages = ["lineup/regular/"] + [f"lineup/regular/{a:03d}-{b:03d}.htm" for a, b in
                                   [(21, 40), (41, 60), (61, 80), (81, 100), (101, 120), (121, 140), (141, 150)]]
    for p in pages:
        url = BASE + p
        h = fetch(url)
        if not h:
            log.append(f"WARN lineup page missing: {p}")
            continue
        for it in parse_blocks(h, url, "lineup-box"):
            if not it["productCode"] or not it["number"]:
                continue
            current_regular[it["productCode"]] = it["number"]
            fill(store, it["productCode"], name=it["name"], number=it["number"],
                 series=series_for_number(it["number"]), action=it["action"], images=it["images"])
            store[it["productCode"]]["lineupImage"] = it["images"][0] if it["images"] else ""
    log.append(f"current regular lineup: {len(current_regular)}")

    # ── 2. ドリームトミカ現行
    url = BASE + "lineup/dream/"
    h = fetch(url)
    dream_current = set()
    if h:
        for it in parse_blocks(h, url, "lineup-box"):
            if not it["productCode"]:
                continue
            dream_current.add(it["productCode"])
            fill(store, it["productCode"], name=it["name"], series="ドリームトミカ", images=it["images"])
    log.append(f"dream lineup: {len(dream_current)}")

    # ── 3. 月別新製品ページ（過去分含む）
    replacements = []  # (yymm, new_jan, old_jan, old_name)
    monthly_seen = 0
    for yymm, (y, m) in month_range():
        url = BASE + f"new/{yymm}.htm"
        h = fetch(url, cache_days=30 if (y, m) < (TODAY.year, TODAY.month) else 0)
        if not h:
            continue
        for it in parse_blocks(h, url, "category_tomica") + parse_blocks(h, url, "lineup-box"):
            if not it["productCode"]:
                continue
            monthly_seen += 1
            series = normalize_series(it["series"], it["number"])
            number = it["number"] if series in ("レギュラー", "ロングタイプ") else None
            fill(store, it["productCode"], name=it["name"], number=number, series=series,
                 action=it["action"], releaseDate=it["releaseDate"] or f"{y}年{m}月", images=it["images"])
            store[it["productCode"]].setdefault("firstSeen", yymm)
            if it["replaces"] and series in ("レギュラー", "ロングタイプ"):
                old = it["replaces"]
                onum_m = NUMBER_RE.search(old["name"])
                old_name = re.sub(r"^\s*No\.\s*\d{1,3}\s*", "", old["name"], flags=re.I).strip() or old["name"]
                fill(store, old["productCode"], name=old_name, number=int(onum_m.group(1)) if onum_m else number,
                     series=series)
                replacements.append((yymm, it["productCode"], old["productCode"], (y, m)))
    log.append(f"monthly blocks: {monthly_seen}, replacements: {len(replacements)}")

    # ── 4. 現行判定
    #   公式ラインナップ（20台ずつのページ）は月次の入れ替えより更新が遅れることがあるため、
    #   「発売月が今月以前の入れ替え情報」を最優先、次にラインナップ、の順で番号の持ち主を決める。
    for jan, it in store.items():
        it.setdefault("isCurrent", True)
        it.pop("status", None)
    now = (TODAY.year, TODAY.month)
    owner = {}   # number -> jan（その番号の現行商品）
    for jan, n in current_regular.items():
        owner[n] = jan
    for yymm, new_jan, old_jan, ym in replacements:
        new_it, old_it = store[new_jan], store[old_jan]
        if ym <= now:
            old_it["isCurrent"] = False
            old_it["status"] = "discontinued"
            old_it["discontinuedDate"] = f"{ym[0]}年{ym[1]}月"
            old_it["replacedBy"] = new_jan
            new_it["replaces"] = old_jan
            new_it["isCurrent"] = True
            new_it["status"] = "current"
            if new_it.get("number"):
                owner[new_it["number"]] = new_jan
        else:
            # 発売前の予告品。店頭予約・発売日にすぐ判定できるよう現行として見せる（旧品はまだ現行）
            new_it["status"] = "upcoming"
            new_it["isCurrent"] = True
            new_it["replaces"] = old_jan
    for jan, it in store.items():
        if it.get("series") not in ("レギュラー", "ロングタイプ") or not it.get("number"):
            continue
        if it.get("status") == "upcoming":
            continue
        holder = owner.get(it["number"])
        if holder == jan:
            it["isCurrent"] = True
            it["status"] = "current"
        elif holder:
            it["isCurrent"] = False
            it["status"] = "discontinued"
            if not it.get("replacedBy"):
                it["replacedBy"] = holder
    for jan, it in store.items():
        it.setdefault("status", "current" if it.get("isCurrent", True) else "discontinued")

    # ── 5. 画像ミラー（判定AIの参照用・384px）
    try:
        from PIL import Image
    except ImportError:
        Image = None
        log.append("WARN Pillow missing; images skipped")
    mirrored = 0
    for jan, it in store.items():
        sources = ([it["lineupImage"]] if it.get("lineupImage") else []) + (it.get("imageSources") or [])
        target = os.path.join(IMG_DIR, f"{jan}.jpg")
        if os.path.exists(target):
            it["imageURL"] = f"images/{jan}.jpg"
            continue
        if not Image or not sources:
            continue
        for src in sources:
            data = fetch(src, binary=True, cache_days=365)
            if not data:
                continue
            try:
                im = Image.open(io.BytesIO(data)).convert("RGB")
                im.thumbnail((384, 384))
                im.save(target, "JPEG", quality=82)
                it["imageURL"] = f"images/{jan}.jpg"
                mirrored += 1
                break
            except Exception as e:  # noqa
                continue
    log.append(f"images mirrored now: {mirrored}, total: {len(os.listdir(IMG_DIR))}")

    merge_community(store, log)
    write_output(store, log)


def write_output(store, log):
    def sort_key(it):
        s = it.get("series", "")
        order = {"レギュラー": 0, "ロングタイプ": 1, "みんなの登録": 9}.get(s, 2)
        return (order, it.get("number") or 9999, it.get("name", ""))
    items = sorted(store.values(), key=sort_key)
    for it in items:
        it.pop("imageSources", None)
        it.pop("lineupImage", None)
    out = {
        "version": TODAY.strftime("%Y%m%d") + "." + hashlib.sha1(
            json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8],
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "items": items,
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cur = sum(1 for i in items if i.get("isCurrent"))
    log.append(f"catalog items: {len(items)} (current {cur}, past {len(items) - cur})")
    print("\n".join(log))


if __name__ == "__main__":
    main()
