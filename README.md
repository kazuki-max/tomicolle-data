# tomicolle-data

トミコレ（iOSアプリ）の配信データ。GitHub Pages で静的配信する。

- `catalog.json` … 全トミカカタログ（現行＋過去分・全シリーズ）。`scripts/sync_catalog.py` が公式サイトを毎週巡回して更新。
- `images/<JAN>.jpg` … 判定AI用の公式商品画像（384px）。
- `references.json` … ユーザー端末から投函された学習データを `scripts/learn.py` が検証・採用した特徴ベクトル。
- `learning/` … 採用・保留・不採用の記録と「みんなの登録」。
- `config.json` … アプリが読む設定（学習データの投函先など）。

投函先は別リポジトリ `tomicolle-inbox`（写真は含まない。特徴ベクトル・読み取り文字・正解ラベルのみ）。
